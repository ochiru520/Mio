from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import io
import json
import os
import secrets
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

import httpx
import qrcode

from .config import settings


_status_cache: dict[str, object] | None = None
_status_cache_at = 0.0
_qrcode_cache: tuple[bytes, str] | None = None
_qrcode_cache_at = 0.0
_control_lock = threading.Lock()
_auto_recovery_suppressed = False


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _running_processes() -> list[tuple[int, int, str, str]]:
    if os.name != "nt":
        return []
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot in (0, -1):
        return []
    processes: list[tuple[int, int, str, str]] = []
    entry = _ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while has_entry:
            pid = int(entry.th32ProcessID)
            parent_pid = int(entry.th32ParentProcessID)
            name = str(entry.szExeFile)
            path = ""
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                try:
                    buffer = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(buffer))
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        path = buffer.value
                finally:
                    kernel32.CloseHandle(handle)
            processes.append((pid, parent_pid, name, path))
            has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    return processes


def _normalized_process_path(value: str | Path) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        raw = str(Path(raw).resolve(strict=False))
    except (OSError, RuntimeError):
        pass
    return os.path.normcase(raw).rstrip("\\/")


def _process_status() -> dict[str, object]:
    supported = os.name == "nt"
    root = _normalized_process_path(settings.napcat_dir)
    napcat_ids: list[int] = []
    qq_ids: list[int] = []
    ordinary_qq_ids: list[int] = []
    processes = _running_processes()
    parents = {pid: parent_pid for pid, parent_pid, _name, _path in processes}
    for pid, _parent_pid, name, path in processes:
        normalized_name = name.casefold()
        normalized_path = _normalized_process_path(path)
        managed = bool(root and normalized_path.startswith(root + "\\"))
        if normalized_name == "napcatwinbootmain.exe" and (managed or not path):
            napcat_ids.append(pid)
    managed_ancestors = set(napcat_ids)
    for pid, _parent_pid, name, path in processes:
        if name.casefold() != "qq.exe":
            continue
        normalized_path = _normalized_process_path(path)
        managed = bool(root and normalized_path.startswith(root + "\\"))
        ancestor = parents.get(pid, 0)
        visited: set[int] = set()
        while ancestor and ancestor not in visited and ancestor not in managed_ancestors:
            visited.add(ancestor)
            ancestor = parents.get(ancestor, 0)
        if managed or ancestor in managed_ancestors:
            qq_ids.append(pid)
        else:
            ordinary_qq_ids.append(pid)
    return {
        "process_check_supported": supported,
        "napcat_process_running": bool(napcat_ids),
        "napcat_process_count": len(napcat_ids),
        "qq_process_running": bool(qq_ids),
        "qq_process_count": len(qq_ids),
        "ordinary_qq_process_running": bool(ordinary_qq_ids),
        "ordinary_qq_process_count": len(ordinary_qq_ids),
    }


def _filesystem_status() -> dict[str, object]:
    control_scripts = {
        action: _control_script(action).exists()
        for action in ("start", "stop", "restart")
    }
    config_path = _webui_config_path()
    config_has_token = False
    if config_path is not None:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config_has_token = bool(str(config.get("token") or "").strip())
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    launchers = _napcat_launchers()
    incomplete_shells = _incomplete_napcat_shells()
    onebot_ready = False
    config_dir = _napcat_config_dir(create=False)
    if config_dir is not None:
        account = str(settings.napcat_account or "").strip()
        candidates = [config_dir / "onebot11.json"]
        if account:
            candidates.insert(0, config_dir / f"onebot11_{account}.json")
        for onebot_path in candidates:
            try:
                payload = json.loads(onebot_path.read_text(encoding="utf-8"))
                clients = payload.get("network", {}).get("websocketClients", [])
                onebot_ready = any(
                    isinstance(item, dict)
                    and item.get("enable")
                    and "/onebot/ws" in str(item.get("url") or "")
                    for item in clients
                )
                if onebot_ready:
                    break
            except (OSError, ValueError, AttributeError, json.JSONDecodeError):
                continue
    return {
        "control_scripts_ready": all(control_scripts.values()),
        "control_scripts": control_scripts,
        "napcat_dir_exists": settings.napcat_dir.is_dir(),
        "napcat_executable_exists": bool(launchers),
        "napcat_launcher": str(launchers[0]) if launchers else "",
        "napcat_repair_required": bool(incomplete_shells and not launchers),
        "napcat_incomplete_launcher": str(incomplete_shells[0]) if incomplete_shells else "",
        "napcat_account": str(settings.napcat_account or ""),
        "napcat_setup_ready": bool(settings.napcat_account and settings.qq_onebot_token and onebot_ready),
        "onebot_config_ready": onebot_ready,
        "webui_config_exists": config_path is not None,
        "webui_config_ready": config_has_token,
        **_process_status(),
    }


def _with_diagnostic(result: dict[str, object]) -> dict[str, object]:
    if not result.get("control_scripts_ready"):
        code = "control_scripts_missing"
        message = "QQ控制脚本不完整，请重新安装或修复 Mio。"
    elif result.get("napcat_repair_required"):
        code = "napcat_repair_required"
        message = "检测到旧版或不完整的 NapCat Shell；请点击安装并配置，让 Mio 补齐新版 Shell。官方 QQ 无需重装。"
    elif not result.get("napcat_dir_exists") or not result.get("napcat_executable_exists"):
        code = "napcat_missing"
        message = "没有找到NapCat运行程序。"
    elif (
        result.get("ordinary_qq_process_running")
        and not result.get("napcat_process_running")
        and not result.get("webui_reachable")
    ):
        code = "ordinary_qq_running"
        message = "普通QQ正在运行；请先从系统托盘彻底退出，再由Mio启动机器人QQ。"
    elif (
        result.get("process_check_supported")
        and not result.get("napcat_process_running")
        and not result.get("webui_reachable")
    ):
        code = "napcat_process_stopped"
        message = "NapCat程序已安装，但当前进程没有运行。"
    elif (
        result.get("process_check_supported")
        and result.get("napcat_process_running")
        and not result.get("qq_process_running")
        and not result.get("webui_reachable")
    ):
        code = "qq_process_stopped"
        message = "NapCat正在运行，但机器人QQ进程没有运行。"
    elif not result.get("webui_config_exists") or not result.get("webui_config_ready"):
        code = "webui_config_missing"
        message = "NapCat WebUI配置缺失或无效。"
    elif not result.get("webui_reachable"):
        code = "webui_unreachable"
        message = "NapCat WebUI暂时无法访问，可能尚未启动。"
    elif not result.get("login_checked"):
        code = "login_unknown"
        message = "NapCat已启动，但暂时无法确认QQ登录状态。"
    elif not result.get("logged_in"):
        code = "login_required"
        message = str(result.get("login_error") or "QQ账号尚未登录。")
    elif not result.get("websocket_connected"):
        code = "onebot_disconnected"
        message = "QQ已登录，但OneBot消息通道尚未连接。"
    else:
        code = "connected"
        message = "QQ登录和OneBot消息通道均正常。"
    result["diagnostic_code"] = code
    result["diagnostic_message"] = message
    return result


def _control_script(action: str) -> Path:
    names = {
        "start": "启动QQ聊天.ps1",
        "stop": "停止QQ聊天.ps1",
        "restart": "重启NapCat.ps1",
    }
    if action not in names:
        raise ValueError(f"不支持的 QQ 控制操作：{action}")
    return settings.agent_control_scripts_dir / names[action]


def run_napcat_control(action: str, *, force_qr_login: bool = False) -> dict[str, object]:
    global _auto_recovery_suppressed, _status_cache, _status_cache_at

    script = _control_script(action)
    if not script.exists():
        raise RuntimeError(f"没有找到控制脚本：{script.name}")
    with _control_lock:
        env = os.environ.copy()
        env.update(
            {
                "MIO_RUNTIME_ROOT": str(settings.project_root.resolve()),
                "MIO_NAPCAT_DIR": str(settings.napcat_dir.resolve()),
                "MIO_NAPCAT_ACCOUNT": str(settings.napcat_account or ""),
                "MIO_NAPCAT_WEBUI_URL": settings.napcat_webui_url,
                "MIO_APP_PORT": str(settings.app_port),
                "MIO_NAPCAT_FORCE_QR": "1" if force_qr_login else "0",
            }
        )
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=str(settings.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode == 0:
        _auto_recovery_suppressed = action == "stop"
        _status_cache = None
        _status_cache_at = 0.0
    return {
        "ok": completed.returncode == 0,
        "action": action,
        "force_qr_login": force_qr_login,
        "output": output[-2000:],
        "returncode": completed.returncode,
    }


def napcat_auto_recovery_allowed() -> bool:
    return not _auto_recovery_suppressed


def _repair_mojibake(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    original_cjk = sum("\u4e00" <= char <= "\u9fff" for char in text)
    repaired_cjk = sum("\u4e00" <= char <= "\u9fff" for char in repaired)
    return repaired if repaired_cjk > original_cjk else text


def _napcat_api_succeeded(payload: object) -> bool:
    """NapCat WebUI uses HTTP 200 for both success and application errors."""
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    if code is None:
        return True
    return str(code).strip() == "0"


def _webui_config_path() -> Path | None:
    config_dir = _napcat_config_dir(create=False)
    if config_dir is None:
        return None
    candidate = config_dir / "webui.json"
    return candidate if candidate.exists() else None


def _napcat_shell_ready(boot_main: Path) -> bool:
    root = boot_main.parent
    return (
        boot_main.is_file()
        and (root / "NapCatWinBootHook.dll").is_file()
        and (root / "napcat.mjs").is_file()
    )


def _incomplete_napcat_shells(root: Path | None = None) -> list[Path]:
    root = root or settings.napcat_dir
    if not root.is_dir():
        return []
    try:
        candidates = [
            item
            for item in root.rglob("NapCatWinBootMain.exe")
            if item.is_file() and not _napcat_shell_ready(item)
        ]
    except OSError:
        return []
    return sorted(candidates, key=lambda path: (len(path.parts), str(path).casefold()))


def _napcat_launchers(root: Path | None = None) -> list[Path]:
    root = root or settings.napcat_dir
    launcher_names = (
        root / "NapCatWinBootMain.exe",
        root / "launcher-user.bat",
        root / "launcher-win10-user.bat",
        root / "launcher.bat",
        root / "launcher-win10.bat",
        root / "napcat.quick.bat",
        root / "napcat.bat",
    )
    found = [
        path
        for path in launcher_names
        if path.is_file()
        and (path.name.casefold() != "napcatwinbootmain.exe" or _napcat_shell_ready(path))
    ]
    try:
        nested = [
            item
            for item in root.rglob("*")
            if item.is_file()
            and item.name.casefold()
            in {
                "napcatwinbootmain.exe",
                "launcher-user.bat",
                "launcher-win10-user.bat",
                "launcher.bat",
                "launcher-win10.bat",
                "napcat.quick.bat",
                "napcat.bat",
            }
            and (item.name.casefold() != "napcatwinbootmain.exe" or _napcat_shell_ready(item))
        ]
    except OSError:
        nested = []
    priority = {
        "napcatwinbootmain.exe": 0,
        "launcher-user.bat": 1,
        "launcher-win10-user.bat": 2,
        "launcher.bat": 3,
        "launcher-win10.bat": 4,
        "napcat.quick.bat": 5,
        "napcat.bat": 6,
    }
    unique = {str(path.resolve()).casefold(): path for path in (*found, *nested)}
    return sorted(
        unique.values(),
        key=lambda path: (priority.get(path.name.casefold(), 99), len(path.parts), str(path).casefold()),
    )


def _napcat_config_dir(*, create: bool) -> Path | None:
    root = settings.napcat_dir
    versions_root = root / "versions"
    if versions_root.is_dir():
        for version_dir in sorted(versions_root.iterdir(), key=lambda item: item.name, reverse=True):
            candidate = version_dir / "resources" / "app" / "napcat" / "config"
            if candidate.is_dir():
                return candidate

    candidates = [
        root / "napcat" / "config",
        root / "resources" / "app" / "napcat" / "config",
        root / "config",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    try:
        nested = next(
            (
                item
                for item in root.rglob("webui.json")
                if item.is_file() and item.parent.name == "config"
            ),
            None,
        )
        if nested is not None:
            return nested.parent
    except OSError:
        pass
    if not create or not root.is_dir():
        return None
    target = root / "napcat" / "config" if (root / "napcat").is_dir() else root / "config"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".mio.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def configure_napcat(account: str) -> dict[str, object]:
    """Persist the bot account and write NapCat's verified OneBot 11 client schema."""
    global _status_cache, _status_cache_at, _qrcode_cache, _qrcode_cache_at

    account = str(account or "").strip()
    if not account.isdigit() or not 5 <= len(account) <= 12:
        raise ValueError("机器人 QQ 号必须是 5 到 12 位数字。")
    if not _napcat_launchers():
        raise FileNotFoundError("NapCat 尚未安装完成。")
    config_dir = _napcat_config_dir(create=True)
    if config_dir is None:
        raise FileNotFoundError("没有找到 NapCat 配置目录。")

    previous_account = str(settings.napcat_account or "").strip()
    token = str(settings.qq_onebot_token or "").strip() or secrets.token_urlsafe(32)
    onebot_path = config_dir / f"onebot11_{account}.json"
    generic_onebot_path = config_dir / "onebot11.json"
    existing: dict[str, object] = {}
    for candidate in (onebot_path, generic_onebot_path):
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
                break
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    network = existing.get("network") if isinstance(existing.get("network"), dict) else {}
    clients = [
        item
        for item in network.get("websocketClients", [])
        if isinstance(item, dict)
        and item.get("name") != "mio-agent"
        and "/onebot/ws" not in str(item.get("url") or "")
    ]
    clients.append(
        {
            "name": "mio-agent",
            "enable": True,
            "url": f"ws://127.0.0.1:{settings.app_port}/onebot/ws",
            "messagePostFormat": "array",
            "reportSelfMessage": False,
            "reconnectInterval": 5000,
            "token": token,
            "debug": False,
            "heartInterval": 30000,
        }
    )
    network = {
        "httpServers": list(network.get("httpServers") or []),
        "httpSseServers": list(network.get("httpSseServers") or []),
        "httpClients": list(network.get("httpClients") or []),
        "websocketServers": list(network.get("websocketServers") or []),
        "websocketClients": clients,
        "plugins": list(network.get("plugins") or []),
    }
    onebot_payload = {
        **existing,
        "network": network,
        "musicSignUrl": str(existing.get("musicSignUrl") or ""),
        "enableLocalFile2Url": bool(existing.get("enableLocalFile2Url", False)),
        "parseMultMsg": bool(existing.get("parseMultMsg", True)),
    }
    _write_json_atomic(onebot_path, onebot_payload)
    _write_json_atomic(generic_onebot_path, onebot_payload)

    webui_path = config_dir / "webui.json"
    try:
        webui = json.loads(webui_path.read_text(encoding="utf-8"))
        if not isinstance(webui, dict):
            webui = {}
    except (OSError, ValueError, json.JSONDecodeError):
        webui = {}
    webui.setdefault("host", "127.0.0.1")
    webui.setdefault("port", 6099)
    if not str(webui.get("token") or "").strip():
        webui["token"] = secrets.token_hex(6)
    webui["autoLoginAccount"] = account
    _write_json_atomic(webui_path, webui)

    base_napcat = config_dir / "napcat.json"
    account_napcat = config_dir / f"napcat_{account}.json"
    if base_napcat.is_file() and not account_napcat.exists():
        account_napcat.write_bytes(base_napcat.read_bytes())

    _write_json_atomic(
        settings.qq_channel_config_path,
        {"schema_version": 1, "account": account, "onebot_token": token},
    )
    object.__setattr__(settings, "napcat_account", account)
    object.__setattr__(settings, "qq_onebot_token", token)
    from .config import save_runtime_settings

    save_runtime_settings({"qq_bot_enabled": True})
    _status_cache = None
    _status_cache_at = 0.0
    _qrcode_cache = None
    _qrcode_cache_at = 0.0
    return {
        "account": account,
        "previous_account": previous_account,
        "account_changed": bool(previous_account and previous_account != account),
        "configured": True,
        "config_dir": str(config_dir),
        "onebot_config": str(onebot_path),
        "websocket_url": f"ws://127.0.0.1:{settings.app_port}/onebot/ws",
    }


def _parse_login_status(payload: object) -> dict[str, object]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = {}
    account = ""
    for key in ("uin", "qq", "account", "user_id", "self_id"):
        value = str(data.get(key) or "").strip()
        if value.isdigit():
            account = value
            break
    return {
        "login_checked": True,
        "logged_in": bool(data.get("isLogin")),
        "webui_account": account,
        "qrcode_available": bool(data.get("qrcodeurl")),
        "login_error": _repair_mojibake(data.get("loginError"))[:240],
    }


def _decode_qrcode_data_url(value: str) -> tuple[bytes, str] | None:
    if not value.startswith("data:image/") or ";base64," not in value:
        return None
    header, encoded = value.split(",", 1)
    media_type = header[5:].split(";", 1)[0].strip().lower()
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error):
        return None
    if not content or len(content) > 2 * 1024 * 1024:
        return None
    return content, media_type if media_type.startswith("image/") else "image/png"


def _render_qrcode_png(value: str) -> tuple[bytes, str] | None:
    content = value.strip()
    if not content or len(content) > 8192:
        return None
    image = qrcode.make(content).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = buffer.getvalue()
    return (encoded, "image/png") if encoded else None


async def get_napcat_login_status(
    *,
    cache_seconds: float = 3.0,
    websocket_connected: bool = False,
) -> dict[str, object]:
    global _status_cache, _status_cache_at

    now = time.monotonic()
    if _status_cache is not None and now - _status_cache_at < cache_seconds:
        cached = dict(_status_cache)
        cached["websocket_connected"] = websocket_connected
        return _with_diagnostic(cached)

    fallback = {
        **_filesystem_status(),
        "webui_reachable": False,
        "login_checked": False,
        "logged_in": False,
        "qrcode_available": False,
        "login_error": "",
        "websocket_connected": websocket_connected,
    }
    config_path = _webui_config_path()
    if config_path is None:
        result = _with_diagnostic(fallback)
        _status_cache = dict(result)
        _status_cache_at = now
        return result
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        secret = str(config.get("token") or "")
        if not secret:
            result = _with_diagnostic(fallback)
            _status_cache = dict(result)
            _status_cache_at = now
            return result
        password_hash = hashlib.sha256(f"{secret}.napcat".encode("utf-8")).hexdigest()
        timeout = httpx.Timeout(2.0, connect=0.6)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            login_response = await client.post(
                f"{settings.napcat_webui_url}/api/auth/login",
                json={"hash": password_hash},
            )
            fallback["webui_reachable"] = True
            login_response.raise_for_status()
            login_payload = login_response.json()
            login_data = login_payload.get("data") if isinstance(login_payload, dict) else None
            credential = str(login_data.get("Credential") or "") if isinstance(login_data, dict) else ""
            if not credential:
                result = _with_diagnostic(fallback)
                _status_cache = dict(result)
                _status_cache_at = now
                return result
            status_response = await client.post(
                f"{settings.napcat_webui_url}/api/QQLogin/CheckLoginStatus",
                headers={"Authorization": f"Bearer {credential}"},
                json={},
            )
            status_response.raise_for_status()
            status_payload = status_response.json()
            if not _napcat_api_succeeded(status_payload):
                result = _with_diagnostic(fallback)
                _status_cache = dict(result)
                _status_cache_at = now
                return result
            result = {
                **fallback,
                **_parse_login_status(status_payload),
                "webui_reachable": True,
            }
    except (OSError, ValueError, httpx.HTTPError):
        result = fallback

    result["websocket_connected"] = websocket_connected
    result = _with_diagnostic(result)
    _status_cache = dict(result)
    _status_cache_at = now
    return result


async def refresh_napcat_qrcode() -> bool:
    global _qrcode_cache, _qrcode_cache_at, _status_cache, _status_cache_at

    config_path = _webui_config_path()
    if config_path is None:
        return False
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        secret = str(config.get("token") or "")
        if not secret:
            return False
        password_hash = hashlib.sha256(f"{secret}.napcat".encode("utf-8")).hexdigest()
        timeout = httpx.Timeout(4.0, connect=1.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            login_response = await client.post(
                f"{settings.napcat_webui_url}/api/auth/login",
                json={"hash": password_hash},
            )
            login_response.raise_for_status()
            login_payload = login_response.json()
            login_data = login_payload.get("data") if isinstance(login_payload, dict) else None
            credential = str(login_data.get("Credential") or "") if isinstance(login_data, dict) else ""
            if not credential:
                return False
            refresh_response = await client.post(
                f"{settings.napcat_webui_url}/api/QQLogin/RefreshQRcode",
                headers={"Authorization": f"Bearer {credential}"},
                json={},
            )
            refresh_response.raise_for_status()
            if not _napcat_api_succeeded(refresh_response.json()):
                return False
    except (OSError, ValueError, httpx.HTTPError):
        return False

    _status_cache = None
    _status_cache_at = 0.0
    _qrcode_cache = None
    _qrcode_cache_at = 0.0
    return True


async def get_napcat_qrcode() -> tuple[bytes, str] | None:
    global _qrcode_cache, _qrcode_cache_at

    now = time.monotonic()
    if _qrcode_cache is not None and now - _qrcode_cache_at < 30:
        return _qrcode_cache
    config_path = _webui_config_path()
    if config_path is None:
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        secret = str(config.get("token") or "")
        if not secret:
            return None
        password_hash = hashlib.sha256(f"{secret}.napcat".encode("utf-8")).hexdigest()
        timeout = httpx.Timeout(5.0, connect=1.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
            login_response = await client.post(
                f"{settings.napcat_webui_url}/api/auth/login",
                json={"hash": password_hash},
            )
            login_response.raise_for_status()
            login_payload = login_response.json()
            login_data = login_payload.get("data") if isinstance(login_payload, dict) else None
            credential = str(login_data.get("Credential") or "") if isinstance(login_data, dict) else ""
            if not credential:
                return None
            status_response = await client.post(
                f"{settings.napcat_webui_url}/api/QQLogin/CheckLoginStatus",
                headers={"Authorization": f"Bearer {credential}"},
                json={},
            )
            status_response.raise_for_status()
            payload = status_response.json()
            if not _napcat_api_succeeded(payload):
                return None
            data = payload.get("data") if isinstance(payload, dict) else None
            qrcode_value = str(data.get("qrcodeurl") or "") if isinstance(data, dict) else ""
            if not qrcode_value:
                qrcode_response = await client.post(
                    f"{settings.napcat_webui_url}/api/QQLogin/GetQQLoginQrcode",
                    headers={"Authorization": f"Bearer {credential}"},
                    json={},
                )
                qrcode_response.raise_for_status()
                qrcode_payload = qrcode_response.json()
                if not _napcat_api_succeeded(qrcode_payload):
                    return None
                qrcode_data = qrcode_payload.get("data") if isinstance(qrcode_payload, dict) else None
                qrcode_value = (
                    str(qrcode_data.get("qrcode") or "")
                    if isinstance(qrcode_data, dict)
                    else ""
                )
            embedded = _decode_qrcode_data_url(qrcode_value)
            if embedded is not None:
                result = embedded
            else:
                result = _render_qrcode_png(qrcode_value)
            if result is not None:
                _qrcode_cache = result
                _qrcode_cache_at = now
            return result
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError):
        return None
