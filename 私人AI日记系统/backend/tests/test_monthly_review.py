from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.config import settings
from app.monthly_review_service import (
    generate_monthly_review,
    last_completed_month,
    month_bounds,
    normalize_month,
    run_monthly_review_once,
)
from app.routes.monthly import api_monthly_list


class MonthlyReviewDateTests(unittest.TestCase):
    def test_month_bounds_use_natural_month_and_leap_year(self) -> None:
        self.assertEqual(month_bounds("2024-02"), ("2024-02-01", "2024-02-29"))
        self.assertEqual(month_bounds("2026-08"), ("2026-08-01", "2026-08-31"))

    def test_last_completed_month_crosses_year_boundary(self) -> None:
        self.assertEqual(last_completed_month(date(2026, 1, 1)), "2025-12")
        self.assertEqual(last_completed_month(date(2026, 8, 18)), "2026-07")

    def test_invalid_months_are_rejected(self) -> None:
        for value in ("2026-00", "2026-13", "2026-8", "2026-08-01", "not-a-month"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_month(value)


class MonthlyReviewGenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = settings.db_path
        object.__setattr__(settings, "db_path", Path(self.temp_dir.name) / "monthly.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    async def test_generation_uses_only_days_inside_selected_natural_month(self) -> None:
        db.upsert_diary("2026-07-31", "七月", "不应进入八月")
        db.upsert_diary("2026-08-01", "八月开始", "开始整理作品集")
        db.upsert_diary("2026-08-31", "八月结束", "完成作品集第一版")
        db.upsert_diary("2026-09-01", "九月", "也不应进入八月")

        with patch(
            "app.monthly_review_service.call_chat_completion",
            new=AsyncMock(return_value="# 2026-08 月记\n\n## 这个月的主线\n- 作品集"),
        ) as call:
            result = await generate_monthly_review("2026-08")

        self.assertTrue(result.created)
        self.assertEqual(result.month_start, "2026-08-01")
        self.assertEqual(result.month_end, "2026-08-31")
        prompt = call.await_args.args[0][1]["content"]
        self.assertIn("开始整理作品集", prompt)
        self.assertIn("完成作品集第一版", prompt)
        self.assertNotIn("不应进入八月", prompt)
        self.assertNotIn("也不应进入八月", prompt)
        self.assertEqual(db.get_monthly_review("2026-08")["markdown_content"], result.markdown_content)

    async def test_empty_month_does_not_call_model(self) -> None:
        with patch(
            "app.monthly_review_service.call_chat_completion",
            new=AsyncMock(return_value="不应调用"),
        ) as call:
            with self.assertRaisesRegex(ValueError, "没有任何日记"):
                await generate_monthly_review("2026-06")
        call.assert_not_awaited()

    async def test_list_api_returns_natural_month_boundaries(self) -> None:
        db.upsert_monthly_review("2024-02", "# 二月月记")
        result = await api_monthly_list()
        self.assertEqual(result[0]["month"], "2024-02")
        self.assertEqual(result[0]["month_start"], "2024-02-01")
        self.assertEqual(result[0]["month_end"], "2024-02-29")

    async def test_automatic_run_generates_previous_complete_month_once(self) -> None:
        db.upsert_diary("2025-12-20", "十二月", "完成年度整理")
        original_enabled = settings.monthly_review_enabled
        original_hour = settings.monthly_review_hour
        object.__setattr__(settings, "monthly_review_enabled", True)
        object.__setattr__(settings, "monthly_review_hour", 10)
        try:
            with (
                patch(
                    "app.monthly_review_service.call_chat_completion",
                    new=AsyncMock(return_value="# 2025-12 月记\n\n年度整理完成"),
                ),
                patch("app.monthly_review_service._notify_monthly_review_ready", new=AsyncMock()),
            ):
                first = await run_monthly_review_once(datetime(2026, 1, 2, 10, 30))
                second = await run_monthly_review_once(datetime(2026, 1, 2, 11, 0))
        finally:
            object.__setattr__(settings, "monthly_review_enabled", original_enabled)
            object.__setattr__(settings, "monthly_review_hour", original_hour)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertIsNotNone(db.get_monthly_review("2025-12"))

    async def test_automatic_run_uses_calendar_month_not_logical_day_boundary(self) -> None:
        db.upsert_diary("2025-12-20", "十二月", "完成年度整理")
        original_enabled = settings.monthly_review_enabled
        original_hour = settings.monthly_review_hour
        original_boundary = settings.day_boundary_hour
        object.__setattr__(settings, "monthly_review_enabled", True)
        object.__setattr__(settings, "monthly_review_hour", 0)
        object.__setattr__(settings, "day_boundary_hour", 4)
        try:
            with (
                patch(
                    "app.monthly_review_service.call_chat_completion",
                    new=AsyncMock(return_value="# 2025-12 月记\n\n年度整理完成"),
                ),
                patch("app.monthly_review_service._notify_monthly_review_ready", new=AsyncMock()),
            ):
                created = await run_monthly_review_once(datetime(2026, 1, 1, 2, 0))
        finally:
            object.__setattr__(settings, "monthly_review_enabled", original_enabled)
            object.__setattr__(settings, "monthly_review_hour", original_hour)
            object.__setattr__(settings, "day_boundary_hour", original_boundary)

        self.assertEqual(created, 1)
        self.assertIsNotNone(db.get_monthly_review("2025-12"))


if __name__ == "__main__":
    unittest.main()
