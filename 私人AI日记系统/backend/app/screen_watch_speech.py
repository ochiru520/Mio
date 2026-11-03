"""Keep unspoken events while playback runs; acknowledge only completed speech."""
from __future__ import annotations

import threading
import time
from typing import Any


class ScreenWatchSpeechGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    @staticmethod
    def _source(status: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(status.get(key) for key in
                     ("mode", "hwnd", "screen_scope", "process_pid", "capture_epoch"))

    def reset(self) -> None:
        with self._lock:
            self._request_id = ""
            self._source_key: tuple[Any, ...] | None = None
            self._deadline = 0.0
            self._latest = False
            self._pending: dict[int, dict[str, Any]] = {}
            self._speaking_ids: tuple[int, ...] = ()

    def remember(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._pending[int(event["id"])] = dict(event)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            selected = sorted(self._pending.values(), key=lambda e: (e.get("importance", 0), e["id"]))[-12:]
            return [dict(e) for e in sorted(selected, key=lambda e: e["id"])]

    def consume(self, event_ids: list[int]) -> None:
        with self._lock:
            for event_id in event_ids:
                self._pending.pop(event_id, None)

    def begin(self, request_id: str, status: dict[str, Any], event_ids: list[int]) -> None:
        with self._lock:
            self._request_id = request_id
            self._source_key = self._source(status)
            # Recovery only if the renderer never reports completion. Normal
            # pacing is driven by voice_ended, never a text-length estimate.
            self._deadline = time.monotonic() + 180
            self._latest = False
            self._speaking_ids = tuple(event_ids)

    def finish(self, request_id: str, *, completed: bool = True) -> bool:
        with self._lock:
            if self._request_id and request_id == self._request_id:
                if completed:
                    for event_id in self._speaking_ids:
                        self._pending.pop(event_id, None)
                self._request_id = ""
                self._latest = True
                return True
            return False

    def disconnected(self) -> bool:
        with self._lock:
            if self._request_id:
                self._request_id = ""
                self._latest = True
                return True
            return False

    def check(self, status: dict[str, Any], *, enabled: bool) -> tuple[bool, bool]:
        with self._lock:
            source_key = self._source(status)
            if self._source_key is not None and self._source_key != source_key:
                # Events belong to the selected source, not an unrelated app.
                self._pending.clear()
                self._latest = True
            self._source_key = source_key
            if self._request_id and (
                not enabled or self._latest
                or time.monotonic() >= self._deadline
            ):
                self._request_id = ""
                self._latest = True
            return bool(self._request_id), self._latest

    def consumed(self) -> None:
        with self._lock:
            self._latest = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"waiting_for_speech": bool(self._request_id),
                    "speech_request_id": self._request_id,
                    "unspoken_events": len(self._pending),
                    "resume_from_latest": self._latest}
