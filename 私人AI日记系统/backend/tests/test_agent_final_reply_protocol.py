"""Final text cannot execute tools or expose provider call syntax to the UI."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app import db, llm, agent_loop_service as loop, agent_task_service as tasks
from app.agent_tool_service import ToolExecutionResult
from test_runtime_safety_v2 import isolated
from test_agent_live_progress import make_task, agent, completion


RAW = '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="agent_read_tool_result">秘密参数</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'


def test_final_context_does_not_request_unavailable_readers():
    result = loop.AgentLoopResult('test', 'completed', 'native',
        (ToolExecutionResult('desktop_observe', 'completed', {'text': '证据' * 5000}, 1),), (), False, 2)
    context = result.model_context()
    assert 'agent_read_tool_result' not in context
    assert 'preview_only' in context and 'desktop_observe' in context


def test_protocol_only_final_is_retried_without_executing_any_tools():
    async def run():
        t = make_task()
        callback = AsyncMock(side_effect=[completion(RAW), completion('专用文档已保存。')])
        with patch.object(loop, 'execute_tool_call', AsyncMock(side_effect=AssertionError('no new tools'))):
            result = await loop.final_model_call(agent(t), callback, [], model_id='synthetic')
        assert result.content == '专用文档已保存。'
        assert callback.await_count == 2
        assert db.get_agent_run('live-run')['model_calls'] == 2
        assert tasks.get(t['id'])['spent_yuan'] == pytest.approx(.002)
        assert result.cost_yuan == pytest.approx(.002)
        events = db.list_agent_task_history(t['id'], kind='events', limit=100)
        assert not any('DSML' in x['payload_json'] or '秘密参数' in x['payload_json'] for x in events)
    asyncio.run(run())


def test_split_protocol_is_not_exposed_in_live_stream():
    async def run():
        t = make_task()
        calls = []
        async def stream(messages, on_delta, **kwargs):
            calls.append(messages)
            content = RAW if len(calls) == 1 else '已保存，操作已停止。'
            for i, char in enumerate(content):
                await on_delta(char, content[:i + 1])
            return completion(content)
        with patch.object(llm, 'call_chat_completion_stream_result', stream):
            result = await loop.final_model_call(agent(t), loop._DEFAULT_FINAL_CALLBACK, [], model_id='synthetic')
        assert result.content == '已保存，操作已停止。' and len(calls) == 2
        events = db.list_agent_task_history(t['id'], kind='events', limit=100)
        text = ''.join(json.loads(x['payload_json']).get('text', '') for x in events if x['event_type'] == 'response_text_delta')
        assert text == result.content
    asyncio.run(run())


def test_repeated_protocol_fails_reply_without_claiming_completion():
    async def run():
        t = make_task()
        callback = AsyncMock(return_value=completion(RAW))
        with pytest.raises(ValueError, match='回复'):
            await loop.final_model_call(agent(t), callback, [], model_id='synthetic')
        assert callback.await_count == 2
        assert tasks.get(t['id'])['spent_yuan'] == pytest.approx(.002)
    asyncio.run(run())


def test_protocol_recovery_respects_remaining_model_call_budget():
    async def run():
        t = make_task()
        tasks.limits({'enabled': True, 'model_calls': 2})
        with db.get_conn() as conn:
            conn.execute('UPDATE agent_runs SET model_calls=1,max_model_calls=2 WHERE run_id=?', ('live-run',))
        callback = AsyncMock(return_value=completion(RAW))
        with pytest.raises(ValueError, match='回复'):
            await loop.final_model_call(agent(t), callback, [], model_id='synthetic')
        callback.assert_awaited_once()
        assert db.get_agent_run('live-run')['model_calls'] == 2
    asyncio.run(run())


def test_unexpected_native_tool_call_in_final_reply_is_not_accepted():
    async def run():
        t = make_task()
        callback = AsyncMock(side_effect=[completion(calls=(llm.ToolCall('id', 'desktop_act', '{}'),)), completion('操作状态已核对。')])
        result = await loop.final_model_call(agent(t), callback, [], model_id='synthetic')
        assert not result.tool_calls and result.content == '操作状态已核对。'
    asyncio.run(run())


def test_explicit_handoff_tool_remains_available():
    async def run():
        t = make_task()
        handoff = completion(calls=(llm.ToolCall('id', 'handoff_to_agent', '{}'),))
        callback = AsyncMock(return_value=handoff)
        schema = [{'type': 'function', 'function': {'name': 'handoff_to_agent'}}]
        result = await loop.final_model_call(agent(t), callback, [], model_id='synthetic', tools=schema)
        assert result.tool_calls == handoff.tool_calls
        callback.assert_awaited_once()
        assert callback.await_args.kwargs['tools'] == schema
        assert '明确提供的交接' in callback.await_args.args[0][-1]['content']
    asyncio.run(run())


def test_historical_reply_cleanup_removes_protocol_but_preserves_surrounding_text():
    from app.chat_service import clean_chat_reply
    assert clean_chat_reply('等待确认。\n' + RAW + '\n已有结果保留。') == '等待确认。\n\n已有结果保留。'
