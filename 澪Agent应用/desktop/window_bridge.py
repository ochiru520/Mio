"""Window bridge behavior, independent of application startup and backend identity.

The host owns the shared bridge lock and supplies the native hit-test installer
and minimum window size. Native drag messages still execute on the UI thread.
"""
from __future__ import annotations


import ctypes
import ctypes.wintypes
import logging
import math
import os
import threading


_WM_BEGIN_WINDOW_DRAG = 0x8000 + 0x4D1


class _WindowPlacement(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.wintypes.UINT),
        ("flags", ctypes.wintypes.UINT),
        ("showCmd", ctypes.wintypes.UINT),
        ("ptMinPosition", ctypes.wintypes.POINT),
        ("ptMaxPosition", ctypes.wintypes.POINT),
        ("rcNormalPosition", ctypes.wintypes.RECT),
    ]


class WorkspaceWindowController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, object] = {}
        self._active = "main"
        self._destroying = False

    def attach(self, role: str, window) -> None:
        if role not in {"main", "agent"}:
            raise ValueError("不支持的工作区窗口")
        with self._lock:
            self._windows[role] = window

    def switch(self, target: str) -> dict[str, object]:
        if target not in {"main", "agent"}:
            return {"ok": False, "error": "不支持的工作区"}
        with self._lock:
            if self._destroying:
                return {"ok": False, "error": "应用正在退出"}
            target_window = self._windows.get(target)
            other_window = self._windows.get("agent" if target == "main" else "main")
        if target_window is None:
            return {"ok": False, "error": "目标窗口尚未就绪"}
        try:
            target_window.show()
            target_window.restore()
            # Preserve the caller's visible window if showing the destination
            # fails, so frontend navigation can still recover in place.
            if other_window is not None:
                other_window.hide()
        except Exception as exc:
            logging.exception("Failed to switch Mio workspace window")
            return {"ok": False, "error": str(exc)}
        with self._lock:
            self._active = target
        return {"ok": True, "workspace": target}

    def destroy_all(self) -> None:
        with self._lock:
            if self._destroying:
                return
            self._destroying = True
            windows = tuple(self._windows.values())
            self._windows.clear()
        for window in windows:
            try:
                window.destroy()
            except Exception:
                logging.exception("Failed to close a Mio workspace window")


class WindowBridgeMixin:
    def _initialize_window_bridge(self) -> None:
        self._window = None
        self._hide_window = None
        self._window_maximized = False
        self._resize_gesture = None
        self._move_gesture = None
        self._restore_bounds = None

    def _prepare_native_window_drag(self, handle: int) -> bool:
        raise NotImplementedError

    def _minimum_window_size(self) -> tuple[int, int]:
        raise NotImplementedError

    def attach_window(self, window, hide_window) -> None:
        self._window = window
        self._hide_window = hide_window

    def window_control(self, action: str) -> dict[str, object]:
        window = self._window
        if window is None:
            return {"ok": False, "error": "主窗口尚未就绪"}
        if action == "minimize":
            window.minimize()
        elif action == "maximize":
            native = getattr(window, "native", None)
            handle = int(native.Handle.ToInt64()) if native is not None and os.name == "nt" else 0
            live = bool(handle and ctypes.windll.user32.IsWindow(handle))
            if live:
                self._window_maximized = bool(ctypes.windll.user32.IsZoomed(handle))
            if self._window_maximized:
                if live:
                    ctypes.windll.user32.ShowWindow(handle, 9)  # SW_RESTORE
                else:
                    window.restore()
                if live and self._restore_bounds:
                    ctypes.windll.user32.SetWindowPos(handle, 0, *self._restore_bounds, 0x0004 | 0x0010)
                self._restore_bounds = None
            else:
                if live:
                    rect = ctypes.wintypes.RECT()
                    if ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect)):
                        self._restore_bounds = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                if live:
                    ctypes.windll.user32.ShowWindow(handle, 3)  # SW_MAXIMIZE
                else:
                    window.maximize()
            self._window_maximized = not self._window_maximized
        elif action == "close":
            if self._hide_window is not None:
                should_close = self._hide_window()
                if should_close:
                    window.destroy()
            else:
                window.hide()
        else:
            return {"ok": False, "error": "不支持的窗口操作"}
        return {"ok": True, "maximized": self._window_maximized}

    def window_drag(self) -> dict[str, object]:
        """Queue a native move on the window's UI thread."""
        window = self._window
        native = getattr(window, "native", None) if window is not None else None
        if native is None or os.name != "nt":
            return {"ok": False, "error": "主窗口尚未就绪"}
        handle = int(native.Handle.ToInt64())
        # Keep the legacy non-client command for test doubles and unusual
        # hosts that expose a handle which is not a live Win32 window.
        if not ctypes.windll.user32.IsWindow(handle):
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(handle, 0x00A1, 2, 0)
            return {"ok": True}
        if not self._prepare_native_window_drag(handle):
            return {"ok": False, "error": "无法初始化窗口拖动"}
        point = ctypes.wintypes.POINT()
        # GetCursorPos can be denied when the WebView and host run at
        # different integrity levels. The owning window thread still has the
        # active left-button gesture, so DefWindowProc can start the move with
        # a zero lParam; do not turn a recoverable coordinate read into a UI
        # error.
        position = 0
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            position = (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16)
        if not ctypes.windll.user32.PostMessageW(handle, _WM_BEGIN_WINDOW_DRAG, 0, position):
            return {"ok": False, "error": "无法启动窗口拖动"}
        return {"ok": True}

    def window_resize(self, direction: str) -> dict[str, object]:
        window = self._window
        native = getattr(window, "native", None) if window is not None else None
        if native is None or os.name != "nt":
            return {"ok": False, "error": "主窗口尚未就绪"}
        hit_code = {
            "left": 10,
            "right": 11,
            "top": 12,
            "top-left": 13,
            "top-right": 14,
            "bottom": 15,
            "bottom-left": 16,
            "bottom-right": 17,
        }.get(str(direction or "").lower())
        if hit_code is None:
            return {"ok": False, "error": "不支持的缩放方向"}
        handle = int(native.Handle.ToInt64())
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.SendMessageW(handle, 0x00A1, hit_code, 0)
        return {"ok": True}

    def window_move_by(self, offset_x: float, offset_y: float,
                       gesture_id: str, finished: bool = False) -> dict[str, object]:
        """Move from screen-coordinate deltas without client-frame offsets."""
        if not isinstance(gesture_id, str) or not gesture_id or len(gesture_id) > 80:
            return {"ok": False, "error": "无效的拖动操作"}
        try:
            dx, dy = float(offset_x), float(offset_y)
        except (TypeError, ValueError):
            return {"ok": False, "error": "无效的拖动距离"}
        if not all(math.isfinite(n) and abs(n) <= 100000 for n in (dx, dy)):
            return {"ok": False, "error": "无效的拖动距离"}
        native = getattr(self._window, "native", None)
        if native is None or os.name != "nt":
            return {"ok": False, "error": "主窗口尚未就绪"}
        handle = int(native.Handle.ToInt64())
        with self._lock:
            if ctypes.windll.user32.IsZoomed(handle):
                # BorderStyle.None cannot restore through the normal caption
                # move loop. Restore only after real pointer motion, anchoring
                # the normal-size window beneath the same cursor position.
                if max(abs(dx), abs(dy)) < 4:
                    return {"ok": True, "maximized": True}
                rect = ctypes.wintypes.RECT()
                point = ctypes.wintypes.POINT()
                placement = _WindowPlacement()
                placement.length = ctypes.sizeof(placement)
                if not (ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect))
                        and ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                        and ctypes.windll.user32.GetWindowPlacement(handle, ctypes.byref(placement))):
                    return {"ok": False, "error": "无法读取窗口还原位置"}
                normal = placement.rcNormalPosition
                width, height = normal.right - normal.left, normal.bottom - normal.top
                if self._restore_bounds:
                    width, height = self._restore_bounds[2:]
                if width <= 0 or height <= 0:
                    return {"ok": False, "error": "窗口还原尺寸无效"}
                scale = (ctypes.windll.user32.GetDpiForWindow(handle) or 96) / 96
                start_x, start_y = point.x - round(dx * scale), point.y - round(dy * scale)
                ratio = min(1.0, max(0.0, (start_x - rect.left) / max(1, rect.right - rect.left)))
                left = start_x - round(width * ratio)
                top = start_y - min(round(46 * scale), max(0, start_y - rect.top))
                ctypes.windll.user32.ShowWindow(handle, 9)  # SW_RESTORE
                ok = bool(ctypes.windll.user32.SetWindowPos(
                    handle, 0, left + round(dx * scale), top + round(dy * scale),
                    width, height, 0x0004 | 0x0010,
                ))
                self._move_gesture = None if finished else (gesture_id, left, top, scale)
                self._window_maximized = False
                self._restore_bounds = None
                return {"ok": ok, **({} if ok else {"error": "窗口还原拖动失败"})}
            if self._move_gesture is None or self._move_gesture[0] != gesture_id:
                rect = ctypes.wintypes.RECT()
                if not ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect)):
                    return {"ok": False, "error": "无法读取窗口位置"}
                scale = (ctypes.windll.user32.GetDpiForWindow(handle) or 96) / 96
                self._move_gesture = (gesture_id, rect.left, rect.top, scale)
            _, left, top, scale = self._move_gesture
            ok = bool(ctypes.windll.user32.SetWindowPos(handle, 0, left + round(dx * scale), top + round(dy * scale), 0, 0, 0x0001 | 0x0004 | 0x0010))
            if finished:
                self._move_gesture = None
        return {"ok": ok, **({} if ok else {"error": "窗口拖动失败"})}

    def window_resize_by(self, direction: str, offset_x: float, offset_y: float,
                         gesture_id: str, finished: bool = False) -> dict[str, object]:
        """Resize from cumulative pointer offsets, independent of WebView capture."""
        if direction not in {"left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right"}:
            return {"ok": False, "error": "不支持的缩放方向"}
        if not isinstance(gesture_id, str) or not gesture_id or len(gesture_id) > 80:
            return {"ok": False, "error": "无效的缩放操作"}
        try:
            dx, dy = float(offset_x), float(offset_y)
        except (TypeError, ValueError):
            return {"ok": False, "error": "无效的缩放距离"}
        if not all(math.isfinite(n) and abs(n) <= 100000 for n in (dx, dy)):
            return {"ok": False, "error": "无效的缩放距离"}
        native = getattr(self._window, "native", None)
        if native is None or os.name != "nt":
            return {"ok": False, "error": "主窗口尚未就绪"}
        handle = int(native.Handle.ToInt64())
        if ctypes.windll.user32.IsZoomed(handle):
            return {"ok": True, "ignored": True, "reason": "maximized"}
        with self._lock:
            if self._resize_gesture is None or self._resize_gesture[0] != gesture_id:
                rect = ctypes.wintypes.RECT()
                if not ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect)):
                    return {"ok": False, "error": "无法读取窗口大小"}
                scale = (ctypes.windll.user32.GetDpiForWindow(handle) or 96) / 96
                self._resize_gesture = (gesture_id, direction, rect.left, rect.top, rect.right, rect.bottom, scale)
            _, saved_direction, left, top, right, bottom, scale = self._resize_gesture
            if saved_direction != direction:
                return {"ok": False, "error": "缩放方向已改变"}
            dx, dy = round(dx * scale), round(dy * scale)
            min_width, min_height = (round(n * scale) for n in self._minimum_window_size())
            if "left" in direction:
                left = min(left + dx, right - min_width)
            if "right" in direction:
                right = max(right + dx, left + min_width)
            if "top" in direction:
                top = min(top + dy, bottom - min_height)
            if "bottom" in direction:
                bottom = max(bottom + dy, top + min_height)
            ok = bool(ctypes.windll.user32.SetWindowPos(handle, 0, left, top, right - left, bottom - top, 0x0004 | 0x0010))
            if finished:
                self._resize_gesture = None
        return {"ok": ok, **({} if ok else {"error": "调整窗口大小失败"})}
