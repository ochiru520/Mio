from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app import db
from app.context_service import SUMMARY_TYPE
from app.routes.memory import (
    ConversationSummaryRequest,
    MemoryTextRequest,
    PendingThreadRequest,
    _memory_data,
    api_add_profile_note,
    api_create_thread,
    api_delete_conversation_summary,
    api_delete_profile_note,
    api_delete_thread,
    api_update_conversation_summary,
    api_update_profile_note,
    api_update_runtime_summary,
    api_update_thread,
)


class MemoryManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_db_path = db.settings.db_path
        self.original_profile_path = db.settings.mio_profile_path
        self.original_runtime_summary_path = db.settings.runtime_summary_path
        object.__setattr__(db.settings, "db_path", root / "test.db")
        object.__setattr__(db.settings, "mio_profile_path", root / "mio-profile.json")
        object.__setattr__(db.settings, "runtime_summary_path", root / "runtime.md")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        object.__setattr__(db.settings, "mio_profile_path", self.original_profile_path)
        object.__setattr__(db.settings, "runtime_summary_path", self.original_runtime_summary_path)
        self.temp_dir.cleanup()

    def test_runtime_summary_is_saved_and_returned(self) -> None:
        asyncio.run(api_update_runtime_summary(MemoryTextRequest(content="# 运行时\n自然聊天")))

        data = _memory_data()
        self.assertIn("自然聊天", data["runtime_summary"]["content"])

    def test_pending_thread_can_be_created_updated_and_deleted(self) -> None:
        created = asyncio.run(api_create_thread(PendingThreadRequest(
            content="明天继续做 Agent",
            conversation_id="desktop_test",
            follow_up_after="2026-08-03T09:00",
        )))
        thread_id = created["id"]
        asyncio.run(api_update_thread(thread_id, PendingThreadRequest(
            content="明天继续测试 Agent",
            conversation_id="desktop_test",
            follow_up_after="",
        )))
        self.assertEqual(_memory_data()["threads"][0]["content"], "明天继续测试 Agent")
        asyncio.run(api_delete_thread(thread_id))
        self.assertEqual(_memory_data()["threads"], [])

    def test_conversation_summary_changes_real_context_memory(self) -> None:
        payload = ConversationSummaryRequest(conversation_id="desktop_test", content="用户正在完善澪。")
        asyncio.run(api_update_conversation_summary(payload))
        row = db.get_latest_memory(SUMMARY_TYPE, tags="desktop_test")
        self.assertIn("用户正在完善澪", row["content"])
        asyncio.run(api_delete_conversation_summary("desktop_test"))
        self.assertIsNone(db.get_latest_memory(SUMMARY_TYPE, tags="desktop_test"))

    def test_profile_notes_can_be_added_edited_and_deleted(self) -> None:
        added = asyncio.run(api_add_profile_note(MemoryTextRequest(content="喜欢自然短句")))
        notes = added["profile"]["preferences"]["custom_notes"]
        note_index = notes.index("喜欢自然短句")
        edited = asyncio.run(api_update_profile_note(note_index, MemoryTextRequest(content="喜欢自然多气泡")))
        self.assertEqual(edited["profile"]["preferences"]["custom_notes"][note_index], "喜欢自然多气泡")
        deleted = asyncio.run(api_delete_profile_note(note_index))
        self.assertNotIn("喜欢自然多气泡", deleted["profile"]["preferences"]["custom_notes"])


if __name__ == "__main__":
    unittest.main()
