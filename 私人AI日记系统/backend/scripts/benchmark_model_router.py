from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auto_router import select_auto_route
from app.model_registry import ModelProfile


def profile(
    model_id: str,
    *,
    vision: bool = False,
    tool_calls: bool = False,
    default: bool = False,
) -> ModelProfile:
    return ModelProfile(
        id=model_id,
        provider_id=f"provider-{model_id}",
        provider_name="阶段七本地假供应商",
        display_name=model_id,
        model=model_id,
        base_urls=("http://127.0.0.1/never-called",),
        api_key="isolated-fixture",
        supports_vision=vision,
        supports_tool_calls=tool_calls,
        supports_structured_output=True,
        context_window_tokens=131072,
        input_price_cny_per_million=1,
        output_price_cny_per_million=2,
        pricing_source="isolated_fixture",
        is_default=default,
    )


PROFILES = [
    profile("model-a", default=True),
    profile("model-b"),
    profile("model-c", vision=True, tool_calls=True),
    profile("model-d", vision=True),
]


CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "simple_chat",
        "message": "嗯，刚吃过饭。",
        "expected": "model-b",
        "metrics": {
            "model-a": (0.95, 1000, 0.008),
            "model-b": (0.99, 350, 0.001),
            "model-c": (0.98, 1500, 0.012),
            "model-d": (0.96, 1200, 0.006),
        },
    },
    {
        "id": "balanced_analysis",
        "message": "帮我分析这两个方案的区别和风险。",
        "expected": "model-a",
        "metrics": {
            "model-a": (0.99, 900, 0.008),
            "model-b": (0.75, 400, 0.001),
            "model-c": (0.96, 1600, 0.012),
            "model-d": (0.90, 1300, 0.006),
        },
    },
    {
        "id": "complex_technical",
        "message": "请设计数据库和接口，实现代码、测试方案并分析失败恢复风险。",
        "expected": "model-c",
        "metrics": {
            "model-a": (0.60, 1100, 0.008),
            "model-b": (0.40, 500, 0.001),
            "model-c": (1.00, 2200, 0.012),
            "model-d": (0.70, 1700, 0.006),
        },
    },
    {
        "id": "vision",
        "message": "看看这张图里哪里有问题。",
        "image_count": 1,
        "expected": "model-d",
        "metrics": {
            "model-c": (0.92, 1800, 0.012),
            "model-d": (0.99, 1100, 0.004),
        },
    },
    {
        "id": "agent_tool",
        "message": "帮我创建一个今晚复测语音的提醒。",
        "expected": "model-b",
        "metrics": {
            "model-a": (0.94, 1000, 0.008),
            "model-b": (0.99, 600, 0.002),
            "model-c": (0.98, 1500, 0.012),
            "model-d": (0.90, 1200, 0.006),
        },
    },
    {
        "id": "document",
        "message": "总结附件里的重点。",
        "text_attachment_chars": 6000,
        "expected": "model-a",
        "metrics": {
            "model-a": (0.99, 800, 0.008),
            "model-b": (0.90, 500, 0.002),
            "model-c": (0.97, 1400, 0.012),
            "model-d": (0.92, 1100, 0.006),
        },
    },
)


def performance(case: dict[str, Any]) -> dict[str, dict[str, object]]:
    return {
        model_id: {
            "sample_count": 10,
            "success_rate": values[0],
            "first_token_p95_ms": values[1],
            "average_cost_yuan": values[2],
        }
        for model_id, values in case["metrics"].items()
    }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    latencies = [float(row["latency_ms"]) for row in rows]
    costs = [float(row["cost_yuan"]) for row in rows]
    successes = sum(bool(row["success"]) for row in rows)
    return {
        "samples": len(rows),
        "successes": successes,
        "success_rate": round(successes / len(rows), 4),
        "average_ms": round(statistics.fmean(latencies), 3),
        "p50_ms": round(percentile(latencies, 0.50), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "max_ms": round(max(latencies), 3),
        "average_cost_yuan": round(statistics.fmean(costs), 6),
        "total_cost_yuan": round(sum(costs), 6),
    }


def run_benchmark(rounds: int = 5) -> dict[str, object]:
    adaptive_rows: list[dict[str, object]] = []
    baseline_rows: list[dict[str, object]] = []
    selections: list[dict[str, object]] = []
    baseline_model = "model-a"
    for round_number in range(1, max(1, rounds) + 1):
        for case in CASES:
            route = select_auto_route(
                case["message"],
                image_count=int(case.get("image_count") or 0),
                text_attachment_chars=int(case.get("text_attachment_chars") or 0),
                profiles=PROFILES,
                performance=performance(case),
            )
            if route.model_id != case["expected"]:
                raise AssertionError(
                    f"{case['id']} selected {route.model_id}, expected {case['expected']}"
                )
            adaptive_values = case["metrics"][route.model_id]
            baseline_values = case["metrics"].get(baseline_model, (0.0, 1000, 0.008))
            adaptive_rows.append({
                "round": round_number,
                "case": case["id"],
                "model_id": route.model_id,
                "success": adaptive_values[0] >= 0.9,
                "latency_ms": adaptive_values[1],
                "cost_yuan": adaptive_values[2],
            })
            baseline_rows.append({
                "round": round_number,
                "case": case["id"],
                "model_id": baseline_model,
                "success": baseline_values[0] >= 0.9,
                "latency_ms": baseline_values[1],
                "cost_yuan": baseline_values[2],
            })
            selections.append({
                "round": round_number,
                "case": case["id"],
                "task_profile": route.task_profile,
                "selected_model_id": route.model_id,
                "fallback_model_id": route.fallback_model_id,
                "reasoning_level": route.reasoning_level,
            })
    adaptive = summarize(adaptive_rows)
    baseline = summarize(baseline_rows)
    passed = (
        float(adaptive["success_rate"]) >= float(baseline["success_rate"])
        and (
            float(adaptive["average_ms"]) < float(baseline["average_ms"])
            or float(adaptive["average_cost_yuan"]) < float(baseline["average_cost_yuan"])
        )
    )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scope": "isolated deterministic task set with local fake model observations; no network or API cost",
        "rounds": max(1, rounds),
        "cases_per_round": len(CASES),
        "baseline_model_id": baseline_model,
        "adaptive": adaptive,
        "fixed_model_baseline": baseline,
        "passed": passed,
        "selections": selections,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark(args.rounds)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
