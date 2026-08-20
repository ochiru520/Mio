from __future__ import annotations

import base64
import json
import unittest
import unittest.mock
from io import BytesIO

import numpy as np
from PIL import Image

from app.screen_capture import (
    DxgiCapture,
    DxgiOutput,
    MssCapture,
    MonitorRect,
    ObsWebSocketCapture,
    _bind_outputs,
    _patch_dxcam_destructor,
    _parse_output_info,
)


class FakeCamera:
    def __init__(self, image: Image.Image) -> None:
        self.image = image
        self.regions: list[tuple[int, int, int, int] | None] = []
        self.released = False

    def grab(self, region=None):
        self.regions.append(region)
        if region is None:
            return np.asarray(self.image.copy())
        left, top, right, bottom = region
        return np.asarray(self.image.crop((left, top, right, bottom)))

    def stop(self) -> None:
        self.released = True

    def release(self) -> None:
        self.released = True


class ScreenCaptureTests(unittest.TestCase):
    def test_output_info_is_parsed_and_bound_to_primary_monitor(self) -> None:
        raw = "Device[0] Output[0]: Res:(1920, 1080) Rot:0 Primary:True\nDevice[0] Output[1]: Res:(1280, 1024) Rot:0 Primary:False"
        specs = _parse_output_info(raw)
        self.assertEqual(len(specs), 2)
        monitors = [
            MonitorRect(-1280, 0, 0, 1024),
            MonitorRect(0, 0, 1920, 1080, primary=True),
        ]
        outputs = _bind_outputs(raw, monitors)
        self.assertEqual([(item.output_idx, item.monitor.left) for item in outputs], [(0, 0), (1, -1280)])
        self.assertTrue(outputs[0].primary)

    def test_all_outputs_are_stitched_and_window_region_uses_local_coordinates(self) -> None:
        capture = DxgiCapture()
        left_monitor = MonitorRect(-640, 0, 0, 360)
        right_monitor = MonitorRect(0, 0, 640, 360, primary=True)
        left_output = DxgiOutput(0, 0, 640, 360, False, left_monitor)
        right_output = DxgiOutput(0, 1, 640, 360, True, right_monitor)
        left_camera = FakeCamera(Image.new("RGB", (640, 360), "red"))
        right_camera = FakeCamera(Image.new("RGB", (640, 360), "blue"))
        capture._outputs = [left_output, right_output]
        capture._cameras = {(0, 0): left_camera, (0, 1): right_camera}
        capture._dxcam = object()

        whole = capture.capture(all_screens=True)
        self.assertEqual(whole.size, (1280, 360))
        self.assertEqual(whole.getpixel((10, 10)), (255, 0, 0))
        self.assertEqual(whole.getpixel((900, 10)), (0, 0, 255))

        window = capture.capture(region=(-600, 40, -400, 140))
        self.assertEqual(window.size, (200, 100))
        self.assertEqual(window.getpixel((10, 10)), (255, 0, 0))
        self.assertEqual(left_camera.regions[-1], (40, 40, 240, 140))

        capture.release()
        self.assertTrue(left_camera.released)
        self.assertTrue(right_camera.released)

    def test_release_makes_unstarted_dxcam_camera_safe_for_its_destructor(self) -> None:
        class UnstartedCamera:
            def __init__(self) -> None:
                self.stop_calls = 0
                self.release_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1
                if self.is_capturing:
                    self.is_capturing = False

            def release(self) -> None:
                self.release_calls += 1
                self.stop()

        camera = UnstartedCamera()
        capture = DxgiCapture()
        capture._cameras = {(0, 0): camera}
        capture.release()
        self.assertFalse(camera.is_capturing)
        self.assertEqual(camera.release_calls, 1)

    def test_dxcam_partial_constructor_destructor_is_safely_patched(self) -> None:
        class Resource:
            def __init__(self) -> None:
                self.released = False

            def release(self) -> None:
                self.released = True

        class PartialCamera:
            def __del__(self) -> None:
                raise AttributeError("is_capturing")

        class FakeDxcam:
            DXCamera = PartialCamera

        _patch_dxcam_destructor(FakeDxcam)
        camera = PartialCamera()
        camera._duplicator = Resource()
        camera._stagesurf = Resource()
        camera.__del__()
        self.assertTrue(camera._duplicator.released)
        self.assertTrue(camera._stagesurf.released)

    def test_mss_capture_converts_rgb_frame_without_persisting_it(self) -> None:
        class FakeShot:
            size = (2, 1)
            rgb = bytes([255, 0, 0, 0, 0, 255])

        class FakeMss:
            monitors = [
                {"left": 0, "top": 0, "width": 2, "height": 1},
                {"left": 0, "top": 0, "width": 2, "height": 1},
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def grab(self, _monitor):
                return FakeShot()

        capture = MssCapture(factory=FakeMss)
        with unittest.mock.patch("app.screen_capture._windows_monitors", return_value=[]):
            image = capture.capture()
        self.assertEqual(image.size, (2, 1))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(image.getpixel((1, 0)), (0, 0, 255))

    def test_obs_websocket_capture_authenticates_and_decodes_source_screenshot(self) -> None:
        image_buffer = BytesIO()
        Image.new("RGB", (4, 3), "purple").save(image_buffer, format="JPEG")
        encoded = base64.b64encode(image_buffer.getvalue()).decode("ascii")

        class FakeConnection:
            def __init__(self) -> None:
                self.messages = [
                    {"op": 0, "d": {"rpcVersion": 1, "authentication": {"salt": "salt", "challenge": "challenge"}}},
                    {"op": 2, "d": {"negotiatedRpcVersion": 1}},
                ]
                self.sent: list[dict] = []
                self.closed = False

            def send(self, content: str) -> None:
                message = json.loads(content)
                self.sent.append(message)
                if message["op"] == 6:
                    request_id = message["d"]["requestId"]
                    self.messages.append({
                        "op": 7,
                        "d": {
                            "requestId": request_id,
                            "requestStatus": {"result": True, "code": 100},
                            "responseData": {"imageData": f"data:image/jpeg;base64,{encoded}"},
                        },
                    })

            def recv(self, _timeout: float) -> str:
                return json.dumps(self.messages.pop(0))

            def close(self) -> None:
                self.closed = True

        connection = FakeConnection()
        capture = ObsWebSocketCapture(
            url="ws://127.0.0.1:4455",
            password="secret",
            source_name="游戏捕获",
        )
        with unittest.mock.patch("websockets.sync.client.connect", return_value=connection):
            image = capture.capture()

        self.assertEqual(image.size, (4, 3))
        self.assertIn("authentication", connection.sent[0]["d"])
        self.assertEqual(connection.sent[1]["d"]["requestData"]["sourceName"], "游戏捕获")
        capture.release()
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
