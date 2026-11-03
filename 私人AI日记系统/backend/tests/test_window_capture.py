from __future__ import annotations

import time
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from app.window_capture import WindowCapture
from app.companion_observation_service import WindowObserver


class FakeWgc:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.events = {}
        self.stop = Mock()

    def event(self, callback):
        self.events[callback.__name__] = callback
        return callback

    def start_free_threaded(self):
        return SimpleNamespace(stop=self.stop)

    def push(self, color):
        pixels = Image.new("RGB", (640, 360), color).tobytes("raw", "BGRX")
        self.events["on_frame_arrived"](SimpleNamespace(
            width=640, height=360, frame_buffer=SimpleNamespace(tobytes=lambda: pixels),
        ), None)


class WindowCaptureTests(unittest.TestCase):
    def test_exact_hwnd_updates_pixels_and_preserves_real_capture_time(self):
        holder = []
        def factory(**kwargs):
            holder.append(FakeWgc(**kwargs))
            return holder[-1]
        capture = WindowCapture(123, capture_factory=factory)
        native = holder[0]
        self.assertEqual(native.kwargs["window_hwnd"], 123)
        self.assertIsNone(native.kwargs["monitor_index"])
        with patch("app.window_capture.time.monotonic", return_value=100):
            native.push("red")
        first = capture.capture()
        self.assertEqual(first.getpixel((50, 50)), (255, 0, 0))
        first.putpixel((50, 50), (0, 0, 0))
        self.assertEqual(capture.capture().getpixel((50, 50)), (255, 0, 0))
        self.assertEqual(capture.capture().info["mio_capture_monotonic"], 100)
        with patch("app.window_capture.time.monotonic", return_value=101):
            native.push("blue")
        self.assertEqual(capture.capture().getpixel((50, 50)), (0, 0, 255))
        self.assertEqual(capture.capture().info["mio_capture_monotonic"], 101)
        native.events["on_closed"]()
        with self.assertRaisesRegex(RuntimeError, "结束"):
            capture.capture()
        capture.release()
        native.stop.assert_called_once()

    def test_selected_window_failure_never_falls_back_to_desktop(self):
        observer = WindowObserver()
        observer._mode = "window"
        observer._hwnd = 123
        observer._analysis_bytes = b"old frame"
        observer._capture_valid = True
        observer._frame_id = 7
        failed = SimpleNamespace(hwnd=123, capture=Mock(side_effect=RuntimeError("窗口停止更新")))
        with (
            patch.object(observer, "_client_bbox", return_value=(0, 0, 640, 360)),
            patch.object(observer, "_window_title", return_value="新视频 - B站"),
            patch("app.companion_observation_service.WindowCapture", return_value=failed),
            patch("app.companion_observation_service.ImageGrab.grab") as desktop,
            patch("app.companion_observation_service.create_native_capture") as dxgi,
            patch("app.companion_observation_service.os.name", "nt"),
        ):
            with self.assertRaisesRegex(RuntimeError, "停止更新"):
                observer.capture()
        desktop.assert_not_called()
        dxgi.assert_not_called()
        self.assertFalse(observer.status()["capture_valid"])
        self.assertIsNone(observer.claim_analysis_frame())

    def test_observer_sends_window_pixels_with_their_original_timestamp(self):
        observer = WindowObserver()
        observer._mode = "window"
        observer._hwnd = 123
        image = Image.new("RGB", (640, 360), "lime")
        stamp = time.monotonic() - 4
        image.info.update(mio_capture_monotonic=stamp, mio_captured_at="2026-11-02T00:00:00")
        isolated = SimpleNamespace(hwnd=123, capture=lambda: image, release=Mock())
        with (
            patch.object(observer, "_client_bbox", return_value=(0, 0, 640, 360)),
            patch.object(observer, "_window_title", return_value="新视频 - B站"),
            patch("app.companion_observation_service.WindowCapture", return_value=isolated),
            patch("app.companion_observation_service.cleanup_legacy_preview"),
            patch("app.companion_observation_service.os.name", "nt"),
        ):
            observer.capture()
            frame = observer.claim_analysis_frame()
            self.assertEqual(frame["title"], "新视频 - B站")
            observer.stop()
        self.assertGreaterEqual(frame["frame_age_seconds"], 4)
        self.assertEqual(frame["captured_at"], "2026-11-02T00:00:00")
        with Image.open(BytesIO(frame["content"])) as supplied:
            self.assertGreater(supplied.getpixel((50, 50))[1], 240)
        isolated.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
