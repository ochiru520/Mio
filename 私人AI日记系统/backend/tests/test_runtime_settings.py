from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import RUNTIME_SETTING_SPECS, load_runtime_settings, save_runtime_settings, settings
from app.main import create_app


class RuntimeSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = {
            "runtime_config_path": settings.runtime_config_path,
            "data_dir": settings.data_dir,
            "db_path": settings.db_path,
            "diary_dir": settings.diary_dir,
            "site_custom_dir": settings.site_custom_dir,
            "photo_dir": settings.photo_dir,
            "agent_attachment_dir": settings.agent_attachment_dir,
            "companion_dir": settings.companion_dir,
        }
        object.__setattr__(settings, "data_dir", root / "数据")
        object.__setattr__(settings, "db_path", root / "数据" / "personal_ai.db")
        object.__setattr__(settings, "diary_dir", root / "数据" / "日记")
        object.__setattr__(settings, "site_custom_dir", root / "网站")
        object.__setattr__(settings, "photo_dir", root / "数据" / "照片")
        object.__setattr__(settings, "agent_attachment_dir", root / "数据" / "Agent附件")
        object.__setattr__(settings, "companion_dir", root / "数据" / "桌宠")
        object.__setattr__(settings, "runtime_config_path", root / "运行设置.json")
        self.original_values = {key: getattr(settings, key) for key in RUNTIME_SETTING_SPECS}

    def tearDown(self) -> None:
        for key, value in self.original_paths.items():
            object.__setattr__(settings, key, value)
        for key, value in self.original_values.items():
            object.__setattr__(settings, key, value)
        self.temp_dir.cleanup()

    def test_save_and_load_runtime_settings(self) -> None:
        saved = save_runtime_settings({
            "chat_context_max_chars": 24000,
            "qq_proactive_enabled": False,
            "qq_allowed_user_ids": "10001, 10002,10001",
            "persona_prompt_path": "资料/人格.md",
            "photo_archive_enabled": False,
            "agent_attachment_max_count": 8,
            "agent_text_attachment_max_chars": 350000,
            "agent_document_attachment_max_bytes": 30 * 1024 * 1024,
            "agent_pdf_max_pages": 180,
            "agent_document_vision_max_pages": 12,
            "screen_reaction_timeout_seconds": 24,
            "screen_history_retention_days": 45,
            "screen_history_max_rows": 36000,
        })

        self.assertEqual(saved["chat_context_max_chars"], 24000)
        self.assertFalse(saved["qq_proactive_enabled"])
        self.assertEqual(saved["qq_allowed_user_ids"], "10001,10002")
        self.assertEqual(settings.qq_allowed_user_ids, ("10001", "10002"))
        self.assertEqual(saved["persona_prompt_path"], str(settings.project_root / "资料" / "人格.md"))
        self.assertFalse(saved["photo_archive_enabled"])
        self.assertEqual(saved["agent_attachment_max_count"], 8)
        self.assertEqual(saved["agent_text_attachment_max_chars"], 350000)
        self.assertEqual(saved["agent_document_attachment_max_bytes"], 30 * 1024 * 1024)
        self.assertEqual(saved["agent_pdf_max_pages"], 180)
        self.assertEqual(saved["agent_document_vision_max_pages"], 12)
        self.assertEqual(saved["screen_reaction_timeout_seconds"], 24)
        self.assertEqual(saved["screen_history_retention_days"], 45)
        self.assertEqual(saved["screen_history_max_rows"], 36000)
        self.assertEqual(load_runtime_settings()["chat_context_max_chars"], 24000)

    def test_invalid_or_unknown_values_are_rejected_without_mutating_settings(self) -> None:
        original = settings.chat_context_max_chars
        with self.assertRaisesRegex(ValueError, "低于允许范围"):
            save_runtime_settings({"chat_context_max_chars": 100})
        self.assertEqual(settings.chat_context_max_chars, original)

        with self.assertRaisesRegex(ValueError, "不支持的运行设置"):
            save_runtime_settings({"openai_api_key": "secret"})
        self.assertFalse(settings.runtime_config_path.exists())

    def test_proactive_idle_range_is_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "最长等待时间"):
            save_runtime_settings({
                "qq_proactive_min_idle_minutes": 180,
                "qq_proactive_max_idle_minutes": 60,
            })

    def test_timezone_image_detail_and_history_relationships_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "有效的 IANA 时区"):
            save_runtime_settings({"timezone": "Mars/Olympus"})

        with self.assertRaisesRegex(ValueError, "只能是 low、auto 或 high"):
            save_runtime_settings({"qq_image_detail": "maximum"})

        with self.assertRaisesRegex(ValueError, "显示历史消息条数"):
            save_runtime_settings({
                "chat_history_limit": 200,
                "chat_raw_history_limit": 100,
            })

        with self.assertRaisesRegex(ValueError, "压缩后保留消息数"):
            save_runtime_settings({
                "chat_history_limit": 60,
                "chat_recent_keep_messages": 80,
                "chat_raw_history_limit": 60,
            })

    def test_new_attachment_and_screen_limits_are_validated(self) -> None:
        for key, value in (
            ("agent_attachment_max_count", 0),
            ("agent_text_attachment_max_chars", 9999),
            ("agent_document_attachment_max_bytes", 1024),
            ("agent_pdf_max_pages", 0),
            ("agent_document_vision_max_pages", 0),
            ("screen_reaction_timeout_seconds", 4),
            ("screen_history_retention_days", 0),
            ("screen_history_max_rows", 999),
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "低于允许范围"):
                    save_runtime_settings({key: value})

    def test_one_invalid_saved_field_does_not_discard_other_fields(self) -> None:
        settings.runtime_config_path.write_text(
            json.dumps({
                "chat_context_max_chars": 26000,
                "memory_context_days": "bad",
                "timezone": "Mars/Olympus",
                "qq_image_detail": "maximum",
            }),
            encoding="utf-8",
        )
        loaded = load_runtime_settings()
        self.assertEqual(loaded["chat_context_max_chars"], 26000)
        self.assertEqual(loaded["memory_context_days"], self.original_values["memory_context_days"])
        self.assertEqual(loaded["timezone"], self.original_values["timezone"])
        self.assertEqual(loaded["qq_image_detail"], self.original_values["qq_image_detail"])

    def test_installed_voice_root_overrides_stale_saved_source_path(self) -> None:
        installed_root = Path(self.temp_dir.name) / "Data" / "音色训练"
        settings.runtime_config_path.write_text(
            json.dumps({"voice_training_dir": r"D:\MioDev\音色训练"}),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"MIO_VOICE_TRAINING_DIR": str(installed_root)}):
            loaded = load_runtime_settings()
        self.assertEqual(Path(loaded["voice_training_dir"]), installed_root.resolve())
        self.assertEqual(settings.voice_training_dir, installed_root.resolve())

    def test_invalid_old_config_does_not_block_saving_another_setting(self) -> None:
        settings.runtime_config_path.write_text(
            json.dumps({"timezone": "Mars/Olympus"}),
            encoding="utf-8",
        )

        saved = save_runtime_settings({"chat_context_max_chars": 28000})

        self.assertEqual(saved["chat_context_max_chars"], 28000)
        self.assertEqual(saved["timezone"], self.original_values["timezone"])

    def test_api_never_exposes_or_accepts_secrets(self) -> None:
        with TestClient(create_app()) as client:
            response = client.get("/api/settings/runtime")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("openai_api_key", response.json()["settings"])

            response = client.patch("/api/settings/runtime", json={"openai_api_key": "secret"})
            self.assertEqual(response.status_code, 400)

    def test_web_search_diagnostic_reports_search_engine(self) -> None:
        from app.web_search_service import WebLookup, WebSource

        lookup = WebLookup(
            query="DeepSeek 最新消息",
            sources=[WebSource(title="结果", url="https://example.com", snippet="摘要")],
            engine="Bing",
            attempts=("Bing：返回 1 条",),
        )
        original_enabled = settings.web_search_enabled
        object.__setattr__(settings, "web_search_enabled", True)
        try:
            with patch("app.routes.settings.perform_web_lookup", new=AsyncMock(return_value=lookup)):
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/settings/web-search/test",
                        json={"query": "帮我查一下 DeepSeek 最新消息"},
                    )
        finally:
            object.__setattr__(settings, "web_search_enabled", original_enabled)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["engine"], "Bing")
        self.assertEqual(len(response.json()["sources"]), 1)

    def test_agent_bootstrap_returns_saved_attachment_limits(self) -> None:
        save_runtime_settings({
            "agent_attachment_max_count": 7,
            "agent_text_attachment_max_chars": 180000,
            "agent_document_attachment_max_bytes": 25 * 1024 * 1024,
            "qq_image_max_bytes": 6 * 1024 * 1024,
        })

        with (
            patch("app.routes.agent.get_model_profile", return_value="test-model"),
            patch("app.routes.agent.list_model_profiles", return_value=["test-model"]),
            patch("app.routes.agent.public_model_profile", return_value={"id": "test-model"}),
            patch("app.routes.agent._primary_conversation_id", return_value="desktop_test"),
            patch("app.routes.agent._conversation_list", return_value=[]),
            patch("app.routes.agent._day_dashboard_payload", return_value={}),
            patch("app.routes.agent._context_usage", return_value={}),
            patch("app.routes.agent.db.get_recent_messages", return_value=[]),
            patch("app.routes.agent._qq_status", new=AsyncMock(return_value={})),
        ):
            with TestClient(create_app()) as client:
                response = client.get("/api/agent/bootstrap")

        self.assertEqual(response.status_code, 200)
        limits = response.json()["attachment_limits"]
        self.assertEqual(limits["max_count"], 7)
        self.assertEqual(limits["image_max_bytes"], 6 * 1024 * 1024)
        self.assertEqual(limits["document_max_bytes"], 25 * 1024 * 1024)
        self.assertEqual(limits["text_max_chars"], 180000)
        self.assertEqual(limits["text_max_bytes"], 720000)

    def test_profile_api_updates_only_safe_personality_fields(self) -> None:
        original_profile_path = settings.mio_profile_path
        original_avatar_path = settings.mio_avatar_path
        original_user_avatar_path = settings.user_avatar_path
        original_chat_background_path = settings.chat_background_path
        object.__setattr__(settings, "mio_profile_path", Path(self.temp_dir.name) / "澪属性.json")
        object.__setattr__(settings, "mio_avatar_path", Path(self.temp_dir.name) / "澪头像.png")
        object.__setattr__(settings, "user_avatar_path", Path(self.temp_dir.name) / "用户头像.png")
        object.__setattr__(settings, "chat_background_path", Path(self.temp_dir.name) / "对话背景.jpg")
        try:
            with TestClient(create_app()) as client:
                response = client.get("/api/settings/profile")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["avatar"]["custom"])
                profile = response.json()["profile"]
                profile["identity"]["name"] = "测试澪"
                profile["behavior"]["new_rule"] = "先观察，再自然回应"
                response = client.patch("/api/settings/profile", json={"profile": profile})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["profile"]["identity"]["name"], "测试澪")
                self.assertEqual(response.json()["profile"]["behavior"]["new_rule"], "先观察，再自然回应")

                profile["preferences"]["custom_notes"] = ["请保存 api key"]
                response = client.patch("/api/settings/profile", json={"profile": profile})
                self.assertEqual(response.status_code, 400)

                profile["preferences"]["custom_notes"] = []
                profile["identity"]["name"] = "   "
                response = client.patch("/api/settings/profile", json={"profile": profile})
                self.assertEqual(response.status_code, 400)

                avatar_data_url = (
                    "data:image/png;base64,"
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
                response = client.post("/api/settings/avatar", json={"data_url": avatar_data_url})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(settings.mio_avatar_path.is_file())
                response = client.get("/api/settings/avatar")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "image/png")
                response = client.delete("/api/settings/avatar")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(settings.mio_avatar_path.exists())

                response = client.post("/api/settings/user-avatar", json={"data_url": avatar_data_url})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(settings.user_avatar_path.is_file())
                response = client.get("/api/settings/user-avatar")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "image/png")
                response = client.delete("/api/settings/user-avatar")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(settings.user_avatar_path.exists())

                response = client.post("/api/settings/chat-background", json={"data_url": avatar_data_url})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(settings.chat_background_path.is_file())
                response = client.get("/api/settings/chat-background")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "image/jpeg")
                response = client.delete("/api/settings/chat-background")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(settings.chat_background_path.exists())
        finally:
            object.__setattr__(settings, "mio_profile_path", original_profile_path)
            object.__setattr__(settings, "mio_avatar_path", original_avatar_path)
            object.__setattr__(settings, "user_avatar_path", original_user_avatar_path)
            object.__setattr__(settings, "chat_background_path", original_chat_background_path)


if __name__ == "__main__":
    unittest.main()
