import asyncio
from contextlib import closing
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, llm, model_runtime as runtime
from app.config import settings, SettingsConflictError, save_runtime_settings
from app.model_registry import ModelProfile
from app.routes.runtime import router
from test_runtime_safety_v2 import isolated


def profile(identifier):
    return ModelProfile(id=identifier, provider_id='synthetic-provider', provider_name='Synthetic',
                        display_name=identifier, model=identifier+'-model', base_urls=('https://invalid.example/v1',),
                        api_key='synthetic', api_mode='responses' if identifier=='responses' else 'chat_completions')


def fake_client(handler):
    constructor = httpx.AsyncClient
    return lambda **kwargs: constructor(transport=httpx.MockTransport(handler), trust_env=False)


def response(request):
    import json
    model = json.loads(request.content)['model']
    if request.url.path.endswith('/responses'):
        content = {'model':model, 'output':[{'type':'message','content':[{'type':'output_text','text':'synthetic'}]}], 'usage':{'input_tokens':12,'output_tokens':3}}
    else:
        content = {'model':model, 'choices':[{'message':{'content':'synthetic'}}], 'usage':{'prompt_tokens':12,'completion_tokens':3}}
    return httpx.Response(200, json=content, headers={'x-request-cost-cny':'0.01'})


def test_record_routing_and_usage_match_actual_model_without_saving_prompts():
    object.__setattr__(settings,'daily_diary_model_id','records')
    @runtime.operation('record', automatic=True)
    async def generate(request_id):
        return await llm.call_chat_completion_result([{'role':'user','content':'PRIVATE_SYNTHETIC_CONTENT'}],model_id='legacy')
    async def run():
        with patch('app.companion_service.load_config',return_value={'chat_model_id':'chat','chat_reasoning_level':'low'}), patch.object(llm,'get_model_profile',side_effect=profile), patch.object(llm.httpx,'AsyncClient',side_effect=fake_client(response)):
            result = await generate('record-request')
            assert result.profile_id=='records'
    asyncio.run(run())
    with closing(db.get_conn()) as conn:
        calls = conn.execute('SELECT * FROM runtime_model_calls').fetchall()
        assert len(calls)==1
        row = dict(calls[0])
        assert row['mode']=='record' and row['selected_model_id']==row['actual_model_id']=='records'
        assert row['request_id']=='record-request' and row['prompt_tokens']==12
        assert row['cost_yuan']==pytest.approx(.01)
        assert 'PRIVATE_SYNTHETIC_CONTENT' not in str(row)
        assert conn.execute('SELECT status FROM runtime_operations').fetchone()[0]=='completed'
    app=FastAPI();app.include_router(router)
    with TestClient(app) as client:
        usage=client.get('/api/runtime/usage').json()
        record=next(item for item in usage['modes'] if item['mode']=='record')
        assert record['calls']==1 and record['unknown_cost_calls']==0
        assert record['p95_duration_ms'] is not None


def test_streaming_responses_fallback_is_counted_once_with_original_request_id():
    async def run():
        with patch.object(llm,'get_model_profile',side_effect=profile), patch.object(llm.httpx,'AsyncClient',side_effect=fake_client(response)):
            result=await llm.call_chat_completion_stream_result([],model_id='responses',request_id='stream-request')
            assert result.content=='synthetic'
    asyncio.run(run())
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_model_calls').fetchone()[0]==1
        assert conn.execute('SELECT request_id FROM runtime_operations').fetchone()[0]=='stream-request'


def test_policy_change_is_versioned_and_mode_disable_blocks_request():
    assert runtime.model_policy('record')['revision']==0
    with patch('app.model_registry.get_model_profile',side_effect=profile):
        policy=runtime.save_model_policy('record',{'selection':'fixed','model_id':'records'},expected_revision=0)
        assert policy['revision']==1
        with pytest.raises(SettingsConflictError):
            runtime.save_model_policy('record',{'model_id':'replacement'},expected_revision=0)
        runtime.save_model_policy('record',{'enabled':False},expected_revision=1)
    @runtime.operation('record',automatic=True)
    async def generate():
        return await llm.call_chat_completion_result([])
    with pytest.raises(runtime.OperationBlocked):asyncio.run(generate())
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_model_calls').fetchone()[0]==0


def test_idle_observation_does_not_create_persistent_noise():
    @runtime.operation('vision',automatic=True,persist=False)
    async def idle(): return False
    async def run():
        for _ in range(5):assert not await idle()
    asyncio.run(run())
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_operations').fetchone()[0]==0


def test_cancelled_model_cost_is_unknown_and_scoped_to_its_operation():
    started=asyncio.Event()
    async def handler(request):
        started.set();await asyncio.Event().wait()
    @runtime.operation('proactive',automatic=True)
    async def generate():return await llm.call_chat_completion_result([],model_id='chat')
    async def run():
        with patch('app.companion_service.load_config',return_value={'chat_model_id':'chat'}), patch.object(llm,'get_model_profile',side_effect=profile), patch.object(llm.httpx,'AsyncClient',side_effect=fake_client(handler)):
            job=asyncio.create_task(generate())
            await asyncio.wait_for(started.wait(),3)
            assert await runtime.cancel_operations(automatic_only=True,reason='privacy')==1
            with pytest.raises(asyncio.CancelledError):await job
    asyncio.run(run())
    with closing(db.get_conn()) as conn:
        row=conn.execute('SELECT status,cost_yuan FROM runtime_model_calls').fetchone()
        assert row['status']=='cancelled' and row['cost_yuan'] is None
        assert conn.execute('SELECT status FROM runtime_operations').fetchone()[0]=='cancelled'


def test_provider_session_is_stable_per_conversation_and_only_sent_to_go():
    from app.provider_compat import client_headers
    captured=[]
    @runtime.operation('agent')
    async def request(conversation_id, request_id):
        captured.append(client_headers('https://opencode.ai/zen/go/v1'))
    async def run():
        await request('desktop_agent_a','request-1')
        await request('desktop_agent_a','request-2')
        await request('desktop_agent_b','request-3')
    asyncio.run(run())
    assert captured[0]['x-opencode-session']==captured[1]['x-opencode-session']
    assert captured[0]['x-opencode-session']!=captured[2]['x-opencode-session']
    assert captured[0]['User-Agent'].startswith('MioAgent/')
    assert 'x-opencode-session' not in client_headers('https://other.example/v1')
    assert 'x-opencode-session' not in client_headers('https://opencode.ai/zen/gopher')


def test_mode_and_task_binding_reach_model_ledger():
    @runtime.operation(lambda values: 'agent' if values['conversation_id'].startswith('desktop_agent_') else 'chat')
    async def request(conversation_id):
        if runtime.current().mode=='agent':runtime.bind_task('task_test')
        await llm.call_chat_completion_result([],model_id='chosen')
    async def run():
        with patch.object(llm,'get_model_profile',side_effect=profile), patch.object(llm.httpx,'AsyncClient',side_effect=fake_client(response)):
            await request('desktop_chat')
            await request('desktop_agent_a')
    asyncio.run(run())
    with closing(db.get_conn()) as conn:
        rows=conn.execute('SELECT mode,conversation_id,task_id FROM runtime_model_calls ORDER BY mode').fetchall()
        assert dict(rows[0])=={'mode':'agent','conversation_id':'desktop_agent_a','task_id':'task_test'}
        assert dict(rows[1])=={'mode':'chat','conversation_id':'desktop_chat','task_id':''}


def test_explicit_vision_and_translation_routes_are_not_replaced_by_chat_default():
    with patch('app.companion_service.load_config', return_value={'chat_model_id':'chat','chat_reasoning_level':'low'}):
        assert runtime.model_selection('vision','vision-model','low') == ('vision-model','low')
        assert runtime.model_selection('translation','translation-model','standard') == ('translation-model','standard')
        assert runtime.model_selection('profile','','standard')[0] == 'chat'


def test_legacy_diary_setting_and_strategy_policy_stay_in_sync():
    with patch('app.model_registry.get_model_profile', side_effect=profile):
        save_runtime_settings({'daily_diary_model_id': 'records'})
        assert runtime.model_policy('record')['model_id'] == 'records'
        save_runtime_settings({'daily_diary_model_id': ''})
        assert runtime.model_policy('record')['selection'] == 'follow_chat'
