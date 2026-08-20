from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import db, route_observation_service


class RouteObservationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "route.db")
        db.init_db()
        route_observation_service.reset_for_tests()

    def tearDown(self) -> None:
        route_observation_service.reset_for_tests()
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_persisted_route_survives_memory_reset(self) -> None:
        route_observation_service.record_completed_route(
            source="desktop",
            mode="automatic",
            request_id="route-persist-1",
            selected_model_id="model-a",
            selected_reasoning_level="medium",
            actual_model_id="model-a",
            task_type="analysis",
            task_profile={"task_type": "analysis", "difficulty": "standard"},
            candidates=[{"model_id": "model-a", "eligible": True, "rank": 1}],
            first_token_latency_ms=120,
            total_latency_ms=800,
            request_cost_yuan=0.01,
        )

        route_observation_service.reset_for_tests()
        restored = route_observation_service.last_route()

        self.assertIsNotNone(restored)
        self.assertEqual(restored["request_id"], "route-persist-1")
        self.assertEqual(restored["task_type"], "analysis")
        self.assertEqual(restored["task_profile"]["difficulty"], "standard")

    def test_metrics_report_success_p95_and_cost(self) -> None:
        for index, latency in enumerate((100, 200, 300, 400, 500), start=1):
            route_observation_service.record_completed_route(
                source="desktop",
                mode="automatic",
                request_id=f"route-success-{index}",
                selected_model_id="model-a",
                selected_reasoning_level="low",
                actual_model_id="model-a",
                task_type="conversation",
                first_token_latency_ms=latency,
                total_latency_ms=latency * 2,
                request_cost_yuan=index / 1000,
            )
        route_observation_service.record_failed_route(
            source="desktop",
            mode="automatic",
            request_id="route-failure-1",
            selected_model_id="model-a",
            selected_reasoning_level="low",
            actual_model_id="model-a",
            task_type="conversation",
            error_code="model_request_failed",
        )

        metrics = route_observation_service.model_performance_snapshot("conversation")["model-a"]

        self.assertEqual(metrics["sample_count"], 6)
        self.assertEqual(metrics["success_count"], 5)
        self.assertEqual(metrics["success_rate"], 0.8333)
        self.assertEqual(metrics["first_token_p95_ms"], 500)
        self.assertEqual(metrics["average_cost_yuan"], 0.003)


if __name__ == "__main__":
    unittest.main()
