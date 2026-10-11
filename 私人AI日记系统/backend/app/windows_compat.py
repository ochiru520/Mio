"""Shared desktop/environment policy; inspecting it never changes the machine."""
from __future__ import annotations

import os
import platform
import struct
import sys

MINIMUM_BUILD = 19045
WEBVIEW2_DOWNLOAD_URL = 'https://developer.microsoft.com/microsoft-edge/webview2/#download'


def evaluate(*, system: str, major: int, build: int, architecture: str, bits: int) -> dict:
    windows = system.lower() == 'windows' and major >= 10
    architecture = architecture.lower()
    # The shipped binaries are x64. Windows 11 also supports x64 emulation.
    arch_ok = bits == 64 and (architecture in {'amd64', 'x86_64'} or
                             (architecture in {'arm64', 'aarch64'} and build >= 22000))
    version_ok = windows and build >= MINIMUM_BUILD
    label = ('Windows 11' if build >= 22000 else 'Windows 10') if windows else system
    reason = ''
    if not version_ok:
        reason = '此版本需要 Windows 10 22H2（构建 19045）或更新的 Windows；请先升级系统。'
    elif not arch_ok:
        reason = '此安装包需要 x64 运行环境；不支持 32 位 Windows 或 Windows 10 ARM。'
    return {'supported': version_ok and arch_ok, 'version_supported': version_ok,
            'architecture_supported': arch_ok, 'os': label, 'build': build,
            'architecture': architecture, 'process_bits': bits, 'reason': reason,
            'minimum': 'Windows 10 22H2 x64',
            'emulated': architecture in {'arm64', 'aarch64'} and arch_ok}


def _native_architecture() -> str:
    """Read the native OS architecture even in an emulated/embedded process."""
    if sys.platform != 'win32':
        return ''
    try:
        import ctypes
        # SYSTEM_INFO is 48 bytes on 64-bit Windows and 36 on 32-bit Windows;
        # its first WORD is wProcessorArchitecture in both layouts.
        info = ctypes.create_string_buffer(64)
        get_info = ctypes.WinDLL('kernel32', use_last_error=True).GetNativeSystemInfo
        get_info.argtypes = [ctypes.c_void_p]
        get_info.restype = None
        get_info(ctypes.byref(info))
        code = ctypes.c_ushort.from_buffer(info).value
        return {0: 'x86', 9: 'amd64', 12: 'arm64'}.get(code, '')
    except (AttributeError, OSError):
        return ''


def snapshot() -> dict:
    version = sys.getwindowsversion() if sys.platform == 'win32' else None
    architecture = (_native_architecture() or
                    os.getenv('PROCESSOR_ARCHITEW6432') or
                    os.getenv('PROCESSOR_ARCHITECTURE') or
                    platform.machine())
    return evaluate(system=platform.system(), major=version.major if version else 0,
                    build=version.build if version else 0,
                    architecture=architecture,
                    bits=struct.calcsize('P') * 8)
