from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, TypeVar


T = TypeVar("T")
Runner = Callable[[], Awaitable[T]]


class ChatRunCancelledError(RuntimeError):
    pass


@dataclass
class TraceRecord:
    trace_id: str
    conversation_id: str
    source: str
    started_at: float = field(default_factory=time.monotonic)
    stages: dict[str, float] = field(default_factory=dict)
    status: str = "running"
    error: str = ""
    request_id: str = ""
    replaced_count: int = 0
    queued_count: int = 0

    def mark(self, stage: str) -> None:
        self.stages[stage] = round((time.monotonic() - self.started_at) * 1000, 1)

    def public_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "source": self.source,
            "status": self.status,
            "error": self.error,
            "request_id": self.request_id,
            "replaced_count": self.replaced_count,
            "queued_count": self.queued_count,
            "elapsed_ms": round((time.monotonic() - self.started_at) * 1000, 1),
            "stages": dict(self.stages),
        }


class RuntimeTraceStore:
    def __init__(self, limit: int = 200) -> None:
        self._limit = max(20, int(limit))
        self._records: deque[TraceRecord] = deque(maxlen=self._limit)
        self._lock = asyncio.Lock()

    async def start(self, conversation_id: str, source: str) -> TraceRecord:
        record = TraceRecord(
            trace_id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            source=source,
        )
        async with self._lock:
            self._records.append(record)
        return record

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            records = list(self._records)[-max(1, min(int(limit), self._limit)) :]
        return [record.public_dict() for record in reversed(records)]

    async def summary(self) -> dict[str, Any]:
        async with self._lock:
            records = list(self._records)
        running = sum(record.status == "running" for record in records)
        failed = sum(record.status == "failed" for record in records)
        completed = sum(record.status == "completed" for record in records)
        return {
            "trace_count": len(records),
            "running": running,
            "completed": completed,
            "failed": failed,
            "latest": records[-1].public_dict() if records else None,
        }


@dataclass
class _RunEntry(Generic[T]):
    runner: Runner[T]
    future: asyncio.Future[T]
    trace: TraceRecord
    started_at: float = field(default_factory=time.monotonic)
    replacement: Runner[T] | None = None
    capture_count: int = 0
    task: asyncio.Task[None] | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_reason: str = ""


@dataclass
class _ConversationState(Generic[T]):
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active: _RunEntry[T] | None = None
    queued: deque[_RunEntry[T]] = field(default_factory=deque)


class ConversationRunCoordinator(Generic[T]):
    """Keep one active model run per conversation and absorb early follow-ups.

    The first request is allowed to start immediately. A message arriving during
    the short capture window cancels that run and starts a fresh run using the
    latest request, so the model sees the earlier user message in history rather
    than producing one answer per unfinished sentence. Later messages queue in
    arrival order instead of racing the active model request.
    """

    def __init__(self, traces: RuntimeTraceStore) -> None:
        self._states: dict[str, _ConversationState[T]] = {}
        self._states_lock = asyncio.Lock()
        self._traces = traces

    async def _state(self, conversation_id: str) -> _ConversationState[T]:
        async with self._states_lock:
            state = self._states.get(conversation_id)
            if state is None:
                state = _ConversationState()
                self._states[conversation_id] = state
            return state

    async def submit(
        self,
        conversation_id: str,
        source: str,
        runner: Runner[T],
        *,
        capture_seconds: float = 4.0,
        max_capture_count: int = 2,
    ) -> T:
        state = await self._state(conversation_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        trace = await self._traces.start(conversation_id, source)
        entry = _RunEntry(runner=runner, future=future, trace=trace)

        async with state.lock:
            active = state.active
            if active is None:
                state.active = entry
                asyncio.create_task(self._run_entry(conversation_id, state, entry))
            elif (
                not active.future.done()
                and time.monotonic() - active.started_at <= max(0.0, capture_seconds)
                and active.capture_count < max(0, max_capture_count)
            ):
                # The active runner must reach its first suspension before it
                # can be replaced. Chat runners save the inbound message before
                # that point, so an immediate follow-up cannot erase the first
                # half of a user's unfinished message.
                await active.started.wait()
                active.replacement = runner
                active.capture_count += 1
                active.trace.replaced_count += 1
                active.trace.mark("follow_up_captured")
                active.trace.queued_count = active.capture_count
                trace.status = "captured"
                trace.mark("captured_by_active_run")
                if active.task is not None:
                    active.task.cancel()
                # All callers in the capture window receive the same final
                # answer. The replacement runner keeps the original future.
                future = active.future
            else:
                state.queued.append(entry)
                active.trace.queued_count += 1
                trace.mark("queued")

        # Shield keeps a disconnected HTTP/QQ caller from cancelling the shared
        # model run that another channel may still be waiting for.
        return await asyncio.shield(future)

    async def cancel(
        self,
        conversation_id: str,
        *,
        source: str = "",
        reason: str = "request_cancelled",
    ) -> int:
        async with self._states_lock:
            state = self._states.get(conversation_id)
        if state is None:
            return 0

        cancelled = 0
        async with state.lock:
            active = state.active
            if active is not None and (not source or active.trace.source == source):
                active.cancel_reason = str(reason or "request_cancelled")[:200]
                active.replacement = None
                if active.task is not None and not active.task.done():
                    active.task.cancel()
                    cancelled += 1

            retained: deque[_RunEntry[T]] = deque()
            while state.queued:
                entry = state.queued.popleft()
                if source and entry.trace.source != source:
                    retained.append(entry)
                    continue
                entry.cancel_reason = str(reason or "request_cancelled")[:200]
                entry.trace.status = "cancelled"
                entry.trace.error = entry.cancel_reason
                entry.trace.mark("cancelled")
                if not entry.future.done():
                    entry.future.set_exception(ChatRunCancelledError(entry.cancel_reason))
                cancelled += 1
            state.queued = retained
        return cancelled

    async def _run_entry(
        self,
        conversation_id: str,
        state: _ConversationState[T],
        entry: _RunEntry[T],
    ) -> None:
        # The task is attached dynamically to support replacement cancellation
        # without exposing asyncio tasks in the public runtime state.
        entry.task = asyncio.current_task()
        entry.started.set()
        trace_token = _current_trace.set(entry.trace)
        try:
            entry.trace.mark("model_started")
            result = await entry.runner()
        except asyncio.CancelledError:
            # A replacement request owns the same conversation slot. The new
            # runner is started by submit() after the cancellation is observed.
            if entry.replacement is not None and not entry.cancel_reason:
                entry.runner = entry.replacement
                entry.replacement = None
                entry.started_at = time.monotonic()
                entry.trace.status = "running"
                entry.task = asyncio.create_task(
                    self._run_entry(conversation_id, state, entry)
                )
            elif not entry.future.done():
                entry.trace.status = "cancelled"
                entry.trace.error = entry.cancel_reason or "request_cancelled"
                entry.trace.mark("cancelled")
                entry.future.set_exception(ChatRunCancelledError(entry.trace.error))
            return
        except Exception as exc:
            entry.trace.status = "failed"
            entry.trace.error = str(exc)[:300]
            entry.trace.mark("failed")
            if not entry.future.done():
                entry.future.set_exception(exc)
        else:
            entry.trace.status = "completed"
            entry.trace.mark("completed")
            if not entry.future.done():
                entry.future.set_result(result)
        finally:
            _current_trace.reset(trace_token)
            async with state.lock:
                if state.active is entry and entry.future.done():
                    state.active = None
                    if state.queued:
                        next_entry = state.queued.popleft()
                        state.active = next_entry
                        asyncio.create_task(
                            self._run_entry(conversation_id, state, next_entry)
                        )
                    elif not state.queued:
                        async with self._states_lock:
                            if self._states.get(conversation_id) is state:
                                self._states.pop(conversation_id, None)


runtime_traces = RuntimeTraceStore()
chat_run_coordinator: ConversationRunCoordinator[Any] = ConversationRunCoordinator(runtime_traces)
_current_trace: ContextVar[TraceRecord | None] = ContextVar("mio_current_trace", default=None)


def mark_runtime_stage(stage: str, *, request_id: str = "") -> None:
    trace = _current_trace.get()
    if trace is None:
        return
    if request_id:
        trace.request_id = request_id
    trace.mark(stage)


def current_runtime_trace_id() -> str:
    trace = _current_trace.get()
    return trace.trace_id if trace is not None else ""


__all__ = [
    "ChatRunCancelledError",
    "ConversationRunCoordinator",
    "RuntimeTraceStore",
    "chat_run_coordinator",
    "current_runtime_trace_id",
    "mark_runtime_stage",
    "runtime_traces",
]
