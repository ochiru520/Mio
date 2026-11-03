"""Select the actual Windows waveOut device used by Mio's local voice."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
import uuid
from typing import Any

from .config import settings

_lock = threading.RLock()
DEFAULT_DEVICE = 0xFFFFFFFF


class _WaveOutCaps(ctypes.Structure):
    _fields_ = [("manufacturer", ctypes.c_uint16), ("product", ctypes.c_uint16),
                ("version", ctypes.c_uint32), ("name", ctypes.c_wchar * 32),
                ("formats", ctypes.c_uint32), ("channels", ctypes.c_uint16),
                ("reserved", ctypes.c_uint16), ("support", ctypes.c_uint32)]


def _enumerate_outputs() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    winmm = ctypes.WinDLL("winmm")
    outputs = []
    occurrences: dict[str, int] = {}
    for index in range(winmm.waveOutGetNumDevs()):
        caps = _WaveOutCaps()
        if winmm.waveOutGetDevCapsW(ctypes.c_size_t(index), ctypes.byref(caps), ctypes.sizeof(caps)):
            continue
        identity = f"{caps.manufacturer}:{caps.product}:{caps.name}"
        ordinal = occurrences.get(identity, 0)
        occurrences[identity] = ordinal + 1
        digest = hashlib.sha256(f"{identity}:{ordinal}".encode("utf-8")).hexdigest()[:24]
        outputs.append({"id": "waveout:" + digest, "label": str(caps.name), "index": index})
    return outputs


def _read_selection() -> str:
    try:
        data = json.loads((settings.data_dir / "audio-output.json").read_text(encoding="utf-8"))
        return str(data.get("output_device_id") or "default")
    except (OSError, ValueError, TypeError, AttributeError):
        return "default"


def status() -> dict[str, Any]:
    with _lock:
        outputs = _enumerate_outputs()
        selected = _read_selection()
        missing = selected != "default" and not any(item["id"] == selected for item in outputs)
        return {"available": os.name == "nt", "output_device_id": selected,
                "outputs": [{"id": "default", "label": "Windows 系统默认输出"}] + [
                    {"id": item["id"], "label": item["label"]} for item in outputs],
                "selection_missing": missing,
                "message": "所选播放设备已断开，请重新选择或改用系统默认后重试。" if missing else
                           "此选择用于 Mio 正式语音；正在播放的片段结束后生效。"}


def select_output(device_id: str) -> dict[str, Any]:
    with _lock:
        if device_id != "default" and not any(item["id"] == device_id for item in _enumerate_outputs()):
            raise ValueError("播放设备不存在或已断开，请刷新设备列表。")
        path = settings.data_dir / "audio-output.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps({"output_device_id": device_id}), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return status()


def resolve_output_device() -> int:
    with _lock:
        selected = _read_selection()
        if selected == "default":
            return DEFAULT_DEVICE
        for item in _enumerate_outputs():
            if item["id"] == selected:
                return int(item["index"])
    raise RuntimeError("Mio 的播放设备已断开，请在设置→语音→设备测试中重新选择。")
