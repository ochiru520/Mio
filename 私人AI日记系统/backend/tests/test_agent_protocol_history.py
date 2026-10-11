import asyncio
from dataclasses import replace
import json
from unittest.mock import AsyncMock, patch

import pytest

from app import db, agent_task_service as tasks
from app import agent_protocol_history as protocol
from app.agent_loop_service import run_agent_loop, _json_compatibility_messages_for
from app.llm import ToolCall
from tests.test_agent_model_first import _completion
from test_agent_result_recovery import make_task, store
from test_runtime_safety_v2 import isolated


@pytest.mark.parametrize('mode', ['chat', 'responses'])
def test_native_protocol_pairs_survive_reload_and_revalidate_source(mode, isolated, tmp_path):
    from app import agent_file_service as files
    from app.agent_tool_service import ToolExecutionResult
    task = make_task()
    root = tmp_path/'authorized'; root.mkdir(); files.roots([str(root)])
    source = root/'evidence.md'; source.write_text('原始事实', encoding='utf-8')
    step_id = store(task, files.read_document(str(source)), 'agent_read_document')
    completion = replace(_completion(ToolCall('call_original', 'agent_read_document', json.dumps({'path': str(source)}))),
        protocol_mode=mode, protocol_message={'reasoning_content': 'synthetic provider reasoning'},
        protocol_items=({'type':'reasoning', 'id':'rs_1', 'encrypted_content':'synthetic-encrypted', 'summary':[]},
                        {'type':'function_call', 'id':'fc_1', 'call_id':'call_original', 'name':'agent_read_document', 'arguments':json.dumps({'path':str(source)}), 'status':'completed'}))
    protocol.record('synthetic_run', 0, completion,
        {'call_original': ToolExecutionResult('agent_read_document', 'completed', {}, step_id)}, task['id'])
    loaded = protocol.history(task['id'], 'test-model')
    assert loaded[0]['tool_calls'][0]['id'] == loaded[1]['tool_call_id'] == 'call_original'
    assert '原始事实' in loaded[1]['content']
    source.write_text('更正事实', encoding='utf-8')
    checked = protocol.history(task['id'], 'test-model')
    assert '原始事实' not in checked[1]['content'] and 'stale' in checked[1]['content']
    if mode == 'responses':
        inputs = protocol.responses_input(checked)
        assert inputs[0]['encrypted_content'] == 'synthetic-encrypted'
        assert inputs[1]['call_id'] == inputs[2]['call_id'] == 'call_original'
        assert inputs[2]['type'] == 'function_call_output'
        assert '_mio_responses_items' not in protocol.chat_messages(checked)[0]
    else:
        assert loaded[0]['reasoning_content'] == 'synthetic provider reasoning'
    assert protocol.history(task['id'], 'another-model') == []
    fallback = _json_compatibility_messages_for(checked, allowed_tool_names=set())
    assert all(message['role'] != 'tool' and 'tool_calls' not in message for message in fallback)
    assert all('synthetic provider reasoning' not in message['content'] for message in fallback)


def test_read_write_dependency_pairs_deferred_calls_explicitly_and_second_plan_replays_native_history(isolated):
    task = make_task()
    read = ToolCall('read_a', 'get_today_state', '{}')
    write = ToolCall('write_b', 'add_diary_material', '{"content":"synthetic"}')
    planner = AsyncMock(side_effect=[_completion(read, write), _completion()])
    dispatcher = AsyncMock(return_value={'daily_thirty_status':'unknown'})
    async def run():
        with patch('app.agent_loop_service.call_chat_completion_result', planner), patch('app.agent_tool_service._dispatch_read_tool', dispatcher):
            return await run_agent_loop(conversation_id=task['conversation_id'], source='desktop', user_message='读取合成状态', source_message_id=0,
                request_id='native_pairs', trace_id='pairs', model_id='test-model', reasoning_level='low',
                allowed_tool_names={'get_today_state','add_diary_material'}, task_id=task['id'])
    result = asyncio.run(run())
    assert result.status == 'completed' and dispatcher.await_count == 1
    second_messages = planner.await_args_list[1].args[0]
    outputs = {item['tool_call_id']: json.loads(item['content']) for item in second_messages if item['role'] == 'tool'}
    assert outputs['read_a']['status'] == 'completed' and outputs['write_b']['status'] == 'deferred'
    assert not any(item.tool_name == 'add_diary_material' for item in result.observations)
    assert protocol.history(task['id'], 'test-model')


def test_protocol_compaction_drops_only_complete_pairs_and_never_leaves_orphaned_results(isolated):
    from app.agent_tool_service import ToolExecutionResult
    task = make_task()
    for index in range(25):
        run_id = f'pair_run_{index}'
        payload = {'text':'synthetic '*1000}
        step_id = store(task, payload, run_id=run_id)
        completion = _completion(ToolCall(f'call_{index}', 'synthetic_read', '{}'))
        protocol.record(run_id, 0, completion, {f'call_{index}': ToolExecutionResult('synthetic_read','completed',payload,step_id)}, task['id'])
    history = protocol.history(task['id'], 'test-model', max_chars=4000)
    ids = {call['id'] for item in history for call in item.get('tool_calls', [])}
    outputs = {item['tool_call_id'] for item in history if item['role'] == 'tool'}
    assert ids == outputs and 'call_24' in ids and len(ids) < 25
    assert '省略' in history[0]['content']
