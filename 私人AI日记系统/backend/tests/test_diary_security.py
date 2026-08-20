from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import create_app
from app.markdown_rendering import render_safe_markdown
from app.routes.diary import api_delete_diary


class DiarySecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name) / "data"
        self.originals = {
            "data_dir": settings.data_dir,
            "db_path": settings.db_path,
            "diary_dir": settings.diary_dir,
        }
        object.__setattr__(settings, "data_dir", root)
        object.__setattr__(settings, "db_path", root / "personal_ai.db")
        object.__setattr__(settings, "diary_dir", root / "日记")
        settings.ensure_directories()
        db.init_db()

    def tearDown(self) -> None:
        for key, value in self.originals.items():
            object.__setattr__(settings, key, value)
        self.temp_dir.cleanup()

    def test_markdown_renderer_keeps_formatting_and_escapes_active_html(self) -> None:
        rendered = render_safe_markdown(
            "# 标题\n\n**正文** <script>fetch('/api/privacy/resume')</script> "
            "<img src=x onerror=fetch('/api/privacy/resume')>\n\n"
            "[危险链接](javascript:alert(1)) [安全链接](https://example.test)"
        )

        self.assertIn("<h1>标题</h1>", rendered)
        self.assertIn("<strong>正文</strong>", rendered)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertIn('href="https://example.test"', rendered)

    def test_diary_detail_route_never_serves_active_markdown_html(self) -> None:
        target_date = "2026-08-14"
        payload = "# 安全标题\n\n<img src=x onerror=fetch('/api/privacy/resume')>"
        db.upsert_diary(target_date, "安全标题", payload, "", "unknown")
        with TestClient(create_app()) as client:
            response = client.get(f"/diaries/{target_date}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("<h1>安全标题</h1>", response.text)
        self.assertNotIn("<img src=x", response.text)

    def test_invalid_diary_dates_and_months_are_rejected(self) -> None:
        db.upsert_diary("2026-08-14", "日记", "# 日记", "", "unknown")
        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/diaries/not-a-date").status_code, 400)
            self.assertEqual(client.get("/diaries/export/month/2026/13.zip").status_code, 400)
            self.assertEqual(client.get("/reviews/not-a-date").status_code, 400)
            self.assertEqual(client.get("/weekly/2026-02-31").status_code, 400)

    def test_export_stops_on_corrupt_database_date(self) -> None:
        db.upsert_diary("../outside", "损坏日期", "不应进入 ZIP", "", "unknown")
        with TestClient(create_app()) as client:
            response = client.get("/diaries/export/all.zip")

        self.assertEqual(response.status_code, 500)
        self.assertIn("无效日期", response.json()["detail"])

    def test_export_uses_only_normalized_date_entries(self) -> None:
        db.upsert_diary("2026-08-14", "日记", "# 正常", "", "unknown")
        with TestClient(create_app()) as client:
            response = client.get("/diaries/export/all.zip")

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), ["2026-08-14.md"])

    def test_diary_delete_does_not_remove_database_row_when_file_delete_fails(self) -> None:
        target_date = "2026-08-14"
        db.upsert_diary(target_date, "日记", "# 正常", "", "unknown")
        diary_path = settings.diary_dir / f"{target_date}.md"
        diary_path.write_text("# 正常", encoding="utf-8")

        with patch("pathlib.Path.unlink", side_effect=OSError("file locked")):
            with self.assertRaisesRegex(Exception, "日记文件正在使用"):
                import asyncio

                asyncio.run(api_delete_diary(target_date))

        self.assertIsNotNone(db.get_diary(target_date))
        self.assertTrue(diary_path.exists())

    def test_diary_delete_restores_file_when_database_delete_fails(self) -> None:
        target_date = "2026-08-14"
        db.upsert_diary(target_date, "日记", "# 正常", "", "unknown")
        diary_path = settings.diary_dir / f"{target_date}.md"
        diary_path.write_text("# 正常", encoding="utf-8")

        with patch("app.routes.diary.db.delete_diary", return_value=False):
            with self.assertRaisesRegex(Exception, "数据已发生变化"):
                import asyncio

                asyncio.run(api_delete_diary(target_date))

        self.assertIsNotNone(db.get_diary(target_date))
        self.assertEqual(diary_path.read_text(encoding="utf-8"), "# 正常")

    def test_diary_delete_restores_file_when_database_raises(self) -> None:
        target_date = "2026-08-14"
        db.upsert_diary(target_date, "日记", "# 正常", "", "unknown")
        diary_path = settings.diary_dir / f"{target_date}.md"
        diary_path.write_text("# 正常", encoding="utf-8")

        with patch("app.routes.diary.db.delete_diary", side_effect=OSError("database locked")):
            with self.assertRaisesRegex(Exception, "文件已恢复"):
                import asyncio

                asyncio.run(api_delete_diary(target_date))

        self.assertIsNotNone(db.get_diary(target_date))
        self.assertEqual(diary_path.read_text(encoding="utf-8"), "# 正常")


if __name__ == "__main__":
    unittest.main()
