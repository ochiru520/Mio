from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from app import runtime_diagnostics


class RuntimeDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        runtime_diagnostics.reset_for_tests()

    async def test_tracks_active_and_recent_requests_without_content(self) -> None:
        request_id = runtime_diagnostics.request_started("post", "/api/agent/chat")

        active = runtime_diagnostics.snapshot()
        runtime_diagnostics.request_finished(request_id, status_code=200)
        finished = runtime_diagnostics.snapshot()

        self.assertEqual(active["requests"]["active_count"], 1)
        self.assertEqual(active["requests"]["active"][0]["path"], "/api/agent/chat")
        self.assertNotIn("content", active["requests"]["active"][0])
        self.assertEqual(finished["requests"]["active_count"], 0)
        self.assertEqual(finished["requests"]["recent"][-1]["status_code"], 200)

    async def test_dynamic_lag_is_visible_before_event_loop_recovers(self) -> None:
        with runtime_diagnostics._lock:
            runtime_diagnostics._last_loop_tick_monotonic = time.monotonic() - 1.5

        result = runtime_diagnostics.snapshot()

        self.assertGreater(result["event_loop"]["current_lag_ms"], 1000)
        self.assertGreater(result["event_loop"]["last_tick_age_ms"], 1400)

    async def test_monitor_records_loop_samples_and_task_state(self) -> None:
        waiting = asyncio.create_task(asyncio.Event().wait())
        runtime_diagnostics.register_background_task("waiting", waiting)
        with patch.object(runtime_diagnostics, "LOOP_SAMPLE_INTERVAL_SECONDS", 0.01):
            monitor = asyncio.create_task(runtime_diagnostics.monitor_loop())
            await asyncio.sleep(0.04)
            monitor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await monitor

        result = runtime_diagnostics.snapshot()
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        self.assertGreaterEqual(result["event_loop"]["samples"], 1)
        self.assertFalse(result["background_tasks"]["waiting"]["done"])


if __name__ == "__main__":
    unittest.main()
