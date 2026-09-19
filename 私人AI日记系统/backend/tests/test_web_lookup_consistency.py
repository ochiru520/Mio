"""Regression coverage for network intent, companion prompts and capability status.

All model calls and external transports are mocked. Persistent state is temporary.
"""
from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import db
from app.auto_router import build_task_profile
from app.chat_service import ChatResult, _chat_with_ai_unlocked
from app.config import settings
from app.llm import CompletionResult
from app.routes.agent import AgentChatRequest, agent_chat
from app.web_search_service import WebLookup, WebSource, should_use_web_lookup


SEARCH_REQUESTS = (
    "帮我查 Python 官方文档",
    "帮我查一下 Python 官方文档",
    "打开 https://docs.python.org/zh-cn/3/",
    "Python 现在的版本是多少？",
    "最近有什么新消息？",
    "帮我查一下 DeepSeek 最新消息",
)
SOURCE = WebSource(title="Python 官方文档", url="https://docs.python.org/zh-cn/3/", snippet="Python 文档测试证据")


class WebLookupConsistencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="mio-web-regression-")
        self.addCleanup(directory.cleanup)
        for name, value in {"db_path": Path(directory.name) / "test.db", "web_search_enabled": True}.items():
            original = getattr(settings, name)
            object.__setattr__(settings, name, value)
            self.addCleanup(object.__setattr__, settings, name, original)
        db.init_db()

    def test_search_intent_matches_router_for_all_reported_forms(self) -> None:
        for message in SEARCH_REQUESTS:
            with self.subTest(message=message):
                self.assertTrue(should_use_web_lookup(message))
                self.assertTrue(build_task_profile(message).requires_tools)

    def test_weather_location_followup_does_not_enter_fast_chat(self) -> None:
        history = [{"role": "user", "content": "今天的天气怎么样？"}]
        self.assertTrue(build_task_profile("我在北京", history_rows=history).requires_tools)

    def test_personal_state_and_explicit_no_network_do_not_trigger_search(self) -> None:
        for message in ("你现在感觉怎么样？", "我最近状态怎么样", "不要联网，只用已有知识解释 Python 版本", "不用搜索，陪我聊聊"):
            with self.subTest(message=message):
                self.assertFalse(should_use_web_lookup(message))
                self.assertFalse(build_task_profile(message).requires_tools)

    def test_capability_questions_are_not_external_search_queries(self) -> None:
        for message in ("你能联网吗？", "你能访问互联网吗？", "MIO 支持联网搜索吗？"):
            with self.subTest(message=message):
                self.assertFalse(should_use_web_lookup(message))

    def test_capability_question_with_a_real_task_is_not_swallowed(self) -> None:
        self.assertTrue(should_use_web_lookup("你能联网吗？帮我查一下 Python 官方文档"))

    async def _capture_chat(self, message: str, *, fast_path: bool = False, lookup: WebLookup | None = None,
                            use_real_lookup: bool = False, agent_result=None, history=None):
        captured = []
        completion = CompletionResult(content="这是测试回复", model="test-model", prompt_tokens=0,
            cached_prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, cost_yuan=0, cost_source="test")

        async def complete(messages, **_kwargs):
            captured.extend(messages)
            return completion, "off", ""

        with ExitStack() as stack:
            def mocked(target, **kwargs):
                return stack.enter_context(patch(target, **kwargs))
            mocked("app.chat_service.resolve_model_id", return_value="test-model")
            mocked("app.chat_service.require_configured")
            mocked("app.chat_service.get_model_profile", return_value=SimpleNamespace(model="test-model", supports_tool_calls=True))
            mocked("app.chat_service.normalize_model_reasoning", return_value="off")
            mocked("app.chat_service.load_manuals", return_value=[])
            mocked("app.chat_service.build_system_prompt", return_value="Test system")
            mocked("app.chat_service.build_chat_context_snapshot", side_effect=lambda _cid, rows: SimpleNamespace(raw_messages=list(rows), system_context=""))
            mocked("app.chat_service.db.get_recent_messages", return_value=history or [])
            mocked("app.chat_service.db.get_latest_message_id", return_value=40)
            mocked("app.chat_service.db.now_iso", return_value="2026-09-19T12:00:00+08:00")
            mocked("app.chat_service._self_snapshot_context_for_message", new=AsyncMock(return_value=""))
            mocked("app.chat_service.system_audio_service.chat_context", return_value="")
            mocked("app.companion_service.set_pet_activity")
            mocked("app.companion_service.infer_speech_emotion", return_value="neutral")
            mocked("app.chat_service.begin_final_response", return_value=0)
            mocked("app.chat_service.run_agent_loop", new=AsyncMock(return_value=agent_result))
            transport = mocked("app.web_search_service._search_web", new=AsyncMock(return_value=([SOURCE], "test", ("test: one result",))))
            page = mocked("app.web_search_service._fetch_page", new=AsyncMock(return_value=SOURCE))
            mocked("app.web_search_service._weather_lookup", new=AsyncMock(return_value=None))
            if not use_real_lookup:
                mocked("app.chat_service.perform_web_lookup", new=AsyncMock(return_value=lookup))
            model = mocked("app.chat_service._complete_chat_reply_with_single_fallback", new=AsyncMock(side_effect=complete))
            result = await _chat_with_ai_unlocked(message, conversation_id="desktop_web_regression", source="desktop",
                model_id="test-model", reasoning_level="off", persist=False, fast_path=fast_path,
                agent_tools_enabled=agent_result is not None)
        system_text = "\n".join(str(item.get("content", "")) for item in captured if item.get("role") == "system")
        return result, system_text, transport, page, model

    async def test_fast_flag_cannot_suppress_required_search(self) -> None:
        for message in ("帮我查 Python 官方文档", "Python 现在的版本是多少？"):
            with self.subTest(message=message):
                _, text, transport, _, _ = await self._capture_chat(message, fast_path=True, use_real_lookup=True)
                transport.assert_awaited_once()
                self.assertIn("Python 文档测试证据", text)

    async def test_fast_flag_cannot_suppress_url_read(self) -> None:
        _, text, _, page, _ = await self._capture_chat("打开 https://docs.python.org/zh-cn/3/", fast_path=True, use_real_lookup=True)
        page.assert_awaited_once()
        self.assertIn("Python 文档测试证据", text)

    async def test_companion_receives_search_failure(self) -> None:
        _, text, _, _, _ = await self._capture_chat("帮我查一下 Python 官方文档", lookup=WebLookup(query="Python", sources=[], error="测试搜索源超时"))
        self.assertIn("测试搜索源超时", text)
        self.assertIn("查询失败", text)

    async def test_companion_receives_empty_result_explanation(self) -> None:
        _, text, _, _, _ = await self._capture_chat("帮我查一下 Python 官方文档", lookup=WebLookup(query="Python", sources=[]))
        self.assertIn("没有拿到可用搜索结果", text)

    async def test_disabled_search_never_calls_transport_and_explains_switch(self) -> None:
        object.__setattr__(settings, "web_search_enabled", False)
        _, text, transport, page, _ = await self._capture_chat("帮我查一下 Python 官方文档", fast_path=True, use_real_lookup=True)
        transport.assert_not_awaited()
        page.assert_not_awaited()
        self.assertIn("联网搜索已关闭", text)

    async def test_capability_status_is_local_truth_not_model_guess(self) -> None:
        for enabled in (True, False):
            object.__setattr__(settings, "web_search_enabled", enabled)
            with self.subTest(enabled=enabled):
                result, _, transport, page, model = await self._capture_chat("你能联网吗？", fast_path=True, use_real_lookup=True)
                transport.assert_not_awaited()
                page.assert_not_awaited()
                model.assert_not_awaited()
                self.assertIn("已开启" if enabled else "已关闭", result.reply)
                self.assertEqual(result.route, "local_web_capability")

    async def test_normal_idle_chat_does_not_start_network(self) -> None:
        _, _, transport, page, model = await self._capture_chat("你好", fast_path=True, use_real_lookup=True)
        transport.assert_not_awaited()
        page.assert_not_awaited()
        model.assert_awaited_once()

    async def test_agent_recovery_does_not_repeat_obsolete_failure(self) -> None:
        from app.agent_loop_service import AgentLoopResult
        from app.agent_tool_service import ToolExecutionResult
        agent = AgentLoopResult(run_id="test-recovery", status="awaiting_response", plan_mode="native",
            observations=(ToolExecutionResult(tool_name="search_web", status="completed",
                result={"sources": [{"title": "recovered-source"}], "error": ""}, step_id=1, receipt_id=1),),
            model_results=(), replanned=False, next_step_index=2)
        _, text, _, _, _ = await self._capture_chat("帮我查一下 Python 官方文档",
            lookup=WebLookup(query="Python", sources=[], error="obsolete-search-error"), agent_result=agent)
        self.assertNotIn("obsolete-search-error", text)
        self.assertIn("recovered-source", text)

    async def test_real_desktop_route_does_not_select_fast_path_for_search(self) -> None:
        for index, message in enumerate(SEARCH_REQUESTS):
            conversation_id = f"desktop_web_route_{index}"
            db.create_agent_conversation(conversation_id)
            captured = {}
            async def fake_chat(_message, **kwargs):
                captured.update(kwargs)
                return ChatResult(reply="测试", replies=["测试"], model_id="test-model")
            with self.subTest(message=message), \
                patch("app.routes.agent.resolve_model_id", return_value="test-model"), \
                patch("app.routes.agent.chat_with_ai", new=fake_chat), \
                patch("app.routes.agent.screen_observation_service.is_screen_chat_follow_up", return_value=False), \
                patch("app.routes.agent._schedule_today_state_analysis"), \
                patch("app.routes.agent.db.ensure_daily_state_today"):
                await agent_chat(AgentChatRequest(message=message, model_id="test-model", conversation_id=conversation_id))
                self.assertFalse(captured["fast_path"])


if __name__ == "__main__":
    unittest.main()
