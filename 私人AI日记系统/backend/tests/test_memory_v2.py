from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app import db
from app.companion_action_service import execute_companion_actions, parse_companion_decision
from app.context_service import build_periodic_memory_context
from app.memory_service import build_structured_memory_context, retrieve_memory_items, save_memory_item
from app.routes.memory import StructuredMemoryRequest, api_archive_memory_item, api_create_memory_item


class StructuredMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "memory-v2.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_same_key_reinforces_then_supersedes_old_memory(self) -> None:
        first = save_memory_item(
            layer="L0",
            category="preference",
            memory_key="preferred_reply_style",
            content="喜欢简短但可以多条的回复",
            source_conversation_id="qq_private_test",
            source_message_id=10,
            confidence=0.95,
        )
        reinforced = save_memory_item(
            layer="L0",
            category="preference",
            memory_key="preferred_reply_style",
            content="喜欢简短但可以多条的回复",
            source_conversation_id="desktop_test",
            source_message_id=11,
            confidence=0.98,
        )
        replacement = save_memory_item(
            layer="L0",
            category="preference",
            memory_key="preferred_reply_style",
            content="普通聊天更喜欢一两句自然短话",
            source_conversation_id="qq_private_test",
            source_message_id=12,
            confidence=0.99,
        )

        self.assertEqual(first["outcome"], "created")
        self.assertEqual(reinforced["id"], first["id"])
        self.assertEqual(reinforced["outcome"], "reinforced")
        self.assertEqual(replacement["outcome"], "superseded")
        active = db.list_structured_memories(status="active")
        history = db.list_structured_memories(status="superseded")
        self.assertEqual([row["content"] for row in active], ["普通聊天更喜欢一两句自然短话"])
        self.assertEqual(int(history[0]["superseded_by"]), replacement["id"])
        self.assertEqual(int(active[0]["source_message_id"]), 12)

    def test_group_chat_cannot_write_or_read_private_memory(self) -> None:
        with self.assertRaisesRegex(ValueError, "群聊"):
            save_memory_item(
                layer="L1",
                category="current_state",
                memory_key="current_state",
                content="最近在准备面试",
                source_conversation_id="qq_group_123",
                confidence=0.9,
            )
        save_memory_item(
            layer="L0",
            category="identity",
            memory_key="nickname",
            content="希望被称呼为小洛",
            source_conversation_id="qq_private_test",
            confidence=0.95,
        )
        self.assertEqual(build_structured_memory_context("qq_group_123", "称呼"), "")

    def test_retrieval_keeps_core_and_prioritizes_relevant_evidence(self) -> None:
        save_memory_item(
            layer="L0",
            category="preference",
            memory_key="reply_style",
            content="喜欢自然短句",
            source_conversation_id="qq_private_test",
            source_message_id=1,
            confidence=0.96,
        )
        save_memory_item(
            layer="L2",
            category="project",
            memory_key="mio_agent",
            content="正在持续开发澪 Agent 应用",
            source_conversation_id="qq_private_test",
            source_message_id=2,
            confidence=0.92,
        )
        rows = retrieve_memory_items("澪 Agent 下一步", limit=2)
        self.assertEqual({row["layer"] for row in rows}, {"L0", "L2"})
        context = build_periodic_memory_context("qq_private_test", "澪 Agent 下一步")
        self.assertIn("分层私人记忆", context)
        self.assertIn("澪 Agent", context)

    def test_memory_api_creates_and_archives_item(self) -> None:
        created = asyncio.run(api_create_memory_item(StructuredMemoryRequest(
            layer="L2",
            category="project",
            memory_key="mio_agent",
            content="澪 Agent 是当前长期项目",
            confidence=1,
            conversation_id="desktop_test",
        )))
        self.assertTrue(created["saved"])
        memory_id = created["memory"]["id"]
        archived = asyncio.run(api_archive_memory_item(memory_id))
        self.assertTrue(archived["archived"])
        self.assertEqual(db.get_structured_memory(memory_id)["status"], "archived")


class StructuredMemoryActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "memory-action.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    async def test_planner_memory_action_is_parsed_and_executed(self) -> None:
        decision = parse_companion_decision(
            '{"assessment":{"confidence":0.98,"needs_clarification":false},'
            '"actions":[{"type":"remember_memory","layer":"L0","category":"preference",'
            '"memory_key":"reply_style","content":"喜欢自然短句","confidence":0.98}]}'
        )
        self.assertEqual(decision.actions[0]["type"], "remember_memory")
        results = await execute_companion_actions(
            decision.actions,
            "qq_private_test",
            "我更喜欢自然短句",
            20,
        )
        self.assertEqual(results[0]["status"], "executed")
        self.assertIn("memory:", results[0]["result"])


if __name__ == "__main__":
    unittest.main()
