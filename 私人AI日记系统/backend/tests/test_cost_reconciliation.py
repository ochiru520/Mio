from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.cost_reconciliation_service import queue_cost_reconciliation, reconcile_one_due_job
from app.llm import CompletionRoute, ProviderCostReference
from app.model_registry import ModelProfile


class CostReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "cost.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_pending_estimate_is_replaced_by_provider_charge(self) -> None:
        request_id = "local-request-1"
        conversation_id = "agent_test"
        db.save_message(
            "assistant",
            "测试回复",
            conversation_id=conversation_id,
            request_id=request_id,
            request_cost_yuan=0.01,
            request_cost_source="provider_estimate",
        )
        reference = ProviderCostReference(
            profile_id="test-model",
            provider_request_id="provider-request-1",
            base_url="https://example.test/v1",
            estimated_cost_yuan=0.01,
            estimated_cost_source="provider_estimate",
        )

        queue_cost_reconciliation(request_id, conversation_id, (reference,))
        pending = db.get_recent_messages(5, conversation_id)[0]

        profile = ModelProfile(
            id="test-model",
            provider_name="test",
            display_name="test",
            model="test-model",
            base_urls=("https://example.test/v1",),
            api_key="secret",
        )
        route = CompletionRoute("https://example.test/v1", "", "直连")
        with (
            patch(
                "app.cost_reconciliation_service._matching_route",
                return_value=(profile, route),
            ),
            patch(
                "app.cost_reconciliation_service._provider_log_cost_yuan",
                new=AsyncMock(return_value=0.00321),
            ),
        ):
            worked = asyncio.run(reconcile_one_due_job())

        resolved = db.get_recent_messages(5, conversation_id)[0]
        self.assertEqual(pending["request_cost_source"], "provider_reconciliation_pending")
        self.assertTrue(worked)
        self.assertAlmostEqual(resolved["request_cost_yuan"], 0.00321)
        self.assertEqual(resolved["request_cost_source"], "provider_reported")
        self.assertEqual(db.list_due_cost_reconciliation_jobs(), [])

    def test_screen_analysis_cost_is_visible_only_after_reconciliation(self) -> None:
        request_id = "screen-request-1"
        db.record_screen_analysis_usage(
            prompt_tokens=100,
            completion_tokens=20,
            cost_yuan=0.01,
            request_id=request_id,
            request_kind="analysis",
            model_id="test-model",
            cost_source="provider_reconciliation_pending",
        )
        reference = ProviderCostReference(
            profile_id="test-model",
            provider_request_id="provider-screen-1",
            base_url="https://example.test/v1",
            estimated_cost_yuan=0.01,
            estimated_cost_source="provider_estimate",
        )
        queue_cost_reconciliation(request_id, "screen_analysis:2026-08-10", (reference,))

        pending = db.get_screen_analysis_usage()
        self.assertEqual(pending["confirmed_request_count"], 0)
        self.assertEqual(pending["confirmed_cost_yuan"], 0)
        self.assertEqual(pending["pending_request_count"], 1)

        profile = ModelProfile(
            id="test-model",
            provider_name="test",
            display_name="test",
            model="test-model",
            base_urls=("https://example.test/v1",),
            api_key="secret",
        )
        route = CompletionRoute("https://example.test/v1", "", "直连")
        with (
            patch(
                "app.cost_reconciliation_service._matching_route",
                return_value=(profile, route),
            ),
            patch(
                "app.cost_reconciliation_service._provider_log_cost_yuan",
                new=AsyncMock(return_value=0.002),
            ),
        ):
            asyncio.run(reconcile_one_due_job())

        resolved = db.get_screen_analysis_usage()
        self.assertEqual(resolved["confirmed_request_count"], 1)
        self.assertAlmostEqual(resolved["confirmed_cost_yuan"], 0.002)
        self.assertEqual(resolved["pending_request_count"], 0)


if __name__ == "__main__":
    unittest.main()
