from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.config import settings
from app.daily_diary_service import run_daily_diary_once
from app.routes.diary import generate_diary_for_date_payload


class DailyDiaryServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = settings.db_path
        self.original_diary_dir = settings.diary_dir
        self.original_enabled = settings.daily_diary_auto_enabled
        object.__setattr__(settings, "db_path", Path(self.temp_dir.name) / "test.db")
        object.__setattr__(settings, "diary_dir", Path(self.temp_dir.name) / "日记")
        object.__setattr__(settings, "daily_diary_auto_enabled", True)
        settings.diary_dir.mkdir(parents=True, exist_ok=True)
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(settings, "db_path", self.original_db_path)
        object.__setattr__(settings, "diary_dir", self.original_diary_dir)
        object.__setattr__(settings, "daily_diary_auto_enabled", self.original_enabled)
        self.temp_dir.cleanup()

    async def test_boundary_run_generates_previous_logical_day_once(self) -> None:
        target_date = "2026-07-15"
        db.add_diary_material("今天完成了主要任务。", date=target_date, source="test")
        generated = {
            "date": target_date,
            "title": "测试日记",
            "skipped": False,
        }
        now = datetime(2026, 7, 16, 4, 1, tzinfo=timezone(timedelta(hours=8)))

        with patch(
            "app.daily_diary_service.generate_diary_for_date_payload",
            new=AsyncMock(return_value=generated),
        ) as generate_mock:
            count = await run_daily_diary_once(now)

        self.assertEqual(count, 1)
        generate_mock.assert_awaited_once_with(target_date, overwrite=False)

    async def test_before_boundary_does_not_close_current_logical_day(self) -> None:
        target_date = "2026-07-15"
        db.add_diary_material("深夜还在继续做。", date=target_date, source="test")
        now = datetime(2026, 7, 16, 1, 0, tzinfo=timezone(timedelta(hours=8)))

        with patch(
            "app.daily_diary_service.generate_diary_for_date_payload",
            new_callable=AsyncMock,
        ) as generate_mock:
            count = await run_daily_diary_once(now)

        self.assertEqual(count, 0)
        generate_mock.assert_not_awaited()

    async def test_existing_diary_is_never_overwritten(self) -> None:
        target_date = "2026-07-15"
        db.upsert_diary(target_date, "已有日记", "# 已有日记", "", "done")
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)

        with patch(
            "app.daily_diary_service.generate_diary_for_date_payload",
            new_callable=AsyncMock,
        ) as generate_mock:
            count = await run_daily_diary_once(now)

        self.assertEqual(count, 0)
        generate_mock.assert_not_awaited()
        self.assertEqual(db.get_diary(target_date)["markdown_content"], "# 已有日记")

    async def test_day_without_content_is_skipped(self) -> None:
        now = datetime(2026, 7, 16, 0, 1, tzinfo=timezone.utc)
        with patch(
            "app.daily_diary_service.generate_diary_for_date_payload",
            new_callable=AsyncMock,
        ) as generate_mock:
            count = await run_daily_diary_once(now)

        self.assertEqual(count, 0)
        generate_mock.assert_not_awaited()

    async def test_date_specific_generation_writes_target_day(self) -> None:
        target_date = "2026-07-15"
        db.add_diary_material("做完了一个功能。", date=target_date, source="test")
        markdown = "# 测试日记\n\n## 今日事件\n做完了一个功能。"

        with patch(
            "app.routes.diary.call_chat_completion",
            new=AsyncMock(return_value=markdown),
        ) as completion, patch(
            "app.routes.diary._diary_model_id",
            return_value="default-diary-model",
        ):
            result = await generate_diary_for_date_payload(target_date, overwrite=False)

        self.assertEqual(result["date"], target_date)
        self.assertFalse(result["skipped"])
        self.assertEqual(db.get_diary(target_date)["markdown_content"], markdown)
        self.assertEqual((settings.diary_dir / f"{target_date}.md").read_text(encoding="utf-8"), markdown)
        self.assertEqual(completion.await_args.kwargs["model_id"], "default-diary-model")
        self.assertEqual(completion.await_args.kwargs["reasoning_level"], "medium")


if __name__ == "__main__":
    unittest.main()
