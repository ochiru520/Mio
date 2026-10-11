import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.config import settings
from app.review_service import generate_review_for_date
from app.weekly_review_service import generate_weekly_review


class DailyReviewSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = settings.db_path
        object.__setattr__(settings, "db_path", Path(self.temp_dir.name) / "review.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    async def test_daily_review_marks_unconfirmed_diary_and_source_counts(self) -> None:
        db.upsert_diary("2026-08-31", "八月末", "完成版本发布核对", confirmed_at=None)
        db.add_diary_material("发布清单", date="2026-08-31", source="checklist")
        with patch(
            "app.review_service.call_chat_completion",
            new=AsyncMock(return_value="# 2026-08-31 AI 回顾\n\n完成核对"),
        ):
            result = await generate_review_for_date("2026-08-31")

        self.assertIn("## 来源与覆盖", result.markdown_content)
        self.assertIn("日记：未确认", result.markdown_content)
        self.assertIn("日记素材：1 条", result.markdown_content)
        self.assertIn("2026-08-31", result.markdown_content)

    async def test_weekly_review_keeps_late_event_and_lists_missing_days(self) -> None:
        late_event = "周日深夜才完成的关键交付。"
        db.upsert_diary("2026-08-16", "周日", ("前序记录。" * 4000) + late_event, confirmed_at=None)
        with patch(
            "app.weekly_review_service.call_chat_completion",
            new=AsyncMock(return_value="- 记录周日关键交付"),
        ) as call:
            result = await generate_weekly_review("2026-08-10")

        self.assertGreater(call.await_count, 2)
        extraction_inputs = [entry.args[0][1]["content"] for entry in call.await_args_list[:-1]]
        self.assertTrue(any(late_event in content for content in extraction_inputs))
        self.assertIn("2026-08-16", result.markdown_content)
        self.assertIn("完全无记录日期", result.markdown_content)
        self.assertIn("2026-08-16 日记未确认", result.markdown_content)


if __name__ == "__main__":
    unittest.main()
