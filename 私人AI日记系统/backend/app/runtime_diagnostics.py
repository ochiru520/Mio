from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections import deque
from typing import Any


LOOP_SAMPLE_INTERVAL_SECONDS = 0.25
RECENT_REQUEST_LIMIT = 32
ACTIVE_REQUEST_LIMIT = 20

_lock = threading.RLock()
_process_started_monotonic = time.monotonic()
_last_loop_tick_monotonic = _process_started_monotonic
_loop_samples = 0
_loop_lag_ms = 0.0
_loop_lag_max_ms = 0.0
_loop_lag_over_100ms = 0
_loop_lag_over_1000ms = 0
_thread_pool_threads = 0
_thread_pool_queue_depth = 0
_active_requests: dict[str, dict[str, Any]] = {}
_recent_requests: deque[dict[str, Any]] = deque(maxlen=RECENT_REQUEST_LIMIT)
_background_tasks: dict[str, asyncio.Task[Any]] = {}


def reset_for_tests() -> None:
    global _process_started_monotonic
    global _last_loop_tick_monotonic
    global _loop_samples
    global _loop_lag_ms
    global _loop_lag_max_ms
    global _loop_lag_over_100ms
    global _loop_lag_over_1000ms
    global _thread_pool_threads
    global _thread_pool_queue_depth
    now = time.monotonic()
    with _lock:
        _process_started_monotonic = now
        _last_loop_tick_monotonic = now
        _loop_samples = 0
        _loop_lag_ms = 0.0
        _loop_lag_max_ms = 0.0
        _loop_lag_over_100ms = 0
        _loop_lag_over_1000ms = 0
        _thread_pool_threads = 0
        _thread_pool_queue_depth = 0
        _active_requests.clear()
        _recent_requests.clear()
        _background_tasks.clear()


def request_started(method: str, path: str) -> str:
    request_id = uuid.uuid4().hex
    with _lock:
        _active_requests[request_id] = {
            "request_id": request_id,
            "method": str(method).upper()[:16],
            "path": str(path)[:300],
            "started_monotonic": time.monotonic(),
        }
    return request_id


def request_finished(request_id: str, *, status_code: int | None, error: str = "") -> None:
    now = time.monotonic()
    with _lock:
        active = _active_requests.pop(request_id, None)
        if active is None:
            return
        _recent_requests.append(
            {
                "request_id": request_id,
                "method": active["method"],
                "path": active["path"],
                "duration_ms": round((now - float(active["started_monotonic"])) * 1000, 3),
                "status_code": status_code,
                "error": str(error)[:200],
            }
        )


def register_background_task(name: str, task: asyncio.Task[Any]) -> None:
    with _lock:
        _background_tasks[str(name)] = task


def unregister_background_task(name: str) -> None:
    with _lock:
        _background_tasks.pop(str(name), None)


def _record_loop_tick(loop: asyncio.AbstractEventLoop, now: float) -> None:
    global _last_loop_tick_monotonic
    global _loop_samples
    global _loop_lag_ms
    global _loop_lag_max_ms
    global _loop_lag_over_100ms
    global _loop_lag_over_1000ms
    global _thread_pool_threads
    global _thread_pool_queue_depth
    with _lock:
        elapsed = max(0.0, now - _last_loop_tick_monotonic)
        lag_ms = max(0.0, elapsed - LOOP_SAMPLE_INTERVAL_SECONDS) * 1000
        _last_loop_tick_monotonic = now
        _loop_samples += 1
        _loop_lag_ms = lag_ms
        _loop_lag_max_ms = max(_loop_lag_max_ms, lag_ms)
        if lag_ms > 100:
            _loop_lag_over_100ms += 1
        if lag_ms > 1000:
            _loop_lag_over_1000ms += 1
        executor = getattr(loop, "_default_executor", None)
        threads = getattr(executor, "_threads", ()) if executor is not None else ()
        queue = getattr(executor, "_work_queue", None) if executor is not None else None
        _thread_pool_threads = len(threads)
        try:
            _thread_pool_queue_depth = int(queue.qsize()) if queue is not None else 0
        except (AttributeError, NotImplementedError):
            _thread_pool_queue_depth = 0


async def monitor_loop() -> None:
    loop = asyncio.get_running_loop()
    # Record immediately so short diagnostic windows cannot cancel before the
    # first scheduled sample (notably the 10ms regression test).
    _record_loop_tick(loop, time.monotonic())
    while True:
        await asyncio.sleep(LOOP_SAMPLE_INTERVAL_SECONDS)
        _record_loop_tick(loop, time.monotonic())


def snapshot() -> dict[str, Any]:
    now = time.monotonic()
    with _lock:
        dynamic_lag_ms = max(
            _loop_lag_ms,
            max(0.0, now - _last_loop_tick_monotonic - LOOP_SAMPLE_INTERVAL_SECONDS) * 1000,
        )
        active_requests = sorted(
            (
                {
                    "request_id": item["request_id"],
                    "method": item["method"],
                    "path": item["path"],
                    "elapsed_ms": round((now - float(item["started_monotonic"])) * 1000, 3),
                }
                for item in _active_requests.values()
            ),
            key=lambda item: float(item["elapsed_ms"]),
            reverse=True,
        )[:ACTIVE_REQUEST_LIMIT]
        background_tasks = {
            name: {
                "done": task.done(),
                "cancelled": task.cancelled(),
                "exception": (
                    type(task.exception()).__name__
                    if task.done() and not task.cancelled() and task.exception() is not None
                    else ""
                ),
            }
            for name, task in _background_tasks.items()
        }
        return {
            "pid": os.getpid(),
            "uptime_seconds": round(max(0.0, now - _process_started_monotonic), 3),
            "event_loop": {
                "current_lag_ms": round(dynamic_lag_ms, 3),
                "max_lag_ms": round(_loop_lag_max_ms, 3),
                "samples": _loop_samples,
                "over_100ms": _loop_lag_over_100ms,
                "over_1000ms": _loop_lag_over_1000ms,
                "last_tick_age_ms": round(max(0.0, now - _last_loop_tick_monotonic) * 1000, 3),
            },
            "requests": {
                "active_count": len(_active_requests),
                "active": active_requests,
                "recent": list(_recent_requests),
            },
            "thread_pool": {
                "threads": _thread_pool_threads,
                "queue_depth": _thread_pool_queue_depth,
            },
            "background_tasks": background_tasks,
            "process_threads": threading.active_count(),
        }
