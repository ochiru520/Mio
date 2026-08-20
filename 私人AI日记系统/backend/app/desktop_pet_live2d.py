from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_BASE = os.getenv("MIO_PET_API_BASE", "http://127.0.0.1:8000").rstrip("/")
SHOW_EVENT_NAME = "Local\\MioAgentDesktopShow-7C53C273"
PET_CHAT_EVENT_NAME = "Local\\MioAgentDesktopPetChat-7C53C273"
BASE_WINDOW_WIDTH = 280
BASE_WINDOW_HEIGHT = 400
DEFAULT_SIZE_PERCENT = 150


def _request_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 5,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result if isinstance(result, dict) else {}


def _normalize_size(value: object) -> int:
    try:
        return max(80, min(240, int(value or DEFAULT_SIZE_PERCENT)))
    except (TypeError, ValueError):
        return DEFAULT_SIZE_PERCENT


def _virtual_screen_bounds() -> tuple[int, int, int, int]:
    try:
        left = int(ctypes.windll.user32.GetSystemMetrics(76))
        top = int(ctypes.windll.user32.GetSystemMetrics(77))
        width = int(ctypes.windll.user32.GetSystemMetrics(78))
        height = int(ctypes.windll.user32.GetSystemMetrics(79))
        if width > 0 and height > 0:
            return left, top, width, height
    except (AttributeError, OSError, ValueError):
        pass
    return 0, 0, 1920, 1080


def _clamp_position(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    left, top, screen_width, screen_height = _virtual_screen_bounds()
    max_x = max(left, left + screen_width - width)
    max_y = max(top, top + screen_height - height)
    return max(left, min(max_x, int(x))), max(top, min(max_y, int(y)))


def _signal_event(name: str) -> bool:
    event_handle = ctypes.windll.kernel32.OpenEventW(0x0002, False, name)
    if not event_handle:
        return False
    ctypes.windll.kernel32.SetEvent(event_handle)
    ctypes.windll.kernel32.CloseHandle(event_handle)
    return True


class Live2DBridge:
    def __init__(self, *, x: int, y: int, width: int, height: int, size_percent: int) -> None:
        self._window = None
        self.x = int(x)
        self.y = int(y)
        self.width = int(width)
        self.height = int(height)
        self.size_percent = int(size_percent)
        self._drag_pointer: tuple[int, int] | None = None
        self._drag_window_origin: tuple[int, int] | None = None
        self._drag_stop: threading.Event | None = None
        self._lock = threading.Lock()

    def _bind_window(self, window: Any) -> None:
        self._window = window

    def begin_drag(self, screen_x: int, screen_y: int) -> dict[str, bool]:
        with self._lock:
            previous_stop = self._drag_stop
            if previous_stop is not None:
                previous_stop.set()
            pointer = (int(screen_x), int(screen_y))
            origin = (self.x, self.y)
            stop = threading.Event()
            self._drag_pointer = pointer
            self._drag_window_origin = origin
            self._drag_stop = stop
        threading.Thread(
            target=self._track_native_drag,
            args=(pointer, origin, stop),
            daemon=True,
            name="mio-live2d-drag",
        ).start()
        return {"ok": True}

    def _track_native_drag(
        self,
        pointer: tuple[int, int],
        origin: tuple[int, int],
        stop: threading.Event,
    ) -> None:
        point = wintypes.POINT()
        while not stop.is_set() and ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                next_x = origin[0] + int(point.x) - pointer[0]
                next_y = origin[1] + int(point.y) - pointer[1]
                with self._lock:
                    if self._drag_stop is not stop or self._window is None:
                        return
                    self.x, self.y = next_x, next_y
                    self._window.move(next_x, next_y)
            time.sleep(1 / 60)
        self._finish_drag(stop)

    def _finish_drag(self, stop: threading.Event | None = None) -> dict[str, bool]:
        with self._lock:
            if stop is not None and self._drag_stop is not stop:
                return {"ok": True}
            if self._drag_stop is not None:
                self._drag_stop.set()
            self._drag_stop = None
            self._drag_pointer = None
            self._drag_window_origin = None
            x, y = self.x, self.y
        try:
            _request_json(
                "/api/companion/position",
                method="PATCH",
                payload={"x": x, "y": y},
            )
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return {"ok": False}
        return {"ok": True}

    def drag_to(self, screen_x: int, screen_y: int) -> dict[str, bool]:
        with self._lock:
            pointer = self._drag_pointer
            if pointer is None or self._window is None:
                return {"ok": False}
            next_pointer = (int(screen_x), int(screen_y))
            self.x += next_pointer[0] - pointer[0]
            self.y += next_pointer[1] - pointer[1]
            self._drag_pointer = next_pointer
            self._window.move(self.x, self.y)
        return {"ok": True}

    def end_drag(self) -> dict[str, bool]:
        return self._finish_drag()

    def cursor_state(self) -> dict[str, float | bool]:
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return {"ok": False, "x": 0.0, "y": 0.0}
        center_x = self.x + self.width / 2
        center_y = self.y + self.height / 2
        return {
            "ok": True,
            "x": max(-1.0, min(1.0, (point.x - center_x) / max(180.0, self.width * 0.75))),
            "y": max(-1.0, min(1.0, (point.y - center_y) / max(220.0, self.height * 0.75))),
        }

    def resize_window(self, percent: int) -> dict[str, int | bool]:
        normalized = _normalize_size(percent)
        if normalized == self.size_percent or self._window is None:
            return {"ok": True, "percent": self.size_percent}
        next_width = round(BASE_WINDOW_WIDTH * normalized / 100)
        next_height = round(BASE_WINDOW_HEIGHT * normalized / 100)
        center_x = self.x + self.width / 2
        center_y = self.y + self.height / 2
        next_x, next_y = _clamp_position(
            round(center_x - next_width / 2),
            round(center_y - next_height / 2),
            next_width,
            next_height,
        )
        with self._lock:
            self.x, self.y = next_x, next_y
            self.width, self.height = next_width, next_height
            self.size_percent = normalized
            self._window.resize(next_width, next_height)
            self._window.move(next_x, next_y)
        return {"ok": True, "percent": normalized}

    def set_size(self, percent: int) -> dict[str, int | bool]:
        result = self.resize_window(percent)
        try:
            _request_json(
                "/api/companion/size",
                method="PATCH",
                payload={"percent": self.size_percent},
            )
            _request_json(
                "/api/companion/position",
                method="PATCH",
                payload={"x": self.x, "y": self.y},
            )
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return {"ok": False, "percent": self.size_percent}
        return result

    def open_chat(self) -> dict[str, bool]:
        if _signal_event(PET_CHAT_EVENT_NAME):
            return {"ok": True}
        if _signal_event(SHOW_EVENT_NAME):
            return {"ok": True}
        try:
            os.startfile(f"{API_BASE}/agent-app/#desktop-pet-chat")
        except OSError:
            return {"ok": False}
        return {"ok": True}

    def open_agent(self) -> dict[str, bool]:
        if _signal_event(SHOW_EVENT_NAME):
            return {"ok": True}
        try:
            os.startfile(f"{API_BASE}/agent-app/")
        except OSError:
            return {"ok": False}
        return {"ok": True}

    def say_hello(self) -> dict[str, bool]:
        try:
            _request_json(
                "/api/companion/voice/speak",
                method="POST",
                payload={"text": "我在这里", "emotion": "gentle"},
                timeout=20,
            )
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return {"ok": False}
        return {"ok": True}

    def toggle_observation(self) -> dict[str, Any]:
        try:
            status = _request_json("/api/companion/status")
            screen = status.get("screen") or status.get("window") or {}
            running = bool(screen.get("running"))
            if running:
                _request_json("/api/companion/screen/stop", method="POST", payload={})
            else:
                _request_json(
                    "/api/companion/screen/start",
                    method="POST",
                    payload={"scope": "primary", "interval_ms": 1000},
                )
            return {"ok": True, "running": not running}
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return {"ok": False, "running": False}

    def close_pet(self) -> dict[str, bool]:
        window = self._window
        if window is not None:
            threading.Timer(0.05, window.destroy).start()
        return {"ok": True}


def _storage_path() -> Path:
    configured = os.getenv("MIO_DESKTOP_STATE_DIR", "").strip()
    if configured:
        root = Path(configured)
    elif Path("D:/").exists():
        root = Path("D:/Mio数据")
    else:
        root = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "MioAgent"
    path = root / "Live2D桌宠WebView数据"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _enable_windows_chroma_transparency(window: Any) -> None:
    """Use the WinForms transparency key behind the transparent WebView canvas."""
    try:
        from System import Action
        from System.Drawing import Color

        form = window.native
        key_color = Color.FromArgb(255, 255, 0, 255)

        def apply_key() -> None:
            form.BackColor = key_color
            form.TransparencyKey = key_color

        if form.InvokeRequired:
            form.Invoke(Action(apply_key))
        else:
            apply_key()
    except Exception:
        # pywebview transparency still provides a usable fallback if the
        # installed WinForms runtime does not expose TransparencyKey.
        return


def main(config: dict[str, Any] | None = None) -> int:
    import webview

    pet_config = dict(config or {})
    percent = _normalize_size(pet_config.get("pet_size_percent"))
    width = round(BASE_WINDOW_WIDTH * percent / 100)
    height = round(BASE_WINDOW_HEIGHT * percent / 100)
    x, y = _clamp_position(
        int(pet_config.get("position_x", 80)),
        int(pet_config.get("position_y", 420)),
        width,
        height,
    )
    bridge = Live2DBridge(
        x=x,
        y=y,
        width=width,
        height=height,
        size_percent=percent,
    )
    window = webview.create_window(
        "Mio Live2D 桌宠",
        f"{API_BASE}/agent-app/live2d-pet/index.html?runtime=pywebview",
        js_api=bridge,
        width=width,
        height=height,
        x=x,
        y=y,
        frameless=True,
        transparent=True,
        on_top=True,
        easy_drag=False,
        shadow=False,
        background_color="#000000",
    )
    bridge._bind_window(window)
    window.events.loaded += lambda: _enable_windows_chroma_transparency(window)

    def cleanup() -> None:
        bridge.end_drag()

    window.events.closed += cleanup
    webview.start(
        gui="edgechromium",
        debug=False,
        private_mode=False,
        storage_path=str(_storage_path()),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
