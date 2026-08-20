from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab

from .config import settings
from .screen_capture import create_compatible_capture, create_native_capture, create_obs_capture
from .screen_frame_processor import calculate_change_metrics


class WindowObserver:
    """Capture a selected window or screen without writing frames to disk."""

    def __init__(self, *, prefer_native: bool = True) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._desired_running = False
        self._hwnd = 0
        self._title = ""
        self._change_percent = 0.0
        self._global_change_percent = 0.0
        self._local_change_percent = 0.0
        self._captured_at = ""
        self._last_thumbnail: Image.Image | None = None
        self._preview_bytes: bytes | None = None
        self._analysis_bytes: bytes | None = None
        self._analysis_change_percent = 0.0
        self._analysis_global_change_percent = 0.0
        self._analysis_local_change_percent = 0.0
        self._cursor: dict[str, Any] = {
            "available": False,
            "inside_capture": False,
            "screen_x": 0,
            "screen_y": 0,
            "relative_x": None,
            "relative_y": None,
        }
        self._frame_id = 0
        self._last_error = ""
        self._capture_backend_error = ""
        self._prefer_native = prefer_native
        self._native_capture = None
        self._native_capture_failed = False
        self._native_capture_retry_at = 0.0
        self._obs_capture = None
        self._screen_capture = None
        self._screen_capture_failed = False
        self._screen_capture_retry_at = 0.0
        self._capture_backend = "imagegrab"
        self._last_frame_monotonic: float | None = None
        # A preview may remain visible after a capture failure, but it must
        # never be treated as a fresh frame for analysis.  Keep this separate
        # from preview availability so stale pixels cannot reach a model.
        self._capture_valid = False
        self._interval_seconds = 1.0
        self._mode = "screen"
        self._screen_scope = "primary"
        preference = os.getenv("MIO_CAPTURE_BACKEND", "auto").strip().lower()
        self._capture_preference = preference if preference in {"auto", "obs"} else "auto"

    def _capture_with_obs(
        self,
        *,
        region: tuple[int, int, int, int] | None,
        all_screens: bool,
    ) -> Image.Image:
        if self._obs_capture is None:
            self._obs_capture = create_obs_capture()
        if self._obs_capture is None:
            raise RuntimeError("OBS备用捕获尚未配置地址和源名称")
        return self._obs_capture.capture(region=region, all_screens=all_screens).convert("RGB")

    def list_windows(self) -> list[dict[str, Any]]:
        if os.name != "nt":
            return []
        user32 = ctypes.windll.user32
        records: list[dict[str, Any]] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @callback_type
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            rect = wintypes.RECT()
            if not title or not user32.GetClientRect(hwnd, ctypes.byref(rect)):
                return True
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width >= 320 and height >= 200:
                records.append({"hwnd": int(hwnd), "title": title, "width": width, "height": height})
            return True

        user32.EnumWindows(callback, 0)
        return sorted(records, key=lambda item: item["title"].lower())

    def select(self, hwnd: int) -> dict[str, Any]:
        match = next((item for item in self.list_windows() if item["hwnd"] == int(hwnd)), None)
        if match is None:
            raise ValueError("没有找到这个窗口，请刷新窗口列表后重试。")
        with self._lock:
            self._hwnd = int(hwnd)
            self._title = str(match["title"])
            self._mode = "window"
            self._last_thumbnail = None
            self._preview_bytes = None
            self._analysis_bytes = None
            self._analysis_change_percent = 0.0
            self._analysis_global_change_percent = 0.0
            self._analysis_local_change_percent = 0.0
            self._capture_valid = False
            self._last_frame_monotonic = None
            self._last_error = ""
            self._native_capture_failed = False
            self._native_capture_retry_at = 0.0
            self._screen_capture_failed = False
            self._screen_capture_retry_at = 0.0
        self.capture()
        return self.status()

    def select_screen(self, scope: str = "primary") -> dict[str, Any]:
        normalized_scope = "all" if scope == "all" else "primary"
        with self._lock:
            self._hwnd = 0
            self._mode = "screen"
            self._screen_scope = normalized_scope
            self._title = "全部屏幕" if normalized_scope == "all" else "主屏幕"
            self._last_thumbnail = None
            self._preview_bytes = None
            self._analysis_bytes = None
            self._analysis_change_percent = 0.0
            self._analysis_global_change_percent = 0.0
            self._analysis_local_change_percent = 0.0
            self._capture_valid = False
            self._last_frame_monotonic = None
            self._last_error = ""
        self.capture()
        return self.status()

    def _client_bbox(self, hwnd: int) -> tuple[int, int, int, int]:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
            raise RuntimeError("所选窗口已经关闭或最小化。")
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise RuntimeError("无法读取所选窗口的位置。")
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 2 or height < 2:
            raise RuntimeError("所选窗口当前没有可截图的画面。")
        return origin.x, origin.y, origin.x + width, origin.y + height

    @staticmethod
    def _cursor_metadata(
        region: tuple[int, int, int, int] | None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": False,
            "inside_capture": False,
            "screen_x": 0,
            "screen_y": 0,
            "relative_x": None,
            "relative_y": None,
        }
        if os.name != "nt":
            return result
        point = wintypes.POINT()
        try:
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                return result
        except (AttributeError, OSError):
            return result
        result.update({"available": True, "screen_x": int(point.x), "screen_y": int(point.y)})
        if region is None:
            return result
        left, top, right, bottom = region
        width = max(1, right - left)
        height = max(1, bottom - top)
        result.update(
            {
                "inside_capture": left <= point.x < right and top <= point.y < bottom,
                "relative_x": round((point.x - left) / width, 4),
                "relative_y": round((point.y - top) / height, 4),
            }
        )
        return result

    def capture(self) -> dict[str, Any]:
        with self._lock:
            hwnd = self._hwnd
            mode = self._mode
            screen_scope = self._screen_scope
        now = time.monotonic()
        if mode == "window" and self._native_capture_failed and now >= self._native_capture_retry_at:
            self._native_capture_failed = False
        if mode == "window" and self._native_capture is None and self._prefer_native and not self._native_capture_failed:
            self._native_capture = create_native_capture()
        if mode == "window" and not hwnd:
            raise ValueError("请先选择一个窗口。")
        try:
            native_region = self._client_bbox(hwnd) if mode == "window" else None
            cursor = self._cursor_metadata(native_region)
            image: Image.Image | None = None
            backend = ""
            obs_error = ""
            if self._capture_preference == "obs":
                try:
                    image = self._capture_with_obs(
                        region=native_region,
                        all_screens=mode == "screen" and screen_scope == "all",
                    )
                    backend = "obs"
                    with self._lock:
                        self._capture_backend_error = ""
                except Exception as obs_exc:
                    obs_error = str(obs_exc)
                    if self._obs_capture is not None:
                        self._obs_capture.release()
                        self._obs_capture = None
                    with self._lock:
                        self._capture_backend_error = f"OBS：{obs_error}"

            # DXGI is reserved for window capture. Whole-screen capture stays on
            # the compatibility path because it is safer with WebView2 overlays.
            use_native_capture = self._native_capture is not None and mode == "window"
            if image is None and use_native_capture:
                try:
                    image = self._native_capture.capture(region=native_region, all_screens=False).convert("RGB")
                    backend = "dxgi"
                    with self._lock:
                        self._capture_backend_error = ""
                except Exception as native_exc:
                    failed_capture = self._native_capture
                    self._native_capture = None
                    self._native_capture_failed = True
                    self._native_capture_retry_at = now + 10.0
                    if failed_capture is not None:
                        failed_capture.release()
                    with self._lock:
                        self._capture_backend_error = f"{native_exc}；本次窗口观察已改用兼容捕获"
                    image = ImageGrab.grab(bbox=native_region, all_screens=True).convert("RGB")
                    backend = "imagegrab (降级)"
            elif image is None and mode == "screen":
                compatible_error = ""
                now = time.monotonic()
                if self._screen_capture_failed and now >= self._screen_capture_retry_at:
                    self._screen_capture_failed = False
                if self._screen_capture is None and not self._screen_capture_failed:
                    self._screen_capture = create_compatible_capture()
                    if self._screen_capture is None:
                        self._screen_capture_failed = True
                if self._screen_capture is not None and not self._screen_capture_failed:
                    try:
                        image = self._screen_capture.capture(all_screens=screen_scope == "all").convert("RGB")
                        backend = "mss (兼容模式)"
                        with self._lock:
                            self._capture_backend_error = ""
                    except Exception as compatible_exc:
                        self._screen_capture_failed = True
                        self._screen_capture_retry_at = now + 10.0
                        compatible_error = str(compatible_exc)
                        failed_capture = self._screen_capture
                        self._screen_capture = None
                        if failed_capture is not None:
                            failed_capture.release()
                        with self._lock:
                            self._capture_backend_error = compatible_error
                if self._screen_capture_failed:
                    if self._native_capture is None and self._prefer_native and not self._native_capture_failed:
                        self._native_capture = create_native_capture()
                    if self._native_capture is not None and not self._native_capture_failed:
                        try:
                            image = self._native_capture.capture(all_screens=screen_scope == "all").convert("RGB")
                            backend = "dxgi (屏幕兼容)"
                        except Exception as native_exc:
                            failed_capture = self._native_capture
                            self._native_capture = None
                            self._native_capture_failed = True
                            self._native_capture_retry_at = now + 10.0
                            if failed_capture is not None:
                                failed_capture.release()
                            with self._lock:
                                self._capture_backend_error = f"MSS：{compatible_error or '不可用'}；DXGI：{native_exc}"
                    if image is None:
                        try:
                            image = ImageGrab.grab(all_screens=screen_scope == "all").convert("RGB")
                            backend = "imagegrab (降级)"
                        except Exception as imagegrab_exc:
                            with self._lock:
                                self._capture_backend = "不可用"
                                self._last_error = (
                                    "屏幕捕获失败。"
                                    f"MSS：{compatible_error or '不可用'}；"
                                    f"ImageGrab：{imagegrab_exc}；"
                                    f"DXGI：{self._capture_backend_error or '不可用'}"
                                )
                            return self.status()
            elif image is None:
                image = ImageGrab.grab(bbox=native_region, all_screens=True).convert("RGB")
                backend = "imagegrab"
            if obs_error and backend != "obs":
                with self._lock:
                    self._capture_backend_error = f"OBS：{obs_error}；已降级为 {backend}"

            thumbnail = image.copy()
            thumbnail.thumbnail((192, 108))
            with self._lock:
                change = calculate_change_metrics(self._last_thumbnail, image)
                self._last_thumbnail = thumbnail
                self._change_percent = change.effective_percent
                self._global_change_percent = change.global_percent
                self._local_change_percent = change.local_percent
                self._captured_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._last_error = ""
                self._capture_backend = backend
                self._cursor = cursor
            analysis_image = image.copy()
            analysis_image.thumbnail((1280, 720))
            analysis_buffer = BytesIO()
            analysis_image.save(analysis_buffer, format="JPEG", quality=86)
            preview_image = analysis_image.copy()
            preview_image.thumbnail((800, 450))
            preview_buffer = BytesIO()
            preview_image.save(preview_buffer, format="JPEG", quality=68, optimize=True)
            with self._lock:
                self._preview_bytes = preview_buffer.getvalue()
                self._analysis_bytes = analysis_buffer.getvalue()
                self._analysis_change_percent = max(self._analysis_change_percent, self._change_percent)
                self._analysis_global_change_percent = max(
                    self._analysis_global_change_percent,
                    self._global_change_percent,
                )
                self._analysis_local_change_percent = max(
                    self._analysis_local_change_percent,
                    self._local_change_percent,
                )
                self._frame_id += 1
                self._last_frame_monotonic = time.monotonic()
                self._capture_valid = True
            cleanup_legacy_preview()
        except Exception as exc:
            with self._lock:
                # Invalidate any pending frame from before the failure.  Keep
                # the last preview for diagnostics, but never expose it via
                # claim_analysis_frame().
                self._analysis_bytes = None
                self._analysis_change_percent = 0.0
                self._analysis_global_change_percent = 0.0
                self._analysis_local_change_percent = 0.0
                self._capture_valid = False
                self._last_error = str(exc)
            raise
        return self.status()

    def start(self, interval_ms: int = 1000) -> dict[str, Any]:
        with self._lock:
            if self._mode == "window" and not self._hwnd:
                raise ValueError("请先选择一个窗口。")
            self._interval_seconds = max(0.5, min(5.0, int(interval_ms) / 1000))
            self._desired_running = True
            already_running = self._thread is not None and self._thread.is_alive()
            if not already_running:
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="mio-game-observer", daemon=True)
                self._thread.start()
        return self.status()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self.capture()
            except Exception:
                time.sleep(1)

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        with self._lock:
            self._desired_running = False
            self._thread = None
            self._preview_bytes = None
            self._analysis_bytes = None
            self._analysis_change_percent = 0.0
            self._analysis_global_change_percent = 0.0
            self._analysis_local_change_percent = 0.0
            self._capture_valid = False
            self._last_frame_monotonic = None
        for attr in ("_native_capture", "_obs_capture", "_screen_capture"):
            capture = getattr(self, attr)
            if capture is not None:
                capture.release()
                setattr(self, attr, None)
        self._screen_capture_failed = False
        self._screen_capture_retry_at = 0.0
        self._native_capture_failed = False
        self._native_capture_retry_at = 0.0
        cleanup_legacy_preview()
        return self.status()

    def take_preview(self) -> bytes | None:
        with self._lock:
            return self._preview_bytes

    def claim_analysis_frame(self, after_frame_id: int = 0) -> dict[str, Any] | None:
        with self._lock:
            if (
                not self._capture_valid
                or self._analysis_bytes is None
                or self._frame_id <= int(after_frame_id)
            ):
                return None
            frame_age_seconds = (
                round(max(0.0, time.monotonic() - self._last_frame_monotonic), 3)
                if self._last_frame_monotonic is not None
                else None
            )
            frame = {
                "frame_id": self._frame_id,
                "content": self._analysis_bytes,
                "change_percent": self._analysis_change_percent,
                "global_change_percent": self._analysis_global_change_percent,
                "local_change_percent": self._analysis_local_change_percent,
                "captured_at": self._captured_at,
                "frame_age_seconds": frame_age_seconds,
                "title": self._title,
                "mode": self._mode,
                "cursor": dict(self._cursor),
            }
            self._analysis_bytes = None
            self._analysis_change_percent = 0.0
            self._analysis_global_change_percent = 0.0
            self._analysis_local_change_percent = 0.0
            return frame

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            last_frame_age_seconds = (
                round(max(0.0, time.monotonic() - self._last_frame_monotonic), 3)
                if self._last_frame_monotonic is not None
                else None
            )
            return {
                "running": running,
                "desired_running": self._desired_running,
                "mode": self._mode,
                "screen_scope": self._screen_scope,
                "hwnd": self._hwnd or None,
                "title": self._title,
                "change_percent": self._change_percent,
                "global_change_percent": self._global_change_percent,
                "local_change_percent": self._local_change_percent,
                "captured_at": self._captured_at,
                "interval_ms": int(self._interval_seconds * 1000),
                "preview_available": self._preview_bytes is not None,
                "frame_id": self._frame_id,
                "pending_change_percent": self._analysis_change_percent,
                "pending_global_change_percent": self._analysis_global_change_percent,
                "pending_local_change_percent": self._analysis_local_change_percent,
                "capture_valid": self._capture_valid,
                "preview_stale": bool(self._preview_bytes is not None and not self._capture_valid),
                "error": self._last_error,
                "capture_backend": self._capture_backend,
                "capture_backend_error": self._capture_backend_error,
                "cursor": dict(self._cursor),
                "last_frame_age_seconds": last_frame_age_seconds,
                "capture_health": (
                    "运行中，等待首帧"
                    if running and self._frame_id <= 0
                    else "运行中，有可用画面"
                    if running and self._capture_valid and self._preview_bytes is not None
                    else "运行中，画面已过期或捕获失败"
                    if running
                    else "已停止"
                ),
            }


GameObserver = WindowObserver


def cleanup_legacy_preview() -> None:
    for path in (
        settings.companion_game_preview_path,
        settings.companion_game_preview_path.with_suffix(".tmp.jpg"),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["WindowObserver", "GameObserver", "cleanup_legacy_preview"]
