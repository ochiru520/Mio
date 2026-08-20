from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator


class MaintenanceModeError(RuntimeError):
    pass


_condition = threading.Condition(threading.RLock())
_status = "available"
_blocked = False
_reason = ""
_active_mutations = 0
_started_at_monotonic = 0.0


def reset_runtime_state() -> None:
    global _status, _blocked, _reason, _active_mutations, _started_at_monotonic
    with _condition:
        _status = "available"
        _blocked = False
        _reason = ""
        _active_mutations = 0
        _started_at_monotonic = 0.0
        _condition.notify_all()


def begin(reason: str) -> None:
    global _status, _blocked, _reason, _started_at_monotonic
    with _condition:
        if _blocked:
            raise MaintenanceModeError(f"应用已处于维护状态：{_status}")
        _status = "draining"
        _blocked = True
        _reason = str(reason)[:300]
        _started_at_monotonic = time.monotonic()
        _condition.notify_all()


def wait_for_quiescence(timeout_seconds: float = 30) -> None:
    global _status
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    with _condition:
        while _active_mutations > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MaintenanceModeError(
                    f"等待在途写请求结束超时，仍有 {_active_mutations} 项。"
                )
            _condition.wait(timeout=remaining)
        _status = "maintenance"
        _condition.notify_all()


def finish(status: str, *, keep_blocked: bool) -> None:
    global _status, _blocked
    with _condition:
        _status = str(status)
        _blocked = bool(keep_blocked)
        _condition.notify_all()


@contextmanager
def mutation_scope() -> Iterator[None]:
    global _active_mutations
    with _condition:
        if _blocked:
            raise MaintenanceModeError("Mio 正在维护数据，暂时只允许读取。")
        _active_mutations += 1
    try:
        yield
    finally:
        with _condition:
            _active_mutations = max(0, _active_mutations - 1)
            _condition.notify_all()


def status() -> dict[str, object]:
    now = time.monotonic()
    with _condition:
        return {
            "status": _status,
            "blocked": _blocked,
            "reason": _reason,
            "active_mutations": _active_mutations,
            "elapsed_seconds": (
                round(max(0.0, now - _started_at_monotonic), 3)
                if _started_at_monotonic
                else 0.0
            ),
        }
