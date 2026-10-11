import asyncio
import base64
from contextlib import closing
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, wechat_service as wx, mobile_task_service as mobile, mobile_file_service as files
from app import agent_task_service as tasks, model_runtime, artifact_service, agent_file_service
from app.secret_store import unprotect_secret
from app.config import settings
from app.routes.wechat import router
from test_mobile_tasks import phone_setup, handoff, outbox, OWNER
from test_runtime_safety_v2 import isolated

CONFIG = {'enabled':True,'token':'synthetic-secret','bot_id':'test@im.bot',
          'user_id':'owner@im.wechat','base_url':wx.API}


def configure():
    wx.save_config(CONFIG)
    return wx.origin_for(CONFIG)


def incoming(text='/任务 新建 写报告', **updates):
    return {'message_id': 42, 'from_user_id':CONFIG['user_id'], 'to_user_id':CONFIG['bot_id'],
            'message_type':1,'message_state':2,'context_token':'synthetic-context',
            'item_list':[{'type':1,'text_item':{'text':text}}], **updates}


async def poll(message=None):
    with patch.object(wx, 'request', AsyncMock(return_value={'ret':0,'msgs':[message or incoming()], 'get_updates_buf':'cursor-1'})):
        await wx.poll_once()


def test_credentials_and_inbox_are_protected_and_public_status_redacted():
    async def run():
        configure(); await poll()
        with closing(db.get_conn()) as conn:
            state = conn.execute("SELECT value FROM wechat_state WHERE key='session'").fetchone()[0]
            inbox = conn.execute('SELECT payload FROM wechat_inbox').fetchone()[0]
        assert state.startswith('dpapi:') and 'synthetic-secret' not in state
        assert inbox.startswith('dpapi:') and '写报告' not in inbox
        assert 'synthetic' not in json.dumps(wx.public_status())
        assert wx.config()['token'] == CONFIG['token']
    asyncio.run(run())


@pytest.mark.parametrize('url', ['http://ilinkai.weixin.qq.com', 'https://evil.example',
    'https://weixin.qq.com.evil.example', 'https://user:pass@ilinkai.weixin.qq.com',
    'https://127.0.0.1', 'https://ilinkai.weixin.qq.com:9999', 'https://ilinkai.weixin.qq.com/path'])
def test_untrusted_api_redirect_rejected(url):
    with pytest.raises(ValueError): wx.trusted_url(url)


def test_cdn_must_be_official_and_not_an_api_host():
    assert wx.trusted_url('https://novac2c.cdn.weixin.qq.com/c2c/download?x=1', cdn=True)
    for url in ['https://localhost/a','https://ilinkai.weixin.qq.com/private','https://evil.cdn.weixin.qq.com.attacker.test/a']:
        with pytest.raises(ValueError): wx.trusted_url(url, cdn=True)


def test_qr_login_supports_redirect_and_verification_without_leaking_credentials():
    async def run():
        mock = AsyncMock(side_effect=[{'qrcode':'qr-session','qrcode_img_content':'https://weixin.qq.com/scan/synthetic'},
            {'status':'scaned_but_redirect','redirect_host':'ilinkai.weixin.qq.com'}, {'status':'need_verifycode'},
            {'status':'confirmed','bot_token':CONFIG['token'],'ilink_bot_id':CONFIG['bot_id'],
             'ilink_user_id':CONFIG['user_id'],'baseurl':wx.API}])
        with patch.object(wx,'request',mock):
            login = await wx.start_login()
            assert login['qr_image'].startswith('data:image/png;base64,')
            assert (await wx.login_status(login['login_id']))['status'] == 'scaned'
            assert (await wx.login_status(login['login_id']))['status'] == 'need_verifycode'
            result = await wx.login_status(login['login_id'],'123456')
        assert result == {'status':'confirmed'}
        assert wx.config() == CONFIG
        assert mock.await_args.kwargs['params']['verify_code'] == '123456'
    asyncio.run(run())


def test_expired_or_wrong_login_never_calls_network():
    async def run():
        with patch.object(wx, 'request', AsyncMock()) as api:
            assert (await wx.login_status('0'*32))['status'] == 'expired'
            api.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('updates', [{'from_user_id':'stranger'}, {'group_id':'group'},
                                  {'message_type':2}, {'message_state':1}, {'to_user_id':'otherbot'}])
def test_only_scanning_owner_private_complete_messages_are_accepted(updates):
    async def run():
        configure(); await poll(incoming(**updates))
        with closing(db.get_conn()) as conn:
            assert not conn.execute('SELECT * FROM wechat_inbox').fetchall()
    asyncio.run(run())


def test_wechat_task_shares_engine_but_not_qq_task_ownership():
    async def run():
        owner = configure(); handoff(OWNER,'qq')
        await poll(); await poll()
        with patch('app.agent_handoff_service.model_selection',return_value=('test-model','low')):
            assert await wx.consume_once()
            assert not await wx.consume_once()
        assert len(mobile.owner_tasks(owner)) == 1
        assert len(mobile.owner_tasks(OWNER)) == 1
        assert mobile.owner_tasks(owner)[0]['id'] != mobile.owner_tasks(OWNER)[0]['id']
        with closing(db.get_conn()) as conn:
            assert conn.execute('SELECT payload FROM wechat_inbox').fetchone()[0] == ''
    asyncio.run(run())


def test_session_switch_while_polling_drops_old_account_payload():
    async def run():
        configure()
        async def response(*args, **kwargs):
            wx.save_config({**CONFIG,'user_id':'new-owner','token':'new-token'})
            return {'ret':0,'msgs':[incoming()],'get_updates_buf':'old-cursor'}
        with patch.object(wx,'request',response): await wx.poll_once()
        with closing(db.get_conn()) as conn:
            assert not conn.execute('SELECT * FROM wechat_inbox').fetchall()
    asyncio.run(run())


def test_plain_chat_uses_handoff_tools_and_durable_reply():
    async def run():
        configure(); await poll(incoming('你好'))
        chat = AsyncMock(return_value=SimpleNamespace(replies=['你好呀'],task_handoff=None))
        with patch('app.chat_service.chat_with_ai',chat), patch('app.model_runtime.model_selection',return_value=('test-model','low')):
            await wx.consume_once()
        options = chat.await_args.kwargs
        assert options['source']=='wechat' and options['handoff_enabled'] and not options['agent_tools_enabled']
        assert options['task_origin']['user_id'] == CONFIG['user_id']
        assert '你好呀' in outbox()[0]['payload_json']
    asyncio.run(run())


def test_cancel_is_processed_while_plain_chat_waits_for_model():
    async def run():
        configure(); task = handoff(wx.origin_for(CONFIG),'wx-cancel')
        await poll(incoming('聊一会',message_id=1))
        started=asyncio.Event(); finish=asyncio.Event()
        async def slow_chat(*args,**kwargs):
            started.set(); await finish.wait()
            return SimpleNamespace(replies=['你好'],task_handoff=None)
        with patch('app.chat_service.chat_with_ai',slow_chat), patch('app.model_runtime.model_selection',return_value=('test-model','low')):
            ordinary=asyncio.create_task(wx.consume_once())
            await asyncio.wait_for(started.wait(),2)
            await poll(incoming('/任务 取消',message_id=2))
            assert await wx.consume_once(controls=True)
            assert tasks.get(task['id'])['status']=='cancelled'
            finish.set(); await ordinary
    asyncio.run(run())


def test_file_encryption_uses_protocol_padding_and_compatible_keys():
    key=bytes(range(16)); data=b'synthetic document content'
    encrypted=wx.crypt(data,key)
    assert len(encrypted)%16==0 and encrypted!=data
    assert wx.crypt(encrypted,key,decrypt=True)==data
    assert wx.aes_key(base64.b64encode(key).decode())==key
    assert wx.aes_key(base64.b64encode(key.hex().encode()).decode())==key
    with pytest.raises(ValueError): wx.aes_key(base64.b64encode(b'short').decode())


def test_file_upload_is_encrypted_and_requires_cdn_receipt():
    async def run():
        configure()
        task=handoff(wx.origin_for(CONFIG),'wx-file')
        artifact=agent_file_service.write_document('报告.txt','synthetic file',task['id'])
        path=__import__('pathlib').Path(artifact['path'])
        captured={}
        async def handler(request):
            captured['body']=request.content
            assert 'authorization' not in request.headers
            return httpx.Response(200,headers={'x-encrypted-param':'cdn-download'})
        real_client=httpx.AsyncClient
        api=AsyncMock(return_value={'upload_full_url':wx.CDN+'/upload?test=1'})
        with patch.object(wx,'request',api), patch('app.web_search_service._validate_public_url',AsyncMock()), \
             patch.object(wx.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs)):
            item=await wx.upload_file(CONFIG,path,'报告.txt')
        key=bytes.fromhex(api.await_args.kwargs['body']['aeskey'])
        assert wx.crypt(captured['body'],key,decrypt=True)==b'synthetic file'
        assert item['file_item']['media']['encrypt_query_param']=='cdn-download'
        assert item['file_item']['len']==str(len(b'synthetic file'))
    asyncio.run(run())


def test_document_receive_and_dedup_use_isolated_attachment_store():
    async def run():
        owner=configure()
        doc=incoming('',item_list=[{'type':4,'file_item':{'file_name':'材料.txt','len':'7','media':{}}}])
        await poll(doc)
        with patch.object(wx,'download_media',AsyncMock(return_value=b'testing')):
            await wx.consume_once()
        assert len(mobile.input_context(owner)[0]['attachment_ids'])==1
        assert not mobile.input_context(OWNER)[0]['attachment_ids']
        await poll(doc)
        assert not await wx.consume_once()
    asyncio.run(run())


def test_delivery_uses_bound_peer_context_and_honest_ack():
    async def run():
        owner=configure(); await poll(incoming('你好'))
        api=AsyncMock(return_value={'ret':0})
        with patch.object(wx,'request',api):
            receipt=await wx.deliver(owner,{'text':'hello'},client_id='stable-id')
        msg=api.await_args.kwargs['body']['msg']
        assert msg['to_user_id']==CONFIG['user_id']
        assert msg['context_token']=='synthetic-context' and msg['client_id']=='stable-id'
        assert receipt['status']=='ok'
        await wx.set_enabled(False)
        with pytest.raises(wx.ApiRejected): await wx.deliver(owner,{'text':'no'},client_id='id')
    asyncio.run(run())


def test_expired_session_disables_polling_and_never_echoes_raw_error():
    async def run():
        configure()
        transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'ret':-14,'errmsg':'secret-token-in-error'}))
        real_client=httpx.AsyncClient
        with patch.object(wx.httpx,'AsyncClient',lambda **kwargs:real_client(transport=transport,**kwargs)):
            with pytest.raises(wx.ApiRejected) as error: await wx.request('getupdates',value=CONFIG)
        assert 'secret-token' not in str(error.value)
        assert not wx.config()['enabled']
    asyncio.run(run())


def test_privacy_cancels_inflight_poll_and_records_no_late_messages():
    async def run():
        configure(); started=asyncio.Event()
        async def slow(*args,**kwargs): started.set(); await asyncio.Event().wait()
        with patch.object(wx,'request',slow):
            pending=asyncio.create_task(wx.poll_once())
            await asyncio.wait_for(started.wait(),2)
            await model_runtime.cancel_operations(automatic_only=True,reason='privacy')
            with pytest.raises(asyncio.CancelledError): await pending
        with closing(db.get_conn()) as conn:
            assert not conn.execute('SELECT * FROM wechat_inbox').fetchall()
    asyncio.run(run())


def test_settings_api_does_not_expose_bound_ids_or_tokens():
    configure(); app=FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        status=client.get('/api/wechat/status')
        assert status.status_code==200
        assert 'synthetic-secret' not in status.text and '@im.' not in status.text
        response=client.patch('/api/wechat/connection',json={'enabled':False})
        assert response.status_code==200 and not response.json()['enabled']
        assert client.delete('/api/wechat/connection').status_code==200
        assert not wx.config()


def test_disconnecting_inflight_poll_keeps_scheduler_alive_for_reconnect():
    async def run():
        configure(); started=asyncio.Event(); reconnected=asyncio.Event(); calls=0
        async def response(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:
                started.set(); await asyncio.Event().wait()
            reconnected.set()
            return {'ret':0,'msgs':[]}
        with patch.object(wx,'request',response):
            worker=asyncio.create_task(wx._worker(receive=True))
            try:
                await asyncio.wait_for(started.wait(),2)
                await wx.set_enabled(False)
                await asyncio.sleep(0.02)
                assert not worker.done()
                await wx.set_enabled(True)
                await asyncio.wait_for(reconnected.wait(),3)
                assert wx.public_status()['connected']
            finally:
                worker.cancel(); await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(run())
