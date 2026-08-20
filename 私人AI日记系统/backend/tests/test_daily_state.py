from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.routes.chat import _chat_log_for_rows, analyze_today_state


class DailyStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "daily-state.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_state_chat_log_only_contains_user_messages(self) -> None:
        rows = [
            {"role": "user", "content": "今天继续做项目", "created_at": "2026-08-10T10:00:00+08:00"},
            {"role": "assistant", "content": "屏幕观察推测你在玩游戏", "created_at": "2026-08-10T10:01:00+08:00"},
        ]

        chat_log = _chat_log_for_rows(rows)

        self.assertIn("今天继续做项目", chat_log)
        self.assertNotIn("屏幕观察", chat_log)

    async def test_manual_analysis_persists_complete_today_state(self) -> None:
        db.save_message("user", "今天推进了项目", source="desktop", conversation_id="desktop_test")
        completion = AsyncMock(
            return_value=(
                '{"daily_thirty_status":"partial","daily_thirty_reason":"推进了项目但时长不明",'
                '"mood":"平稳","mood_score":3,"key_events":"推进项目",'
                '"avoidance_signals":"未确认","next_min_action":"完成一次回归测试"}'
            )
        )
        with (
            patch("app.routes.chat.require_configured"),
            patch("app.routes.chat.call_chat_completion", completion),
        ):
            result = await analyze_today_state()

        state = db.get_daily_state()
        self.assertEqual(result["mood_score"], 3)
        self.assertEqual(state["key_events"], "推进项目")
        self.assertEqual(state["next_min_action"], "完成一次回归测试")
        sent_messages = completion.await_args.args[0]
        self.assertIn("今天推进了项目", sent_messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
