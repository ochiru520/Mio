"""Cross-module regressions; all model requests use synthetic fixtures."""
import asyncio
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, model_runtime as runtime, agent_task_service as tasks
from app.agent_handoff_service import create_handoff
from app.agent_loop_service import source_context_messages
from app.agent_tool_service import ToolExecutionContext
from app.routes.agent_tasks import router
from test_runtime_safety_v2 import isolated


def context(request='audit', conversation='desktop_chat'):
    return ToolExecutionContext(run_id=request, request_id=request, trace_id='audit',
        conversation_id=conversation, source_message_id=12, user_message='synthetic source detail', step_index=0)


def handoff(**kwargs):
    with patch('app.companion_service.load_config', return_value={'chat_model_id':'chosen'}):
        return create_handoff({'goal':'process attached file'}, context(), **kwargs)


def test_saved_fixed_policy_overrides_persisted_model():
    with patch('app.model_registry.get_model_profile'):
        runtime.save_model_policy('agent', {'selection':'fixed','model_id':'new'}, expected_revision=0)
    with patch('app.companion_service.load_config', return_value={'chat_reasoning_level':'high'}):
        assert runtime.model_selection('agent','old','low') == ('new','high')


@pytest.mark.parametrize('mode', runtime.MODES)
def test_explicit_follow_policy_overrides_legacy_callers(mode):
    runtime.save_model_policy(mode, {'selection':'follow_chat'}, expected_revision=0)
    with patch('app.companion_service.load_config', return_value={'chat_model_id':'new','chat_reasoning_level':'high'}):
        assert runtime.model_selection(mode,'old','low') == ('new','high')


def test_disabled_agent_handoff_has_no_partial_conversation():
    runtime.save_model_policy('agent', {'enabled':False}, expected_revision=0)
    with pytest.raises(runtime.OperationBlocked):
        handoff()
    with db.get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_conversations').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM agent_tasks').fetchone()[0] == 0


def test_handoff_transaction_rolls_back_on_task_insert_failure():
    with db.get_conn() as conn:
        conn.execute("CREATE TRIGGER fail_task BEFORE INSERT ON agent_tasks BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(Exception, match='synthetic'):
        handoff()
    with db.get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_conversations').fetchone()[0] == 0


def test_source_text_image_and_document_survive_handoff_to_planner():
    image={'type':'image_url','image_url':{'url':'data:image/png;base64,c3ludGhldGlj'}}
    history=[{'role':'user','content':'earlier constraints'}, {'role':'user','content':[
        {'type':'text','text':'synthetic document extracted text'}, image]}]
    info=handoff(conversation_context=history)
    task=tasks.get(info['task_id'])
    messages=source_context_messages(task['context'])
    assert messages[1]['content']=='earlier constraints'
    assert messages[2]['content'][1]==image
    assert messages[2]['role']=='user'
    assert task['snapshot']['source_conversation_id']=='desktop_chat'


def test_handoff_without_history_retains_original_user_message():
    task=tasks.get(handoff()['task_id'])
    assert task['context'][0]['content']=='synthetic source detail'


def test_failure_agrees_between_task_and_operation():
    info=handoff()
    with patch('app.agent_loop_service.run_agent_loop', new=AsyncMock(side_effect=RuntimeError('synthetic failure'))):
        with pytest.raises(RuntimeError, match='synthetic failure'):
            asyncio.run(tasks.continue_task(info['task_id']))
    assert tasks.get(info['task_id'])['status']=='failed'
    with db.get_conn() as conn:
        assert conn.execute("SELECT status FROM runtime_operations WHERE purpose='continue_task'").fetchone()[0]=='failed'


def test_privacy_stop_cancels_linked_job_but_keeps_parent_resumable():
    info=handoff()
    tasks.update(info['task_id'], status='waiting_jobs', waiting_jobs=['job_synthetic'])
    with patch('app.creation_service.get_job', return_value={'id':'job_synthetic','status':'running'}), \
         patch('app.creation_service.cancel_job', new=AsyncMock()) as cancel:
        asyncio.run(tasks.stop_tasks(stop_linked_jobs=True))
        cancel.assert_awaited_once_with('job_synthetic')
    assert tasks.get(info['task_id'])['status']=='paused'
    assert tasks.resume(info['task_id'])['status']=='ready'


def test_handoff_lookup_survives_reload_and_is_scoped_to_source():
    info=handoff()
    app=FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        assert client.get('/work/handoff/source/desktop_chat').json()['handoff']['task_id']==info['task_id']
        assert client.get('/work/handoff/source/desktop_other').json()['handoff'] is None
    with TestClient(app) as client:
        assert client.get('/work/handoff/source/desktop_chat').json()['handoff']['conversation_id']==info['conversation_id']
