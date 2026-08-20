from __future__ import annotations

import json
import math
import threading
from collections import deque
from datetime import datetime
from typing import Any, Iterable

from . import db


_lock = threading.Lock()
_history: deque[dict[str, Any]] = deque(maxlen=100)


def _clean_number(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _json(value: object, fallback: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return fallback


def _parsed(value: object, fallback: object) -> object:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def record_route_result(
    *,
    source: str,
    mode: str,
    selected_model_id: str,
    selected_reasoning_level: str,
    success: bool,
    request_id: str = "",
    actual_model_id: str = "",
    connection_route: str = "",
    difficulty: str = "",
    reason: str = "",
    latency_budget_ms: int = 0,
    first_token_latency_ms: float | int | None = None,
    total_latency_ms: float | int | None = None,
    request_cost_yuan: float | int | None = None,
    request_cost_source: str = "",
    error_code: str = "",
    task_type: str = "conversation",
    task_profile: dict[str, object] | None = None,
    candidates: Iterable[dict[str, object]] = (),
    escalated_from_model_id: str = "",
) -> dict[str, Any]:
    clean_candidates = [dict(item) for item in candidates if isinstance(item, dict)][:12]
    clean_task_profile = dict(task_profile or {})
    item = {
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "request_id": str(request_id or "")[:80],
        "source": str(source or "unknown")[:40],
        "mode": "automatic" if mode == "automatic" else "manual",
        "selected_model_id": str(selected_model_id or "")[:200],
        "selected_reasoning_level": str(selected_reasoning_level or "")[:50],
        "actual_model_id": str(actual_model_id or selected_model_id or "")[:200],
        "connection_route": str(connection_route or "")[:80],
        "difficulty": str(difficulty or "")[:40],
        "task_type": str(task_type or clean_task_profile.get("task_type") or "conversation")[:60],
        "task_profile": clean_task_profile,
        "candidates": clean_candidates,
        "reason": str(reason or ("用户或共享设置指定" if mode != "automatic" else "自动路由"))[:500],
        "latency_budget_ms": max(0, int(latency_budget_ms or 0)),
        "first_token_latency_ms": _clean_number(first_token_latency_ms),
        "total_latency_ms": _clean_number(total_latency_ms),
        "request_cost_yuan": _clean_number(request_cost_yuan),
        "request_cost_source": str(request_cost_source or "")[:60],
        "success": bool(success),
        "error_code": str(error_code or "")[:120],
        "escalated_from_model_id": str(escalated_from_model_id or "")[:200],
    }
    with _lock:
        _history.append(item)
    if item["request_id"]:
        try:
            db.save_model_route_observation(
                request_id=item["request_id"],
                source=item["source"],
                mode=item["mode"],
                task_type=item["task_type"],
                difficulty=item["difficulty"],
                selected_model_id=item["selected_model_id"],
                actual_model_id=item["actual_model_id"],
                reasoning_level=item["selected_reasoning_level"],
                success=item["success"],
                error_code=item["error_code"],
                first_token_latency_ms=item["first_token_latency_ms"],
                total_latency_ms=item["total_latency_ms"],
                request_cost_yuan=item["request_cost_yuan"],
                request_cost_source=item["request_cost_source"],
                candidates_json=_json(clean_candidates, "[]"),
                task_profile_json=_json(clean_task_profile, "{}"),
                escalated_from_model_id=item["escalated_from_model_id"],
                reason=item["reason"],
            )
        except Exception:
            pass
    return dict(item)


def record_completed_route(**kwargs: Any) -> dict[str, Any]:
    return record_route_result(success=True, **kwargs)


def record_failed_route(**kwargs: Any) -> dict[str, Any]:
    return record_route_result(success=False, **kwargs)


def _row_to_public(row: object) -> dict[str, Any]:
    item = dict(row)  # type: ignore[arg-type]
    return {
        "recorded_at": str(item.get("created_at") or ""),
        "request_id": str(item.get("request_id") or ""),
        "source": str(item.get("source") or "unknown"),
        "mode": str(item.get("mode") or "manual"),
        "selected_model_id": str(item.get("selected_model_id") or ""),
        "selected_reasoning_level": str(item.get("reasoning_level") or ""),
        "actual_model_id": str(item.get("actual_model_id") or ""),
        "connection_route": "",
        "difficulty": str(item.get("difficulty") or ""),
        "task_type": str(item.get("task_type") or "conversation"),
        "task_profile": _parsed(item.get("task_profile_json"), {}),
        "candidates": _parsed(item.get("candidates_json"), []),
        "reason": str(item.get("reason") or ""),
        "latency_budget_ms": 0,
        "first_token_latency_ms": item.get("first_token_latency_ms"),
        "total_latency_ms": item.get("total_latency_ms"),
        "request_cost_yuan": item.get("request_cost_yuan"),
        "request_cost_source": str(item.get("request_cost_source") or ""),
        "success": bool(item.get("success")),
        "error_code": str(item.get("error_code") or ""),
        "escalated_from_model_id": str(item.get("escalated_from_model_id") or ""),
    }


def last_route() -> dict[str, Any] | None:
    with _lock:
        if _history:
            return dict(_history[-1])
    try:
        rows = db.list_model_route_observations(limit=1)
    except Exception:
        rows = []
    return _row_to_public(rows[0]) if rows else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return round(ordered[index], 3)


def model_performance_snapshot(task_type: str = "") -> dict[str, dict[str, object]]:
    try:
        rows = db.list_model_route_observations(limit=2000)
    except Exception:
        return {}
    requested_task = str(task_type or "").strip()
    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        model_id = str(item.get("actual_model_id") or item.get("selected_model_id") or "").strip()
        if model_id:
            by_model.setdefault(model_id, []).append(item)
    result: dict[str, dict[str, object]] = {}
    for model_id, all_rows in by_model.items():
        matching = [row for row in all_rows if str(row.get("task_type") or "") == requested_task]
        samples = matching if requested_task and len(matching) >= 3 else all_rows
        successes = [row for row in samples if bool(row.get("success"))]
        first_tokens = [
            float(row["first_token_latency_ms"])
            for row in successes
            if row.get("first_token_latency_ms") is not None
        ]
        totals = [
            float(row["total_latency_ms"])
            for row in successes
            if row.get("total_latency_ms") is not None
        ]
        costs = [
            float(row["request_cost_yuan"])
            for row in successes
            if row.get("request_cost_yuan") is not None
        ]
        result[model_id] = {
            "sample_count": len(samples),
            "success_count": len(successes),
            "success_rate": round(len(successes) / len(samples), 4) if samples else None,
            "first_token_average_ms": round(sum(first_tokens) / len(first_tokens), 3) if first_tokens else None,
            "first_token_p95_ms": _p95(first_tokens),
            "total_average_ms": round(sum(totals) / len(totals), 3) if totals else None,
            "total_p95_ms": _p95(totals),
            "average_cost_yuan": round(sum(costs) / len(costs), 6) if costs else None,
            "priced_sample_count": len(costs),
            "task_specific_sample_count": len(matching),
        }
    return result


def reset_for_tests() -> None:
    with _lock:
        _history.clear()


__all__ = [
    "last_route",
    "model_performance_snapshot",
    "record_completed_route",
    "record_failed_route",
    "record_route_result",
    "reset_for_tests",
]
