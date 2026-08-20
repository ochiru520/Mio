from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


PREFIX = "dpapi:"
_ENTROPY = b"mio-agent-model-provider-v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("当前系统不支持 Windows DPAPI。")


def protect_secret(secret: str) -> str:
    _require_windows()
    content = str(secret or "")
    if not content:
        return ""
    input_blob, input_buffer = _blob(content.encode("utf-8"))
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    success = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Mio Agent",
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    _ = (input_buffer, entropy_buffer)
    if not success:
        raise ctypes.WinError()
    try:
        protected = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
    return PREFIX + base64.b64encode(protected).decode("ascii")


def unprotect_secret(value: str) -> str:
    _require_windows()
    protected = str(value or "")
    if not protected:
        return ""
    if not protected.startswith(PREFIX):
        raise ValueError("密钥不是受保护的 DPAPI 数据。")
    try:
        encoded = base64.b64decode(protected[len(PREFIX) :], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("受保护的密钥数据已经损坏。") from exc
    input_blob, input_buffer = _blob(encoded)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output_blob = _DataBlob()
    success = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    _ = (input_buffer, entropy_buffer)
    if not success:
        raise ctypes.WinError()
    try:
        content = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
    return content.decode("utf-8")
