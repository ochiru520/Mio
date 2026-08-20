from __future__ import annotations

import unittest

from app import window_topology_service


class WindowTopologyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        window_topology_service.reset_for_tests()

    def tearDown(self) -> None:
        window_topology_service.reset_for_tests()

    def test_records_stable_window_identity_and_recent_actions(self) -> None:
        window_topology_service.record({
            "source": "electron-main",
            "runtime": "electron",
            "window_id": "pet-chat-input",
            "pid": 42,
            "action": "created",
            "correlation_id": "create-1",
            "visible": False,
            "bounds": {"x": 10, "y": 20, "width": 520, "height": 84},
        })
        window_topology_service.record({
            "source": "electron-main",
            "runtime": "electron",
            "window_id": "pet-chat-input",
            "pid": 42,
            "action": "shown",
            "correlation_id": "show-1",
            "visible": True,
            "focused": True,
            "bounds": {"x": 11, "y": 21, "width": 520, "height": 84},
        })

        result = window_topology_service.snapshot()

        self.assertEqual(result["active_count"], 1)
        self.assertEqual(result["visible_count"], 1)
        self.assertEqual(result["windows"][0]["correlation_id"], "show-1")
        self.assertEqual(len(result["recent_events"]), 2)

    def test_closed_window_is_not_active(self) -> None:
        window_topology_service.record({
            "source": "agent-ui",
            "runtime": "pywebview",
            "window_id": "agent-main",
            "pid": 7,
            "action": "closed",
            "correlation_id": "close-1",
            "visible": True,
            "focused": True,
        })

        result = window_topology_service.snapshot()

        self.assertEqual(result["active_count"], 0)
        self.assertEqual(result["visible_count"], 0)
        self.assertFalse(result["windows"][0]["focused"])

    def test_rejects_untraceable_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少"):
            window_topology_service.record({"window_id": "agent-main"})


if __name__ == "__main__":
    unittest.main()
