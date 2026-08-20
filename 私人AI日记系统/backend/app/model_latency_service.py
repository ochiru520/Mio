from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import asdict, dataclass


MAX_SAMPLES = 24
EWMA_ALPHA = 0.35


@dataclass(frozen=True)
class ModelLatencyStats:
    model_id: str
    sample_count: int
    first_token_ewma_ms: float | None
    first_token_p95_ms: float | None
    total_ewma_ms: float | None
    last_first_token_ms: float | None
    last_total_ms: float | None

    @property
    def preferred_first_token_ms(self) -> float | None:
        return self.first_token_p95_ms or self.first_token_ewma_ms

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _MutableStats:
    first_token_samples: deque[float]
    total_samples: deque[float]
    first_token_ewma_ms: float | None = None
    total_ewma_ms: float | None = None


_stats: dict[str, _MutableStats] = {}
_lock = threading.Lock()


def _clean_latency(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _p95(values: deque[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return round(ordered[index], 2)


def _update_ewma(previous: float | None, value: float) -> float:
    return round(value if previous is None else previous + EWMA_ALPHA * (value - previous), 2)


def record_latency(
    model_id: str,
    *,
    first_token_latency_ms: float | int | None,
    total_latency_ms: float | int | None,
) -> None:
    key = str(model_id or "").strip()
    if not key:
        return
    first = _clean_latency(first_token_latency_ms)
    total = _clean_latency(total_latency_ms)
    if first is None and total is None:
        return
    with _lock:
        current = _stats.setdefault(
            key,
            _MutableStats(
                first_token_samples=deque(maxlen=MAX_SAMPLES),
                total_samples=deque(maxlen=MAX_SAMPLES),
            ),
        )
        if first is not None:
            current.first_token_samples.append(first)
            current.first_token_ewma_ms = _update_ewma(current.first_token_ewma_ms, first)
        if total is not None:
            current.total_samples.append(total)
            current.total_ewma_ms = _update_ewma(current.total_ewma_ms, total)


def get_latency_stats(model_id: str) -> ModelLatencyStats:
    key = str(model_id or "").strip()
    with _lock:
        current = _stats.get(key)
        if current is None:
            return ModelLatencyStats(key, 0, None, None, None, None, None)
        return ModelLatencyStats(
            model_id=key,
            sample_count=max(len(current.first_token_samples), len(current.total_samples)),
            first_token_ewma_ms=current.first_token_ewma_ms,
            first_token_p95_ms=_p95(current.first_token_samples),
            total_ewma_ms=current.total_ewma_ms,
            last_first_token_ms=current.first_token_samples[-1] if current.first_token_samples else None,
            last_total_ms=current.total_samples[-1] if current.total_samples else None,
        )


def all_latency_stats() -> list[ModelLatencyStats]:
    with _lock:
        keys = list(_stats)
    return [get_latency_stats(key) for key in keys]


def reset_latency_stats() -> None:
    with _lock:
        _stats.clear()
