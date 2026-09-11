from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import db
from app.memory_service import build_structured_memory_context, save_memory_item
from app.runtime_profiles import COMPANION_TOOL_NAMES, resolve_runtime_profile


class RuntimeProfileTests(unittest.TestCase):
    def test_companion_and_agent_share_identity_but_have_different_capabilities(self) -> None:
        companion = resolve_runtime_profile("companion")
        agent = resolve_runtime_profile("agent")

        self.assertEqual(companion.mode, "companion")
        self.assertEqual(agent.mode, "agent")
        self.assertFalse(companion.show_execution_trace)
        self.assertTrue(agent.show_execution_trace)
        self.assertIn("search_memory", companion.allowed_tool_names)
        self.assertNotIn("comfyui_generate_image", COMPANION_TOOL_NAMES)
        self.assertIn("comfyui_generate_image", agent.allowed_tool_names)


class SharedMemorySourceWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "test.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_memory_records_source_window_without_changing_shared_store(self) -> None:
        saved = save_memory_item(
            layer="L2",
            category="project",
            memory_key="agent_project",
            content="Agent 完成了一个项目任务",
            source_conversation_id="desktop_agent_shared",
            confidence=0.9,
        )
        row = db.get_structured_memory(int(saved["id"]))

        self.assertIsNotNone(row)
        self.assertEqual(row["source_window"], "agent")

    def test_companion_memory_is_visible_to_agent_without_sharing_raw_chat(self) -> None:
        saved = save_memory_item(
            layer="L1",
            category="preference",
            memory_key="window_preference",
            content="用户偏好先看今天和昨天的记录，再判断当前安排",
            source_conversation_id="desktop_companion_shared",
            confidence=0.9,
        )

        context = build_structured_memory_context("desktop_agent_shared", "昨天的安排")

        self.assertIn("先看今天和昨天的记录", context)
        self.assertIn("来源窗口 companion", context)
        self.assertEqual(db.get_structured_memory(int(saved["id"]))["source_window"], "companion")


if __name__ == "__main__":
    unittest.main()
