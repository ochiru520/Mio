"""Exercise actual outbound Agent payloads against a local in-memory transport."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import agent_loop_service as loop, db, llm
from app.model_registry import ModelProfile
from test_agent_result_recovery import make_task
from test_runtime_safety_v2 import isolated


@pytest.mark.parametrize('api_mode', ['chat_completions', 'responses'])
@pytest.mark.parametrize('native', [True, False])
def test_actual_agent_payload_keeps_current_clock_and_state_after_history(api_mode, native, isolated):
    task = make_task()
    payloads = []
    client_class = httpx.AsyncClient
    profile = ModelProfile(id='test-model', provider_id='synthetic', provider_name='Synthetic',
                           display_name='Synthetic', model='test-model', api_key='synthetic-only',
                           base_urls=('https://example.test/v1',), api_mode=api_mode)
    call = {'id': 'read_state', 'type': 'function',
            'function': {'name': 'get_today_state', 'arguments': '{}'}}

    def transport(request):
        payloads.append(json.loads(request.content))
        first = len(payloads) == 1
        if native:
            message = {'content': None, 'tool_calls': [call]} if first else {'content': 'NO_TOOL'}
        else:
            message = {'content': json.dumps({'tool_calls': [
                {'call_id': 'read_state', 'name': 'get_today_state', 'arguments': {}}
            ] if first else []})}
        if api_mode == 'responses':
            output = ([{'type': 'function_call', 'id': 'fc_state', 'call_id': 'read_state',
                        'name': 'get_today_state', 'arguments': '{}'}] if native and first else
                      [{'type': 'message', 'role': 'assistant', 'content': [
                          {'type': 'output_text', 'text': message['content']}]}])
            body = {'id': 'resp_synthetic', 'model': 'test-model', 'output': output}
        else:
            body = {'model': 'test-model', 'choices': [{'message': message, 'finish_reason': 'stop'}]}
        body['usage'] = {'prompt_tokens': 40, 'completion_tokens': 5}
        return httpx.Response(200, request=request, json=body)

    def client(**kwargs):
        return client_class(**kwargs, transport=httpx.MockTransport(transport))

    async def completion(messages, **kwargs):
        # Invoke the production payload serializer without choosing the SSE path.
        return await llm.call_chat_completion_result(messages, **kwargs)

    original_runtime = loop._planner_runtime_message
    times = ['2026-10-24T10:00:00+08:00', '2026-10-24T10:00:01+08:00']

    def runtime(conversation_id):
        with patch.object(db, 'now_iso', return_value=times[len(payloads)]):
            return original_runtime(conversation_id)

    source = [
        {'role': 'system', 'content': 'SYNTHETIC_SOURCE_SYSTEM_DO_NOT_ELEVATE'},
        {'role': 'user', 'content': '读取合成状态'},
    ]
    with (
        patch.object(loop, 'call_chat_completion_result', completion),
        patch.object(loop, '_planner_runtime_message', side_effect=runtime),
        patch('app.workspace_service.project_context', side_effect=[
            'SYNTHETIC_PROJECT_REFERENCE revision=1', 'SYNTHETIC_PROJECT_REFERENCE revision=2']),
        patch('app.agent_tool_service._dispatch_read_tool', AsyncMock(return_value={'synthetic': True})),
        patch.object(llm, 'require_configured'),
        patch.object(llm, 'resolve_model_id', return_value=profile.id),
        patch.object(llm, 'get_model_profile', return_value=profile),
        patch.object(llm, '_completion_routes', return_value=[
            llm.CompletionRoute('https://example.test/v1', '', 'synthetic', api_mode=api_mode)]),
        patch.object(llm.httpx, 'AsyncClient', client),
    ):
        result = asyncio.run(loop.run_agent_loop(
            conversation_id=task['conversation_id'], source='desktop', user_message='读取合成状态',
            source_message_id=0, request_id='synthetic-cache', trace_id='synthetic-cache',
            model_id=profile.id, reasoning_level='low', task_id=task['id'],
            allowed_tool_names={'get_today_state'}, allow_native_tools=native, context_snapshot=source))
    assert result.status == 'completed' and len(payloads) == 2
    key = 'input' if api_mode == 'responses' else 'messages'
    first, second = [payload[key] for payload in payloads]
    runtime_indices = [next(i for i, message in enumerate(messages)
                            if '本次规划的当前运行信息' in str(message.get('content', '')))
                       for messages in (first, second)]
    first_runtime, second_runtime = runtime_indices
    assert first[:first_runtime] == second[:first_runtime]
    assert times[0] in first[first_runtime]['content']
    assert times[1] in second[second_runtime]['content']
    assert times[0] not in json.dumps(second, ensure_ascii=False)
    for revision, messages in enumerate((first, second), start=1):
        for marker in ('SYNTHETIC_SOURCE_SYSTEM_DO_NOT_ELEVATE', 'SYNTHETIC_PROJECT_REFERENCE'):
            matches = [item for item in messages if marker in str(item.get('content', ''))]
            assert len(matches) == 1 and matches[0]['role'] == 'user'
            if marker == 'SYNTHETIC_PROJECT_REFERENCE':
                assert matches[0]['content'].endswith(f'revision={revision}')
                assert messages.index(matches[0]) > runtime_indices[revision - 1]
        current = [item for item in messages if str(item.get('content', '')).startswith('持久任务状态与先前观察')]
        assert len(current) == 1 and current[0]['role'] == 'user'
        assert '先读取当前能力' in messages[0]['content']
        assert '不重复成功写入' in messages[0]['content']
    if native:
        assert payloads[0]['tools'] == payloads[1]['tools']
        if api_mode == 'responses':
            call_index = next(i for i, item in enumerate(second) if item.get('type') == 'function_call')
            assert second[call_index]['call_id'] == second[call_index + 1]['call_id'] == 'read_state'
            assert second[call_index + 1]['type'] == 'function_call_output'
        else:
            call_index = next(i for i, item in enumerate(second) if item.get('tool_calls'))
            assert second[call_index + 1]['role'] == 'tool'
            assert second[call_index]['tool_calls'][0]['id'] == second[call_index + 1]['tool_call_id']
        assert call_index + 1 < second_runtime
    else:
        assert '工具 Schema' in first[1]['content'] and first[1]['role'] == 'system'
        assert all(item.get('role') != 'tool' and 'tool_calls' not in item for item in second)
        # JSON planning records its executed receipt in current task data,
        # rather than inventing a native assistant/tool protocol turn.
        state = next(item for item in second
                     if str(item.get('content', '')).startswith('持久任务状态与先前观察'))
        assert 'get_today_state' in state['content'] and 'completed' in state['content']


def test_direct_planner_call_keeps_runtime_at_tail(isolated):
    with patch.object(loop, '_planner_history', return_value='合成历史'):
        messages = loop._planner_messages('synthetic', '合成请求', 0)
    assert '合成历史' in messages[1]['content'] and '合成请求' in messages[1]['content']
    assert messages[-1]['role'] == 'user' and '当前时间' in messages[-1]['content']
    assert '当前时间' not in messages[1]['content']
