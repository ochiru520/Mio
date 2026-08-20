from __future__ import annotations

import unittest

from scripts.benchmark_model_router import run_benchmark


class ModelRouterBenchmarkTests(unittest.TestCase):
    def test_five_round_adaptive_route_beats_fixed_baseline(self) -> None:
        report = run_benchmark(5)

        self.assertTrue(report["passed"])
        self.assertGreaterEqual(
            report["adaptive"]["success_rate"],
            report["fixed_model_baseline"]["success_rate"],
        )
        self.assertLess(
            report["adaptive"]["average_cost_yuan"],
            report["fixed_model_baseline"]["average_cost_yuan"],
        )
        self.assertEqual(len(report["selections"]), 30)


if __name__ == "__main__":
    unittest.main()
