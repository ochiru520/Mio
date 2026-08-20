from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app import db
from app.config import settings


class LogicalDayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = settings.db_path
        self.original_boundary = settings.day_boundary_hour
        object.__setattr__(settings, "db_path", Path(self.temp_dir.name) / "test.db")
        object.__setattr__(settings, "day_boundary_hour", 4)
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(settings, "db_path", self.original_db_path)
        object.__setattr__(settings, "day_boundary_hour", self.original_boundary)
        self.temp_dir.cleanup()

    def test_logical_date_changes_at_four(self) -> None:
        self.assertEqual(
            db.today_string(datetime.fromisoformat("2026-07-18T03:59:59+08:00")),
            "2026-07-17",
        )
        self.assertEqual(
            db.today_string(datetime.fromisoformat("2026-07-18T04:00:00+08:00")),
            "2026-07-18",
        )

    def test_message_query_uses_four_to_four_window(self) -> None:
        rows = [
            ("too_early", "2026-07-17T03:59:59+08:00"),
            ("start", "2026-07-17T04:00:00+08:00"),
            ("late_night", "2026-07-18T03:59:59+08:00"),
            ("next_day", "2026-07-18T04:00:00+08:00"),
        ]
        with db.get_conn() as conn:
            conn.executemany(
                """
                INSERT INTO messages (role, content, source, conversation_id, created_at)
                VALUES ('user', ?, 'test', 'logical-day', ?)
                """,
                rows,
            )

        contents = [row["content"] for row in db.get_today_messages("2026-07-17")]
        self.assertEqual(contents, ["start", "late_night"])


if __name__ == "__main__":
    unittest.main()
