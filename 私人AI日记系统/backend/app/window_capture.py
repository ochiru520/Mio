"""Capture pixels belonging to one HWND, excluding desktop overlays."""
from __future__ import annotations

import threading
import time

from PIL import Image


class WindowCapture:
    def __init__(self, hwnd: int, *, capture_factory=None) -> None:
        if capture_factory is None:
            try:
                from windows_capture import WindowsCapture
            except ImportError as exc:
                raise RuntimeError("缺少窗口捕获组件 windows-capture，无法安全读取所选窗口。") from exc
            capture_factory = WindowsCapture
        self.hwnd = int(hwnd)
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._image: Image.Image | None = None
        self._received = 0.0
        self._closed = False
        self._error = ""
        self._capture = capture_factory(
            cursor_capture=False, draw_border=False, monitor_index=None,
            window_name=None, window_hwnd=self.hwnd,
        )

        @self._capture.event
        def on_frame_arrived(frame, control):
            now = time.monotonic()
            with self._lock:
                if self._closed or now - self._received < 0.2:
                    return
            try:
                # The native buffer expires after this callback. Own the pixels.
                image = Image.frombytes("RGB", (frame.width, frame.height),
                                        frame.frame_buffer.tobytes(), "raw", "BGRX")
                image.thumbnail((1280, 720), Image.Resampling.BILINEAR)
                image.info["mio_capture_monotonic"] = now
                image.info["mio_captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                with self._lock:
                    if not self._closed:
                        self._image = image
                        self._received = now
            except Exception as exc:
                with self._lock:
                    self._error = f"读取窗口画面失败：{exc}"
            self._ready.set()

        @self._capture.event
        def on_closed():
            with self._lock:
                self._closed = True
            self._ready.set()

        self._control = self._capture.start_free_threaded()

    def capture(self) -> Image.Image:
        if not self._ready.wait(3.0):
            raise RuntimeError("所选窗口未提供画面；不会改为捕获桌面。")
        with self._lock:
            if self._closed or self._error or self._image is None:
                raise RuntimeError(self._error or "所选窗口捕获已经结束；请重新选择窗口。")
            return self._image.copy()

    def release(self) -> None:
        with self._lock:
            self._closed = True
            self._image = None
        self._ready.set()
        self._control.stop()
