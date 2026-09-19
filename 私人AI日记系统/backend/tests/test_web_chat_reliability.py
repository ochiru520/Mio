"""Regression coverage for search dispatch, companion errors and local capability replies.

LLM replies and search transport are mocked; no provider or private data is used.
"""
from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import db, web_search_service as web
from app.auto_router import build_task_profile
from app.chat_service import _chat_with_ai_unlocked
from app.config import settings
from app.llm import CompletionResult


QUERIES = (
    "帮我查 Python 官方文档",
    "帮我查一下 Python 官方文档",
    "打开 https://docs.python.org/zh-cn/3/",
    "Python 现在的版本是多少？",
    "最近有什么新消息？",
    "帮我查一下 DeepSeek 最新消息",
)


@contextmanager
def setting(name, value):
    original = getattr(settings, name)
    object.__setattr__(settings, name, value)
    try:
        yield
    finally:
        object.__setattr__(settings, name, original)


class WebIntentConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.enabled = setting("web_search_enabled", True)
        self.enabled.__enter__()
        self.addCleanup(self.enabled.__exit__, None, None, None)

    def test_all_search_requests_bypass_plain_chat_classification(self):
        for query in QUERIES:
            with self.subTest(query=query):
                self.assertTrue(web.should_use_web_lookup(query))
                self.assertTrue(build_task_profile(query).requires_tools)

    def test_contextual_weather_followup_is_routed_for_lookup(self):
        rows = [{"role": "user", "content": "今天天气怎么样？"},
                {"role": "assistant", "content": "你在哪个城市？"}]
        self.assertTrue(build_task_profile("我在合川", history_rows=rows).requires_tools)

    def test_personal_state_and_greetings_do_not_search(self):
        for query in ("你好", "我现在心情怎么样？", "你现在感觉怎么样？", "现在几点？"):
            with self.subTest(query=query):
                self.assertFalse(web.should_use_web_lookup(query))
                self.assertFalse(build_task_profile(query).requires_tools)

    def test_capability_question_is_not_used_as_search_keywords(self):
        for query in ("你能联网吗？", "你能不能联网？", "你能访问互联网吗？",
                      "Mio 支持联网搜索吗？", "联网功能怎么开启？"):
            with self.subTest(query=query):
                self.assertFalse(web.should_use_web_lookup(query))

    def test_concrete_query_after_capability_question_still_searches(self):
        self.assertTrue(web.should_use_web_lookup("你能联网吗？帮我查一下 Python 官方文档"))


class WebChatReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mio-web-regression-")
        self.addCleanup(directory.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(setting("db_path", Path(directory.name) / "test.db"))
        self.stack.enter_context(setting("web_search_enabled", True))
        db.init_db()

    async def run_chat(self, message, *, fast=True, lookup=None, history=None, persist=False):
        self.messages = []
        completion = CompletionResult(content="这是测试回复。", model="test", prompt_tokens=0,
                                      cached_prompt_tokens=0, completion_tokens=0,
                                      reasoning_tokens=0, cost_yuan=0, cost_source="test")

        async def complete(messages, **kwargs):
            self.messages.extend(messages)
            return completion, "off", ""

        with ExitStack() as stack:
            patches = {
                "resolve_model_id": {"return_value": "test-model"},
                "require_configured": {},
                "get_model_profile": {"return_value": SimpleNamespace(model="test-model")},
                "normalize_model_reasoning": {"return_value": "off"},
                "load_manuals": {"return_value": []},
                "build_system_prompt": {"return_value": "system"},
                "build_chat_context_snapshot": {"side_effect": lambda cid, rows: SimpleNamespace(raw_messages=list(rows), system_context="")},
                "build_fast_chat_context_snapshot": {"side_effect": lambda cid, rows: SimpleNamespace(raw_messages=list(rows), system_context="")},
                "build_chat_context": {"new": AsyncMock(side_effect=lambda cid, rows: SimpleNamespace(raw_messages=list(rows), system_context=""))},
                "system_audio_service.chat_context": {"return_value": ""},
                "db.get_recent_messages": {"return_value": [dict(row, created_at="2026-09-19T11:59:00+08:00") for row in (history or [])]},
                "db.get_latest_message_id": {"return_value": 40},
                "db.now_iso": {"return_value": "2026-09-19T12:00:00+08:00"},
                "_self_snapshot_context_for_message": {"new": AsyncMock(return_value="")},
                "_complete_chat_reply_with_single_fallback": {"new": AsyncMock(side_effect=complete)},
                "queue_cost_reconciliation": {},
                "schedule_companion_actions": {},
            }
            self.mocks = {key: stack.enter_context(patch("app.chat_service." + key, **args))
                          for key, args in patches.items()}
            stack.enter_context(patch("app.companion_service.set_pet_activity"))
            stack.enter_context(patch("app.companion_service.infer_speech_emotion", return_value="neutral"))
            self.lookup_mock = stack.enter_context(patch("app.chat_service.perform_web_lookup", new=AsyncMock(return_value=lookup)))
            result = await _chat_with_ai_unlocked(message, conversation_id="web-regression-test", source="desktop",
                                                model_id="test-model", persist=persist,
                                                fast_path=fast, agent_tools_enabled=False)
        return result

    async def test_fast_path_cannot_suppress_required_search(self):
        result = web.WebLookup(query="Python", sources=[web.WebSource("Python docs", "https://docs.python.org/", "OFFICIAL_TEST_RESULT")])
        await self.run_chat("帮我查 Python 官方文档", lookup=result)
        self.lookup_mock.assert_awaited_once()
        self.assertIn("OFFICIAL_TEST_RESULT", str(self.messages))

    async def test_failed_search_context_reaches_companion_model(self):
        for fast in (False, True):
            with self.subTest(fast=fast):
                await self.run_chat("帮我查一下 Python", fast=fast,
                                    lookup=web.WebLookup("Python", [], error="TEST_SEARCH_TIMEOUT"))
                self.assertIn("TEST_SEARCH_TIMEOUT", str(self.messages))

    async def test_empty_search_context_reaches_companion_model(self):
        await self.run_chat("帮我查一下 Python", fast=False, lookup=web.WebLookup("Python", []))
        self.assertIn("没有拿到可用搜索结果", str(self.messages))

    async def test_fast_path_uses_weather_followup_context(self):
        rows = [{"id": 1, "role": "user", "content": "今天天气怎么样？"},
                {"id": 2, "role": "assistant", "content": "你在哪个城市？"}]
        await self.run_chat("我在合川", history=rows)
        self.lookup_mock.assert_awaited_once_with("合川 今天 天气")

    async def test_capability_reply_uses_local_setting_without_model_or_search(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), setting("web_search_enabled", enabled):
                result = await self.run_chat("你能联网吗？")
                self.assertEqual(result.route, "local_web_capability")
                self.assertIn("已开启" if enabled else "已关闭", result.reply)
                self.assertIn("测试联网", result.reply)
                self.assertNotIn("联网正常", result.reply)
                self.lookup_mock.assert_not_awaited()
                self.mocks["_complete_chat_reply_with_single_fallback"].assert_not_awaited()

    async def test_capability_reply_is_persisted_as_same_request(self):
        result = await self.run_chat("联网功能怎么开启？", persist=True)
        rows = db.get_recent_messages(10, "web-regression-test")
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
        self.assertEqual(rows[1]["content"], result.reply)
        self.assertEqual(rows[0]["request_id"], rows[1]["request_id"])
        self.assertEqual(result.route, "local_web_capability")

    async def test_disabled_search_never_calls_transport_and_reports_reason(self):
        with setting("web_search_enabled", False), patch.object(web, "_search_web", new=AsyncMock()) as search:
            result = await web.perform_web_lookup("帮我查一下 Python")
        search.assert_not_awaited()
        # A disabled lookup is a no-op; chat_service supplies the precise
        # permission context independently rather than inventing a network error.
        self.assertIsNone(result)
        with setting("web_search_enabled", False):
            await self.run_chat("帮我查一下 Python", lookup=None)
        self.assertIn("联网搜索已关闭", str(self.messages))


if __name__ == "__main__":
    unittest.main()
