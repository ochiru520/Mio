from __future__ import annotations

import ctypes
import http.client
import importlib.util
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import settings
from .model_registry import list_model_profiles
from .napcat_service import _incomplete_napcat_shells, _napcat_launchers


MINIMUM_FREE_BYTES = 1024**3
WHISPER_DISCOVERY_VERSION = 2


def _item(
    item_id: str,
    label: str,
    status: str,
    detail: str,
    *,
    action: str = "",
) -> dict[str, object]:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "detail": detail,
        "action": action,
    }


# ---------- 自动探索常见安装位置 ----------

_UPPER_NAMES: dict[str, tuple[str, ...]] = {}


def _scan_roots() -> list[Path]:
    """返回适合做浅扫描的根目录：所有本地磁盘根 + 用户常见安装目录。"""
    roots: list[Path] = []

    def _add(path: Path) -> None:
        try:
            exists = path.is_dir()
        except OSError:
            return
        key = str(path).casefold()
        if exists and all(str(item).casefold() != key for item in roots):
            roots.append(path)

    try:
        drive_mask = int(ctypes.windll.kernel32.GetLogicalDrives()) if os.name == "nt" else 0
        for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            if drive_mask and not drive_mask & (1 << index):
                continue
            drive = Path(f"{letter}:\\")
            if os.name == "nt":
                drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(str(drive)))
                if drive_type not in {2, 3}:  # removable / fixed，跳过断开的网络盘和光驱
                    continue
            _add(drive)
    except (AttributeError, OSError, ValueError):
        for letter in "CDEFGH":
            _add(Path(f"{letter}:\\"))
    for env_name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        raw = os.getenv(env_name)
        if raw:
            _add(Path(raw))
    return roots


def _nearby_scan_roots() -> list[Path]:
    """优先扫描当前程序、数据和安装目录的各级父目录，覆盖较深的便携目录。"""
    seeds = [settings.voice_training_dir, Path.cwd(), Path(sys.executable).parent]
    for env_name in ("MIO_RUNTIME_ROOT", "MIO_DESKTOP_STATE_DIR", "MIO_VOICE_TRAINING_DIR"):
        raw = os.getenv(env_name)
        if raw:
            seeds.append(Path(raw))
    roots: list[Path] = []
    seen: set[str] = set()
    for seed in seeds:
        try:
            resolved = seed.resolve()
        except OSError:
            continue
        if resolved.is_file():
            resolved = resolved.parent
        for candidate in (resolved, *resolved.parents):
            try:
                key = str(candidate.resolve()).casefold()
            except OSError:
                continue
            if key in seen or not candidate.is_dir():
                continue
            seen.add(key)
            roots.append(candidate)
    return roots


def _dir_matches(name: str, patterns: tuple[str, ...]) -> bool:
    if not name:
        return False
    folded = name.lower()
    for pattern in patterns:
        if pattern in folded:
            return True
    return False


_SKIP_DIR_NAMES = frozenset(
    {
        "$recycle.bin",
        "system volume information",
        "recovery",
        "windows",
        "program files",
        "program files (x86)",
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "site-packages",
        "python",
        "python310",
        "winsxs",
        "msocache",
        "perflogs",
    }
)


def _find_dirs_named(roots: list[Path], patterns: tuple[str, ...], max_depth: int = 1) -> list[Path]:
    """在给定根目录下浅扫描名字匹配的目录，避免全盘深递归（只扫到 max_depth 层）。"""

    def _walk(directory: Path, depth: int) -> list[Path]:
        found: list[Path] = []
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            return found
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if not is_dir:
                continue
            if entry.name.lower() in _SKIP_DIR_NAMES:
                continue
            if _dir_matches(entry.name, patterns):
                found.append(Path(entry.path))
            if depth < max_depth:
                found.extend(_walk(Path(entry.path), depth + 1))
        return found

    found: list[Path] = []
    for root in roots:
        if root is None or not root.is_dir():
            continue
        found.extend(_walk(root, 1))
    # 去重（按大小写折叠的绝对路径）
    seen: set[str] = set()
    unique: list[Path] = []
    for path in found:
        key = str(path.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


VOICE_API_MARKERS = ("api_v2.py", "api.py")
VOICE_CONFIG_MARKERS = (
    Path("GPT_SoVITS/configs/tts_infer.yaml"),
    Path("GPT_SoVITS/configs/tts_infer_v2.yaml"),
)
WHISPER_REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def _voice_models_ready(engine_root: Path) -> bool:
    pretrained = engine_root / "GPT_SoVITS" / "pretrained_models"
    if not pretrained.is_dir():
        return False
    try:
        files = [path for path in pretrained.rglob("*") if path.is_file() and path.stat().st_size > 0]
    except OSError:
        return False
    relative_names = [str(path.relative_to(pretrained)).replace("\\", "/").lower() for path in files]
    has_hubert = any("hubert" in name and name.endswith((".bin", ".pt", ".pth")) for name in relative_names)
    has_bert = any(
        ("roberta" in name or "bert" in name) and name.endswith((".bin", ".pt", ".pth"))
        for name in relative_names
    )
    has_gpt = any(name.endswith(".ckpt") for name in relative_names)
    has_sovits = any(name.endswith(".pth") for name in relative_names)
    return has_hubert and has_bert and has_gpt and has_sovits


def _voice_layout(root: Path) -> dict[str, object] | None:
    """识别 GPT-SoVITS 常见版本布局，不把入口文件名锁死在 api_v2.py。"""
    candidates = (root, root / "GPT-SoVITS", root / "GPT_SoVITS")
    for engine_root in candidates:
        api = next((engine_root / name for name in VOICE_API_MARKERS if (engine_root / name).is_file()), None)
        if api is None:
            continue
        python_candidates = (
            root / ".voice-env" / "Scripts" / "python.exe",
            root.parent / ".voice-env" / "Scripts" / "python.exe",
            engine_root / ".voice-env" / "Scripts" / "python.exe",
            engine_root / "runtime" / "python.exe",
            engine_root / "runtime" / "python_embeded" / "python.exe",
            engine_root / "runtime" / "python" / "python.exe",
            engine_root / "runtime" / "python310" / "python.exe",
            engine_root / "runtime" / "python312" / "python.exe",
            engine_root / "python" / "python.exe",
            engine_root / "python_embeded" / "python.exe",
            engine_root / "venv" / "Scripts" / "python.exe",
        )
        config = next(
            (engine_root / marker for marker in VOICE_CONFIG_MARKERS if (engine_root / marker).is_file()),
            None,
        )
        launcher_ready = any(
            (engine_root / name).is_file()
            for name in ("go-api.bat", "go-api.ps1", "go-webui.bat", "go-webui.ps1")
        )
        runtime_ready = any(path.is_file() for path in python_candidates) or launcher_ready
        models_ready = _voice_models_ready(engine_root)
        runnable = runtime_ready and config is not None and models_ready
        missing: list[str] = []
        if not runtime_ready:
            missing.append("Python 运行环境或启动器")
        if config is None:
            missing.append("推理配置")
        if not models_ready:
            missing.append("基础模型权重")
        return {
            "root": engine_root,
            "api": api,
            "layout": "新版 api_v2.py" if api.name == "api_v2.py" else "兼容旧版 api.py",
            "runnable": runnable,
            "runtime_ready": runtime_ready,
            "models_ready": models_ready,
            "missing": missing,
        }
    return None


@lru_cache(maxsize=1)
def _find_voice_roots() -> list[Path]:
    """探测 GPT-SoVITS / 音色训练 常见位置（磁盘根 + 用户目录，浅扫 3 层）。"""
    roots = _scan_roots()
    candidates = []
    for path in _find_dirs_named(roots, ("音色训练", "gpt-sovits", "gpt_sovits"), max_depth=3):
        candidates.append(path)
        if (path / "GPT-SoVITS").is_dir():
            candidates.append(path / "GPT-SoVITS")
    if settings.voice_training_dir not in candidates:
        candidates.append(settings.voice_training_dir)
    if settings.voice_training_dir / "GPT-SoVITS" not in candidates:
        candidates.append(settings.voice_training_dir / "GPT-SoVITS")
    valid: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        layout = _voice_layout(candidate)
        if layout is None:
            continue
        engine_root = Path(layout["root"])
        key = str(engine_root.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            valid.append(engine_root)
    return valid


def _whisper_model_directory(root: Path) -> Path | None:
    cache = root / "cache" / "faster-whisper"
    if cache.is_dir():
        try:
            repositories = list(cache.glob("models--*faster-whisper-*"))
        except OSError:
            repositories = []
        for repository in repositories:
            snapshots = repository / "snapshots"
            if not snapshots.is_dir():
                continue
            try:
                candidates = [path for path in snapshots.iterdir() if path.is_dir()]
            except OSError:
                candidates = []
            for snapshot in candidates:
                if all((snapshot / name).is_file() for name in WHISPER_REQUIRED_FILES):
                    return snapshot
    bundled_models = root / "GPT-SoVITS" / "tools" / "asr" / "models"
    if bundled_models.is_dir():
        try:
            candidates = list(bundled_models.glob("faster-whisper-*"))
        except OSError:
            candidates = []
        for candidate in candidates:
            if (candidate / "config.json").is_file() and (candidate / "model.bin").is_file():
                return candidate
    return None


def _whisper_runtime_layout(root: Path) -> dict[str, object]:
    python = root / ".voice-env" / "Scripts" / "python.exe"
    site_packages_candidates = (
        root / ".voice-env" / "Lib" / "site-packages",
        root / ".voice-env" / "lib" / "site-packages",
    )
    package_ready = any(
        (site_packages / "faster_whisper" / "__init__.py").is_file()
        and (site_packages / "ctranslate2" / "__init__.py").is_file()
        for site_packages in site_packages_candidates
    )
    model = _whisper_model_directory(root)
    return {
        "root": root,
        "python": python,
        "cache": root / "cache" / "faster-whisper",
        "model": model,
        "python_ready": python.is_file() and package_ready,
        "model_ready": model is not None,
        "ready": python.is_file() and package_ready and model is not None,
    }


@lru_cache(maxsize=1)
def whisper_runtime_candidates() -> tuple[dict[str, object], ...]:
    candidates: list[Path] = [settings.voice_training_dir]
    for path in _find_voice_roots():
        candidates.append(path)
        if path.name.casefold() in {"gpt-sovits", "gpt_sovits"}:
            candidates.append(path.parent)
    search_patterns = (
        "音色训练",
        "voice-training",
        "voice_training",
        "faster-whisper",
        "faster_whisper",
    )
    layouts: list[dict[str, object]] = []
    seen: set[str] = set()

    def _add(candidate: Path) -> None:
        try:
            key = str(candidate.resolve()).casefold()
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        layout = _whisper_runtime_layout(candidate)
        if layout["python_ready"] or layout["model_ready"]:
            layouts.append(layout)

    def _add_discovered(path: Path) -> None:
        folded_name = path.name.casefold()
        if folded_name in {"faster-whisper", "faster_whisper"} and path.parent.name.casefold() == "cache":
            _add(path.parent.parent)
        elif _dir_matches(path.name, ("音色训练", "voice-training", "voice_training")):
            _add(path)

    for candidate in candidates:
        _add(candidate)
    if any(layout["ready"] for layout in layouts):
        return tuple(layouts)

    # 从离当前程序最近的目录开始逐级找；完整环境一旦找到便立即停止。
    for scan_root in _nearby_scan_roots():
        for path in _find_dirs_named([scan_root], search_patterns, max_depth=2):
            _add_discovered(path)
        if any(layout["ready"] for layout in layouts):
            return tuple(layouts)

    # 最后的兜底才逐盘扫描 5 层，覆盖自定义盘符和更深的工具目录。
    for scan_root in _scan_roots():
        for path in _find_dirs_named([scan_root], search_patterns, max_depth=5):
            _add_discovered(path)
        if any(layout["ready"] for layout in layouts):
            break
    return tuple(layouts)


def find_whisper_runtime() -> dict[str, object] | None:
    return next((layout for layout in whisper_runtime_candidates() if layout["ready"]), None)


@lru_cache(maxsize=1)
def _find_napcat_roots() -> list[Path]:
    """探测 NapCat 常见位置（磁盘根 + 用户目录，浅扫 3 层）。"""
    roots = _scan_roots()
    candidates = list(_find_dirs_named(roots, ("napcat",), max_depth=3))
    if settings.napcat_dir not in candidates:
        candidates.append(settings.napcat_dir)
    return candidates


def _find_ready_napcat_root(roots: list[Path]) -> Path | None:
    return next((root for root in roots if root.is_dir() and _napcat_launchers(root)), None)


def _configured_napcat_environment(roots: list[Path]) -> tuple[bool, Path | None, str]:
    """Report readiness for the path Mio will actually launch.

    A complete shell elsewhere on disk is useful diagnostic evidence, but it
    must not make the dependency center green while ``settings.napcat_dir``
    still points at an incomplete legacy shell.
    """
    configured = settings.napcat_dir
    if configured.is_dir() and _napcat_launchers(configured):
        return True, configured, f"已检测到 NapCat：{configured}"

    configured_key = str(configured.resolve()).casefold()
    alternate = _find_ready_napcat_root(
        [
            root
            for root in roots
            if str(root.resolve()).casefold() != configured_key
        ]
    )
    if _incomplete_napcat_shells(configured):
        detail = f"当前 NapCat 目录缺少新版 Shell 文件，需要修复：{configured}"
    elif configured.is_dir():
        detail = f"当前 NapCat 目录没有完整可启动的 Shell：{configured}"
    else:
        detail = f"当前 NapCat 安装目录尚不存在：{configured}"
    if alternate is not None:
        detail += f"；另在 {alternate} 发现完整 Shell，但 Mio 当前不会从那里启动"
    return False, alternate, detail


@lru_cache(maxsize=1)
def _find_ollama_executables() -> list[Path]:
    """探测 ollama.exe 常见位置（含“本地视觉/local_vision”目录与标准安装位置）。"""
    exes: list[Path] = []
    roots = _scan_roots()
    resolved: set[str] = set()

    def _add(candidate: Path) -> None:
        nonlocal exes
        if candidate.is_file():
            key = str(candidate.resolve()).casefold()
            if key not in resolved:
                resolved.add(key)
                exes.append(candidate)

    # 1) 全盘浅扫 “ollama” 命名的目录
    for path in _find_dirs_named(roots, ("ollama",), max_depth=3):
        _add(path / "ollama.exe")
        _add(path / "Ollama" / "ollama.exe")
    # 2) 全盘浅扫 “本地视觉 / local_vision” 命名的目录，检查其 Ollama/ollama.exe
    for path in _find_dirs_named(roots, ("本地视觉", "local_vision"), max_depth=3):
        _add(path / "Ollama" / "ollama.exe")
        _add(path / "ollama.exe")
    # 3) 标准安装位置
    candidate = settings.local_vision_dir / "Ollama" / "ollama.exe"
    _add(candidate)
    for root in roots:
        _add(root / "Ollama" / "ollama.exe")
    _add(Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    _add(Path(os.getenv("PROGRAMFILES", "")) / "Ollama" / "ollama.exe")
    # 4) PATH
    which = shutil.which("ollama")
    if which:
        _add(Path(which))
    return exes


def _configured_voice_service_url() -> str:
    try:
        from .companion_service import load_config

        return str(load_config().get("gpt_sovits_url") or "").strip().rstrip("/")
    except (OSError, TypeError, ValueError):
        return ""


@lru_cache(maxsize=8)
def _probe_voice_service(base_url: str) -> tuple[bool, str]:
    """只探测本机 GPT-SoVITS API，避免把任意远端地址纳入环境检查。"""
    parsed = urlparse(base_url)
    host = str(parsed.hostname or "").lower()
    if parsed.scheme != "http" or host not in {"127.0.0.1", "localhost", "::1"}:
        return False, ""
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(host, parsed.port or 80, timeout=0.8)
        prefix = parsed.path.rstrip("/")
        connection.request("GET", f"{prefix}/openapi.json" or "/openapi.json")
        response = connection.getresponse()
        body = response.read(1024 * 1024)
        if response.status != 200:
            return False, ""
        payload = json.loads(body.decode("utf-8"))
        paths = payload.get("paths") if isinstance(payload, dict) else {}
        path_names = {str(name).lower() for name in paths} if isinstance(paths, dict) else set()
        signature = any(
            name in path_names
            for name in ("/tts", "/set_gpt_weights", "/set_sovits_weights", "/control")
        )
        info = payload.get("info") if isinstance(payload, dict) else {}
        title = str(info.get("title") or "") if isinstance(info, dict) else ""
        if not signature and "gpt" not in title.lower() and "sovits" not in title.lower():
            return False, ""
        version = str(info.get("version") or "").strip() if isinstance(info, dict) else ""
        version_hint = f"，版本 {version}" if version else ""
        return True, f"本地 GPT-SoVITS 服务可访问：{base_url}{version_hint}"
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError, http.client.HTTPException):
        return False, ""
    finally:
        if connection is not None:
            connection.close()


def refresh_detection_cache() -> None:
    """清除安装位置和服务探测缓存，供重新检查与安装完成后调用。"""
    _find_voice_roots.cache_clear()
    _find_napcat_roots.cache_clear()
    _find_ollama_executables.cache_clear()
    whisper_runtime_candidates.cache_clear()
    _probe_voice_service.cache_clear()
    _gpu_info.cache_clear()


def _format_bytes(value: int) -> str:
    size = max(0, int(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            number = f"{size:.1f}" if unit in {"GB", "TB"} else f"{size:.0f}"
            return f"{number} {unit}"
        size /= 1024
    return "0 B"


def _memory_bytes() -> int:
    if os.name != "nt":
        return 0

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    return int(status.total_physical) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0


def _ollama_manifest_complete(manifest: Path, models_root: Path) -> bool:
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    items: list[object] = [payload.get("config")]
    layers = payload.get("layers")
    if isinstance(layers, list):
        items.extend(layers)
    checked = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        digest = str(item.get("digest") or "")
        if not digest.startswith("sha256:"):
            return False
        blob = models_root / "blobs" / digest.replace(":", "-")
        try:
            expected_size = max(0, int(item.get("size") or 0))
            if not blob.is_file() or (expected_size and blob.stat().st_size != expected_size):
                return False
        except (OSError, TypeError, ValueError):
            return False
        checked += 1
    return checked > 0


def _ollama_model_ready(models_root: Path) -> bool:
    base = models_root / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5vl"
    candidates = (base / "3b", base / "latest")
    return any(_ollama_manifest_complete(candidate, models_root) for candidate in candidates)


def _windows_label() -> str:
    if os.name != "nt":
        return platform.platform()
    version = sys.getwindowsversion()
    product = "Windows 11" if version.build >= 22000 else "Windows 10"
    return f"{product} · 构建 {version.build}"


@lru_cache(maxsize=1)
def _gpu_info() -> list[dict[str, object]]:
    if os.name != "nt":
        return []
    command = shutil.which("nvidia-smi")
    if command:
        try:
            completed = subprocess.run(
                [
                    command,
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            rows: list[dict[str, object]] = []
            for line in completed.stdout.splitlines():
                name, separator, memory = line.rpartition(",")
                if separator and name.strip():
                    rows.append({"name": name.strip(), "memory_mb": int(memory.strip() or 0)})
            if rows:
                return rows
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if not powershell:
        return []
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            errors="replace",
            timeout=4,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        payload = json.loads(completed.stdout or "[]")
        records = payload if isinstance(payload, list) else [payload]
        return [
            {
                "name": str(record.get("Name") or "显示适配器"),
                "memory_mb": max(0, int(record.get("AdapterRAM") or 0) // 1024**2),
            }
            for record in records
            if isinstance(record, dict)
        ]
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return []


def _webview2_version() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    client_id = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{client_id}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{client_id}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{client_id}"),
    )
    for hive, key_path in keys:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                version = str(winreg.QueryValueEx(key, "pv")[0] or "").strip()
            if version and version != "0.0.0.0":
                return version
        except OSError:
            continue
    application_roots = (
        Path(os.getenv("PROGRAMFILES(X86)", "")) / "Microsoft/EdgeWebView/Application",
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft/EdgeWebView/Application",
    )
    for root in application_roots:
        if not root.is_dir():
            continue
        versions = sorted((path.name for path in root.iterdir() if path.is_dir()), reverse=True)
        if versions:
            return versions[0]
    return ""


def _data_directory_check() -> tuple[bool, str, int]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(settings.data_dir).free
    probe = settings.data_dir / f".environment-check-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="ascii")
        writable = probe.read_text(encoding="ascii") == "ok"
    except OSError:
        writable = False
    finally:
        probe.unlink(missing_ok=True)
    return writable, str(settings.data_dir.resolve()), free_bytes


def _database_check() -> tuple[bool, str]:
    try:
        with sqlite3.connect(settings.db_path) as connection:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        return result.lower() == "ok", result
    except (OSError, sqlite3.Error) as exc:
        return False, str(exc)


def _required_checks() -> tuple[list[dict[str, object]], dict[str, object]]:
    windows_ready = os.name == "nt" and platform.system().lower() == "windows"
    architecture = platform.machine() or "未知"
    architecture_ready = architecture.lower() in {"amd64", "x86_64", "arm64"} and sys.maxsize > 2**32
    writable, data_path, free_bytes = _data_directory_check()
    database_ready, database_detail = _database_check()
    webview_version = _webview2_version()
    webview_ready = bool(webview_version)

    os_label = _windows_label()
    checks = [
        _item("windows", "Windows 系统", "available" if windows_ready else "unsupported", os_label),
        _item("architecture", "64 位运行环境", "available" if architecture_ready else "unsupported", architecture),
        _item(
            "webview2",
            "WebView2 界面运行库",
            "available" if webview_ready else "missing",
            f"版本 {webview_version}" if webview_ready else "没有检测到 WebView2 Runtime",
            action="安装 Microsoft Edge WebView2 Runtime" if not webview_ready else "",
        ),
        _item(
            "data_directory",
            "数据目录可写",
            "available" if writable else "missing",
            data_path if writable else f"无法写入 {data_path}",
            action="更换数据目录或检查目录权限" if not writable else "",
        ),
        _item(
            "disk_space",
            "数据盘空间",
            "available" if free_bytes >= MINIMUM_FREE_BYTES else "missing",
            f"可用 {_format_bytes(free_bytes)}，基础运行至少保留 1 GB",
            action="清理空间或更换数据目录" if free_bytes < MINIMUM_FREE_BYTES else "",
        ),
        _item(
            "database",
            "本地数据库",
            "available" if database_ready else "missing",
            "SQLite 读写与快速校验正常" if database_ready else f"数据库检查失败：{database_detail}",
        ),
        _item("backend", "内置后端", "available", "当前环境检查接口运行正常"),
    ]
    return checks, {
        "os": os_label,
        "architecture": architecture,
        "python_bundled": bool(getattr(sys, "frozen", False)),
        "memory_bytes": _memory_bytes(),
        "memory_label": _format_bytes(_memory_bytes()),
        "gpus": _gpu_info(),
        "data_directory": data_path,
        "free_disk_bytes": free_bytes,
        "free_disk_label": _format_bytes(free_bytes),
    }


def _optional_checks(system: dict[str, object]) -> list[dict[str, object]]:
    profiles = list_model_profiles()
    vision_profiles = [profile for profile in profiles if profile.supports_vision]

    # —— 本地视觉：自动探测 ollama.exe + qwen2.5vl 模型 ——
    ollama_exes = _find_ollama_executables()
    ollama = ollama_exes[0] if ollama_exes else None
    ollama_home = (
        settings.local_vision_dir
        if (settings.local_vision_dir / "models").is_dir()
        else (ollama.parent / ".." if ollama else settings.local_vision_dir)
    )
    model_ready = _ollama_model_ready(settings.local_vision_dir / "models")
    if not model_ready:
        # 在常见 Ollama 模型目录里验证 manifest 引用的每个 blob 都完整存在。
        for probe in (ollama_home / "models", Path(os.getenv("USERPROFILE", "")) / ".ollama" / "models"):
            if _ollama_model_ready(probe):
                model_ready = True
            if model_ready:
                break
    local_vision_probe: dict[str, object] = {}
    try:
        from . import local_vision_service

        # Do not start Ollama during onboarding. If it is already running,
        # perform a bounded real inference check; otherwise report unverified.
        local_vision_probe = local_vision_service.probe_inference(force=True)
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        local_vision_probe = {"ready": False, "state": "failed", "error": str(exc)}
    local_vision_ready = ollama is not None and model_ready and bool(local_vision_probe.get("ready"))

    # —— 本地语音：优先探测 Genie ONNX，兼容旧 GPT-SoVITS ——
    voice_roots = _find_voice_roots()
    voice_ready = False
    voice_hint = "Genie ONNX 本地音色尚未检测到"
    try:
        from . import genie_tts_service

        genie_status = genie_tts_service.runtime_status()
    except (ImportError, OSError, ValueError):
        genie_status = {"ready": False, "missing": ["Genie 环境检测失败"], "runtime_dir": ""}
    if genie_status["ready"]:
        voice_ready = True
        voice_hint = f"Genie ONNX CPU 已就绪：{genie_status['runtime_dir']}"
    else:
        for root in voice_roots:
            layout = _voice_layout(root)
            if layout is None:
                continue
            layout_hint = str(layout["layout"])
            engine_root = Path(layout["root"])
            if bool(layout["runnable"]):
                voice_ready = True
                voice_hint = f"检测到旧 GPT-SoVITS（{layout_hint}）：{engine_root}；建议安装 Genie ONNX"
                break
            missing = "、".join(str(item) for item in layout.get("missing", [])) or "必要文件"
            voice_hint = f"检测到旧 GPT-SoVITS（{layout_hint}），但缺少{missing}：{engine_root}"
        service_ready, service_hint = _probe_voice_service(_configured_voice_service_url())
        if service_ready:
            voice_ready = True
            voice_hint = service_hint + "；当前仍是旧运行时"

    # —— QQ 通道：自动探测 NapCat ——
    napcat_roots = _find_napcat_roots()
    napcat_ready, napcat_found, napcat_detail = _configured_napcat_environment(napcat_roots)

    live2d_index = settings.agent_frontend_dir / "live2d-pet/index.html"
    live2d_model = settings.agent_frontend_dir / "live2d-pet/models/hiyori/Hiyori.model3.json"
    live2d_ready = live2d_index.is_file() and live2d_model.is_file()
    capture_ready = importlib.util.find_spec("mss") is not None

    # —— 系统声音理解：完整探测可实际启动的 Python 环境 + whisper 模型 ——
    audio_worker = settings.agent_control_scripts_dir / "system_audio_worker.py"
    whisper_worker_ok = audio_worker.is_file()
    whisper_layouts = whisper_runtime_candidates()
    whisper_runtime = next((layout for layout in whisper_layouts if layout["ready"]), None)
    audio_ready = whisper_worker_ok and whisper_runtime is not None
    if audio_ready:
        runtime_root = Path(whisper_runtime["root"])
        if runtime_root.resolve() == settings.voice_training_dir.resolve():
            audio_detail = f"声音转写环境与模型已找到：{runtime_root}"
        else:
            audio_detail = f"已复用电脑上的 faster-whisper 环境：{runtime_root}"
    elif whisper_layouts:
        partial = whisper_layouts[0]
        missing_parts = []
        if not partial["python_ready"]:
            missing_parts.append("可运行的 faster-whisper Python 环境")
        if not partial["model_ready"]:
            missing_parts.append("完整本地模型")
        audio_detail = f"电脑上发现部分文件，但缺少{'、'.join(missing_parts)}：{partial['root']}"
    elif not whisper_worker_ok:
        audio_detail = "程序缺少系统声音转写工作器"
    else:
        audio_detail = "尚未找到可运行的 faster-whisper 环境与完整本地模型"

    gpu_memory = max((int(item.get("memory_mb") or 0) for item in system.get("gpus", [])), default=0)
    local_recommendation = "当前已经可以使用"
    probe_state = str(local_vision_probe.get("state") or "unverified")
    probe_error = str(local_vision_probe.get("error") or "")
    if not model_ready:
        local_recommendation = "模型文件未完整安装，不能进行真实推理"
    elif probe_state == "oom":
        local_recommendation = "模型已安装，但当前系统提交内存不足，真实推理会 OOM"
    elif probe_state == "unverified":
        local_recommendation = "模型已安装，但 Ollama 未运行，尚未完成真实推理验证"
    elif not local_vision_ready:
        local_recommendation = "模型已安装，但真实推理探针失败"
    if not local_vision_ready and probe_error:
        local_recommendation += f"：{probe_error[:240]}"
    if not local_vision_ready:
        local_recommendation = (
            "显存达到 6 GB，可按需安装；不会自动下载"
            if gpu_memory >= 6144
            else "建议先使用云端视觉；本地视觉需要额外显存与磁盘"
        )

    return [
        _item(
            "cloud_model",
            "云端或兼容 API 模型",
            "configured" if profiles else "unconfigured",
            f"已配置 {len(profiles)} 个模型" if profiles else "尚未配置；首次向导需要先完成一次真实聊天测试",
            action="前往模型与 API 设置" if not profiles else "",
        ),
        _item(
            "cloud_vision",
            "云端视觉",
            "configured" if vision_profiles else "unconfigured",
            f"有 {len(vision_profiles)} 个模型声明支持图片" if vision_profiles else "当前没有声明支持图片的云端模型",
            action="添加支持图片的模型" if not vision_profiles else "",
        ),
        _item(
            "local_vision",
            "本地视觉模型",
            "available" if local_vision_ready else ("degraded" if model_ready else "unconfigured"),
            f"Qwen2.5-VL 3B：{local_recommendation}",
            action="稍后在屏幕观察设置中安装或选择目录" if not local_vision_ready else "",
        ),
        _item(
            "voice",
            "Mio 的本地语音",
            "available" if voice_ready else "unconfigured",
            voice_hint,
            action="稍后在语音设置中配置" if not voice_ready else "",
        ),
        _item(
            "qq",
            "QQ 通道",
            "configured" if napcat_ready else ("degraded" if napcat_found else "unconfigured"),
            napcat_detail,
            action="在 QQ 设置中安装并修复当前目录" if not napcat_ready else "",
        ),
        _item(
            "live2d",
            "Live2D 桌宠",
            "available" if live2d_ready else "unconfigured",
            "内置运行库和示例模型已就绪" if live2d_ready else "没有找到完整的 Live2D 运行资源",
            action="可以先使用静态形象" if not live2d_ready else "",
        ),
        _item(
            "screen_capture",
            "屏幕与窗口捕获",
            "available" if capture_ready else "unsupported",
            "基础截图组件可用；具体游戏和显卡仍需实际测试" if capture_ready else "缺少基础截图组件",
        ),
        _item(
            "system_audio",
            "系统声音理解",
            "available" if audio_ready else "unconfigured",
            audio_detail,
            action="一键安装会补齐运行环境和模型" if not audio_ready else "",
        ),
    ]


def environment_status(*, refresh: bool = True) -> dict[str, Any]:
    if refresh:
        refresh_detection_cache()
    required, system = _required_checks()
    optional = _optional_checks(system)
    core_ready = all(item["status"] == "available" for item in required)
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "core_ready": core_ready,
        "required": required,
        "optional": optional,
        "system": system,
        "summary": {
            "required_ready": sum(item["status"] == "available" for item in required),
            "required_total": len(required),
            "optional_ready": sum(item["status"] in {"available", "configured"} for item in optional),
            "optional_total": len(optional),
        },
    }


def passive_environment_status() -> dict[str, Any]:
    """Report readiness without creating files, directories, or database state."""
    windows_ready = os.name == "nt" and platform.system().lower() == "windows"
    architecture = platform.machine() or "未知"
    architecture_ready = architecture.lower() in {"amd64", "x86_64", "arm64"} and sys.maxsize > 2**32
    webview_version = _webview2_version()
    data_directory_exists = settings.data_dir.is_dir()
    try:
        free_bytes = shutil.disk_usage(settings.data_dir).free if data_directory_exists else 0
    except OSError:
        free_bytes = 0
    database_exists = settings.db_path.is_file()
    profiles = list_model_profiles()
    configured_models = sum(bool(profile.api_key and profile.base_urls) for profile in profiles)
    required = {
        "windows": windows_ready,
        "architecture": architecture_ready,
        "webview2": bool(webview_version),
        "data_directory": data_directory_exists,
        "disk_space": free_bytes >= MINIMUM_FREE_BYTES,
        "database": database_exists,
        "backend": True,
    }
    gpus = _gpu_info()
    return {
        "mode": "passive_read_only",
        "core_ready": all(required.values()),
        "required": required,
        "summary": {
            "required_ready": sum(required.values()),
            "required_total": len(required),
            "configured_models": configured_models,
            "model_total": len(profiles),
            "gpu_count": len(gpus),
            "memory_bytes": _memory_bytes(),
            "free_disk_bytes": free_bytes,
            "webview2_version": webview_version,
        },
    }


__all__ = ["environment_status", "passive_environment_status"]
