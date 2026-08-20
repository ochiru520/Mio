from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.context_service import (
    build_chat_context,
    build_fast_chat_context_snapshot,
    estimate_tokens,
    preview_chat_context_usage,
)
from app.memory_service import retrieve_memory_items, save_memory_candidate, save_memory_item


class ContextAndMemoryVNextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        self.original_max_tokens = db.settings.chat_context_max_tokens
        self.original_max_chars = db.settings.chat_context_max_chars
        self.original_recent_keep = db.settings.chat_recent_keep_messages
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "test.db")
        object.__setattr__(db.settings, "chat_context_max_tokens", 1000)
        object.__setattr__(db.settings, "chat_context_max_chars", 200000)
        object.__setattr__(db.settings, "chat_recent_keep_messages", 4)
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        object.__setattr__(db.settings, "chat_context_max_tokens", self.original_max_tokens)
        object.__setattr__(db.settings, "chat_context_max_chars", self.original_max_chars)
        object.__setattr__(db.settings, "chat_recent_keep_messages", self.original_recent_keep)
        self.temp_dir.cleanup()

    def test_mixed_language_token_estimate_is_nonzero(self) -> None:
        self.assertGreater(estimate_tokens("澪在测试 Agent vNext 123"), 5)

    def test_context_reports_warning_and_compression_threshold(self) -> None:
        conversation_id = "desktop_token_test"
        for index in range(4):
            db.save_message(
                "user",
                f"第{index}段" + "这是用于上下文预算测试的中文内容" * 25,
                conversation_id=conversation_id,
            )
        rows = db.get_recent_messages(limit=50, conversation_id=conversation_id)

        usage = preview_chat_context_usage(conversation_id, list(rows))

        self.assertTrue(usage["warning"])
        self.assertTrue(usage["compression_triggered"])
        self.assertEqual(usage["max_tokens"], 1000)
        self.assertGreater(usage["used_tokens"], 0)

    def test_context_compresses_old_rows_after_eighty_two_percent(self) -> None:
        conversation_id = "desktop_compress_test"
        for index in range(8):
            db.save_message(
                "user" if index % 2 == 0 else "assistant",
                f"第{index}段" + "需要保留的重要上下文" * 18,
                conversation_id=conversation_id,
            )
        rows = list(db.get_recent_messages(limit=50, conversation_id=conversation_id))
        compressor = AsyncMock(return_value="用户正在测试 token 上下文压缩。")

        with patch("app.context_service._compress_old_messages", compressor):
            context = asyncio.run(build_chat_context(conversation_id, rows))

        compressor.assert_awaited_once()
        self.assertTrue(context.compression_triggered)
        self.assertIsNotNone(db.get_latest_memory("conversation_summary", tags=conversation_id))

    def test_fast_chat_context_keeps_only_recent_bounded_messages(self) -> None:
        conversation_id = "desktop_fast_chat"
        for index in range(20):
            db.save_message(
                "user" if index % 2 == 0 else "assistant",
                f"message-{index}-" + "x" * 300,
                conversation_id=conversation_id,
            )
        rows = list(db.get_recent_messages(limit=50, conversation_id=conversation_id))

        context = build_fast_chat_context_snapshot(
            conversation_id,
            rows,
            recent_messages=6,
            max_tokens=900,
        )

        self.assertLessEqual(len(context.raw_messages), 6)
        self.assertLessEqual(context.used_tokens, 900)
        self.assertFalse(context.compression_triggered)

    def test_fts_retrieves_chinese_memory(self) -> None:
        save_memory_item(
            layer="L0",
            category="preference",
            memory_key="preferred_reply_style",
            content="用户喜欢自然简短的多气泡回复",
            source_conversation_id="desktop_test",
            confidence=0.98,
        )

        rows = retrieve_memory_items("简短回复", limit=5)

        self.assertTrue(any("简短" in str(row["content"]) for row in rows))

    def test_candidate_confirm_supersede_and_sleep_wake(self) -> None:
        active = save_memory_item(
            layer="L0",
            category="preference",
            memory_key="preferred_reply_style",
            content="用户喜欢一句话回复",
            source_conversation_id="desktop_test",
            confidence=0.98,
        )
        candidate = save_memory_candidate(
            layer="L0",
            category="preference",
            memory_key="preferred_reply_style",
            content="用户更喜欢两三条自然短句",
            source_conversation_id="desktop_test",
            confidence=0.68,
        )

        self.assertFalse(any(int(row["id"]) == int(candidate["id"]) for row in retrieve_memory_items("短句")))
        self.assertTrue(db.confirm_structured_memory_candidate(int(candidate["id"])))
        self.assertEqual(str(db.get_structured_memory(int(active["id"]))["status"]), "superseded")
        self.assertEqual(str(db.get_structured_memory(int(candidate["id"]))["status"]), "active")

        self.assertTrue(db.set_structured_memory_status(int(candidate["id"]), "sleeping"))
        self.assertFalse(any(int(row["id"]) == int(candidate["id"]) for row in retrieve_memory_items("短句")))
        self.assertTrue(db.set_structured_memory_status(int(candidate["id"]), "active"))
        self.assertTrue(any(int(row["id"]) == int(candidate["id"]) for row in retrieve_memory_items("短句")))

    def test_stale_recent_memory_automatically_sleeps(self) -> None:
        saved = save_memory_item(
            layer="L1",
            category="current_state",
            memory_key="current_project",
            content="用户最近正在做旧项目",
            source_conversation_id="desktop_test",
            confidence=0.90,
        )
        old_time = (datetime.now().astimezone() - timedelta(days=30)).isoformat(timespec="seconds")
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE structured_memories SET last_seen_at = ?, updated_at = ? WHERE id = ?",
                (old_time, old_time, int(saved["id"])),
            )

        rows = retrieve_memory_items("旧项目", limit=5)

        self.assertFalse(any(int(row["id"]) == int(saved["id"]) for row in rows))
        self.assertEqual(str(db.get_structured_memory(int(saved["id"]))["status"]), "sleeping")

    def test_retrieved_memory_refreshes_last_seen_time(self) -> None:
        saved = save_memory_item(
            layer="L2",
            category="project",
            memory_key="mio_agent",
            content="用户长期维护澪 Agent 项目",
            source_conversation_id="desktop_test",
            confidence=0.90,
        )
        old_time = (datetime.now().astimezone() - timedelta(days=90)).isoformat(timespec="seconds")
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE structured_memories SET last_seen_at = ? WHERE id = ?",
                (old_time, int(saved["id"])),
            )

        self.assertTrue(retrieve_memory_items("澪 Agent", limit=5))
        refreshed = datetime.fromisoformat(str(db.get_structured_memory(int(saved["id"]))["last_seen_at"]))
        self.assertGreater(refreshed, datetime.fromisoformat(old_time))


if __name__ == "__main__":
    unittest.main()
