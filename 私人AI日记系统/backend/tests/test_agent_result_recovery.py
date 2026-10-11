import asyncio
import json
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock, patch

import pytest

from app import db, agent_task_service as tasks, workspace_service as workspace, agent_file_service as files
from app import agent_result_context as results
from app.agent_loop_service import run_agent_loop
from app.llm import ToolCall
from tests.test_agent_model_first import _completion
from test_runtime_safety_v2 import isolated


def make_task():
    workspace.initialize()
    with patch('app.model_runtime.model_selection', return_value=('test-model', 'low')):
        return workspace.enqueue_goal('读取并整理合成资料')


def store(task, payload, name='synthetic_read', run_id='synthetic_run'):
    db.create_agent_run(run_id, run_id, conversation_id=task['conversation_id'])
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO agent_task_runs VALUES(?,?,?)', (run_id, task['id'], run_id))
    _, step = db.claim_agent_run_step(run_id, 0, 'tool_call', run_id+':'+name, tool_name=name)
    db.update_agent_run_step(step['id'], 'completed', result_json=results.encode(payload))
    return step['id']


def test_long_result_survives_connection_reload_and_can_be_read_to_the_end(isolated):
    task = make_task()
    payload = {'text': '合成正文'*16000+'末尾事实证据', 'source': 'synthetic'}
    step_id = store(task, payload)
    compact = results.model_result('synthetic_read', payload, step_id, task['id'])
    assert compact['preview_only'] and compact['full_result']['arguments']['step_id'] == step_id
    slices, offset, hashes = [], 0, set()
    while True:
        page = results.read(task['id'], step_id=step_id, offset=offset, max_chars=1000)
        slices.append(page['text']); hashes.add(page['sha256'])
        if not page['has_more']:
            break
        offset = page['next_offset']
    assert json.loads(''.join(slices)) == payload and len(hashes) == 1
    assert '末尾事实证据' in slices[-1]
    with pytest.raises(ValueError, match='不属于'):
        results.read(make_task()['id'], step_id=step_id)


def test_compaction_keeps_recent_statuses_and_older_history_remains_pageable(isolated):
    task = make_task()
    observations = []
    for index in range(65):
        payload = {'text': 'long synthetic '*5000, 'ordinal': index}
        step_id = store(task, payload, run_id=f'run_{index}')
        observations.append({'tool_name': 'synthetic_read', 'status': 'completed', 'result': payload, 'step_id': step_id})
    context = results.compact_observations(observations, task['id'], max_chars=8000)
    assert len(results.encode(context)) < 9000 and context['context_compacted']
    assert context['observations'][-1]['step_id'] == observations[-1]['step_id']
    page1 = results.read(task['id'], limit=50)
    page2 = results.read(task['id'], after_id=page1['next_id'], limit=50)
    assert len(page1['results']) == 50 and page1['has_more']
    assert len(page2['results']) == 15 and not page2['has_more']
    assert {row['id'] for row in page1['results']} | {row['id'] for row in page2['results']} == {item['step_id'] for item in observations}


def test_model_json_bound_is_valid_and_does_not_truncate_durable_payload(isolated):
    from app.agent_loop_service import _json, _json_full
    value = {'content': '长内容' * 10000, 'tail': '保留'}
    bounded = _json(value, 500)
    assert len(bounded) <= 500 and json.loads(bounded)['preview_only']
    assert json.loads(_json_full(value)) == value


def test_recalled_document_and_reader_slices_recheck_source_changes_and_permissions(isolated, tmp_path):
    task = make_task()
    root = tmp_path/'authorized'; root.mkdir(); files.roots([str(root)])
    source = root/'synthetic.md'; source.write_text('原有合成事实', encoding='utf-8')
    payload = files.read_document(str(source))
    step_id = store(task, payload, 'agent_read_document')
    old_slice = results.read(task['id'], step_id=step_id)
    reader_step = store(task, old_slice, 'agent_read_tool_result', 'reader_run')
    assert '原有合成事实' in results.read(task['id'], step_id=reader_step)['text']
    source.write_text('资料发生更正', encoding='utf-8')
    changed = results.read(task['id'], step_id=reader_step)
    assert changed['sha256'] != old_slice['sha256'] and '原有合成事实' not in changed['text']
    assert json.loads(changed['text'])['source_status'] == 'stale'
    files.roots([])
    assert json.loads(results.read(task['id'], step_id=step_id)['text'])['source_status'] == 'unavailable'


@pytest.mark.parametrize('failure_role, expected_calls', [('planner_request', 0), ('planner_response', 1)])
def test_critical_checkpoint_failure_stops_before_any_tool_side_effect(isolated, failure_role, expected_calls):
    task = make_task()
    planner = AsyncMock(return_value=_completion(ToolCall('write','add_diary_material',json.dumps({'content':'合成素材'}))))
    execute = AsyncMock()
    original = db.append_agent_run_message
    def persist(*args, **kwargs):
        if args[3] == failure_role:
            raise OSError('synthetic disk full')
        return original(*args, **kwargs)
    async def run():
        with patch('app.agent_loop_service.call_chat_completion_result', planner), patch('app.agent_loop_service.execute_tool_call', execute), patch.object(db, 'append_agent_run_message', side_effect=persist):
            return await run_agent_loop(conversation_id=task['conversation_id'], source='desktop', user_message='整理合成资料',
                source_message_id=0, request_id=f'checkpoint_{failure_role}', trace_id='checkpoint', model_id='test-model',
                reasoning_level='low', allowed_tool_names={'add_diary_material'}, task_id=task['id'])
    result = asyncio.run(run())
    assert result.status == 'paused' and tasks.get(task['id'])['status'] == 'paused'
    assert db.get_agent_run(result.run_id)['status'] == 'failed'
    assert planner.await_count == expected_calls and execute.await_count == 0
    assert '关键执行记录' in result.error


def test_event_logging_failure_is_telemetry_and_does_not_stop_completed_no_tool_task(isolated):
    task = make_task()
    async def run():
        with patch('app.agent_loop_service.call_chat_completion_result', AsyncMock(return_value=_completion())), patch.object(db, 'append_agent_run_event', side_effect=OSError('synthetic telemetry failure')):
            return await run_agent_loop(conversation_id=task['conversation_id'], source='desktop', user_message='无需工具', source_message_id=0,
                request_id='telemetry_failure', trace_id='test', model_id='test-model', reasoning_level='low', allowed_tool_names=set(), task_id=task['id'])
    assert asyncio.run(run()).status == 'completed'


def test_tool_receipt_loss_pauses_and_cannot_replay_the_completed_external_effect(isolated):
    from app.agent_tool_service import execute_tool_call, ToolExecutionContext
    from app.agent_result_context import AgentCheckpointError
    task = make_task()
    write = AsyncMock(return_value={'ok': True, 'result': 'synthetic effect'})
    planner = AsyncMock(return_value=_completion(ToolCall('write', 'add_diary_material', '{"content":"合成素材"}')))
    original = db.update_agent_run_step
    def save(step_id, status, **kwargs):
        step = db.get_agent_run_step(step_id)
        if step['tool_name'] == 'add_diary_material' and status == 'completed':
            raise sqlite3.OperationalError('synthetic result persistence failure')
        return original(step_id, status, **kwargs)
    async def run():
        with patch('app.agent_loop_service.call_chat_completion_result', planner), patch('app.agent_tool_service._dispatch_write_tool', write), patch('app.agent_tool_service._verified_result', return_value=(True, '')), patch.object(db, 'update_agent_run_step', side_effect=save):
            result = await run_agent_loop(conversation_id=task['conversation_id'], source='desktop', user_message='记录合成素材', source_message_id=0,
                request_id='lost_receipt', trace_id='test', model_id='test-model', reasoning_level='low', allowed_tool_names={'add_diary_material'}, task_id=task['id'])
        assert result.status == 'paused' and write.await_count == 1
        with pytest.raises(ValueError, match='旧执行结果'):
            tasks.resume(task['id'])
        context = ToolExecutionContext(run_id=result.run_id, request_id='lost_receipt', trace_id='test', conversation_id=task['conversation_id'],
            source_message_id=0, user_message='记录合成素材', step_index=1, source='desktop', tool_call_id='write', task_id=task['id'])
        with patch('app.agent_tool_service._dispatch_write_tool', write), pytest.raises(AgentCheckpointError, match='旧回执'):
            await execute_tool_call('add_diary_material', {'content':'合成素材'}, context)
        assert write.await_count == 1
    asyncio.run(run())
