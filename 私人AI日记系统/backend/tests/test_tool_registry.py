from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app import db
from app.companion_action_service import execute_companion_actions
from app.routes.agent import agent_tool_receipts, agent_tools
from app.tool_registry import ToolPermission, tool_registry


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "test.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_registry_contains_all_permission_levels(self) -> None:
        permissions = {definition.permission for definition in tool_registry.list()}
        self.assertEqual(
            permissions,
            {
                ToolPermission.READ_ONLY,
                ToolPermission.LOW_RISK_WRITE,
                ToolPermission.HIGH_RISK_WRITE,
            },
        )
        self.assertTrue(tool_registry.require("edit_today_diary").has_explicit_intent("把今天的日记补充一下"))
        self.assertFalse(tool_registry.require("edit_today_diary").has_explicit_intent("今天有点累"))

    def test_low_risk_write_executes_and_creates_redacted_receipt(self) -> None:
        source_id = db.save_message("user", "今天完成了测试", conversation_id="desktop_test")
        results = asyncio.run(
            execute_companion_actions(
                [
                    {
                        "type": "add_diary_material",
                        "content": "完成了工具注册测试",
                        "confidence": 0.95,
                        "api_key": "should-not-appear",
                    }
                ],
                "desktop_test",
                "今天完成了测试",
                source_id,
            )
        )

        self.assertEqual(results[0]["status"], "executed")
        receipt = db.list_tool_execution_receipts(limit=1)[0]
        self.assertEqual(str(receipt["tool_name"]), "add_diary_material")
        self.assertEqual(str(receipt["permission"]), "low_risk_write")
        self.assertEqual(str(receipt["status"]), "executed")
        payload = json.loads(str(receipt["request_json"]))
        self.assertEqual(payload["api_key"], "[已脱敏]")

    def test_high_risk_write_without_explicit_intent_waits_for_confirmation(self) -> None:
        db.upsert_diary(
            db.today_string(),
            "今天",
            "# 今天\n\n已有的正式日记。",
        )
        source_id = db.save_message("user", "今天有点累", conversation_id="desktop_test")
        results = asyncio.run(
            execute_companion_actions(
                [
                    {
                        "type": "edit_today_diary",
                        "instruction": "补充今天很累",
                        "confidence": 0.95,
                    }
                ],
                "desktop_test",
                "今天有点累",
                source_id,
            )
        )

        self.assertEqual(results[0]["status"], "needs_confirmation")
        receipt = db.list_tool_execution_receipts(limit=1)[0]
        self.assertEqual(str(receipt["permission"]), "high_risk_write")
        self.assertEqual(str(receipt["status"]), "needs_confirmation")

    def test_tool_catalog_api_exposes_permission_metadata(self) -> None:
        payload = asyncio.run(agent_tools())

        by_name = {item["name"]: item for item in payload["tools"]}
        self.assertEqual(by_name["get_today_state"]["permission"], "read_only")
        self.assertEqual(by_name["add_diary_material"]["permission"], "low_risk_write")
        self.assertEqual(by_name["edit_today_diary"]["permission"], "high_risk_write")
        self.assertTrue(by_name["edit_today_diary"]["requires_explicit_intent"])

    def test_receipt_api_returns_redacted_parsed_request(self) -> None:
        db.start_tool_execution_receipt(
            "desktop_test",
            "add_diary_material",
            "low_risk_write",
            json.dumps({"content": "测试", "api_key": "[已脱敏]"}, ensure_ascii=False),
            "executed",
        )

        payload = asyncio.run(agent_tool_receipts(limit=10))

        self.assertEqual(payload["receipts"][0]["request"]["api_key"], "[已脱敏]")
        self.assertNotIn("request_json", payload["receipts"][0])


if __name__ == "__main__":
    unittest.main()
