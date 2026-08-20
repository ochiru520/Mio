from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import companion_service, db, environment_check_service, onboarding_service, privacy_service
from app.config import settings
from app.main import create_app


class OnboardingPrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name) / "数据"
        self.originals = {
            "data_dir": settings.data_dir,
            "db_path": settings.db_path,
            "diary_dir": settings.diary_dir,
            "mio_profile_path": settings.mio_profile_path,
            "model_profiles_path": settings.model_profiles_path,
            "runtime_config_path": settings.runtime_config_path,
            "agent_attachment_dir": settings.agent_attachment_dir,
            "companion_dir": settings.companion_dir,
            "companion_config_path": settings.companion_config_path,
            "companion_sprite_dir": settings.companion_sprite_dir,
        }
        object.__setattr__(settings, "data_dir", root)
        object.__setattr__(settings, "db_path", root / "personal_ai.db")
        object.__setattr__(settings, "diary_dir", root / "日记")
        object.__setattr__(settings, "mio_profile_path", root / "澪属性.json")
        object.__setattr__(settings, "model_profiles_path", root / "模型供应商.json")
        object.__setattr__(settings, "runtime_config_path", root / "运行设置.json")
        object.__setattr__(settings, "agent_attachment_dir", root / "Agent附件")
        object.__setattr__(settings, "companion_dir", root / "桌宠")
        object.__setattr__(settings, "companion_config_path", root / "桌宠" / "设置.json")
        object.__setattr__(settings, "companion_sprite_dir", root / "桌宠" / "动作")
        settings.ensure_directories()
        db.init_db()

    def tearDown(self) -> None:
        for key, value in self.originals.items():
            object.__setattr__(settings, key, value)
        self.temp_dir.cleanup()

    def test_empty_data_requires_onboarding_and_completion_saves_choices(self) -> None:
        status = onboarding_service.onboarding_status()
        self.assertFalse(status["completed"])
        # 模型供应商可以跳过：未验证模型时也能完成向导（verified=False）。
        skipped = onboarding_service.complete_onboarding({"assistant_name": "小澪", "user_address": "小落"})
        self.assertTrue(skipped["completed"])
        self.assertFalse(skipped["model_verification"]["verified"])

        profile = SimpleNamespace(
            id="verified-model",
            provider_id="provider-1",
            provider_name="测试供应商",
            model="test-model",
        )
        with patch.object(onboarding_service, "get_model_profile", return_value=profile):
            onboarding_service.record_model_verification(profile.id)
            completed = onboarding_service.complete_onboarding(
                {
                    "assistant_name": "小澪",
                    "user_address": "小落",
                    "web_search_enabled": True,
                    "proactive_enabled": True,
                    "daily_diary_auto_enabled": True,
                    "qq_enabled": False,
                }
            )

        self.assertTrue(completed["completed"])
        self.assertEqual(onboarding_service.onboarding_status()["mode"], "new_user_completed")
        self.assertIn("小澪", settings.mio_profile_path.read_text(encoding="utf-8"))
        self.assertIn("小落", settings.mio_profile_path.read_text(encoding="utf-8"))

    def test_first_launch_disables_screen_and_audio_without_overwriting_existing_config(self) -> None:
        onboarding_service.prepare_first_launch_defaults()
        first_launch = companion_service.load_config()
        self.assertFalse(first_launch["screen_ai_enabled"])
        self.assertFalse(first_launch["screen_audio_enabled"])
        self.assertFalse(first_launch["speak_screen_observations"])

        companion_service.save_config({"screen_ai_enabled": True, "screen_audio_enabled": True})
        onboarding_service.prepare_first_launch_defaults()
        existing = companion_service.load_config()
        self.assertTrue(existing["screen_ai_enabled"])
        self.assertTrue(existing["screen_audio_enabled"])

    def test_existing_user_is_marked_compatible_without_wizard(self) -> None:
        db.save_message("user", "旧消息", conversation_id="default")
        status = onboarding_service.onboarding_status()

        self.assertTrue(status["completed"])
        self.assertEqual(status["mode"], "legacy_existing_user")
        self.assertIn("聊天记录", status["existing_data"])

    def test_privacy_pause_and_resume_restores_previous_choices(self) -> None:
        from app.config import save_runtime_settings

        save_runtime_settings(
            {
                "web_search_enabled": True,
                "qq_bot_enabled": True,
                "qq_proactive_enabled": True,
                "daily_diary_auto_enabled": True,
                "qq_image_send_to_model": True,
            }
        )
        companion_service.save_config(
            {"screen_ai_enabled": True, "screen_audio_enabled": True, "speak_proactive": True}
        )
        with (
            patch.object(companion_service.window_observer, "stop") as stop_observer,
            patch("app.privacy_service.screen_observation_service.end_session") as end_session,
            patch("app.privacy_service.system_audio_service.stop") as stop_audio,
            patch(
                "app.routes.onebot.disconnect_all_connections",
                new=AsyncMock(return_value=2),
            ) as disconnect_qq,
        ):
            paused = asyncio.run(privacy_service.pause_sensitive_capabilities())
        self.assertTrue(paused["paused"])
        self.assertTrue(stop_observer.called)
        self.assertTrue(end_session.called)
        self.assertTrue(stop_audio.called)
        self.assertEqual(paused["qq_connections_disconnected"], 2)
        self.assertFalse(settings.web_search_enabled)
        self.assertFalse(settings.qq_bot_enabled)
        self.assertFalse(companion_service.load_config()["screen_audio_enabled"])

        resumed = privacy_service.resume_sensitive_capabilities()
        self.assertFalse(resumed["paused"])
        self.assertTrue(settings.web_search_enabled)
        self.assertTrue(settings.qq_bot_enabled)
        self.assertTrue(settings.qq_proactive_enabled)
        self.assertTrue(companion_service.load_config()["screen_audio_enabled"])

    def test_privacy_pause_reports_partial_failure_and_retries_without_losing_snapshot(self) -> None:
        from app.config import save_runtime_settings

        save_runtime_settings({"web_search_enabled": True, "qq_bot_enabled": True})
        companion_service.save_config({"screen_ai_enabled": True, "screen_audio_enabled": True})
        with (
            patch.object(companion_service.window_observer, "stop", side_effect=OSError("capture busy")),
            patch("app.privacy_service.screen_observation_service.end_session") as end_session,
            patch("app.privacy_service.system_audio_service.stop") as stop_audio,
            patch(
                "app.routes.onebot.disconnect_all_connections",
                new=AsyncMock(side_effect=RuntimeError("qq close failed")),
            ) as disconnect_qq,
        ):
            with self.assertRaisesRegex(ValueError, "未能确认全部敏感能力"):
                asyncio.run(privacy_service.pause_sensitive_capabilities())

        failed = privacy_service.privacy_status()
        self.assertFalse(failed["paused"])
        self.assertEqual(failed["transition"], "pause_incomplete")
        self.assertEqual(
            {item["action"] for item in failed["failed_actions"]},
            {"screen_capture", "qq_connections"},
        )
        self.assertFalse(settings.web_search_enabled)
        self.assertTrue(end_session.called)
        self.assertTrue(stop_audio.called)
        self.assertTrue(disconnect_qq.await_count)

        with (
            patch.object(companion_service.window_observer, "stop"),
            patch("app.privacy_service.screen_observation_service.end_session"),
            patch("app.privacy_service.system_audio_service.stop"),
            patch("app.routes.onebot.disconnect_all_connections", new=AsyncMock(return_value=0)),
        ):
            retried = asyncio.run(privacy_service.pause_sensitive_capabilities())
        self.assertTrue(retried["paused"])

        resumed = privacy_service.resume_sensitive_capabilities()
        self.assertFalse(resumed["paused"])
        self.assertTrue(settings.web_search_enabled)
        self.assertTrue(settings.qq_bot_enabled)
        self.assertTrue(companion_service.load_config()["screen_ai_enabled"])

    def test_privacy_resume_failure_keeps_pause_state_and_snapshot(self) -> None:
        from app.config import save_runtime_settings

        save_runtime_settings({"web_search_enabled": True})
        companion_service.save_config({"screen_ai_enabled": True})
        with (
            patch.object(companion_service.window_observer, "stop"),
            patch("app.privacy_service.screen_observation_service.end_session"),
            patch("app.privacy_service.system_audio_service.stop"),
            patch("app.routes.onebot.disconnect_all_connections", new=AsyncMock(return_value=0)),
        ):
            asyncio.run(privacy_service.pause_sensitive_capabilities())

        real_save_runtime_settings = privacy_service.save_runtime_settings
        calls = 0

        def fail_first_restore(values):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("settings locked")
            return real_save_runtime_settings(values)

        with patch("app.privacy_service.save_runtime_settings", side_effect=fail_first_restore):
            with self.assertRaisesRegex(ValueError, "敏感能力仍按暂停处理"):
                privacy_service.resume_sensitive_capabilities()

        failed = privacy_service.privacy_status()
        self.assertTrue(failed["paused"])
        self.assertEqual(failed["transition"], "resume_incomplete")
        self.assertFalse(settings.web_search_enabled)

        resumed = privacy_service.resume_sensitive_capabilities()
        self.assertFalse(resumed["paused"])
        self.assertTrue(settings.web_search_enabled)

    def test_privacy_resume_state_write_failure_reapplies_pause(self) -> None:
        from app.config import save_runtime_settings

        save_runtime_settings({"web_search_enabled": True})
        companion_service.save_config({"screen_ai_enabled": True})
        with (
            patch.object(companion_service.window_observer, "stop"),
            patch("app.privacy_service.screen_observation_service.end_session"),
            patch("app.privacy_service.system_audio_service.stop"),
            patch("app.routes.onebot.disconnect_all_connections", new=AsyncMock(return_value=0)),
        ):
            asyncio.run(privacy_service.pause_sensitive_capabilities())

        real_save_state = privacy_service._save_state
        calls = 0

        def fail_success_state_once(payload):
            nonlocal calls
            calls += 1
            if calls == 1 and payload.get("paused") is False:
                raise OSError("state file locked")
            return real_save_state(payload)

        with patch("app.privacy_service._save_state", side_effect=fail_success_state_once):
            with self.assertRaisesRegex(ValueError, "已重新暂停"):
                privacy_service.resume_sensitive_capabilities()

        failed = privacy_service.privacy_status()
        self.assertTrue(failed["paused"])
        self.assertEqual(failed["transition"], "resume_incomplete")
        self.assertFalse(settings.web_search_enabled)
        self.assertFalse(companion_service.load_config()["screen_ai_enabled"])

    def test_onboarding_and_privacy_routes(self) -> None:
        with TestClient(create_app()) as client:
            status = client.get("/api/onboarding/status")
            self.assertEqual(status.status_code, 200)
            self.assertFalse(status.json()["completed"])
            environment = client.get("/api/onboarding/environment")
            self.assertEqual(environment.status_code, 200)
            self.assertIn("core_ready", environment.json())
            self.assertTrue(environment.json()["required"])
            self.assertTrue(environment.json()["optional"])
            blocked = client.post(
                "/api/onboarding/complete",
                json={"assistant_name": "澪", "user_address": "你"},
            )
            # 未配置模型也允许完成向导（可以跳过，之后在设置里配置）
            self.assertEqual(blocked.status_code, 200)
            self.assertFalse(blocked.json()["model_verification"]["verified"])
            profile = SimpleNamespace(
                id="verified-model",
                provider_id="provider-1",
                provider_name="测试供应商",
                model="test-model",
            )
            with patch.object(onboarding_service, "get_model_profile", return_value=profile):
                onboarding_service.record_model_verification(profile.id)
                completed = client.post(
                    "/api/onboarding/complete",
                    json={"assistant_name": "澪", "user_address": "你"},
                )
            self.assertEqual(completed.status_code, 200)
            privacy = client.get("/api/privacy/status")
            self.assertEqual(privacy.status_code, 200)
            self.assertIn("capabilities", privacy.json())

    def test_bootstrap_allows_first_launch_without_a_model(self) -> None:
        with (
            patch("app.routes.agent.list_model_profiles", return_value=[]),
            patch("app.routes.agent.get_model_profile", side_effect=ValueError("模型未配置")),
            TestClient(create_app()) as client,
        ):
            response = client.get("/api/agent/bootstrap")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["onboarding"]["completed"])
        self.assertIsNone(response.json()["model"])
        self.assertEqual(response.json()["models"], [])

    def test_environment_check_does_not_require_optional_models(self) -> None:
        with (
            patch.object(environment_check_service, "_webview2_version", return_value="123.0"),
            patch.object(environment_check_service, "_gpu_info", return_value=[]),
            patch.object(environment_check_service, "_memory_bytes", return_value=8 * 1024**3),
            patch.object(environment_check_service, "list_model_profiles", return_value=[]),
        ):
            result = environment_check_service.environment_status()

        self.assertTrue(result["core_ready"])
        cloud_model = next(item for item in result["optional"] if item["id"] == "cloud_model")
        self.assertEqual(cloud_model["status"], "unconfigured")
        self.assertEqual(result["system"]["memory_label"], "8.0 GB")


if __name__ == "__main__":
    unittest.main()
