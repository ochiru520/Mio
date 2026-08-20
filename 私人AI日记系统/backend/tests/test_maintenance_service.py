from __future__ import annotations

import threading
import time
import unittest

from app import maintenance_service


class MaintenanceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        maintenance_service.reset_runtime_state()

    def tearDown(self) -> None:
        maintenance_service.reset_runtime_state()

    def test_maintenance_waits_for_inflight_mutation(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def mutate() -> None:
            with maintenance_service.mutation_scope():
                entered.set()
                release.wait(2)

        worker = threading.Thread(target=mutate)
        worker.start()
        self.assertTrue(entered.wait(1))
        maintenance_service.begin("restore")
        timer = threading.Timer(0.05, release.set)
        timer.start()
        started = time.monotonic()
        maintenance_service.wait_for_quiescence(1)
        elapsed = time.monotonic() - started
        worker.join(1)

        self.assertGreaterEqual(elapsed, 0.04)
        self.assertEqual(maintenance_service.status()["status"], "maintenance")

    def test_blocked_state_rejects_new_mutation_until_released(self) -> None:
        maintenance_service.begin("restore")
        maintenance_service.wait_for_quiescence(1)

        with self.assertRaises(maintenance_service.MaintenanceModeError):
            with maintenance_service.mutation_scope():
                pass

        maintenance_service.finish("rollback_complete", keep_blocked=False)
        with maintenance_service.mutation_scope():
            pass
        status = maintenance_service.status()
        self.assertEqual(status["status"], "rollback_complete")
        self.assertFalse(status["blocked"])

    def test_completed_result_does_not_prevent_next_maintenance_run(self) -> None:
        maintenance_service.begin("first restore")
        maintenance_service.wait_for_quiescence(1)
        maintenance_service.finish("rollback_complete", keep_blocked=False)

        maintenance_service.begin("second restore")
        maintenance_service.wait_for_quiescence(1)

        status = maintenance_service.status()
        self.assertEqual(status["status"], "maintenance")
        self.assertTrue(status["blocked"])


if __name__ == "__main__":
    unittest.main()
