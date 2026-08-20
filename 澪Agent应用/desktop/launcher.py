from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import winreg
from collections import deque
from pathlib import Path

try:
    from desktop.runtime_root_migration import choose_runtime_root
except ModuleNotFoundError:
    from runtime_root_migration import choose_runtime_root


APP_NAME = "Mio"
HOST = "127.0.0.1"
PORT = int(os.environ.get("MIO_PORT", "8000"))
# 让桌宠等子进程跟随本实例端口（不覆盖用户显式设置）
os.environ.setdefault("MIO_PET_API_BASE", f"http://{HOST}:{PORT}")


def _instance_channel_name(channel: str, port: int) -> str:
    suffix = "" if port == 8000 else f"-{port}"
    return f"Local\\{channel}-7C53C273{suffix}"


MUTEX_NAME = _instance_channel_name("MioAgentDesktop", PORT)
SHOW_EVENT_NAME = _instance_channel_name("MioAgentDesktopShow", PORT)
PET_CHAT_EVENT_NAME = _instance_channel_name("MioAgentDesktopPetChat", PORT)
INSTALL_DATA_DIR_FILENAME = "数据目录.txt"
BUILD_MANIFEST_FILENAME = "构建清单.json"


def _read_installed_data_dir_config(config_path: Path) -> str:
    try:
        raw = config_path.read_bytes()
    except OSError:
        return ""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            configured = raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
        if configured and encoding != "utf-8-sig":
            try:
                config_path.write_text(configured, encoding="utf-8")
            except OSError:
                pass
        return configured
    return ""


def _installed_state_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    config_path = Path(sys.executable).resolve().parent / INSTALL_DATA_DIR_FILENAME
    configured = _read_installed_data_dir_config(config_path)
    if not configured:
        return None
    path = Path(os.path.expandvars(configured)).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _desktop_state_dir() -> Path:
    configured = os.getenv("MIO_DESKTOP_STATE_DIR", "").strip()
    if configured:
        root = Path(configured)
    elif installed := _installed_state_dir():
        root = installed
    elif getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent / "Data"
    elif Path("D:/").exists():
        root = Path("D:/Mio数据")
    else:
        root = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "MioAgent"
    root.mkdir(parents=True, exist_ok=True)
    return root


STATE_DIR = _desktop_state_dir()
os.environ.setdefault("MIO_DESKTOP_STATE_DIR", str(STATE_DIR))
LOG_PATH = STATE_DIR / "desktop.log"
RUNTIME_CONFIG_PATH = STATE_DIR / "运行配置.json"
WEBVIEW_DATA_DIR = STATE_DIR / "WebView数据"
PET_CHAT_WEBVIEW_DATA_DIR = STATE_DIR / "桌宠聊天WebView数据"
WEBVIEW_FAILURE_MARKER_PATH = STATE_DIR / "WebView界面恢复.json"
WEBVIEW_CACHE_REPAIR_VERSION_PATH = STATE_DIR / "WebView缓存修复版本.txt"
FULL_RECOVERY_HISTORY_PATH = STATE_DIR / "整应用恢复历史.json"
BACKEND_RECOVERY_STATE_PATH = STATE_DIR / "后端恢复状态.json"
DESKTOP_PREFERENCES_PATH = STATE_DIR / "桌面偏好.json"
WEBVIEW_CACHE_REPAIR_VERSION = "17"
MAIN_WINDOW_MIN_SIZE = (480, 500)
PET_CHAT_WINDOW_SIZE = (520, 84)
BACKEND_HEALTH_CHECK_INTERVAL_SECONDS = 10
BACKEND_HEALTH_FAILURE_THRESHOLD = 3
BACKEND_RECOVERY_WINDOW_SECONDS = 600
BACKEND_RECOVERY_MAX_ATTEMPTS = 2
FULL_RECOVERY_WINDOW_SECONDS = 600
FULL_RECOVERY_MAX_ATTEMPTS = 2
WEBVIEW_RECOVERY_ARGUMENT = "--recover-webview-parent="
SCREEN_PREVIEW_ARGUMENT = "--screen-preview-window"
SCREEN_PREVIEW_PARENT_ARGUMENT = "--screen-preview-parent="
PET_CHAT_WINDOW_ARGUMENT = "--pet-chat-window"
PET_CHAT_PARENT_ARGUMENT = "--pet-chat-parent="
VOICE_PACKAGE_IMPORT_WORKER_ARGUMENT = "--voice-package-import-worker"
WEBVIEW_UNSTABLE_RENDERING_ARGUMENTS = ("--disable-gpu", "--disable-gpu-compositing")
_WEBVIEW_RECOVERY_CALLBACK = None

DEFAULT_DESKTOP_PREFERENCES = {
    "close_to_background": True,
    "background_notifications": True,
}


def _build_manifest_path() -> Path | None:
    configured = os.getenv("MIO_BUILD_MANIFEST", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / BUILD_MANIFEST_FILENAME)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _read_build_manifest() -> dict[str, object]:
    path = _build_manifest_path()
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        return {}
    return payload


def _expected_build_id() -> str:
    return str(_read_build_manifest().get("build_id") or "").strip()


def _desktop_runtime_identity(runtime_root: Path) -> dict[str, object]:
    manifest = _read_build_manifest()
    return {
        "exe_path": str(Path(sys.executable).resolve()),
        "build_id": str(manifest.get("build_id") or "development-unmanifested"),
        "app_version": str(manifest.get("app_version") or "development"),
        "runtime_root": str(runtime_root.resolve()),
        "state_root": str(STATE_DIR.resolve()),
        "database_path": str((runtime_root / "数据" / "personal_ai.db").resolve()),
        "manifest_path": str(_build_manifest_path() or ""),
    }
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE = "MioAgent"
WEBVIEW2_CLIENT_ID = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _read_desktop_preferences() -> dict[str, bool]:
    preferences = dict(DEFAULT_DESKTOP_PREFERENCES)
    try:
        saved = json.loads(DESKTOP_PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        saved = {}
    if isinstance(saved, dict):
        for key in preferences:
            if key in saved:
                preferences[key] = bool(saved[key])
    return preferences


def _read_pet_chat_anchor() -> tuple[int, int] | None:
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{PORT}/api/companion/chat-anchor",
            timeout=1.5,
        ) as response:
            document = json.loads(response.read().decode("utf-8"))
        payload = document.get("anchor") if isinstance(document, dict) else None
        if not isinstance(payload, dict):
            return None
        anchor_x = int(payload["anchor_x"])
        anchor_y = int(payload["anchor_y"])
    except (OSError, KeyError, TypeError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return None
    return anchor_x, anchor_y


def _pet_chat_window_position(anchor: tuple[int, int] | None) -> tuple[int, int] | None:
    if anchor is None:
        return None
    anchor_x, anchor_y = anchor
    width, height = PET_CHAT_WINDOW_SIZE
    virtual_left = ctypes.windll.user32.GetSystemMetrics(76)
    virtual_top = ctypes.windll.user32.GetSystemMetrics(77)
    virtual_right = virtual_left + ctypes.windll.user32.GetSystemMetrics(78)
    virtual_bottom = virtual_top + ctypes.windll.user32.GetSystemMetrics(79)
    x = max(virtual_left, min(virtual_right - width, round(anchor_x - width / 2)))
    y = max(virtual_top, min(virtual_bottom - height, anchor_y - height - 12))
    return x, y


def _position_pet_chat_window(window) -> None:
    position = _pet_chat_window_position(_read_pet_chat_anchor())
    if position is not None:
        window.move(*position)


def _make_pet_chat_window_transparent(window) -> bool:
    if os.name != "nt" or window is None or getattr(window, "native", None) is None:
        return False
    from System.Drawing import Color  # type: ignore[import-not-found]

    native = window.native
    key = Color.FromArgb(1, 2, 3)
    native.AllowTransparency = True
    native.BackColor = key
    native.TransparencyKey = key
    return True


def _make_pet_chat_window_interactive(window) -> bool:
    if os.name != "nt" or window is None or getattr(window, "native", None) is None:
        return False
    handle = int(window.native.Handle.ToInt64())
    get_window_long = ctypes.windll.user32.GetWindowLongPtrW
    set_window_long = ctypes.windll.user32.SetWindowLongPtrW
    extended_style = int(get_window_long(handle, -20))
    set_window_long(handle, -20, extended_style & ~0x20)  # WS_EX_TRANSPARENT
    ctypes.windll.user32.SetWindowPos(
        handle,
        -1,  # HWND_TOPMOST
        0,
        0,
        0,
        0,
        0x0001 | 0x0002 | 0x0010 | 0x0020,
    )
    return True


def _make_main_window_resizable(window) -> bool:
    if os.name != "nt" or window is None or getattr(window, "native", None) is None:
        return False
    handle = int(window.native.Handle.ToInt64())
    get_window_long = ctypes.windll.user32.GetWindowLongPtrW
    set_window_long = ctypes.windll.user32.SetWindowLongPtrW
    style = int(get_window_long(handle, -16))
    set_window_long(handle, -16, style | 0x00040000 | 0x00020000)  # THICKFRAME | MINIMIZEBOX
    ctypes.windll.user32.SetWindowPos(
        handle,
        0,
        0,
        0,
        0,
        0,
        0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020,
    )
    _install_resize_hit_test(handle)
    return True


_RESIZE_SUBCLASS_CALLBACKS: dict[int, object] = {}


def _resize_hit_code(window_handle: int, lparam: int, border: int = 8) -> int | None:
    rect = ctypes.wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(window_handle, ctypes.byref(rect)):
        return None
    x = ctypes.c_short(lparam & 0xFFFF).value
    y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
    left = x < rect.left + border
    right = x >= rect.right - border
    top = y < rect.top + border
    bottom = y >= rect.bottom - border
    if top and left:
        return 13
    if top and right:
        return 14
    if bottom and left:
        return 16
    if bottom and right:
        return 17
    if left:
        return 10
    if right:
        return 11
    if top:
        return 12
    if bottom:
        return 15
    return None


def _install_resize_hit_test(window_handle: int) -> bool:
    if os.name != "nt" or window_handle in _RESIZE_SUBCLASS_CALLBACKS:
        return window_handle in _RESIZE_SUBCLASS_CALLBACKS
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
        ctypes.c_size_t,
        ctypes.c_size_t,
    )
    def_subclass_proc = ctypes.windll.comctl32.DefSubclassProc
    def_subclass_proc.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    def_subclass_proc.restype = ctypes.c_ssize_t
    set_window_subclass = ctypes.windll.comctl32.SetWindowSubclass
    set_window_subclass.argtypes = [
        ctypes.c_void_p,
        callback_type,
        ctypes.c_size_t,
        ctypes.c_size_t,
    ]
    set_window_subclass.restype = ctypes.c_bool

    @callback_type
    def subclass_proc(hwnd, message, wparam, lparam, subclass_id, reference_data):
        if message == 0x0084 and not ctypes.windll.user32.IsZoomed(hwnd):  # WM_NCHITTEST
            hit_code = _resize_hit_code(int(hwnd), int(lparam))
            if hit_code is not None:
                return hit_code
        return def_subclass_proc(hwnd, message, wparam, lparam)

    installed = bool(set_window_subclass(
        window_handle,
        subclass_proc,
        0x4D494F52,
        0,
    ))
    if installed:
        _RESIZE_SUBCLASS_CALLBACKS[window_handle] = subclass_proc
    return installed


def _write_desktop_preferences(updates: dict[str, object]) -> dict[str, bool]:
    preferences = _read_desktop_preferences()
    for key in preferences:
        if key in updates:
            preferences[key] = bool(updates[key])
    DESKTOP_PREFERENCES_PATH.write_text(
        json.dumps(preferences, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return preferences


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    return f'"{Path(sys.executable).resolve()}" "{Path(__file__).resolve()}"'


def _windows_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, WINDOWS_RUN_VALUE)
    except OSError:
        return False
    return bool(str(value).strip())


def _set_windows_startup(enabled: bool) -> bool:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, WINDOWS_RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, WINDOWS_RUN_VALUE)
            except FileNotFoundError:
                pass
    return _windows_startup_enabled()


def _webview_cache_paths(webview_data_dir: Path) -> list[Path]:
    webview_root = webview_data_dir / "EBWebView"
    return [
        webview_root / "Default" / "Cache",
        webview_root / "Default" / "GPUCache",
        webview_root / "Default" / "Code Cache",
        webview_root / "Default" / "DawnGraphiteCache",
        webview_root / "Default" / "DawnWebGPUCache",
        webview_root / "GPUPersistentCache",
        webview_root / "GrShaderCache",
        webview_root / "ShaderCache",
    ]


def _clear_webview_render_caches(webview_data_dir: Path = WEBVIEW_DATA_DIR) -> list[str]:
    base = webview_data_dir.resolve()
    cleared: list[str] = []
    for path in _webview_cache_paths(webview_data_dir):
        resolved = path.resolve()
        if not resolved.is_relative_to(base):
            raise RuntimeError(f"拒绝清理 WebView 数据目录之外的路径：{resolved}")
        if not resolved.exists():
            continue
        shutil.rmtree(resolved)
        cleared.append(str(resolved))
    return cleared


def _configure_webview_runtime() -> str:
    variable = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
    arguments = [
        argument
        for argument in os.environ.get(variable, "").split()
        if argument not in WEBVIEW_UNSTABLE_RENDERING_ARGUMENTS
    ]
    configured = " ".join(arguments).strip()
    os.environ[variable] = configured
    return configured


def _prepare_webview_storage() -> None:
    failure_recovery = WEBVIEW_FAILURE_MARKER_PATH.exists()
    try:
        current_version = WEBVIEW_CACHE_REPAIR_VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        current_version = ""
    version_repair = current_version != WEBVIEW_CACHE_REPAIR_VERSION
    if not failure_recovery and not version_repair:
        return

    reason = "上次 WebView2 异常" if failure_recovery else "WebView2 缓存修复升级"
    try:
        cleared = _clear_webview_render_caches()
        logging.info("Cleared WebView render caches after %s: %s", reason, cleared or "none")
        WEBVIEW_CACHE_REPAIR_VERSION_PATH.write_text(
            WEBVIEW_CACHE_REPAIR_VERSION,
            encoding="utf-8",
        )
        WEBVIEW_FAILURE_MARKER_PATH.unlink(missing_ok=True)
    except Exception:
        logging.exception("Failed to clear WebView render caches after %s", reason)


def _write_webview_failure_marker(failure_kind: str, reason: str) -> None:
    payload = {
        "failure_kind": failure_kind,
        "reason": reason,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        WEBVIEW_FAILURE_MARKER_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logging.exception("Failed to write WebView recovery marker")


def _failure_requires_webview_cache_repair(failure_kind: str) -> bool:
    return failure_kind != "BackendUnavailable"


def _wait_for_recovery_parent() -> None:
    argument = next(
        (item for item in sys.argv[1:] if item.startswith(WEBVIEW_RECOVERY_ARGUMENT)),
        "",
    )
    if not argument:
        return
    try:
        parent_pid = int(argument.removeprefix(WEBVIEW_RECOVERY_ARGUMENT))
    except ValueError:
        return
    finally:
        try:
            sys.argv.remove(argument)
        except ValueError:
            pass

    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        return
    try:
        ctypes.windll.kernel32.WaitForSingleObject(handle, 15000)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _spawn_webview_recovery_process() -> None:
    argument = f"{WEBVIEW_RECOVERY_ARGUMENT}{os.getpid()}"
    if getattr(sys, "frozen", False):
        command = [sys.executable, argument]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), argument]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(command, close_fds=True, creationflags=creation_flags)


def _configure_source_import_path() -> None:
    if getattr(sys, "frozen", False):
        return
    backend_root = Path(__file__).resolve().parents[2] / "私人AI日记系统" / "backend"
    if backend_root.exists() and str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))


def _configure_logging(log_path: Path = LOG_PATH) -> RotatingFileHandler:
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_mio_desktop_handler", False):
            return handler
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler._mio_desktop_handler = True
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


def _message_box(message: str, title: str = APP_NAME, error: bool = False) -> None:
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, message, title, flags)


def _webview2_runtime_version() -> str:
    registry_paths = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
    )
    for hive, key_path in registry_paths:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                version = str(winreg.QueryValueEx(key, "pv")[0] or "").strip()
            if version and version != "0.0.0.0":
                return version
        except OSError:
            continue
    for root in (
        Path(os.getenv("PROGRAMFILES(X86)", "")) / "Microsoft/EdgeWebView/Application",
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft/EdgeWebView/Application",
    ):
        if not root.is_dir():
            continue
        versions = sorted((path.name for path in root.iterdir() if path.is_dir()), reverse=True)
        if versions:
            return versions[0]
    return ""


def _require_webview2_runtime() -> str:
    version = _webview2_runtime_version()
    if not version:
        raise RuntimeError(
            "没有检测到 Microsoft Edge WebView2 Runtime。\n"
            "请先安装 WebView2 Runtime，再重新打开 Mio。"
        )
    return version


def _acquire_single_instance() -> int | None:
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.kernel32.CloseHandle(handle)
        event_handle = ctypes.windll.kernel32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)
        if event_handle:
            ctypes.windll.kernel32.SetEvent(event_handle)
            ctypes.windll.kernel32.CloseHandle(event_handle)
        else:
            _message_box("Mio 已经在后台运行了，请从系统托盘打开。")
        return None
    return int(handle)


def _create_show_event() -> int:
    handle = ctypes.windll.kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
    if not handle:
        raise RuntimeError("无法创建窗口唤醒事件。")
    return int(handle)


def _signal_show_event() -> bool:
    event_handle = ctypes.windll.kernel32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)
    if not event_handle:
        return False
    try:
        return bool(ctypes.windll.kernel32.SetEvent(event_handle))
    finally:
        ctypes.windll.kernel32.CloseHandle(event_handle)


def _create_pet_chat_event() -> int:
    handle = ctypes.windll.kernel32.CreateEventW(None, False, False, PET_CHAT_EVENT_NAME)
    if not handle:
        raise RuntimeError("无法创建桌宠对话唤醒事件。")
    return int(handle)


def _candidate_legacy_roots() -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv("MIO_LEGACY_PROJECT_ROOT", "").strip()
    if configured:
        candidates.append(Path(configured))
    if not getattr(sys, "frozen", False):
        candidates.append(Path(__file__).resolve().parents[2] / "私人AI日记系统")
    unique: list[Path] = []
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _choose_runtime_root() -> Path:
    return choose_runtime_root(
        STATE_DIR,
        RUNTIME_CONFIG_PATH,
        _candidate_legacy_roots(),
    )


def _configure_runtime_environment(runtime_root: Path) -> None:
    os.environ["MIO_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["MIO_DESKTOP_APP"] = "1"
    os.environ["MIO_SCREEN_OBSERVER_PROCESS"] = "1"
    voice_root = _voice_runtime_root()
    if getattr(sys, "frozen", False) or "MIO_VOICE_TRAINING_DIR" not in os.environ:
        # Installed builds always keep optional models under the selected Mio
        # data root. A stale developer environment variable must never redirect
        # another user's installation back to a source checkout.
        os.environ["MIO_VOICE_TRAINING_DIR"] = str(voice_root)
    manifest_path = _build_manifest_path()
    if manifest_path is not None:
        os.environ["MIO_BUILD_MANIFEST"] = str(manifest_path)
        expected_build_id = _expected_build_id()
        if expected_build_id:
            os.environ["MIO_EXPECTED_BUILD_ID"] = expected_build_id
    for variable, filename in (
        ("RUNTIME_SUMMARY_PATH", "澪运行时说明书.md"),
        ("PERSONA_PROMPT_PATH", "澪_私人AI人格设定与提示词.md"),
        ("PERSONAL_MANUAL_PATH", "个人说明书.txt"),
        ("TALENT_MANUAL_PATH", "个人天赋使用说明书.txt"),
    ):
        candidate = runtime_root / filename
        if candidate.exists() and variable not in os.environ:
            os.environ[variable] = str(candidate)


def _voice_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return STATE_DIR / "音色训练"
    return Path(__file__).resolve().parents[2] / "音色训练"


def _prepare_bundled_voice_runtime() -> bool:
    """Repair an older portable Genie venv after it was migrated into Data."""
    root = _voice_runtime_root()
    python_home = root / "Python310"
    python = root / ".genie-env" / "Scripts" / "python.exe"
    config = root / ".genie-env" / "pyvenv.cfg"
    required = (
        python_home / "python.exe",
        python_home / "python310.dll",
        python,
        root / ".genie-env" / "Lib" / "site-packages" / "genie_tts" / "__init__.py",
        root / ".genie-env" / "Lib" / "site-packages" / "jieba" / "__init__.py",
        root / "GenieData" / "chinese-hubert-base" / "chinese-hubert-base.onnx",
        root / "models" / "genie" / "mio-v1" / "mio-genie-v2.json",
    )
    if not all(path.is_file() for path in required):
        return False
    desired = (
        f"home = {python_home.resolve()}\n"
        "include-system-site-packages = false\n"
        "version = 3.10.11\n"
    )
    try:
        if not config.is_file() or config.read_text(encoding="utf-8", errors="replace") != desired:
            config.write_text(desired, encoding="utf-8")
    except OSError:
        logging.exception("Unable to relocate bundled Mio voice runtime")
        return False
    return True


def _bundled_default_voice_path() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / "default_voice" / "mio_v2_00.wav"


def _prepare_bundled_default_voice(runtime_root: Path) -> bool:
    """Seed a new Mio profile or repair only the bundled ``mio`` profile."""
    config_path = runtime_root / "数据" / "桌宠" / "设置.json"
    source = _bundled_default_voice_path()
    if not source.is_file():
        logging.warning("Bundled Mio reference audio is missing: %s", source)
        return False
    destination = runtime_root / "数据" / "桌宠" / "默认参考音频" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source, destination)
    voice_root = _voice_runtime_root()
    gpt_weights = voice_root / "GPT-SoVITS" / "GPT_weights_v2" / "mio_v1-e15.ckpt"
    sovits_weights = voice_root / "GPT-SoVITS" / "SoVITS_weights_v2" / "mio_v1_e8_s200.pth"
    bundled_profile = {
        "name": "Mio 默认音色",
        "engine": "gpt_sovits",
        "gpt_sovits_ref_audio": str(destination.resolve()),
        "gpt_sovits_prompt_text": "つまらないものですが、いや、ありがとうございます。",
        "gpt_sovits_prompt_language": "ja",
        "gpt_sovits_text_language": "auto",
        "gpt_sovits_translate_to_japanese": False,
        "use_emotion_references": True,
    }
    if gpt_weights.is_file() and sovits_weights.is_file():
        bundled_profile["gpt_sovits_gpt_weights"] = str(gpt_weights.resolve())
        bundled_profile["gpt_sovits_sovits_weights"] = str(sovits_weights.resolve())

    if config_path.exists():
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            logging.exception("Unable to read existing Mio voice settings")
            return False
        profiles = saved.get("voice_profiles") if isinstance(saved, dict) else None
        profile = profiles.get("mio") if isinstance(profiles, dict) else None
        if not isinstance(profile, dict):
            return False
        before = json.dumps(saved, ensure_ascii=False, sort_keys=True)
        repair_fields = {
            key: value
            for key, value in bundled_profile.items()
            if key not in {"gpt_sovits_text_language", "gpt_sovits_translate_to_japanese"}
        }
        profile.update(repair_fields)
        if str(saved.get("default_voice_profile_id") or "") == "mio":
            for key, value in repair_fields.items():
                if key.startswith("gpt_sovits_"):
                    saved[key] = value
        if json.dumps(saved, ensure_ascii=False, sort_keys=True) == before:
            return False
        _write_state_json(config_path, saved)
        logging.info("Repaired bundled Mio voice profile: %s", destination)
        return True

    initial = {
        "default_voice_profile_id": "mio",
        "voice_profiles": {"mio": bundled_profile},
    }
    initial.update({
        key: value
        for key, value in bundled_profile.items()
        if key.startswith("gpt_sovits_")
    })
    _write_state_json(config_path, initial)
    logging.info("Seeded bundled Mio reference audio: %s", destination)
    return True


_LAST_BACKEND_HEALTH_ERROR = ""


def _health() -> dict[str, object] | None:
    global _LAST_BACKEND_HEALTH_ERROR
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        _LAST_BACKEND_HEALTH_ERROR = f"{type(exc).__name__}: {exc}"
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        _LAST_BACKEND_HEALTH_ERROR = "健康接口返回了无效内容"
        return None
    _LAST_BACKEND_HEALTH_ERROR = ""
    return data


def _port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.4)
        return connection.connect_ex((HOST, PORT)) == 0


def _same_path(left: object, right: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))
    except (OSError, TypeError, ValueError):
        return False


def _backend_health_matches_runtime(runtime_root: Path) -> bool:
    global _LAST_BACKEND_HEALTH_ERROR
    health = _health()
    if health is None:
        return False
    if not _same_path(health.get("project_root"), runtime_root):
        _LAST_BACKEND_HEALTH_ERROR = (
            f"运行根不匹配：期望 {runtime_root}，实际 {health.get('project_root') or '未提供'}"
        )
        return False
    expected_build_id = _expected_build_id()
    if expected_build_id and str(health.get("build_id") or "") != expected_build_id:
        _LAST_BACKEND_HEALTH_ERROR = (
            f"构建身份不匹配：期望 {expected_build_id}，实际 {health.get('build_id') or '未提供'}"
        )
        return False
    return True


def _write_state_json(
    path: Path,
    payload: dict[str, object],
    *,
    attempts: int = 12,
    retry_delay_seconds: float = 0.025,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    maximum_attempts = max(1, int(attempts))
    for attempt in range(maximum_attempts):
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(serialized, encoding="utf-8")
            os.replace(temporary, path)
            return
        except OSError as exc:
            retryable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
            if not retryable or attempt + 1 >= maximum_attempts:
                raise
            time.sleep(max(0.0, retry_delay_seconds) * min(attempt + 1, 4))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _voice_package_import_job_dir() -> Path:
    return STATE_DIR / "音色包导入任务"


def _voice_package_import_command(job_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, VOICE_PACKAGE_IMPORT_WORKER_ARGUMENT, str(job_path)]
    return [sys.executable, str(Path(__file__).resolve()), VOICE_PACKAGE_IMPORT_WORKER_ARGUMENT, str(job_path)]


def _run_voice_package_import_worker(job_path: Path) -> int:
    job_root = _voice_package_import_job_dir().resolve()
    try:
        resolved_job_path = job_path.resolve()
        if not resolved_job_path.is_relative_to(job_root):
            raise ValueError("音色包导入任务路径无效。")
        job = json.loads(resolved_job_path.read_text(encoding="utf-8-sig"))
        source_path = Path(str(job.get("source_path") or "")).resolve()
        status_path = Path(str(job.get("status_path") or "")).resolve()
        if not status_path.is_relative_to(job_root):
            raise ValueError("音色包导入状态路径无效。")
        if not source_path.is_file():
            raise ValueError("选择的音色包不存在。")

        from app import companion_service

        last_progress_write_at = 0.0
        last_progress_percent: int | None = None
        last_progress_phase = ""
        last_progress_message = ""

        def report(progress: dict[str, object]) -> None:
            nonlocal last_progress_write_at, last_progress_percent, last_progress_phase, last_progress_message
            now = time.monotonic()
            phase = str(progress.get("phase") or "")
            message = str(progress.get("message") or "")
            try:
                percent = int(progress.get("percent", 0))
            except (TypeError, ValueError):
                percent = 0
            phase_changed = phase != last_progress_phase or message != last_progress_message
            if not phase_changed and now - last_progress_write_at < 0.25:
                return
            if not phase_changed and percent == last_progress_percent and now - last_progress_write_at < 1.0:
                return
            last_progress_write_at = now
            last_progress_percent = percent
            last_progress_phase = phase
            last_progress_message = message
            try:
                _write_state_json(status_path, {"state": "running", **progress})
            except OSError as exc:
                logging.warning("Unable to record voice package import progress: %s", exc)

        report({"phase": "checking", "message": "正在检查音色包", "percent": 0})
        imported = companion_service.import_voice_package_file(source_path, progress=report)
        _write_state_json(
            status_path,
            {
                "state": "completed",
                "ok": True,
                "phase": "completed",
                "message": "音色包导入完成",
                "percent": 100,
                "imported": imported,
            },
            attempts=40,
            retry_delay_seconds=0.05,
        )
        return 0
    except Exception as exc:
        logging.error("Voice package import worker failed: %s\n%s", exc, traceback.format_exc())
        try:
            status_value = locals().get("status_path")
            if isinstance(status_value, Path) and status_value.is_relative_to(job_root):
                _write_state_json(
                    status_value,
                    {
                        "state": "completed",
                        "ok": False,
                        "phase": "failed",
                        "message": "音色包导入失败",
                        "error": str(exc),
                    },
                    attempts=40,
                    retry_delay_seconds=0.05,
                )
        except Exception:
            logging.exception("Unable to record voice package import failure")
        return 1


def _local_backend_diagnostics() -> dict[str, object]:
    try:
        from app.runtime_diagnostics import snapshot

        result = snapshot()
        try:
            from app.subservice_health import snapshot as subservice_snapshot

            result["subservices"] = subservice_snapshot()
        except Exception:
            logging.exception("Unable to collect local subservice diagnostics")
        return result if isinstance(result, dict) else {}
    except ModuleNotFoundError:
        return {}
    except Exception:
        logging.exception("Unable to collect local backend diagnostics")
        return {}


def _recover_local_subservices(diagnostics: dict[str, object]) -> dict[str, object]:
    try:
        from app.subservice_health import recover_failed

        health = diagnostics.get("subservices")
        return recover_failed(health if isinstance(health, dict) else None)
    except ModuleNotFoundError:
        return {"attempted": False, "recovered": [], "failed": [], "fused": []}
    except Exception as exc:
        logging.exception("Unable to recover failed backend subservices")
        return {
            "attempted": True,
            "recovered": [],
            "failed": [{"service_id": "unknown", "error": str(exc)[:500]}],
            "fused": [],
        }


def _record_backend_recovery_state(
    status: str,
    reason: str,
    *,
    thread_alive: bool,
    port_open: bool,
    diagnostics: dict[str, object],
    attempts_in_window: int,
) -> None:
    try:
        _write_state_json(
            BACKEND_RECOVERY_STATE_PATH,
            {
                "status": status,
                "updated_at_epoch": time.time(),
                "reason": reason,
                "thread_alive": thread_alive,
                "port_open": port_open,
                "attempts_in_window": attempts_in_window,
                "diagnostics": diagnostics,
            },
        )
    except OSError:
        logging.exception("Unable to persist backend recovery state")


def _claim_full_recovery(
    failure_kind: str,
    *,
    history_path: Path = FULL_RECOVERY_HISTORY_PATH,
    now: float | None = None,
    window_seconds: float = FULL_RECOVERY_WINDOW_SECONDS,
    max_attempts: int = FULL_RECOVERY_MAX_ATTEMPTS,
) -> bool:
    current = time.time() if now is None else float(now)
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        history = list(payload.get("attempts") or []) if isinstance(payload, dict) else []
    except (OSError, ValueError, json.JSONDecodeError):
        history = []
    cutoff = current - max(1.0, float(window_seconds))
    recent = [
        item
        for item in history
        if isinstance(item, dict) and float(item.get("at_epoch") or 0) >= cutoff
    ]
    allowed = len(recent) < max(1, int(max_attempts))
    if allowed:
        recent.append({"at_epoch": current, "failure_kind": str(failure_kind)[:100]})
    _write_state_json(
        history_path,
        {
            "status": "armed" if allowed else "fused",
            "window_seconds": float(window_seconds),
            "max_attempts": int(max_attempts),
            "attempts": recent,
        },
    )
    return allowed


def _watch_backend_health(
    stop_event: threading.Event,
    runtime_root: Path,
    *,
    interval_seconds: float = BACKEND_HEALTH_CHECK_INTERVAL_SECONDS,
    failure_threshold: int = BACKEND_HEALTH_FAILURE_THRESHOLD,
    backend_runtime=None,
    diagnostics_provider=_local_backend_diagnostics,
    subservice_recovery_provider=_recover_local_subservices,
    recovery_window_seconds: float = BACKEND_RECOVERY_WINDOW_SECONDS,
    recovery_max_attempts: int = BACKEND_RECOVERY_MAX_ATTEMPTS,
) -> None:
    consecutive_failures = 0
    recovery_attempts: deque[float] = deque()
    while not stop_event.wait(max(0.01, interval_seconds)):
        if _backend_health_matches_runtime(runtime_root):
            consecutive_failures = 0
            continue
        consecutive_failures += 1
        logging.warning(
            "Mio backend health check failed (%s/%s): %s",
            consecutive_failures,
            failure_threshold,
            _LAST_BACKEND_HEALTH_ERROR or "健康接口不可用",
        )
        if consecutive_failures >= max(1, failure_threshold):
            detail = _LAST_BACKEND_HEALTH_ERROR or "健康接口不可用"
            backend_thread = backend_runtime.current_thread() if backend_runtime is not None else None
            thread_alive = bool(backend_thread is not None and backend_thread.is_alive())
            port_open = _port_is_open()
            diagnostics = diagnostics_provider()
            port_text = f"{PORT}端口" + ("仍在监听" if port_open else "未监听")
            reason = (
                f"后端连续 {consecutive_failures} 次健康检查失败；最近错误：{detail}；"
                f"{'后端线程存活' if thread_alive else '后端线程已退出或不可管理'}；"
                f"{port_text}"
            )
            if thread_alive and port_open:
                subservice_recovery = subservice_recovery_provider(diagnostics)
                diagnostics = dict(diagnostics)
                diagnostics["subservice_recovery"] = subservice_recovery
                recovered_services = list(subservice_recovery.get("recovered") or [])
                logging.error(
                    "Backend health timed out while thread and port stayed alive; "
                    "full application recovery suppressed. recovered_subservices=%s "
                    "reason=%s diagnostics=%s",
                    recovered_services,
                    reason,
                    json.dumps(diagnostics, ensure_ascii=False, sort_keys=True),
                )
                _record_backend_recovery_state(
                    "subservice_recovered" if recovered_services else "degraded_no_restart",
                    reason,
                    thread_alive=True,
                    port_open=True,
                    diagnostics=diagnostics,
                    attempts_in_window=len(recovery_attempts),
                )
                consecutive_failures = 0
                continue

            current = time.monotonic()
            cutoff = current - max(1.0, recovery_window_seconds)
            while recovery_attempts and recovery_attempts[0] < cutoff:
                recovery_attempts.popleft()
            if len(recovery_attempts) >= max(1, recovery_max_attempts):
                logging.critical("Backend recovery circuit opened: %s", reason)
                _record_backend_recovery_state(
                    "fused",
                    reason,
                    thread_alive=thread_alive,
                    port_open=port_open,
                    diagnostics=diagnostics,
                    attempts_in_window=len(recovery_attempts),
                )
                return
            if backend_runtime is None:
                logging.error("Backend cannot be recovered independently: %s", reason)
                _record_backend_recovery_state(
                    "unmanaged",
                    reason,
                    thread_alive=thread_alive,
                    port_open=port_open,
                    diagnostics=diagnostics,
                    attempts_in_window=len(recovery_attempts),
                )
                return

            recovery_attempts.append(current)
            if backend_runtime.recover(reason):
                logging.warning("Backend recovered without restarting the WebView: %s", reason)
                _record_backend_recovery_state(
                    "recovered",
                    reason,
                    thread_alive=thread_alive,
                    port_open=port_open,
                    diagnostics=diagnostics,
                    attempts_in_window=len(recovery_attempts),
                )
                consecutive_failures = 0
                continue
            logging.critical("Independent backend recovery failed: %s", reason)
            _record_backend_recovery_state(
                "recovery_failed",
                reason,
                thread_alive=thread_alive,
                port_open=port_open,
                diagnostics=diagnostics,
                attempts_in_window=len(recovery_attempts),
            )
            return


def _start_backend(runtime_root: Path):
    existing = _health()
    if existing is not None:
        if not _same_path(existing.get("project_root"), runtime_root):
            raise RuntimeError(f"{PORT}端口上正在运行另一份 Mio 后端。请先关闭旧后端，再启动桌面应用。")
        return None, None
    if _port_is_open():
        raise RuntimeError(f"{PORT}端口已被其他程序占用，Mio 无法启动。")

    import uvicorn
    from app.main import app

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="mio-backend", daemon=True)
    thread.start()

    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        health = _health()
        if health is not None and _same_path(health.get("project_root"), runtime_root):
            return server, thread
        if not thread.is_alive():
            break
        time.sleep(0.25)
    server.should_exit = True
    raise RuntimeError(f"Mio 的后端没有正常启动。日志位置：{LOG_PATH}")


class BackendRuntime:
    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.server = None
        self.thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            self.server, self.thread = _start_backend(self.runtime_root)

    def current_thread(self) -> threading.Thread | None:
        with self._lock:
            return self.thread

    def stop(self, *, timeout: float = 8) -> bool:
        with self._lock:
            server = self.server
            thread = self.thread
            if server is not None:
                server.should_exit = True
            if thread is not None:
                thread.join(timeout=max(0.0, timeout))
            stopped = thread is None or not thread.is_alive()
            if stopped:
                self.server = None
                self.thread = None
            return stopped

    def recover(self, reason: str) -> bool:
        logging.error("Attempting independent backend recovery: %s", reason)
        if not self.stop(timeout=8):
            logging.error("Backend thread did not stop; recovery aborted")
            return False
        try:
            self.start()
        except Exception:
            logging.exception("Failed to restart embedded backend")
            return False
        return self.thread is not None and self.thread.is_alive() and _port_is_open()


def _tray_image():
    from PIL import Image, ImageDraw

    candidates = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "desktop" / "mio.ico",
        Path(__file__).resolve().parent / "mio.ico",
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return Image.open(candidate).convert("RGBA")
            except OSError:
                pass
    image = Image.new("RGBA", (64, 64), "#eaf1f4")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill="#8aadb9")
    draw.ellipse((22, 20, 27, 25), fill="#ffffff")
    draw.ellipse((37, 20, 42, 25), fill="#ffffff")
    draw.arc((20, 22, 44, 45), 20, 160, fill="#ffffff", width=3)
    return image


def _background_notification_text(rows) -> str:
    messages = [" ".join(str(row["content"] or "").split()) for row in rows]
    messages = [message for message in messages if message]
    if not messages:
        return "Mio 发来了一条新消息"
    visible = messages[-3:]
    body = "\n".join(visible)
    if len(messages) > len(visible):
        body = f"还有 {len(messages) - len(visible)} 条消息\n{body}"
    return body if len(body) <= 240 else body[:237].rstrip() + "..."


def _watch_background_messages(tray_icon, window_in_background: threading.Event, stop: threading.Event) -> None:
    from app import db
    from app.config import settings

    conversation_id = (
        f"qq_private_{settings.qq_allowed_user_ids[0]}"
        if settings.qq_allowed_user_ids
        else "default"
    )

    last_message_id: int | None = None
    while not stop.wait(2):
        try:
            if last_message_id is None:
                last_message_id = db.get_latest_message_id(
                    role="assistant",
                    conversation_id=conversation_id,
                )
                continue
            rows = db.get_messages_after_id(
                last_message_id,
                role="assistant",
                limit=50,
                conversation_id=conversation_id,
            )
            if not rows:
                continue
            last_message_id = int(rows[-1]["id"])
            if not window_in_background.is_set():
                continue

            # Give consecutive short bubbles a moment to arrive, then show one notification.
            if stop.wait(0.8):
                return
            extra_rows = db.get_messages_after_id(
                last_message_id,
                role="assistant",
                limit=50,
                conversation_id=conversation_id,
            )
            if extra_rows:
                rows.extend(extra_rows)
                last_message_id = int(extra_rows[-1]["id"])
            if not _read_desktop_preferences()["background_notifications"]:
                continue
            tray_icon.notify(_background_notification_text(rows), "Mio 发来消息")
        except Exception:
            logging.exception("Failed to show background message notification")


def _webview_failure_action(failure_kind: str) -> str:
    normalized = failure_kind.rsplit(".", 1)[-1].lower()
    if normalized in {
        "renderprocessexited",
        "framerenderprocessexited",
        "renderprocessunresponsive",
    }:
        return "reload"
    if normalized in {"browserprocessexited", "gpuprocessexited"}:
        return "restart"
    return "log"


def _screen_preview_command() -> list[str]:
    arguments = [SCREEN_PREVIEW_ARGUMENT, f"{SCREEN_PREVIEW_PARENT_ARGUMENT}{os.getpid()}"]
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), *arguments]


def _pet_chat_window_command() -> list[str]:
    arguments = [PET_CHAT_WINDOW_ARGUMENT, f"{PET_CHAT_PARENT_ARGUMENT}{os.getpid()}"]
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), *arguments]


def _notify_pet_chat_window_state(open_: bool) -> bool:
    request = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/companion/chat-window/state",
        data=json.dumps({"open": bool(open_)}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return 200 <= int(response.status) < 300
    except (OSError, urllib.error.URLError):
        return False


def _pet_chat_dom_diagnostic_script() -> str:
    return r"""
(() => {
  const composer = document.querySelector('.standalone-pet-chat-composer');
  const drag = document.querySelector('.standalone-pet-chat-drag');
  const textarea = document.querySelector('.standalone-pet-chat-composer textarea');
  const rectOf = (element) => {
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {
      left: Number(rect.left.toFixed(2)),
      top: Number(rect.top.toFixed(2)),
      right: Number(rect.right.toFixed(2)),
      bottom: Number(rect.bottom.toFixed(2)),
      width: Number(rect.width.toFixed(2)),
      height: Number(rect.height.toFixed(2)),
    };
  };
  const labelOf = (element) => {
    if (!element) return 'NONE';
    const className = typeof element.className === 'string'
      ? element.className.trim().split(/\s+/).filter(Boolean).slice(0, 3).join('.')
      : '';
    return `${element.tagName || 'UNKNOWN'}${className ? `.${className}` : ''}`;
  };
  const base = {
    ready: Boolean(composer && drag && textarea),
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      device_pixel_ratio: window.devicePixelRatio || 1,
    },
    composer: { rect: rectOf(composer) },
    drag: { rect: rectOf(drag) },
    textarea: { rect: rectOf(textarea) },
  };
  if (!base.ready) return JSON.stringify(base);

  const dragRect = drag.getBoundingClientRect();
  const textareaRect = textarea.getBoundingClientRect();
  const overlapWidth = Math.max(0, Math.min(dragRect.right, textareaRect.right) - Math.max(dragRect.left, textareaRect.left));
  const overlapHeight = Math.max(0, Math.min(dragRect.bottom, textareaRect.bottom) - Math.max(dragRect.top, textareaRect.top));
  const dragPoint = {
    x: dragRect.left + dragRect.width / 2,
    y: dragRect.top + dragRect.height / 2,
  };
  const dragHit = document.elementFromPoint(dragPoint.x, dragPoint.y);
  const inputSamples = [];
  for (let row = 0; row < 4; row += 1) {
    for (let column = 0; column < 5; column += 1) {
      const x = textareaRect.left + textareaRect.width * ((column + 0.5) / 5);
      const y = textareaRect.top + textareaRect.height * ((row + 0.5) / 4);
      const hit = document.elementFromPoint(x, y);
      inputSamples.push({
        x: Number(x.toFixed(2)),
        y: Number(y.toFixed(2)),
        hit: labelOf(hit),
        textarea: hit === textarea || textarea.contains(hit),
        cursor: hit ? window.getComputedStyle(hit).cursor : '',
      });
    }
  }
  base.drag.cursor = window.getComputedStyle(drag).cursor;
  base.drag.hit = labelOf(dragHit);
  base.drag.hit_matches = dragHit === drag || drag.contains(dragHit);
  base.textarea.cursor = window.getComputedStyle(textarea).cursor;
  base.textarea.hit_samples = inputSamples;
  base.textarea.hit_count = inputSamples.filter((sample) => sample.textarea).length;
  base.textarea.cursor_values = [...new Set(inputSamples.map((sample) => sample.cursor))];
  base.overlap = {
    width: Number(overlapWidth.toFixed(2)),
    height: Number(overlapHeight.toFixed(2)),
    area: Number((overlapWidth * overlapHeight).toFixed(2)),
  };
  base.passed = (
    base.overlap.area === 0
    && base.drag.cursor === 'move'
    && base.drag.hit_matches
    && base.textarea.cursor === 'text'
    && base.textarea.hit_count === inputSamples.length
    && inputSamples.every((sample) => sample.cursor === 'text')
  );
  return JSON.stringify(base);
})()
""".strip()


def _collect_pet_chat_dom_diagnostics(
    window,
    *,
    attempts: int = 50,
    interval_seconds: float = 0.1,
) -> dict[str, object] | None:
    last_result: dict[str, object] | None = None
    last_error = ""
    for attempt in range(max(1, int(attempts))):
        try:
            raw_result = window.evaluate_js(_pet_chat_dom_diagnostic_script())
            result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            if isinstance(result, dict):
                last_result = result
                if result.get("ready"):
                    logging.info(
                        "Pet chat DOM diagnostics: %s",
                        json.dumps(result, ensure_ascii=False, sort_keys=True),
                    )
                    return result
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < max(1, int(attempts)):
            time.sleep(max(0.0, float(interval_seconds)))
    logging.warning(
        "Pet chat DOM diagnostics unavailable: result=%s error=%s",
        json.dumps(last_result, ensure_ascii=False, sort_keys=True) if last_result else "none",
        last_error or "none",
    )
    return last_result


def _notify_window_topology(
    window_id: str,
    action: str,
    *,
    window=None,
    pid: int | None = None,
    visible: bool = False,
    focused: bool = False,
) -> bool:
    bounds = {}
    # pywebview position properties block until the native window exists.
    # The "created" event is emitted before webview.start(), so it must carry
    # empty bounds instead of waiting on properties that cannot resolve yet.
    native_window = getattr(window, "native", None) if window is not None else None
    if native_window is not None:
        try:
            bounds = {
                "x": int(getattr(window, "x", 0) or 0),
                "y": int(getattr(window, "y", 0) or 0),
                "width": int(getattr(window, "width", 0) or 0),
                "height": int(getattr(window, "height", 0) or 0),
            }
        except (TypeError, ValueError):
            bounds = {}
    payload = {
        "source": "desktop-launcher",
        "runtime": "pywebview",
        "window_id": str(window_id),
        "pid": max(0, int(pid if pid is not None else os.getpid())),
        "action": str(action),
        "correlation_id": f"{os.getpid()}-{time.time_ns()}",
        "visible": bool(visible),
        "focused": bool(focused),
        "bounds": bounds,
    }
    request = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/companion/window-topology/events",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return 200 <= int(response.status) < 300
    except (OSError, urllib.error.URLError):
        return False


def _process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return True
    synchronize = 0x00100000
    wait_timeout = 0x00000102
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _focus_process_window(process_id: int) -> bool:
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(window_handle, _lparam):
        owner = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(window_handle, ctypes.byref(owner))
        if owner.value == process_id and ctypes.windll.user32.IsWindowVisible(window_handle):
            found.append(int(window_handle))
            return False
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    if not found:
        return False
    window_handle = found[0]
    ctypes.windll.user32.ShowWindow(window_handle, 9)
    ctypes.windll.user32.SetForegroundWindow(window_handle)
    return True


class DesktopBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._preview_process: subprocess.Popen[bytes] | None = None
        self._pet_chat_process: subprocess.Popen[bytes] | None = None
        self._voice_import_jobs: dict[str, dict[str, object]] = {}
        self._window = None
        self._hide_window = None
        self._window_maximized = False

    def attach_window(self, window, hide_window) -> None:
        self._window = window
        self._hide_window = hide_window

    def hide_pet_chat_window(self) -> dict[str, object]:
        with self._lock:
            process = self._pet_chat_process
            self._pet_chat_process = None
        already_closed = process is None or process.poll() is not None
        process_id = int(getattr(process, "pid", 0) or 0) if process is not None else 0
        _notify_pet_chat_window_state(False)
        try:
            self._stop_child_process(process)
        except (OSError, subprocess.SubprocessError) as exc:
            logging.exception("Failed to close the desktop pet chat window")
            return {"ok": False, "error": str(exc)}
        if not already_closed:
            _notify_window_topology("pet-chat-input", "closed", pid=process_id)
        return {"ok": True, "already_closed": already_closed}

    def toggle_pet_chat_window(self) -> dict[str, object]:
        with self._lock:
            process = self._pet_chat_process
            visible = process is not None and process.poll() is None
        if visible:
            result = self.hide_pet_chat_window()
            return {**result, "visible": False}
        result = self.open_pet_chat_window()
        return {**result, "visible": bool(result.get("ok"))}

    def window_control(self, action: str) -> dict[str, object]:
        window = self._window
        if window is None:
            return {"ok": False, "error": "主窗口尚未就绪"}
        if action == "minimize":
            window.minimize()
        elif action == "maximize":
            if self._window_maximized:
                window.restore()
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

    def get_desktop_preferences(self) -> dict[str, object]:
        return {
            "ok": True,
            **_read_desktop_preferences(),
            "windows_startup": _windows_startup_enabled(),
        }

    def set_desktop_preferences(self, values: dict[str, object] | None = None) -> dict[str, object]:
        values = values if isinstance(values, dict) else {}
        try:
            preferences = _write_desktop_preferences(values)
            windows_startup = _windows_startup_enabled()
            if "windows_startup" in values:
                windows_startup = _set_windows_startup(bool(values["windows_startup"]))
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **preferences, "windows_startup": windows_startup}

    def open_screen_preview(self) -> dict[str, object]:
        with self._lock:
            if self._preview_process is not None and self._preview_process.poll() is None:
                return {"ok": True, "already_open": True}
            try:
                self._preview_process = subprocess.Popen(
                    _screen_preview_command(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                logging.exception("Failed to open the isolated screen preview")
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "already_open": False}

    def open_pet_chat_window(self) -> dict[str, object]:
        with self._lock:
            if self._pet_chat_process is not None and self._pet_chat_process.poll() is None:
                _focus_process_window(self._pet_chat_process.pid)
                return {"ok": True, "already_open": True}
            try:
                self._pet_chat_process = subprocess.Popen(
                    _pet_chat_window_command(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                logging.exception("Failed to open the desktop pet chat window")
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "already_open": False}

    def import_live2d_model(self) -> dict[str, object]:
        if self._window is None:
            return {"ok": False, "error": "主窗口尚未就绪。"}
        try:
            import webview
            from app import companion_service

            selected = self._window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=str(Path.home()),
                allow_multiple=False,
            )
            if not selected:
                return {"ok": False, "canceled": True}
            source_path = selected[0] if isinstance(selected, (list, tuple)) else selected
            model = companion_service.import_live2d_model_directory(str(source_path))
            companion_service.save_config({"pet_renderer": "live2d", "live2d_model_id": model["id"]})
            if companion_service.pet_running():
                companion_service.restart_pet()
            return {"ok": True, "model": model}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def import_voice_package(self) -> dict[str, object]:
        """Select a package and import it outside the WebView and main application process."""
        if self._window is None:
            return {"ok": False, "error": "主窗口尚未就绪。"}
        job_path: Path | None = None
        status_path: Path | None = None
        try:
            import webview

            selected = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                directory=str(Path.home()),
                allow_multiple=False,
                file_types=("ZIP 音色包 (*.zip)",),
            )
            if not selected:
                return {"ok": False, "canceled": True}
            source_path = Path(selected[0] if isinstance(selected, (list, tuple)) else selected).resolve()
            if not source_path.is_file() or source_path.stat().st_size <= 0:
                return {"ok": False, "error": "选择的音色包不存在或为空。"}

            job_id = uuid.uuid4().hex
            job_dir = _voice_package_import_job_dir()
            job_path = job_dir / f"{job_id}.job.json"
            status_path = job_dir / f"{job_id}.status.json"
            _write_state_json(
                job_path,
                {"source_path": str(source_path), "status_path": str(status_path)},
            )
            _write_state_json(
                status_path,
                {"state": "starting", "phase": "starting", "message": "正在启动独立导入任务", "percent": 0},
            )
            process = subprocess.Popen(
                _voice_package_import_command(job_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self._lock:
                self._voice_import_jobs[job_id] = {
                    "process": process,
                    "job_path": job_path,
                    "status_path": status_path,
                }
            logging.info(
                "Started isolated voice package import: job=%s pid=%s size=%s",
                job_id,
                process.pid,
                source_path.stat().st_size,
            )
            return {
                "ok": True,
                "started": True,
                "job_id": job_id,
                "filename": source_path.name,
                "total_bytes": source_path.stat().st_size,
            }
        except (OSError, ValueError) as exc:
            if job_path is not None:
                job_path.unlink(missing_ok=True)
            if status_path is not None:
                status_path.unlink(missing_ok=True)
            return {"ok": False, "error": str(exc)}

    def voice_package_import_status(self, job_id: str) -> dict[str, object]:
        safe_job_id = str(job_id or "").strip()
        with self._lock:
            job = self._voice_import_jobs.get(safe_job_id)
        if job is None:
            return {"state": "completed", "ok": False, "error": "找不到这次音色包导入任务。"}

        process = job.get("process")
        job_path = job.get("job_path")
        status_path = job.get("status_path")
        status: dict[str, object] = {}
        if isinstance(status_path, Path):
            try:
                loaded = json.loads(status_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    status = loaded
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                status = {}
        return_code = process.poll() if process is not None and hasattr(process, "poll") else 1
        if status.get("state") != "completed" and return_code is not None:
            status = {
                "state": "completed",
                "ok": False,
                "phase": "failed",
                "error": f"独立导入任务异常退出（代码 {return_code}），主应用没有受到影响。",
            }
        if status.get("state") == "completed":
            with self._lock:
                self._voice_import_jobs.pop(safe_job_id, None)
            if isinstance(job_path, Path):
                job_path.unlink(missing_ok=True)
            if isinstance(status_path, Path):
                status_path.unlink(missing_ok=True)
            logging.info("Finished isolated voice package import: job=%s ok=%s", safe_job_id, status.get("ok"))
        return status or {"state": "running", "phase": "starting", "message": "正在启动独立导入任务", "percent": 0}

    @staticmethod
    def _stop_child_process(process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def close_child_windows(self) -> None:
        with self._lock:
            processes = (self._preview_process, self._pet_chat_process)
            pet_chat_process = self._pet_chat_process
            self._preview_process = None
            self._pet_chat_process = None
        pet_chat_was_active = pet_chat_process is not None and pet_chat_process.poll() is None
        pet_chat_pid = int(getattr(pet_chat_process, "pid", 0) or 0) if pet_chat_process is not None else 0
        for process in processes:
            try:
                self._stop_child_process(process)
            except (OSError, subprocess.SubprocessError):
                logging.exception("Failed to close a Mio child window")
        if pet_chat_was_active:
            _notify_pet_chat_window_state(False)
            _notify_window_topology("pet-chat-input", "closed", pid=pet_chat_pid)


class PetChatWindowBridge:
    def __init__(self) -> None:
        self._window = None
        self._closing = threading.Event()

    def attach_window(self, window) -> None:
        self._window = window

    def window_drag(self) -> dict[str, object]:
        window = self._window
        native = getattr(window, "native", None) if window is not None else None
        if native is None or os.name != "nt":
            return {"ok": False, "error": "桌宠对话窗口尚未就绪"}
        handle = int(native.Handle.ToInt64())
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.SendMessageW(handle, 0x00A1, 2, 0)
        return {"ok": True}

    def hide_pet_chat_window(self) -> dict[str, object]:
        window = self._window
        if window is None or self._closing.is_set():
            return {"ok": True, "already_closed": True}
        self._closing.set()
        _notify_window_topology("pet-chat-input", "hidden", window=window)
        try:
            window.evaluate_js("window.dispatchEvent(new CustomEvent('mio:pet-chat-hidden'))")
        except Exception:
            logging.exception("Failed to notify the desktop pet chat before closing")
        threading.Timer(0.05, window.destroy).start()
        return {"ok": True, "already_closed": False}


def _run_pet_chat_window(parent_pid: int) -> int:
    import webview

    _configure_webview_runtime()
    bridge = PetChatWindowBridge()
    window = webview.create_window(
        "",
        f"http://{HOST}:{PORT}/agent-app/?v={WEBVIEW_CACHE_REPAIR_VERSION}#pet-chat-window",
        js_api=bridge,
        width=PET_CHAT_WINDOW_SIZE[0],
        height=PET_CHAT_WINDOW_SIZE[1],
        min_size=(360, 76),
        resizable=True,
        frameless=True,
        easy_drag=False,
        shadow=False,
        on_top=True,
        transparent=True,
        background_color="#010203",
        confirm_close=False,
    )
    bridge.attach_window(window)
    _notify_window_topology("pet-chat-input", "created", window=window)
    diagnostic_started = threading.Event()

    def configure_window(action: str) -> None:
        try:
            _position_pet_chat_window(window)
            _make_pet_chat_window_transparent(window)
            _make_pet_chat_window_interactive(window)
            _notify_window_topology(
                "pet-chat-input",
                action,
                window=window,
                visible=True,
                focused=action == "shown",
            )
        except Exception:
            logging.exception("Failed to configure the desktop pet chat window")

    def handle_loaded() -> None:
        configure_window("loaded")
        if diagnostic_started.is_set():
            return
        diagnostic_started.set()
        threading.Thread(
            target=_collect_pet_chat_dom_diagnostics,
            args=(window,),
            name="mio-pet-chat-dom-diagnostics",
            daemon=True,
        ).start()

    window.events.shown += lambda: configure_window("shown")
    window.events.loaded += handle_loaded
    if parent_pid > 0:
        def watch_parent() -> None:
            while _process_is_alive(parent_pid):
                time.sleep(1)
            try:
                window.destroy()
            except Exception:
                pass

        threading.Thread(target=watch_parent, name="mio-pet-chat-parent", daemon=True).start()

    webview.start(
        gui="edgechromium",
        debug=False,
        private_mode=False,
        storage_path=str(PET_CHAT_WEBVIEW_DATA_DIR),
    )
    _notify_window_topology("pet-chat-input", "closed", window=window)
    _notify_pet_chat_window_state(False)
    return 0


def _run_window(
    show_event_handle: int,
    pet_chat_event_handle: int,
    runtime_root: Path,
    backend_runtime: BackendRuntime | None = None,
) -> None:
    global _WEBVIEW_RECOVERY_CALLBACK

    import webview
    import pystray

    _install_webview_recovery()

    webview.settings["ALLOW_DOWNLOADS"] = True
    bridge = DesktopBridge()
    window = webview.create_window(
        APP_NAME,
        f"http://{HOST}:{PORT}/agent-app/?v={WEBVIEW_CACHE_REPAIR_VERSION}",
        js_api=bridge,
        width=1280,
        height=820,
        min_size=MAIN_WINDOW_MIN_SIZE,
        resizable=True,
        frameless=True,
        # Only the custom titlebar carries the drag region.  Leaving easy_drag
        # enabled makes every click in a frameless window look like a move.
        easy_drag=False,
        shadow=True,
        background_color="#e9eff1",
        confirm_close=False,
    )
    _notify_window_topology("agent-main", "created", window=window)
    exit_requested = threading.Event()
    restart_requested = threading.Event()
    listener_stop = threading.Event()
    window_in_background = threading.Event()
    webview_loaded = threading.Event()
    webview_dom_ready = threading.Event()

    def mark_loaded():
        webview_loaded.set()
        _notify_window_topology("agent-main", "loaded", window=window, visible=True)

    def mark_dom_ready():
        try:
            result = window.evaluate_js("document.documentElement.dataset.mioReady === 'true'")
            if result is True or str(result).lower() == "true":
                webview_dom_ready.set()
        except Exception:
            logging.debug("WebView DOM readiness probe is not available yet", exc_info=True)

    window.events.loaded += mark_loaded

    def configure_main_window():
        try:
            _make_main_window_resizable(window)
        except Exception:
            logging.exception("Failed to enable native main-window resizing")

    def report_main_window_shown():
        _notify_window_topology("agent-main", "shown", window=window, visible=True, focused=True)

    window.events.shown += configure_main_window
    window.events.shown += report_main_window_shown
    window.events.loaded += configure_main_window

    def hide_in_background():
        if exit_requested.is_set():
            return True
        if not _read_desktop_preferences()["close_to_background"]:
            exit_requested.set()
            tray_icon.stop()
            return True
        window_in_background.set()
        _notify_window_topology("agent-main", "hidden", window=window)
        # The WinForms closing callback runs on the UI thread. Hide only after
        # the close has been cancelled, otherwise pywebview can finish closing.
        threading.Timer(0.05, window.hide).start()
        return False

    window.events.closing += hide_in_background
    bridge.attach_window(window, hide_in_background)

    def mark_minimized():
        window_in_background.set()
        _notify_window_topology("agent-main", "minimized", window=window)

    def mark_restored():
        window_in_background.clear()
        _notify_window_topology("agent-main", "shown", window=window, visible=True, focused=True)

    window.events.minimized += mark_minimized
    window.events.restored += mark_restored

    tray_icon = pystray.Icon(APP_NAME, _tray_image(), APP_NAME)

    def request_webview_recovery(failure_kind: str, reason: str):
        if exit_requested.is_set() or restart_requested.is_set():
            return
        try:
            recovery_allowed = _claim_full_recovery(failure_kind)
        except OSError:
            logging.exception("Unable to update full recovery circuit state")
            recovery_allowed = False
        if not recovery_allowed:
            logging.critical(
                "Full application recovery circuit opened: kind=%s reason=%s",
                failure_kind,
                reason,
            )
            _message_box(
                "Mio 连续恢复失败，已停止自动重启以保留现场。请保持应用关闭并查看桌面日志。",
                error=True,
            )
            return
        restart_requested.set()
        if _failure_requires_webview_cache_repair(failure_kind):
            _write_webview_failure_marker(failure_kind, reason)
        logging.error(
            "Restarting Mio after unrecoverable runtime failure: kind=%s reason=%s",
            failure_kind,
            reason,
        )
        try:
            _spawn_webview_recovery_process()
        except Exception:
            logging.exception("Failed to launch WebView2 recovery process")

        def close_failed_window():
            exit_requested.set()
            tray_icon.stop()
            bridge.close_child_windows()
            try:
                window.destroy()
            except Exception:
                logging.exception("Failed to close the broken WebView2 window")

        def force_exit_if_stuck():
            if restart_requested.is_set():
                logging.error("WebView2 window did not close in time; forcing process exit")
                os._exit(0)

        threading.Timer(0.1, close_failed_window).start()
        threading.Timer(6, force_exit_if_stuck).start()

    _WEBVIEW_RECOVERY_CALLBACK = request_webview_recovery

    def restore_window(*, open_pet_chat: bool = False):
        try:
            window_in_background.clear()
            window.show()
            _notify_window_topology("agent-main", "shown", window=window, visible=True, focused=True)
            if open_pet_chat:
                window.evaluate_js(
                    "window.location.hash = '#desktop-pet-chat';"
                    "window.dispatchEvent(new CustomEvent('mio:open-pet-chat'));"
                )
        except Exception:
            logging.exception("Failed to restore Mio window")

    def show_window(_icon=None, _item=None):
        restore_window()

    def exit_app(_icon=None, _item=None):
        exit_requested.set()
        tray_icon.stop()
        bridge.close_child_windows()
        try:
            window.destroy()
        except Exception:
            logging.exception("Failed to close Mio window")

    tray_icon.menu = pystray.Menu(
        pystray.MenuItem("打开 Mio", show_window, default=True),
        pystray.MenuItem("退出 Mio", exit_app),
    )

    def listen_for_restore():
        while not listener_stop.is_set():
            result = ctypes.windll.kernel32.WaitForSingleObject(show_event_handle, 500)
            if result == 0:
                show_window()

    listener = threading.Thread(target=listen_for_restore, name="mio-window-restore", daemon=True)
    listener.start()

    def listen_for_pet_chat():
        while not listener_stop.is_set():
            result = ctypes.windll.kernel32.WaitForSingleObject(pet_chat_event_handle, 500)
            if result == 0:
                bridge.toggle_pet_chat_window()

    pet_chat_listener = threading.Thread(
        target=listen_for_pet_chat,
        name="mio-pet-chat-restore",
        daemon=True,
    )
    pet_chat_listener.start()
    tray_thread = threading.Thread(target=tray_icon.run, name="mio-tray", daemon=True)
    tray_thread.start()
    notification_thread = threading.Thread(
        target=_watch_background_messages,
        args=(tray_icon, window_in_background, listener_stop),
        name="mio-background-notifications",
        daemon=True,
    )
    notification_thread.start()

    def watch_webview_heartbeat():
        if not webview_loaded.wait(45):
            if not listener_stop.is_set() and not exit_requested.is_set():
                request_webview_recovery("HeartbeatStartupTimeout", "页面在 45 秒内没有完成加载")
            return
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not listener_stop.is_set() and not exit_requested.is_set():
            mark_dom_ready()
            if webview_dom_ready.is_set():
                break
            time.sleep(0.25)
        if not webview_dom_ready.is_set() and not listener_stop.is_set() and not exit_requested.is_set():
            request_webview_recovery("DomReadyTimeout", "页面已加载但应用在 30 秒内没有报告可操作状态")
            return
        consecutive_failures = 0
        while not listener_stop.wait(20):
            if exit_requested.is_set() or restart_requested.is_set():
                return
            probe_finished = threading.Event()
            probe_succeeded = threading.Event()

            def probe():
                try:
                    window.evaluate_js("Date.now()")
                    probe_succeeded.set()
                except Exception:
                    logging.exception("WebView2 heartbeat probe failed")
                finally:
                    probe_finished.set()

            threading.Thread(target=probe, name="mio-webview-heartbeat-probe", daemon=True).start()
            if not probe_finished.wait(12):
                request_webview_recovery("HeartbeatUnresponsive", "页面连续 12 秒没有响应")
                return
            if probe_succeeded.is_set():
                consecutive_failures = 0
                continue
            consecutive_failures += 1
            if consecutive_failures >= 2:
                request_webview_recovery("HeartbeatFailed", "页面连续两次无法执行脚本")
                return

    heartbeat_thread = threading.Thread(
        target=watch_webview_heartbeat,
        name="mio-webview-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    backend_health_thread = threading.Thread(
        target=_watch_backend_health,
        args=(listener_stop, runtime_root),
        kwargs={"backend_runtime": backend_runtime},
        name="mio-backend-health",
        daemon=True,
    )
    backend_health_thread.start()
    webview.start(
        gui="edgechromium",
        debug=False,
        private_mode=False,
        storage_path=str(WEBVIEW_DATA_DIR),
    )
    _notify_window_topology("agent-main", "closed", window=window)
    bridge.close_child_windows()
    listener_stop.set()
    _WEBVIEW_RECOVERY_CALLBACK = None
    tray_icon.stop()
    listener.join(timeout=2)
    pet_chat_listener.join(timeout=2)
    notification_thread.join(timeout=3)
    heartbeat_thread.join(timeout=2)
    backend_health_thread.join(timeout=2)


def _install_webview_recovery() -> None:
    """Recover renderer failures and restart after browser or GPU failures."""
    try:
        from webview.platforms import edgechromium
    except Exception:
        logging.exception("Unable to install WebView2 recovery handler")
        return

    edge_class = edgechromium.EdgeChrome
    if getattr(edge_class, "_mio_recovery_installed", False):
        return

    original_ready = edge_class.on_webview_ready

    def on_webview_ready(self, sender, args):
        original_ready(self, sender, args)
        if not args.IsSuccess:
            return

        def on_process_failed(failed_sender, failed_args):
            failure_kind = str(getattr(failed_args, "ProcessFailedKind", "unknown"))
            failure_reason = str(getattr(failed_args, "Reason", "unknown"))
            process_description = str(getattr(failed_args, "ProcessDescription", ""))
            logging.error(
                "WebView2 process failed: kind=%s reason=%s process=%s",
                failure_kind,
                failure_reason,
                process_description,
            )
            action = _webview_failure_action(failure_kind)
            if action == "reload":
                try:
                    failed_sender.CoreWebView2.Reload()
                    logging.info("WebView2 page reloaded after renderer failure")
                    return
                except Exception:
                    logging.exception("Failed to reload WebView2 after renderer failure")
                    action = "restart"
            if action == "restart" and _WEBVIEW_RECOVERY_CALLBACK is not None:
                _WEBVIEW_RECOVERY_CALLBACK(failure_kind, failure_reason)

        def on_browser_process_exited(_environment, exited_args):
            exit_kind = str(getattr(exited_args, "BrowserProcessExitKind", "unknown"))
            process_id = str(getattr(exited_args, "BrowserProcessId", "unknown"))
            logging.error(
                "WebView2 browser process exited: kind=%s pid=%s",
                exit_kind,
                process_id,
            )
            if _WEBVIEW_RECOVERY_CALLBACK is not None:
                _WEBVIEW_RECOVERY_CALLBACK(
                    "BrowserProcessExited",
                    f"{exit_kind}, pid={process_id}",
                )

        def on_permission_requested(_webview, permission_args):
            try:
                uri = str(getattr(permission_args, "Uri", ""))
                kind = str(getattr(permission_args, "PermissionKind", ""))
                if uri.startswith(f"http://{HOST}:{PORT}/") and "Microphone" in kind:
                    from System import Enum
                    state_type = permission_args.State.GetType()
                    permission_args.State = Enum.Parse(state_type, "Allow")
                    permission_args.Handled = True
            except Exception:
                logging.exception("Failed to grant local microphone permission")

        self._mio_process_failed_handler = on_process_failed
        self._mio_browser_process_exited_handler = on_browser_process_exited
        self._mio_permission_requested_handler = on_permission_requested
        try:
            sender.CoreWebView2.ProcessFailed += on_process_failed
        except Exception:
            logging.exception("Failed to subscribe to WebView2 ProcessFailed")
        try:
            sender.CoreWebView2.Environment.BrowserProcessExited += on_browser_process_exited
        except Exception:
            logging.exception("Failed to subscribe to WebView2 BrowserProcessExited")
        try:
            sender.CoreWebView2.PermissionRequested += on_permission_requested
        except Exception:
            logging.exception("Failed to subscribe to WebView2 PermissionRequested")

    edge_class.on_webview_ready = on_webview_ready
    edge_class._mio_recovery_installed = True


def main() -> int:
    _configure_logging()
    _wait_for_recovery_parent()
    _configure_source_import_path()
    if VOICE_PACKAGE_IMPORT_WORKER_ARGUMENT in sys.argv:
        argument_index = sys.argv.index(VOICE_PACKAGE_IMPORT_WORKER_ARGUMENT)
        if argument_index + 1 >= len(sys.argv):
            return 2
        return _run_voice_package_import_worker(Path(sys.argv[argument_index + 1]))
    if SCREEN_PREVIEW_ARGUMENT in sys.argv:
        parent_argument = next(
            (item for item in sys.argv if item.startswith(SCREEN_PREVIEW_PARENT_ARGUMENT)),
            "",
        )
        try:
            parent_pid = int(parent_argument.removeprefix(SCREEN_PREVIEW_PARENT_ARGUMENT))
        except ValueError:
            parent_pid = 0
        from screen_preview import run_preview_window

        return run_preview_window(host=HOST, port=PORT, parent_pid=parent_pid)
    if PET_CHAT_WINDOW_ARGUMENT in sys.argv:
        parent_argument = next(
            (item for item in sys.argv if item.startswith(PET_CHAT_PARENT_ARGUMENT)),
            "",
        )
        try:
            parent_pid = int(parent_argument.removeprefix(PET_CHAT_PARENT_ARGUMENT))
        except ValueError:
            parent_pid = 0
        return _run_pet_chat_window(parent_pid)
    if WORKER_ARGUMENT := next((arg for arg in sys.argv if arg == "--screen-observer-worker"), ""):
        os.environ["MIO_SCREEN_OBSERVER_WORKER"] = "1"
        from app.screen_observer_process import worker_main

        return worker_main()
    if "--desktop-pet" in sys.argv:
        from app.desktop_pet import main as desktop_pet_main

        return desktop_pet_main()
    mutex_handle = _acquire_single_instance()
    if mutex_handle is None:
        return 0

    backend_runtime = None
    show_event_handle = None
    pet_chat_event_handle = None
    try:
        show_event_handle = _create_show_event()
        pet_chat_event_handle = _create_pet_chat_event()
        logging.info("WebView2 runtime: %s", _require_webview2_runtime())
        runtime_root = _choose_runtime_root()
        _configure_runtime_environment(runtime_root)
        if _prepare_bundled_voice_runtime():
            logging.info("Mio voice runtime is ready: %s", _voice_runtime_root())
        _prepare_bundled_default_voice(runtime_root)
        logging.info(
            "Runtime identity: %s",
            json.dumps(_desktop_runtime_identity(runtime_root), ensure_ascii=False, sort_keys=True),
        )
        logging.info("WebView2 browser arguments: %s", _configure_webview_runtime())
        _prepare_webview_storage()
        backend_runtime = BackendRuntime(runtime_root)
        backend_runtime.start()
        _run_window(show_event_handle, pet_chat_event_handle, runtime_root, backend_runtime)
        return 0
    except Exception as exc:
        logging.error("Desktop startup failed: %s\n%s", exc, traceback.format_exc())
        _message_box(str(exc), error=True)
        return 1
    finally:
        if backend_runtime is not None:
            backend_runtime.stop(timeout=8)
        if show_event_handle is not None:
            ctypes.windll.kernel32.CloseHandle(show_event_handle)
        if pet_chat_event_handle is not None:
            ctypes.windll.kernel32.CloseHandle(pet_chat_event_handle)
        ctypes.windll.kernel32.CloseHandle(mutex_handle)


if __name__ == "__main__":
    raise SystemExit(main())
