import asyncio
from contextlib import closing
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import db, conversation_service as conversations, wechat_history as history
from app import mobile_task_service as mobile, mobile_file_service as files, wechat_service as wx
from app.routes.wechat import router
from test_mobile_tasks import phone_setup
from test_runtime_safety_v2 import isolated
from test_wechat_channel import configure, incoming, poll


def rows(conversation):
    with closing(db.get_conn()) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id',(conversation,))]


def test_bound_and_historical_wechat_conversations_are_visible_and_resolvable():
    origin=configure(); identifier=mobile.conversation_id(origin)
    assert any(x['id']==identifier and x['kind']=='wechat' for x in conversations.list_conversations())
    db.save_message('user','saved message',source='wechat',conversation_id=identifier)
    wx.save_config({})
    assert conversations.resolve_conversation_id(identifier)==identifier
    assert any(x['id']==identifier and x['preview']=='saved message' for x in conversations.list_conversations())
    with pytest.raises(ValueError): conversations.resolve_conversation_id('wechat_private_'+'0'*24)


def test_command_and_receipt_are_saved_once_and_delivery_is_not_assumed():
    async def run():
        origin=configure();identifier=mobile.conversation_id(origin)
        await poll(incoming('/任务 帮助'))
        assert await wx.consume_once(controls=True)
        assert not await wx.consume_once(controls=True)
        before=rows(identifier)
        assert len(before)==2
        assert before[0]['content']=='/任务 帮助'
        shown=conversations.public_messages(before)
        assert shown[1]['delivery_status']=='pending'
        history.backfill();history.backfill()
        assert len(rows(identifier))==2
        with patch.object(wx,'connected',return_value=True), patch.object(wx,'deliver',AsyncMock(return_value={'ret':0})):
            assert await mobile.flush_outbox()==1
        assert conversations.public_messages(rows(identifier))[1]['delivery_status']=='sent'
    asyncio.run(run())


def test_normal_model_reply_is_not_duplicated_by_outbox_or_backfill():
    origin=configure();identifier=mobile.conversation_id(origin)
    db.save_message('assistant','hello',source='wechat',conversation_id=identifier)
    mobile.queue(origin,'hello',key='wechat-event:reply',record_history=False)
    history.backfill()
    assert len(rows(identifier))==1


def test_old_outbox_recovered_without_resending_and_unknown_kept_unknown():
    origin=configure();identifier=mobile.conversation_id(origin)
    mobile.queue(origin,'receipt',key='old:command')
    with closing(db.get_conn()) as conn,conn:
        conn.execute("DELETE FROM messages WHERE conversation_id=?",(identifier,))
        conn.execute("UPDATE mobile_task_outbox SET status='unknown'")
    history.backfill()
    assert len(rows(identifier))==1
    assert conversations.public_messages(rows(identifier))[0]['delivery_status']=='unknown'
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT status FROM mobile_task_outbox').fetchone()[0]=='unknown'


def test_real_messages_api_includes_transport_state_without_internal_delivery_key():
    from app.routes.agent import router as agent_router
    origin = configure(); identifier = mobile.conversation_id(origin)
    mobile.queue(origin, 'pending receipt', key='api:command')
    # This is the production query shape, not the SELECT * fixture helper.
    assert 'delivery_key' not in db.get_recent_messages(conversation_id=identifier)[0].keys()
    app = FastAPI(); app.include_router(agent_router)
    with TestClient(app) as client:
        response = client.get('/api/agent/messages', params={'conversation_id': identifier})
        assert response.status_code == 200
        assert response.json()[0]['delivery_status'] == 'pending'
        with closing(db.get_conn()) as conn, conn:
            conn.execute("UPDATE mobile_task_outbox SET status='unknown',error='synthetic interrupted send'")
        response = client.get('/api/agent/messages', params={'conversation_id': identifier})
        assert response.json()[0]['delivery_status'] == 'unknown'
        assert response.json()[0]['delivery_error'] == 'synthetic interrupted send'


def test_backfilled_old_receipt_does_not_replace_latest_chat_or_preview():
    origin = configure(); identifier = mobile.conversation_id(origin)
    mobile.queue(origin, 'old receipt', key='old:receipt-order')
    with closing(db.get_conn()) as conn, conn:
        conn.execute('DELETE FROM messages WHERE conversation_id=?', (identifier,))
        conn.execute("UPDATE mobile_task_outbox SET created_at='2026-09-01T10:00:00+08:00'")
    db.save_message('user', 'latest chat', source='wechat', conversation_id=identifier)
    history.backfill()
    assert [row['content'] for row in db.get_recent_messages(limit=1, conversation_id=identifier)] == ['latest chat']
    assert [row['content'] for row in db.get_recent_messages(conversation_id=identifier)] == ['old receipt', 'latest chat']
    assert next(x['preview'] for x in conversations.list_conversations() if x['id']==identifier) == 'latest chat'


def test_wechat_document_history_download_and_tamper_detection():
    async def run():
        origin=configure();identifier=mobile.conversation_id(origin)
        file_id=await files.store_document(origin,'event-file','example.txt',b'synthetic attachment')
        history.backfill();history.backfill()
        messages=conversations.public_messages(rows(identifier))
        assert len(messages)==1 and messages[0]['attachments'][0]['name']=='example.txt'
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            response=client.get('/api/wechat/attachments/'+file_id)
            assert response.status_code==200 and response.content==b'synthetic attachment'
            with closing(db.get_conn()) as conn:
                path=conn.execute('SELECT path FROM mobile_task_attachments WHERE id=?',(file_id,)).fetchone()[0]
            from pathlib import Path
            Path(path).write_bytes(b'changed')
            assert client.get('/api/wechat/attachments/'+file_id).status_code==409
            assert client.get('/api/wechat/attachments/invalid').status_code==404
    asyncio.run(run())


def test_incoming_image_passes_persistent_attachment_to_chat():
    async def run():
        import base64
        from test_creation_features import PNG_DATA_URL
        configure()
        await poll(incoming('',item_list=[{'type':2,'image_item':{}}]))
        fake=AsyncMock(return_value=SimpleNamespace(task_handoff=None,replies=['image received']))
        with patch.object(wx,'download_media',AsyncMock(return_value=base64.b64decode(PNG_DATA_URL.split(',')[1]))),patch('app.chat_service.chat_with_ai',fake):
            await wx.consume_once()
        attachments=fake.await_args.kwargs['attachment_metadata']
        assert len(attachments)==1 and attachments[0]['kind']=='image'
        assert attachments[0]['url'].startswith('/agent-files/')
    asyncio.run(run())
