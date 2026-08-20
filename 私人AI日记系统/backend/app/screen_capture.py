from __future__ import annotations

import ctypes
import base64
import hashlib
import json
import os
import re
import secrets
import time
from ctypes import wintypes
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import mss
from PIL import Image


@dataclass(frozen=True)
class MonitorRect:
    left: int
    top: int
    right: int
    bottom: int
    primary: bool = False

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class DxgiOutput:
    device_idx: int
    output_idx: int
    width: int
    height: int
    primary: bool
    monitor: MonitorRect


OUTPUT_INFO_RE = re.compile(
    r"Device\[(?P<device>\d+)]\s+Output\[(?P<output>\d+)]:\s+"
    r"Res:\((?P<width>\d+),\s*(?P<height>\d+)\).*?Primary:(?P<primary>True|False)",
    re.IGNORECASE,
)


def _patch_dxcam_destructor(dxcam_module: Any) -> None:
    """Make dxcam 0.0.5 safe when DXCamera.__init__ fails halfway."""
    camera_type = getattr(dxcam_module, "DXCamera", None)
    if camera_type is None or getattr(camera_type, "_mio_safe_destructor", False):
        return
    original_destructor = getattr(camera_type, "__del__", None)

    def safe_destructor(camera: Any) -> None:
        if hasattr(camera, "is_capturing") and callable(original_destructor):
            try:
                original_destructor(camera)
                return
            except Exception:
                pass
        for attribute in ("_duplicator", "_stagesurf"):
            resource = getattr(camera, attribute, None)
            release = getattr(resource, "release", None)
            if callable(release):
                try:
                    release()
                except Exception:
                    pass

    camera_type.__del__ = safe_destructor
    camera_type._mio_safe_destructor = True


def _windows_monitors() -> list[MonitorRect]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    records: list[MonitorRect] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.RECT),
        ctypes.c_long,
    )

    @callback_type
    def callback(monitor_handle, _hdc, rect_pointer, _data):
        rect = rect_pointer.contents

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        primary = bool(user32.GetMonitorInfoW(monitor_handle, ctypes.byref(info)) and info.dwFlags & 1)
        records.append(MonitorRect(rect.left, rect.top, rect.right, rect.bottom, primary))
        return True

    user32.EnumDisplayMonitors(None, None, callback, 0)
    return records


def _parse_output_info(raw: object) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for match in OUTPUT_INFO_RE.finditer(str(raw or "")):
        records.append({
            "device_idx": int(match.group("device")),
            "output_idx": int(match.group("output")),
            "width": int(match.group("width")),
            "height": int(match.group("height")),
            "primary": match.group("primary").casefold() == "true",
        })
    return records


def _bind_outputs(raw_output_info: object, monitors: list[MonitorRect]) -> list[DxgiOutput]:
    specs = _parse_output_info(raw_output_info)
    if not specs or not monitors:
        return []
    unused = list(monitors)
    bindings: list[DxgiOutput] = []
    for spec in sorted(specs, key=lambda item: (not bool(item["primary"]), int(item["device_idx"]), int(item["output_idx"]))):
        candidates = [
            monitor
            for monitor in unused
            if monitor.width == int(spec["width"]) and monitor.height == int(spec["height"])
        ]
        if bool(spec["primary"]):
            preferred = next((monitor for monitor in candidates if monitor.primary), None)
        else:
            preferred = next((monitor for monitor in candidates if not monitor.primary), None)
        monitor = preferred or (candidates[0] if candidates else (unused[0] if unused else None))
        if monitor is None:
            continue
        unused.remove(monitor)
        bindings.append(DxgiOutput(
            device_idx=int(spec["device_idx"]),
            output_idx=int(spec["output_idx"]),
            width=int(spec["width"]),
            height=int(spec["height"]),
            primary=bool(spec["primary"]),
            monitor=monitor,
        ))
    return bindings


def _intersection_area(region: tuple[int, int, int, int], monitor: MonitorRect) -> int:
    left = max(region[0], monitor.left)
    top = max(region[1], monitor.top)
    right = min(region[2], monitor.right)
    bottom = min(region[3], monitor.bottom)
    return max(0, right - left) * max(0, bottom - top)


class DxgiCapture:
    """Lazy multi-output wrapper around DXCAM Desktop Duplication."""

    backend_name = "dxgi"

    def __init__(self) -> None:
        self._cameras: dict[tuple[int, int], Any] = {}
        self._dxcam: Any = None
        self._outputs: list[DxgiOutput] | None = None

    @classmethod
    def available(cls) -> bool:
        try:
            import dxcam  # type: ignore

            return True
        except (ImportError, OSError):
            return False

    def _ensure_outputs(self) -> list[DxgiOutput]:
        if self._outputs is not None:
            return self._outputs
        try:
            import dxcam  # type: ignore

            _patch_dxcam_destructor(dxcam)
            self._dxcam = dxcam
            self._outputs = _bind_outputs(dxcam.output_info(), _windows_monitors())
        except Exception as exc:
            self.release()
            raise RuntimeError(f"DXGI 初始化失败：{exc}") from exc
        if not self._outputs:
            raise RuntimeError("DXGI 没有找到可用的显示器输出。")
        return self._outputs

    def _camera(self, output: DxgiOutput) -> Any:
        key = (output.device_idx, output.output_idx)
        camera = self._cameras.get(key)
        if camera is not None:
            return camera
        try:
            camera = self._dxcam.create(
                device_idx=output.device_idx,
                output_idx=output.output_idx,
                output_color="RGB",
            )
        except Exception as exc:
            raise RuntimeError(f"DXGI 输出 {output.device_idx}:{output.output_idx} 初始化失败：{exc}") from exc
        if camera is None:
            raise RuntimeError(f"DXGI 输出 {output.device_idx}:{output.output_idx} 不可用。")
        self._cameras[key] = camera
        return camera

    def _grab(self, output: DxgiOutput, local_region: tuple[int, int, int, int] | None = None) -> Image.Image:
        camera = self._camera(output)
        try:
            frame = camera.grab(region=local_region) if local_region else camera.grab()
        except Exception as exc:
            raise RuntimeError(f"DXGI 捕获失败：{exc}") from exc
        if frame is None:
            raise RuntimeError("DXGI 暂时没有返回画面，可能是显示器正在切换。")
        try:
            return Image.fromarray(frame).convert("RGB")
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"DXGI 画面格式无法转换：{exc}") from exc

    def _primary_output(self) -> DxgiOutput:
        outputs = self._ensure_outputs()
        return next((output for output in outputs if output.primary or output.monitor.primary), outputs[0])

    def _capture_all_outputs(self) -> tuple[Image.Image, tuple[int, int]]:
        outputs = self._ensure_outputs()
        left = min(output.monitor.left for output in outputs)
        top = min(output.monitor.top for output in outputs)
        right = max(output.monitor.right for output in outputs)
        bottom = max(output.monitor.bottom for output in outputs)
        canvas = Image.new("RGB", (right - left, bottom - top), "black")
        for output in outputs:
            frame = self._grab(output)
            if frame.size != (output.monitor.width, output.monitor.height):
                frame = frame.resize((output.monitor.width, output.monitor.height), Image.Resampling.BILINEAR)
            canvas.paste(frame, (output.monitor.left - left, output.monitor.top - top))
        return canvas, (left, top)

    def capture(
        self,
        *,
        region: tuple[int, int, int, int] | None = None,
        all_screens: bool = False,
    ) -> Image.Image:
        if all_screens:
            return self._capture_all_outputs()[0]
        if region is None:
            return self._grab(self._primary_output())

        outputs = self._ensure_outputs()
        output = max(outputs, key=lambda item: _intersection_area(region, item.monitor))
        overlap = _intersection_area(region, output.monitor)
        requested_area = max(0, region[2] - region[0]) * max(0, region[3] - region[1])
        if overlap <= 0:
            raise RuntimeError("所选窗口不在可用的 DXGI 显示器范围内。")
        if overlap != requested_area:
            canvas, origin = self._capture_all_outputs()
            return canvas.crop((
                region[0] - origin[0],
                region[1] - origin[1],
                region[2] - origin[0],
                region[3] - origin[1],
            ))
        local_region = (
            region[0] - output.monitor.left,
            region[1] - output.monitor.top,
            region[2] - output.monitor.left,
            region[3] - output.monitor.top,
        )
        return self._grab(output, local_region)

    def release(self) -> None:
        cameras = list(self._cameras.values())
        self._cameras.clear()
        self._outputs = None
        self._dxcam = None
        for camera in cameras:
            # dxcam 0.0.5 does not initialize this flag until start(). Its
            # destructor calls release() unconditionally, so set the safe
            # idle value before invoking either cleanup method.
            if not hasattr(camera, "is_capturing"):
                try:
                    camera.is_capturing = False
                except Exception:
                    pass
            for name in ("stop", "release"):
                method = getattr(camera, name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass


class MssCapture:
    """Stable GDI-compatible capture for whole-screen observation."""

    backend_name = "mss"

    def __init__(self, factory=None) -> None:
        self._factory = factory or mss.MSS

    def capture(self, *, all_screens: bool = False) -> Image.Image:
        monitors = _windows_monitors()
        primary = next((monitor for monitor in monitors if monitor.primary), None)
        with self._factory() as capture:
            if all_screens:
                monitor = capture.monitors[0]
            elif primary is not None:
                monitor = {
                    "left": primary.left,
                    "top": primary.top,
                    "width": primary.width,
                    "height": primary.height,
                }
            else:
                monitor = capture.monitors[1]
            shot = capture.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def release(self) -> None:
        return None


class ObsWebSocketCapture:
    """Optional OBS WebSocket 5 source capture, kept out of the default path."""

    backend_name = "obs"

    def __init__(
        self,
        *,
        url: str,
        password: str,
        source_name: str,
        timeout: float = 8.0,
    ) -> None:
        self._url = url.strip()
        self._password = password
        self._source_name = source_name.strip()
        self._timeout = max(2.0, float(timeout))
        self._connection: Any = None
        self._request_id = 0

    @classmethod
    def from_environment(cls) -> "ObsWebSocketCapture | None":
        url = os.getenv("OBS_WEBSOCKET_URL", "").strip()
        source_name = os.getenv("OBS_SOURCE_NAME", "").strip()
        if not url or not source_name:
            return None
        return cls(
            url=url,
            password=os.getenv("OBS_WEBSOCKET_PASSWORD", ""),
            source_name=source_name,
            timeout=float(os.getenv("OBS_WEBSOCKET_TIMEOUT_SECONDS", "8")),
        )

    @classmethod
    def available(cls) -> bool:
        return bool(os.getenv("OBS_WEBSOCKET_URL", "").strip() and os.getenv("OBS_SOURCE_NAME", "").strip())

    def _connect(self) -> Any:
        if self._connection is not None:
            return self._connection
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise RuntimeError("OBS备用捕获需要 websockets 依赖") from exc
        connection = connect(self._url, open_timeout=self._timeout, close_timeout=self._timeout)
        hello = json.loads(connection.recv(self._timeout))
        if int(hello.get("op", -1)) != 0:
            connection.close()
            raise RuntimeError("OBS WebSocket 没有返回 Hello")
        hello_data = dict(hello.get("d") or {})
        identify: dict[str, Any] = {
            "rpcVersion": int(hello_data.get("rpcVersion") or 1),
        }
        authentication = hello_data.get("authentication")
        if authentication:
            if not self._password:
                connection.close()
                raise RuntimeError("OBS WebSocket 已启用密码，但没有配置 OBS_WEBSOCKET_PASSWORD")
            auth = dict(authentication)
            secret = base64.b64encode(
                hashlib.sha256((self._password + str(auth.get("salt") or "")).encode("utf-8")).digest()
            ).decode("ascii")
            identify["authentication"] = base64.b64encode(
                hashlib.sha256((secret + str(auth.get("challenge") or "")).encode("utf-8")).digest()
            ).decode("ascii")
        connection.send(json.dumps({"op": 1, "d": identify}, ensure_ascii=False))
        identified = json.loads(connection.recv(self._timeout))
        if int(identified.get("op", -1)) != 2:
            connection.close()
            raise RuntimeError("OBS WebSocket 鉴权失败")
        self._connection = connection
        return connection

    def capture(
        self,
        *,
        region: tuple[int, int, int, int] | None = None,
        all_screens: bool = False,
    ) -> Image.Image:
        del region, all_screens
        connection = self._connect()
        self._request_id += 1
        request_id = f"mio-screen-{self._request_id}-{secrets.token_hex(4)}"
        connection.send(json.dumps({
            "op": 6,
            "d": {
                "requestType": "GetSourceScreenshot",
                "requestId": request_id,
                "requestData": {
                    "sourceName": self._source_name,
                    "imageFormat": "jpg",
                    "imageCompressionQuality": 80,
                },
            },
        }, ensure_ascii=False))
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            response = json.loads(connection.recv(max(0.1, deadline - time.monotonic())))
            if int(response.get("op", -1)) != 7:
                continue
            data = dict(response.get("d") or {})
            if str(data.get("requestId") or "") != request_id:
                continue
            if str(data.get("requestStatus", {}).get("result") or "").lower() != "true":
                comment = str(data.get("requestStatus", {}).get("comment") or "OBS拒绝了截图请求")
                raise RuntimeError(comment)
            image_data = str((data.get("responseData") or {}).get("imageData") or "")
            if "," not in image_data:
                raise RuntimeError("OBS 返回的截图格式无效")
            _, encoded = image_data.split(",", 1)
            with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                return image.convert("RGB")
        raise TimeoutError("OBS 截图请求超时")

    def release(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def create_native_capture() -> DxgiCapture | None:
    if os.name != "nt" or not DxgiCapture.available():
        return None
    return DxgiCapture()


def create_compatible_capture() -> MssCapture | None:
    if os.name != "nt":
        return None
    return MssCapture()


def create_obs_capture() -> ObsWebSocketCapture | None:
    return ObsWebSocketCapture.from_environment()


__all__ = [
    "DxgiCapture",
    "DxgiOutput",
    "MssCapture",
    "ObsWebSocketCapture",
    "MonitorRect",
    "_bind_outputs",
    "_parse_output_info",
    "create_compatible_capture",
    "create_native_capture",
    "create_obs_capture",
]
