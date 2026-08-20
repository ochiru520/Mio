from __future__ import annotations

from collections import deque
from datetime import datetime
import threading
from typing import Any


_lock = threading.RLock()
_windows: dict[str, dict[str, Any]] = {}
_events: deque[dict[str, Any]] = deque(maxlen=80)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def record(event: dict[str, Any]) -> dict[str, Any]:
    window_id = str(event.get("window_id") or "").strip()[:100]
    source = str(event.get("source") or "").strip()[:100]
    runtime = str(event.get("runtime") or "").strip()[:80]
    action = str(event.get("action") or "").strip().lower()[:80]
    correlation_id = str(event.get("correlation_id") or "").strip()[:120]
    if not window_id or not source or not runtime or not action or not correlation_id:
        raise ValueError("窗口事件缺少 source/runtime/window_id/action/correlation_id。")

    bounds = event.get("bounds") if isinstance(event.get("bounds"), dict) else {}
    normalized = {
        "source": source,
        "runtime": runtime,
        "window_id": window_id,
        "pid": max(0, int(event.get("pid") or 0)),
        "action": action,
        "correlation_id": correlation_id,
        "visible": bool(event.get("visible")),
        "focused": bool(event.get("focused")),
        "bounds": {
            key: int(bounds.get(key) or 0)
            for key in ("x", "y", "width", "height")
        },
        "recorded_at": _now_iso(),
    }
    if action in {"closed", "destroyed", "exit"}:
        normalized["visible"] = False
        normalized["focused"] = False
    with _lock:
        _windows[window_id] = normalized
        _events.append(normalized)
    return dict(normalized)


def snapshot() -> dict[str, Any]:
    with _lock:
        windows = [dict(value) for value in _windows.values()]
        events = [dict(value) for value in _events]
    windows.sort(key=lambda item: item["window_id"])
    return {
        "windows": windows,
        "active_count": sum(
            1
            for item in windows
            if item["action"] not in {"closed", "destroyed", "exit"}
        ),
        "visible_count": sum(1 for item in windows if item["visible"]),
        "recent_events": events[-20:],
    }


def reset_for_tests() -> None:
    with _lock:
        _windows.clear()
        _events.clear()


__all__ = ["record", "reset_for_tests", "snapshot"]
