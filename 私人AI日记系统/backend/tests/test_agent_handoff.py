import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import db
from app.agent_handoff_service import create_handoff
from app.agent_tool_service import ToolExecutionContext
from app.chat_service import ChatResult
from app.llm import CompletionResult, ToolCall
from app.config import settings
from test_runtime_safety_v2 import isolated


def context(request_id='chat-handoff-1'):
    return ToolExecutionContext(run_id=request_id, request_id=request_id, trace_id='trace',
        conversation_id='desktop_chat', source_message_id=12, user_message='请整理这批文件', step_index=0)


def test_model_handoff_creates_persistent_agent_goal_and_is_idempotent():
    arguments={'goal':'整理这批文件并输出目录报告','constraints':['保留原文件'],'completion_criteria':['报告可下载'],'reason':'需要多步文件操作'}
    with patch('app.companion_service.load_config', return_value={'chat_model_id':'chosen','chat_reasoning_level':'high'}):
        first=create_handoff(arguments, context())
        second=create_handoff(arguments, context())
    assert first['task_id']==second['task_id']
    assert first['conversation_id'].startswith('desktop_agent_')
    assert first['status']=='ready'
    task=db.get_agent_task(first['task_id']) if hasattr(db,'get_agent_task') else None
    assert task is None or task['status']=='ready'
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE conversation_id=?",(first['conversation_id'],)).fetchone()[0]==1
        assert conn.execute("SELECT COUNT(*) FROM agent_conversations WHERE id=?",(first['conversation_id'],)).fetchone()[0]==1


def test_handoff_preserves_explicit_constraints_in_task_snapshot():
    arguments={'goal':'导出透明 PNG','constraints':['不要覆盖原图'],'completion_criteria':['文件存在且哈希可验证']}
    with patch('app.companion_service.load_config', return_value={'chat_model_id':'chosen','chat_reasoning_level':'low'}):
        result=create_handoff(arguments, context('chat-handoff-2'))
    task=__import__('app.agent_task_service',fromlist=['get']).get(result['task_id'])
    assert task['status']=='ready'
    assert task['snapshot']['constraints']==['不要覆盖原图']
    assert task['snapshot']['completion_criteria']==['文件存在且哈希可验证']


def test_chat_model_handoff_creates_task_without_entering_agent_loop():
    async def run():
        completion=CompletionResult(content='', model='chosen', profile_id='chosen', prompt_tokens=2,
            cached_prompt_tokens=0, completion_tokens=1, reasoning_tokens=0, cost_yuan=0,
            cost_source='test', tool_calls=(ToolCall('handoff','handoff_to_agent',
                '{"goal":"整理文件","constraints":["保留原文件"],"completion_criteria":["报告可下载"]}'),))
        chat_context=SimpleNamespace(system_context='', raw_messages=[])
        with patch('app.chat_service.db.get_recent_messages',return_value=[]), \
             patch('app.chat_service.db.get_latest_message_id',return_value=10), \
             patch('app.chat_service.resolve_model_id',return_value='chosen'), \
             patch('app.chat_service.require_configured'), \
             patch('app.chat_service.get_model_profile',return_value=SimpleNamespace(model='chosen',supports_tool_calls=True)), \
             patch('app.chat_service.normalize_model_reasoning',return_value='low'), \
             patch('app.chat_service.build_chat_context',new=AsyncMock(return_value=chat_context)), \
             patch('app.chat_service.perform_web_lookup',new=AsyncMock(return_value=None)), \
             patch('app.chat_service._self_snapshot_context_for_message',new=AsyncMock(return_value='')), \
             patch('app.chat_service.load_manuals',return_value={}), \
             patch('app.chat_service.build_system_prompt',return_value='test'), \
             patch('app.chat_service._build_current_time_context',return_value=''), \
             patch('app.chat_service.system_audio_service.chat_context',return_value=''), \
             patch('app.companion_service.set_pet_activity'), \
             patch('app.companion_service.infer_speech_emotion',return_value='neutral'), \
             patch('app.chat_service._complete_chat_reply_with_single_fallback',new=AsyncMock(return_value=(completion,'low',''))), \
             patch('app.chat_service._remove_replayed_previous_turn',side_effect=lambda replies,*args: replies):
            from app import chat_service as chat
            result=await chat._chat_with_ai_unlocked('整理文件',conversation_id='desktop_chat',source='desktop',
                model_id='chosen',reasoning_level='low',persist=False,agent_tools_enabled=False,
                fast_path=False,handoff_enabled=True)
        assert result.task_handoff['status']=='ready'
        assert ''.join(result.replies)=='我已经把这件事交给 Agent 继续处理了详细进度会出现在 Agent 页面'
        assert db.get_agent_conversation(result.task_handoff['conversation_id']) is not None if hasattr(db,'get_agent_conversation') else True
    asyncio.run(run())
