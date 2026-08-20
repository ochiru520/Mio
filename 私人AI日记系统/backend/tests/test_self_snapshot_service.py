from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import environment_check_service, route_observation_service, self_snapshot_service
from app.model_registry import ModelProfile
from app.routes import self_state as self_state_routes


def _profile(*, configured: bool = True) -> ModelProfile:
    return ModelProfile(
        id="test-provider:test-model",
        provider_name="测试供应商",
        display_name="测试模型",
        model="test-model",
        base_urls=("https://model.example/v1",),
        api_key="sk-private-value" if configured else "",
        provider_id="test-provider",
        supports_vision=True,
        input_price_cny_per_million=1.0,
        output_price_cny_per_million=2.0,
        pricing_source="test",
    )


def _services(error: str = "") -> dict[str, object]:
    state = "failed" if error else "ready"
    return {
        "services": {
            "tts": {
                "state": state,
                "enabled": True,
                "running": not error,
                "ready": not error,
                "last_error": error,
                "recovery_scope": "gpt_sovits_process",
            },
            "phone": {
                "state": "stopped",
                "enabled": False,
                "running": False,
                "ready": False,
                "last_error": "",
                "recovery_scope": "phone_session",
            },
        }
    }


def _privacy() -> dict[str, object]:
    return {
        "capabilities": [
            {"id": "qq", "enabled": False, "destination": "QQ"},
            {"id": "screen_observation", "enabled": False, "destination": "本机"},
            {"id": "system_audio", "enabled": False, "destination": "本机"},
            {"id": "web_search", "enabled": False, "destination": "搜索服务"},
            {"id": "proactive", "enabled": False, "destination": "本机"},
            {"id": "automatic_records", "enabled": False, "destination": "本机"},
        ],
        "local_data": {
            "database": "D:/private/personal.db",
            "diaries": "D:/private/diaries",
        },
    }


class SelfSnapshotServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self_snapshot_service.reset_for_tests()
        route_observation_service.reset_for_tests()

    def tearDown(self) -> None:
        self_snapshot_service.reset_for_tests()
        route_observation_service.reset_for_tests()

    def test_active_view_scope_is_lazy_and_does_not_collect_other_sources(self) -> None:
        self_snapshot_service.report_active_view("chat")
        forbidden = [
            patch.object(self_snapshot_service.privacy_service, "privacy_status", side_effect=AssertionError),
            patch.object(self_snapshot_service.subservice_health, "snapshot", side_effect=AssertionError),
            patch.object(self_snapshot_service, "list_model_profiles", side_effect=AssertionError),
            patch.object(self_snapshot_service, "runtime_identity", side_effect=AssertionError),
            patch.object(self_snapshot_service.companion_service, "load_config", side_effect=AssertionError),
            patch.object(self_snapshot_service.companion_service, "pet_running", side_effect=AssertionError),
            patch.object(
                self_snapshot_service.environment_check_service,
                "passive_environment_status",
                side_effect=AssertionError,
            ),
        ]
        for mocked in forbidden:
            mocked.start()
        try:
            snapshot = self_snapshot_service.build_self_snapshot(["active_view"])
        finally:
            for mocked in reversed(forbidden):
                mocked.stop()

        self.assertEqual(snapshot["included_scopes"], ["active_view"])
        self.assertEqual(snapshot["active_view"]["view_id"], "chat")
        self.assertNotIn("models", snapshot)
        self.assertNotIn("service_health", snapshot)

    def test_active_view_validation_and_freshness(self) -> None:
        with self.assertRaisesRegex(ValueError, "页面"):
            self_snapshot_service.report_active_view("secret-page")
        with self.assertRaisesRegex(ValueError, "设置分区"):
            self_snapshot_service.report_active_view("settings", section_id="secret")

        with patch.object(self_snapshot_service.time, "monotonic", return_value=100.0):
            reported = self_snapshot_service.report_active_view("settings", section_id="models")
        self.assertFalse(reported["stale"])
        self.assertEqual(reported["section_label"], "模型与 API")

        with patch.object(self_snapshot_service.time, "monotonic", return_value=401.0):
            stale = self_snapshot_service.get_active_view()
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["age_seconds"], 301.0)

    def test_snapshot_redacts_secrets_urls_paths_and_private_locations(self) -> None:
        secret = "sk-abcdefghijk12345"
        route_observation_service.record_completed_route(
            source="desktop",
            mode="automatic",
            selected_model_id="test-provider:test-model",
            selected_reasoning_level="high",
            connection_route="https://private.example/v1",
            reason=f"api_key={secret} at D:\\private\\route.txt",
        )
        snapshot = self_snapshot_service.build_self_snapshot(
            ["overview", "capabilities", "service_health", "models", "last_route", "environment"],
            privacy=_privacy(),
            services=_services(
                f"Bearer {secret}; failed at D:\\private\\voice.log and https://private.example/error"
            ),
            profiles=[_profile()],
            identity={
                "status": "warning",
                "build_id": "test-build",
                "app_version": "0.6-test",
                "source_mode": True,
                "warnings": [f"database D:\\private\\personal.db token={secret}"],
            },
            companion_config={"voice_enabled": True},
            pet_running=False,
            environment={"mode": "passive_read_only", "core_ready": True},
        )
        encoded = json.dumps(snapshot, ensure_ascii=False)

        self.assertNotIn(secret, encoded)
        self.assertNotIn("D:\\private", encoded)
        self.assertNotIn("private.example", encoded)
        self.assertNotIn("personal.db", encoded)
        self.assertNotIn("sk-private-value", encoded)
        self.assertIn("[secret]", encoded)
        self.assertIn("[path]", encoded)
        self.assertIn("[url]", encoded)
        self.assertFalse(snapshot["privacy"]["contains_private_content"])

    def test_capabilities_have_required_contract_and_use_real_pet_state(self) -> None:
        snapshot = self_snapshot_service.build_self_snapshot(
            ["capabilities"],
            privacy=_privacy(),
            services=_services(),
            profiles=[_profile()],
            companion_config={"voice_enabled": True},
            pet_running=False,
        )
        capabilities = snapshot["capabilities"]
        required = {
            "capability_id",
            "description",
            "channels",
            "enabled",
            "health",
            "failure_reason",
            "risk_level",
            "cost",
            "privacy_destination",
            "tool_ids",
        }
        self.assertGreaterEqual(len(capabilities), 15)
        self.assertTrue(all(required <= set(item) for item in capabilities))
        pet = next(item for item in capabilities if item["capability_id"] == "desktop_pet")
        self.assertEqual(pet["health"], "idle")
        self.assertIn("渲染器", pet["failure_reason"])
        tools = next(item for item in capabilities if item["capability_id"] == "self_awareness")
        self.assertEqual(len(tools["tool_ids"]), 5)

    def test_service_health_exposes_only_sanitized_failure(self) -> None:
        health = self_snapshot_service._public_service_health(
            _services("password=private-pass at /home/user/private.log")
        )
        encoded = json.dumps(health, ensure_ascii=False)
        self.assertEqual(health["overall"], "degraded")
        self.assertNotIn("private-pass", encoded)
        self.assertNotIn("/home/user", encoded)
        self.assertNotIn("last_error", encoded)

    def test_scope_selection_is_task_specific(self) -> None:
        self.assertEqual(
            set(self_snapshot_service.scopes_for_message("你现在在哪个页面")),
            {"active_view", "capabilities"},
        )
        self.assertEqual(
            set(self_snapshot_service.scopes_for_message("你现在为什么选择这个模型")),
            {"budget", "last_route", "models"},
        )
        self.assertEqual(self_snapshot_service.scopes_for_message("今天天气怎么样"), ())

    def test_context_limit_always_returns_valid_json(self) -> None:
        oversized = {
            "schema_version": 1,
            "generated_at": "2026-08-15T10:00:00+08:00",
            "read_only": True,
            "included_scopes": ["capabilities", "models", "tools"],
            "capabilities": [
                {
                    "capability_id": f"capability-{index}",
                    "enabled": True,
                    "health": "available",
                    "failure_reason": "x" * 500,
                }
                for index in range(120)
            ],
            "models": [
                {
                    "model_id": f"model-{index}",
                    "display_name": "模型" * 100,
                    "configured": True,
                    "supports_vision": True,
                    "latency": {"sample_count": index},
                }
                for index in range(40)
            ],
            "tools": [
                {"name": f"tool-{index}", "permission": "read_only"}
                for index in range(80)
            ],
        }
        with patch.object(self_snapshot_service, "build_self_snapshot", return_value=oversized):
            context = self_snapshot_service.context_for_message("澪，你自己现在会做什么")
        encoded = context.splitlines()[-1]

        decoded = json.loads(encoded)
        self.assertIsInstance(decoded, dict)
        self.assertLessEqual(len(encoded), self_snapshot_service.CONTEXT_MAX_CHARS)

    def test_route_observation_returns_copy_and_can_be_reset(self) -> None:
        recorded = route_observation_service.record_completed_route(
            source="desktop_pet_call",
            mode="manual",
            selected_model_id="test-model",
            selected_reasoning_level="off",
            actual_model_id="actual-model",
            total_latency_ms=120.5,
            task_type="analysis",
            task_profile={"task_type": "analysis", "difficulty": "standard"},
            candidates=[{
                "model_id": "actual-model",
                "eligible": True,
                "rank": 1,
                "sample_count": 5,
                "success_rate": 1.0,
            }],
        )
        recorded["source"] = "changed"
        self.assertEqual(route_observation_service.last_route()["source"], "desktop_pet_call")
        public = self_snapshot_service.build_self_snapshot(["last_route"])["last_route"]
        self.assertEqual(public["task_type"], "analysis")
        self.assertEqual(public["task_profile"]["difficulty"], "standard")
        self.assertEqual(public["candidates"][0]["sample_count"], 5)
        self.assertTrue(public["success"])
        route_observation_service.reset_for_tests()
        with patch("app.route_observation_service.db.list_model_route_observations", return_value=[]):
            self.assertIsNone(route_observation_service.last_route())

    def test_fixed_state_is_stable_for_five_rounds(self) -> None:
        kwargs = {
            "privacy": _privacy(),
            "services": _services(),
            "profiles": [_profile()],
            "companion_config": {"voice_enabled": True},
            "pet_running": True,
        }
        rounds = [
            self_snapshot_service.build_self_snapshot(["capabilities"], **kwargs)["capabilities"]
            for _ in range(5)
        ]
        self.assertTrue(all(current == rounds[0] for current in rounds[1:]))
        self.assertEqual(
            [next(item for item in current if item["capability_id"] == "desktop_pet")["health"] for current in rounds],
            ["available"] * 5,
        )

    def test_budget_scope_does_not_create_a_missing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "missing" / "personal.db"
            original_db_path = self_snapshot_service.settings.db_path
            object.__setattr__(self_snapshot_service.settings, "db_path", db_path)
            try:
                with (
                    patch.object(self_snapshot_service.db, "get_token_usage_summary", side_effect=AssertionError),
                    patch.object(self_snapshot_service.db, "get_screen_analysis_usage", side_effect=AssertionError),
                ):
                    snapshot = self_snapshot_service.build_self_snapshot(
                        ["budget"],
                        companion_config={"screen_daily_cost_limit_yuan": 3.0},
                    )
            finally:
                object.__setattr__(self_snapshot_service.settings, "db_path", original_db_path)

        self.assertFalse(snapshot["budget"]["usage_available"])
        self.assertFalse(db_path.exists())


class PassiveEnvironmentStatusTests(unittest.TestCase):
    def test_passive_environment_status_does_not_create_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data_dir = root / "missing-data"
            db_path = data_dir / "personal.db"
            original_data_dir = environment_check_service.settings.data_dir
            original_db_path = environment_check_service.settings.db_path
            object.__setattr__(environment_check_service.settings, "data_dir", data_dir)
            object.__setattr__(environment_check_service.settings, "db_path", db_path)
            try:
                with (
                    patch.object(environment_check_service, "_webview2_version", return_value="1.0"),
                    patch.object(environment_check_service, "_gpu_info", return_value=[]),
                    patch.object(environment_check_service, "_memory_bytes", return_value=1024),
                    patch.object(environment_check_service, "list_model_profiles", return_value=[]),
                ):
                    result = environment_check_service.passive_environment_status()
            finally:
                object.__setattr__(environment_check_service.settings, "data_dir", original_data_dir)
                object.__setattr__(environment_check_service.settings, "db_path", original_db_path)

        self.assertEqual(result["mode"], "passive_read_only")
        self.assertFalse(result["required"]["data_directory"])
        self.assertFalse(data_dir.exists())


class SelfStateRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self_snapshot_service.reset_for_tests()
        app = FastAPI()
        app.include_router(self_state_routes.router, prefix="/api/agent")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self_snapshot_service.reset_for_tests()

    def test_active_view_report_round_trip_and_scoped_state(self) -> None:
        response = self.client.post(
            "/api/agent/self/active-view",
            json={"view_id": "settings", "section_id": "models", "visible": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["section_id"], "models")

        current = self.client.get("/api/agent/self/active-view")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["view_id"], "settings")

        snapshot = self.client.get("/api/agent/self/state?scopes=active_view")
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.json()["included_scopes"], ["active_view"])

    def test_rejects_unknown_scope_and_view(self) -> None:
        scope = self.client.get("/api/agent/self/state?scopes=active_view,private")
        view = self.client.post(
            "/api/agent/self/active-view",
            json={"view_id": "private", "section_id": "", "visible": True},
        )
        self.assertEqual(scope.status_code, 400)
        self.assertEqual(view.status_code, 400)


if __name__ == "__main__":
    unittest.main()
