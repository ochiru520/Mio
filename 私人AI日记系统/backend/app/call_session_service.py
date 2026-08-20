from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import threading
import time
from typing import Any, Callable, TypeVar
import uuid


T = TypeVar("T")


class CallSessionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class CallTurnToken:
    call_session_id: str
    generation: int
    turn_id: int
    response_id: str


class CallSessionManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation = 0
        self._state: dict[str, Any] = self._empty_state()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "active": False,
            "call_session_id": "",
            "generation": 0,
            "next_turn_id": 1,
            "current_turn_id": 0,
            "current_response_id": "",
            "stage": "idle",
            "started_at": "",
            "updated_at": "",
            "ended_at": "",
            "transcript": "",
            "reply": "",
            "error": "",
            "device": {},
            "asr": {},
            "voice_started": {},
            "voice_ended": {},
            "last_interrupted_response_id": "",
            "last_interrupted_text": "",
            "last_interrupted_monotonic": 0.0,
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            self._generation += 1
            now = self._now_iso()
            self._state = {
                **self._empty_state(),
                "active": True,
                "call_session_id": uuid.uuid4().hex,
                "generation": self._generation,
                "stage": "connecting",
                "started_at": now,
                "updated_at": now,
            }
            return self._public_state_unlocked()

    def stop(self, call_session_id: str = "", *, reason: str = "call_ended") -> bool:
        with self._lock:
            if not self._state["active"]:
                return False
            if call_session_id and call_session_id != self._state["call_session_id"]:
                return False
            now = self._now_iso()
            self._state.update(
                {
                    "active": False,
                    "stage": "ended",
                    "error": str(reason or "call_ended")[:200],
                    "updated_at": now,
                    "ended_at": now,
                }
            )
            self._generation += 1
            self._state["generation"] = self._generation
            return True

    def fail_start(self, call_session_id: str, error: str) -> bool:
        with self._lock:
            if not self._state["active"] or call_session_id != self._state["call_session_id"]:
                return False
            now = self._now_iso()
            self._state.update(
                {
                    "active": False,
                    "stage": "error",
                    "error": str(error or "call_start_failed")[:500],
                    "updated_at": now,
                    "ended_at": now,
                }
            )
            self._generation += 1
            self._state["generation"] = self._generation
            return True

    def mark_listening(self, call_session_id: str) -> bool:
        with self._lock:
            if self._state["active"] and call_session_id == self._state["call_session_id"]:
                self._state["stage"] = "listening"
                self._state["updated_at"] = self._now_iso()
                return True
            return False

    def is_active_session(self, call_session_id: str) -> bool:
        with self._lock:
            return bool(
                self._state["active"]
                and call_session_id
                and call_session_id == self._state["call_session_id"]
            )

    def begin_turn(self, call_session_id: str, turn_id: int = 0) -> CallTurnToken:
        with self._lock:
            if not self._state["active"]:
                raise CallSessionConflict("电话尚未接通。")
            if call_session_id and call_session_id != self._state["call_session_id"]:
                raise CallSessionConflict("电话会话已经变化，请忽略这段旧录音。")
            expected = int(self._state["next_turn_id"])
            requested = int(turn_id or expected)
            if requested != expected:
                raise CallSessionConflict(f"电话轮次已失效：期望 {expected}，收到 {requested}。")
            response_id = f"call-{self._state['call_session_id']}-{requested}-{uuid.uuid4().hex[:12]}"
            self._state.update(
                {
                    "next_turn_id": expected + 1,
                    "current_turn_id": requested,
                    "current_response_id": response_id,
                    "stage": "asr",
                    "transcript": "",
                    "reply": "",
                    "error": "",
                    "asr": {},
                    "voice_started": {},
                    "voice_ended": {},
                    "updated_at": self._now_iso(),
                }
            )
            return CallTurnToken(
                call_session_id=str(self._state["call_session_id"]),
                generation=int(self._state["generation"]),
                turn_id=requested,
                response_id=response_id,
            )

    def _is_current_unlocked(self, token: CallTurnToken) -> bool:
        return bool(
            self._state["active"]
            and token.call_session_id == self._state["call_session_id"]
            and token.generation == self._state["generation"]
            and token.turn_id == self._state["current_turn_id"]
            and token.response_id == self._state["current_response_id"]
        )

    def is_current(self, token: CallTurnToken) -> bool:
        with self._lock:
            return self._is_current_unlocked(token)

    def require_current(self, token: CallTurnToken) -> None:
        with self._lock:
            if not self._is_current_unlocked(token):
                raise CallSessionConflict("电话会话已结束或轮次已被替换。")

    def update_turn(self, token: CallTurnToken, stage: str, **fields: Any) -> None:
        with self._lock:
            if not self._is_current_unlocked(token):
                raise CallSessionConflict("电话会话已结束或轮次已被替换。")
            allowed = {"transcript", "reply", "error", "asr"}
            self._state.update({key: value for key, value in fields.items() if key in allowed})
            self._state["stage"] = str(stage or self._state["stage"])[:50]
            self._state["updated_at"] = self._now_iso()

    def commit_if_current(self, token: CallTurnToken, callback: Callable[[], T]) -> T:
        with self._lock:
            if not self._is_current_unlocked(token):
                raise CallSessionConflict("电话会话已结束，迟到回复不会写入历史。")
            result = callback()
            self._state["stage"] = "awaiting_voice"
            self._state["updated_at"] = self._now_iso()
            return result

    def set_device(self, call_session_id: str, device: dict[str, Any]) -> bool:
        with self._lock:
            if not self._state["active"] or call_session_id != self._state["call_session_id"]:
                return False
            self._state["device"] = dict(device)
            self._state["updated_at"] = self._now_iso()
            return True

    def interrupt(self, call_session_id: str = "", response_id: str = "") -> str:
        with self._lock:
            if not self._state["active"]:
                return ""
            if call_session_id and call_session_id != self._state["call_session_id"]:
                return ""
            active_response_id = str(self._state["current_response_id"] or "")
            if response_id and response_id != active_response_id:
                return ""
            if not active_response_id:
                return ""
            self._state["last_interrupted_response_id"] = active_response_id
            self._state["last_interrupted_text"] = str(self._state["reply"] or "")
            self._state["last_interrupted_monotonic"] = time.monotonic()
            self._state["stage"] = "listening"
            self._state["updated_at"] = self._now_iso()
            return active_response_id

    def echo_reference(self, token: CallTurnToken, max_age_seconds: float = 8.0) -> str:
        with self._lock:
            if not self._is_current_unlocked(token):
                return ""
            age = time.monotonic() - float(self._state["last_interrupted_monotonic"] or 0)
            if age < 0 or age > max(0.1, float(max_age_seconds)):
                return ""
            return str(self._state["last_interrupted_text"] or "")

    def record_voice_started(self, response_id: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            if not self._state["active"] or response_id != self._state["current_response_id"]:
                return False
            self._state["voice_started"] = {
                "received_at": self._now_iso(),
                "response_id": response_id,
                "mode": str(payload.get("mode") or "")[:50],
                "first_audio_latency_ms": payload.get("first_audio_latency_ms"),
            }
            self._state["stage"] = "speaking"
            self._state["updated_at"] = self._now_iso()
            return True

    def record_voice_ended(self, response_id: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            if not self._state["active"] or response_id != self._state["current_response_id"]:
                return False
            reason = str(payload.get("reason") or "finished")[:80]
            self._state["voice_ended"] = {
                "received_at": self._now_iso(),
                "response_id": response_id,
                "reason": reason,
            }
            self._state["stage"] = "listening" if reason != "error" else "error"
            self._state["error"] = "" if reason != "error" else "语音播放失败"
            self._state["updated_at"] = self._now_iso()
            return True

    def _public_state_unlocked(self) -> dict[str, Any]:
        state = dict(self._state)
        state.pop("generation", None)
        state.pop("last_interrupted_monotonic", None)
        state["device"] = dict(self._state.get("device") or {})
        state["asr"] = dict(self._state.get("asr") or {})
        state["voice_started"] = dict(self._state.get("voice_started") or {})
        state["voice_ended"] = dict(self._state.get("voice_ended") or {})
        state["has_recent_echo_reference"] = bool(
            self._state.get("last_interrupted_text")
            and time.monotonic() - float(self._state.get("last_interrupted_monotonic") or 0) <= 8.0
        )
        state.pop("last_interrupted_text", None)
        return state

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._public_state_unlocked()


manager = CallSessionManager()


__all__ = [
    "CallSessionConflict",
    "CallSessionManager",
    "CallTurnToken",
    "manager",
]
