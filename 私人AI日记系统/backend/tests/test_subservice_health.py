from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from app import companion_service, local_vision_service, subservice_health
from app.routes import companion


class PassiveServiceHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        subservice_health.reset_recovery_state_for_tests()

    def tearDown(self) -> None:
        subservice_health.reset_recovery_state_for_tests()

    def test_voice_and_local_vision_passive_status_never_probe_network(self) -> None:
        with (
            patch.object(
                companion_service,
                "_probe_gpt_sovits",
                side_effect=AssertionError("TTS network probe must not run"),
            ),
            patch.object(
                local_vision_service,
                "_request_json",
                side_effect=AssertionError("vision network probe must not run"),
            ),
        ):
            voice = companion_service.voice_runtime_health()
            vision = local_vision_service.passive_status()

        self.assertIn("probe_stale", voice)
        self.assertIn("probe_stale", vision)

    def test_subservice_snapshot_reports_all_isolated_runtime_boundaries(self) -> None:
        with (
            patch.object(
                companion,
                "call_runtime_status",
                return_value={
                    "active": True,
                    "started_observer": True,
                    "previous_audio_model": "base",
                    "asr": {
                        "running": True,
                        "ready": False,
                        "phase": "loading_model",
                        "last_error": "",
                        "model": "large-v3-turbo",
                        "quality_requests": {},
                    },
                },
            ),
            patch.object(
                companion_service,
                "voice_runtime_health",
                return_value={
                    "managed_running": True,
                    "observed_running": True,
                    "last_error": "",
                    "warmup_state": "ready",
                    "warmup_error": "",
                },
            ),
            patch.object(
                local_vision_service,
                "passive_status",
                return_value={
                    "runtime_installed": True,
                    "model_installed": True,
                    "owned_server": True,
                    "observed_running": True,
                    "pulling": False,
                    "last_error": "",
                },
            ),
            patch(
                "app.screen_observation_service.runtime_health_status",
                return_value={
                    "capture": {
                        "running": True,
                        "preview_available": True,
                        "last_frame_age_seconds": 0.2,
                        "error": "",
                        "capture_backend_error": "",
                        "mode": "screen",
                        "capture_backend": "mss",
                        "frame_id": 3,
                    },
                    "enabled": True,
                    "analysis_in_progress": False,
                    "last_analyzed_at": "",
                    "last_error": "",
                    "last_model": "",
                    "last_pipeline_timings": {},
                },
            ),
            patch(
                "app.routes.onebot.runtime_health_status",
                return_value={
                    "enabled": True,
                    "websocket_connections": 0,
                    "active_queue_count": 0,
                    "active_pending_ack_count": 0,
                    "delivery": {},
                },
            ),
        ):
            result = subservice_health.snapshot()

        self.assertTrue(result["passive"])
        self.assertEqual(
            set(result["services"]),
            {
                "phone",
                "asr_system_audio",
                "tts",
                "screen_capture",
                "screen_analysis",
                "local_vision",
                "qq",
            },
        )
        self.assertEqual(result["services"]["phone"]["state"], "degraded")
        self.assertEqual(result["services"]["asr_system_audio"]["state"], "starting")
        self.assertEqual(result["services"]["qq"]["state"], "offline")

    def test_disabled_screen_analysis_is_not_reported_as_degraded(self) -> None:
        with (
            patch.object(
                companion,
                "call_runtime_status",
                return_value={
                    "active": False,
                    "started_observer": False,
                    "asr": {
                        "running": False,
                        "ready": False,
                        "desired_running": False,
                        "phase": "stopped",
                        "last_error": "",
                    },
                },
            ),
            patch.object(
                companion_service,
                "voice_runtime_health",
                return_value={
                    "managed_running": False,
                    "observed_running": False,
                    "desired_running": False,
                    "last_error": "",
                    "warmup_state": "idle",
                    "warmup_error": "",
                },
            ),
            patch.object(
                local_vision_service,
                "passive_status",
                return_value={
                    "model_installed": False,
                    "owned_server": False,
                    "observed_running": False,
                    "desired_running": False,
                    "pulling": False,
                    "last_error": "",
                },
            ),
            patch(
                "app.screen_observation_service.runtime_health_status",
                return_value={
                    "enabled": False,
                    "capture": {
                        "running": False,
                        "desired_running": False,
                        "preview_available": False,
                        "error": "",
                    },
                    "analysis_in_progress": False,
                    "last_error": "屏幕 AI 已关闭；仅保留画面捕获。",
                },
            ),
            patch(
                "app.routes.onebot.runtime_health_status",
                return_value={"enabled": False, "websocket_connections": 0},
            ),
        ):
            result = subservice_health.snapshot()

        analysis = result["services"]["screen_analysis"]
        self.assertEqual(analysis["state"], "disabled")
        self.assertFalse(analysis["enabled"])
        self.assertFalse(analysis["ready"])
        self.assertEqual(analysis["last_error"], "")
        self.assertIn("已关闭", analysis["details"]["status_message"])
        self.assertNotIn("screen_analysis", result["degraded_services"])

    def test_failed_services_recover_only_their_own_runtime_for_five_rounds(self) -> None:
        recovery_cases = {
            "asr_system_audio": "audio",
            "tts": "tts",
            "local_vision": "vision",
            "screen_capture": "capture",
        }
        for service_id, expected in recovery_cases.items():
            for round_index in range(5):
                with self.subTest(service=service_id, round=round_index + 1):
                    subservice_health.reset_recovery_state_for_tests()
                    services = {
                        name: {
                            "service_id": name,
                            "state": "failed" if name == service_id else "ready",
                            "enabled": name == service_id,
                            "details": {
                                "mode": "screen",
                                "screen_scope": "primary",
                                "interval_ms": 1000,
                            },
                        }
                        for name in recovery_cases
                    }
                    with (
                        patch(
                            "app.companion_service.load_config",
                            return_value={"screen_audio_enabled": True},
                        ),
                        patch("app.system_audio_service.start") as recover_audio,
                        patch("app.companion_service.restart_voice_service") as recover_tts,
                        patch("app.local_vision_service.restart_server") as recover_vision,
                        patch.object(
                            companion.companion_service.window_observer,
                            "stop",
                        ) as capture_stop,
                        patch.object(
                            companion.companion_service.window_observer,
                            "select_screen",
                        ) as capture_select,
                        patch.object(
                            companion.companion_service.window_observer,
                            "start",
                        ) as capture_start,
                    ):
                        result = subservice_health.recover_failed({"services": services})

                    called = {
                        "audio": recover_audio.call_count,
                        "tts": recover_tts.call_count,
                        "vision": recover_vision.call_count,
                        "capture": capture_stop.call_count + capture_select.call_count + capture_start.call_count,
                    }
                    self.assertEqual(result["recovered"], [service_id])
                    self.assertGreater(called[expected], 0)
                    self.assertTrue(all(count == 0 for name, count in called.items() if name != expected))

    def test_subservice_recovery_circuit_opens_after_two_attempts(self) -> None:
        health = {
            "services": {
                "tts": {
                    "service_id": "tts",
                    "state": "failed",
                    "enabled": True,
                    "details": {},
                }
            }
        }
        with patch("app.companion_service.restart_voice_service") as restart:
            first = subservice_health.recover_failed(health)
            second = subservice_health.recover_failed(health)
            third = subservice_health.recover_failed(health)

        self.assertEqual(restart.call_count, 2)
        self.assertTrue(first["attempted"])
        self.assertTrue(second["attempted"])
        self.assertEqual(third["fused"], ["tts"])

    async def test_companion_status_builds_probe_heavy_payload_off_event_loop(self) -> None:
        event_loop_thread = threading.get_ident()

        def payload() -> dict[str, int]:
            return {"worker_thread": threading.get_ident()}

        with patch.object(companion, "_status_payload", side_effect=payload):
            result = await companion.companion_status()

        self.assertNotEqual(result["worker_thread"], event_loop_thread)


if __name__ == "__main__":
    unittest.main()
