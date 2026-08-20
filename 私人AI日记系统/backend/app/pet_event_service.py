from __future__ import annotations

import asyncio
import ctypes
from datetime import datetime
import os
import threading
import time
from ctypes import wintypes
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from . import db


DESKTOP_PET_CONVERSATION_ID = "desktop_pet"
POLL_INTERVAL_SECONDS = 0.25
FOREGROUND_INTERVAL_SECONDS = 0.75
SPEECH_SOURCE_PRIORITIES: dict[str, int] = {
    "desktop_pet": 100,
    "desktop_pet_call": 105,
    "desktop": 95,
    "web": 95,
    "qq": 95,
    "startup": 70,
    "desktop_pet_wake": 68,
    "game": 62,
    "screen": 56,
    "proactive": 35,
    "desktop_proactive": 35,
}


def speech_priority(source: str) -> int:
    normalized = str(source or "").strip().lower()
    if normalized.startswith("qq_group"):
        return 90
    return SPEECH_SOURCE_PRIORITIES.get(normalized, 75)


def speech_timeline(rows: list[Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    offset_ms = 0
    for index, row in enumerate(rows):
        text = " ".join(str(row["content"] or "").split())
        if not text:
            continue
        duration_hint_ms = max(900, min(12_000, len(text) * 185))
        timeline.append(
            {
                "index": index,
                "message_id": int(row["id"]),
                "text": text,
                "offset_ms": offset_ms,
                "duration_hint_ms": duration_hint_ms,
            }
        )
        offset_ms += duration_hint_ms + 220
    return timeline


def foreground_window_snapshot() -> dict[str, Any]:
    if os.name != "nt":
        return {"hwnd": None, "title": ""}
    user32 = ctypes.windll.user32
    hwnd = int(user32.GetForegroundWindow() or 0)
    if not hwnd:
        return {"hwnd": None, "title": ""}
    length = int(user32.GetWindowTextLengthW(hwnd) or 0)
    title = ""
    if length > 0:
        buffer = ctypes.create_unicode_buffer(min(length, 500) + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value.strip()[:500]
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    normalized_process_id = int(process_id.value or 0)
    return {
        "hwnd": hwnd,
        "title": title,
        "process_id": normalized_process_id,
        "process_name": _foreground_process_name(normalized_process_id),
    }


def _foreground_process_name(process_id: int) -> str:
    if os.name != "nt" or process_id <= 0:
        return ""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value).strip()[:260]
    except (AttributeError, OSError):
        return ""
    finally:
        kernel32.CloseHandle(handle)


class PetEventHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._client_info: dict[WebSocket, dict[str, Any]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cursor: int | None = None
        self._lock = threading.Lock()
        self._last_connected_at = ""
        self._last_disconnected_at = ""
        self._last_event_at = ""
        self._last_error = ""
        self._renderer_errors: list[str] = []
        self._interaction_count = 0
        self._renderer: dict[str, Any] = {}
        self._foreground: dict[str, Any] = {}
        self._last_voice_started: dict[str, Any] = {}
        self._last_voice_ended: dict[str, Any] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def has_clients(self) -> bool:
        return self.client_count() > 0

    def _desktop_renderer_count_unlocked(self) -> int:
        return sum(
            1
            for client in self._clients
            if str(self._client_info.get(client, {}).get("runtime") or "")
            in {"electron", "pywebview"}
        )

    def desktop_renderer_count(self) -> int:
        with self._lock:
            return self._desktop_renderer_count_unlocked()

    def has_desktop_renderer(self) -> bool:
        return self.desktop_renderer_count() > 0

    def status(self) -> dict[str, Any]:
        with self._lock:
            desktop_renderer_count = self._desktop_renderer_count_unlocked()
            return {
                "connected": bool(self._clients),
                "connection_count": len(self._clients),
                "desktop_renderer_connected": desktop_renderer_count > 0,
                "desktop_renderer_count": desktop_renderer_count,
                "last_connected_at": self._last_connected_at,
                "last_disconnected_at": self._last_disconnected_at,
                "last_event_at": self._last_event_at,
                "last_error": self._last_error,
                "renderer_errors": list(self._renderer_errors[-5:]),
                "interaction_count": self._interaction_count,
                "renderer": dict(self._renderer),
                "foreground": dict(self._foreground),
                "last_voice_started": dict(self._last_voice_started),
                "last_voice_ended": dict(self._last_voice_ended),
            }

    async def register(self, websocket: WebSocket) -> None:
        await websocket.accept()
        with self._lock:
            was_empty = not self._clients
            self._loop = asyncio.get_running_loop()
            self._clients.add(websocket)
            self._client_info[websocket] = {
                "runtime": "unknown",
                "connected_at": self._now(),
            }
            self._last_connected_at = self._now()
            self._last_error = ""
            if was_empty:
                self._cursor = db.get_latest_message_id(
                    role="assistant",
                    conversation_id=DESKTOP_PET_CONVERSATION_ID,
                )

    async def unregister(self, websocket: WebSocket) -> None:
        with self._lock:
            self._clients.discard(websocket)
            self._client_info.pop(websocket, None)
            self._last_disconnected_at = self._now()

    async def send(self, websocket: WebSocket, event_type: str, payload: dict[str, Any]) -> bool:
        try:
            await websocket.send_json(
                {"type": event_type, "timestamp": self._now(), "payload": payload}
            )
            with self._lock:
                self._last_event_at = self._now()
            return True
        except (OSError, RuntimeError, WebSocketDisconnect):
            await self.unregister(websocket)
            return False

    async def broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        await asyncio.gather(
            *(self.send(client, event_type, payload) for client in clients),
            return_exceptions=True,
        )

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            loop = self._loop
            has_clients = bool(self._clients)
        if loop is None or loop.is_closed() or not has_clients:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(event_type, payload), loop)

    async def serve(self, websocket: WebSocket) -> None:
        await self.register(websocket)
        from . import companion_service, screen_observation_service

        await self.send(
            websocket,
            "ready",
            {
                "protocol_version": 3,
                "settings": companion_service.load_config(),
                "activity": companion_service.pet_activity_status(),
                "screen": screen_observation_service.status(),
            },
        )
        try:
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                event_type = str(message.get("type") or "").strip().lower()
                payload = message.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                if event_type == "ping":
                    await self.send(websocket, "pong", {"monotonic": time.monotonic()})
                elif event_type in {"clicked", "double_clicked", "dragged", "interaction"}:
                    with self._lock:
                        self._interaction_count += 1
                    companion_service.set_pet_activity(
                        "listening",
                        emotion=str(payload.get("emotion") or "gentle"),
                        source="live2d_interaction",
                        ttl_seconds=3,
                    )
                elif event_type == "chat_requested":
                    companion_service.signal_pet_chat_window(payload)
                elif event_type == "agent_requested":
                    companion_service.signal_agent_window()
                elif event_type == "renderer_error":
                    error = str(payload.get("message") or "Live2D 渲染器报告未知错误")[:500]
                    with self._lock:
                        self._last_error = error
                        self._renderer_errors.append(error)
                        self._renderer_errors = self._renderer_errors[-20:]
                elif event_type == "renderer_ready":
                    with self._lock:
                        runtime = str(payload.get("runtime") or "unknown")[:30]
                        self._client_info[websocket] = {
                            "runtime": runtime,
                            "model_id": str(payload.get("model_id") or "")[:100],
                            "ready_at": self._now(),
                        }
                        self._last_error = ""
                        self._renderer = {
                            "runtime": runtime,
                            "model_id": str(payload.get("model_id") or "")[:100],
                            "model_name": str(payload.get("model_name") or "")[:200],
                            "capabilities": payload.get("capabilities")
                            if isinstance(payload.get("capabilities"), dict)
                            else {},
                            "ready_at": self._now(),
                        }
                elif event_type == "voice_started":
                    response_id = str(payload.get("response_id") or "")[:100]
                    with self._lock:
                        self._last_voice_started = {
                            "received_at": self._now(),
                            "request_id": str(payload.get("request_id") or "")[:100],
                            "response_id": response_id,
                            "mode": str(payload.get("mode") or "")[:50],
                            "first_audio_latency_ms": payload.get("first_audio_latency_ms"),
                        }
                    from . import call_session_service

                    call_session_service.manager.record_voice_started(response_id, payload)
                    companion_service.set_pet_activity(
                        "speaking",
                        emotion=str(payload.get("emotion") or "neutral"),
                        source="electron_live2d",
                        ttl_seconds=90,
                    )
                elif event_type == "voice_ended":
                    response_id = str(payload.get("response_id") or "")[:100]
                    with self._lock:
                        self._last_voice_ended = {
                            "received_at": self._now(),
                            "request_id": str(payload.get("request_id") or "")[:100],
                            "response_id": response_id,
                            "reason": str(payload.get("reason") or "")[:80],
                        }
                    from . import call_session_service

                    call_session_service.manager.record_voice_ended(response_id, payload)
                    companion_service.set_pet_activity(
                        "idle",
                        source="electron_live2d",
                        ttl_seconds=0,
                    )
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            await self.unregister(websocket)

    @staticmethod
    def _should_speak(source: str, config: dict[str, Any]) -> bool:
        if not bool(config.get("voice_enabled", True)):
            return False
        if source in {"proactive", "desktop_proactive"}:
            return bool(config.get("speak_proactive", False))
        if source == "screen":
            return bool(config.get("speak_screen_observations", False))
        if source in {"game", "desktop_pet_wake"}:
            return bool(config.get("speak_game_observations", True))
        return True

    async def message_loop(self) -> None:
        while True:
            try:
                if not self.has_clients():
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue
                with self._lock:
                    cursor = self._cursor
                if cursor is None:
                    cursor = db.get_latest_message_id(
                        role="assistant",
                        conversation_id=DESKTOP_PET_CONVERSATION_ID,
                    )
                    with self._lock:
                        self._cursor = cursor
                rows = db.get_messages_after_id(
                    cursor,
                    role="assistant",
                    limit=50,
                    conversation_id=DESKTOP_PET_CONVERSATION_ID,
                )
                if not rows:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue
                await asyncio.sleep(0.12)
                rows = db.get_messages_after_id(
                    cursor,
                    role="assistant",
                    limit=50,
                    conversation_id=DESKTOP_PET_CONVERSATION_ID,
                )
                from . import companion_service

                config = companion_service.load_config()
                index = 0
                while index < len(rows):
                    row = rows[index]
                    request_id = str(row["request_id"] or "")
                    grouped = [row]
                    index += 1
                    while (
                        request_id
                        and index < len(rows)
                        and str(rows[index]["request_id"] or "") == request_id
                    ):
                        grouped.append(rows[index])
                        index += 1
                    text = " ".join(
                        str(item["content"] or "").strip()
                        for item in grouped
                        if str(item["content"] or "").strip()
                    )
                    if not text:
                        continue
                    source = str(grouped[0]["source"] or "")
                    response_id = request_id or f"message-{int(grouped[-1]['id'])}"
                    priority = speech_priority(source)
                    emotion = str(
                        grouped[0]["emotion"]
                        or companion_service.speech_emotion_info(text)["id"]
                    )
                    try:
                        model_id = str(grouped[0]["model_id"] or "")
                    except (KeyError, IndexError):
                        model_id = ""
                    await self.broadcast(
                        "speak",
                        {
                            "message_id": int(grouped[-1]["id"]),
                            "request_id": request_id,
                            "response_id": response_id,
                            "priority": priority,
                            "interruptible": True,
                            "timeline": speech_timeline(grouped),
                            "text": text,
                            "source": source,
                            "emotion": emotion,
                            "model_id": model_id,
                            "should_speak": self._should_speak(source, config),
                            "created_at": str(grouped[0]["created_at"] or ""),
                        },
                    )
                with self._lock:
                    self._cursor = int(rows[-1]["id"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._last_error = f"桌宠事件循环异常：{exc}"
                await asyncio.sleep(1.0)

    async def foreground_loop(self) -> None:
        previous_hwnd: int | None = None
        previous_title = ""
        while True:
            try:
                if not self.has_clients():
                    previous_hwnd = None
                    previous_title = ""
                    await asyncio.sleep(FOREGROUND_INTERVAL_SECONDS)
                    continue
                snapshot = foreground_window_snapshot()
                hwnd = snapshot.get("hwnd")
                title = str(snapshot.get("title") or "")
                if hwnd != previous_hwnd or title != previous_title:
                    previous_hwnd = int(hwnd) if hwnd else None
                    previous_title = title
                    payload = {
                        **snapshot,
                        "changed_at": self._now(),
                    }
                    with self._lock:
                        self._foreground = dict(payload)
                    await self.broadcast("foreground_changed", payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._last_error = f"前台窗口感知异常：{exc}"
            await asyncio.sleep(FOREGROUND_INTERVAL_SECONDS)


hub = PetEventHub()


def has_clients() -> bool:
    return hub.has_clients()


def has_desktop_renderer() -> bool:
    return hub.has_desktop_renderer()


def publish(event_type: str, payload: dict[str, Any]) -> None:
    hub.publish(event_type, payload)


def status() -> dict[str, Any]:
    return hub.status()


async def serve(websocket: WebSocket) -> None:
    await hub.serve(websocket)


async def event_loop() -> None:
    await asyncio.gather(hub.message_loop(), hub.foreground_loop())
