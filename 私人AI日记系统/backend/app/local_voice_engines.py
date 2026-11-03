"""Own only explicitly installed loopback TTS children; never discover/kill external servers."""
from __future__ import annotations

import atexit
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx

from .probe_process import ProbeProcess, creation_flags

ENGINES = {"gpt_sovits_v5", "indextts2", "aniflive", "mio_v2"}
URL_KEYS = {"gpt_sovits_v5": "gpt_sovits_v5_url", "indextts2": "indextts2_url", "aniflive": "aniflive_url", "mio_v2": "gpt_sovits_url"}
NATIVE_PROFILE_FIELDS = ("gpt_sovits_gpt_weights", "gpt_sovits_sovits_weights", "gpt_sovits_ref_audio",
                         "gpt_sovits_prompt_text", "gpt_sovits_prompt_language")


def selected_native_profile(config: dict) -> dict:
    """Resolve the saved default without importing the companion facade."""
    selected = dict(config)
    profile_id = str(config.get("voice_profile_id") or config.get("default_voice_profile_id") or "mio")
    profiles = config.get("voice_profiles") or {}
    if "voice_profile_id" not in config and isinstance(profiles, dict) and isinstance(profiles.get(profile_id), dict):
        selected.update(profiles[profile_id])
    selected["voice_profile_id"] = profile_id
    return selected


def native_selection_key(config: dict) -> tuple:
    selected = selected_native_profile(config)
    return (str(config.get("gpt_sovits_url") or "http://127.0.0.1:9880").rstrip("/"),
            selected["voice_profile_id"], *(str(selected.get(key) or "") for key in NATIVE_PROFILE_FIELDS))


def _native_profile_entry(entry: dict, config: dict) -> dict:
    selected = selected_native_profile(config)
    values = {"--gpt": selected.get("gpt_sovits_gpt_weights"), "--sovits": selected.get("gpt_sovits_sovits_weights"),
              "--reference": selected.get("gpt_sovits_ref_audio"), "--reference-text": selected.get("gpt_sovits_prompt_text"),
              "--reference-language": selected.get("gpt_sovits_prompt_language") or "ja"}
    if not any(selected.get(key) for key in NATIVE_PROFILE_FIELDS):
        return entry  # Existing machine manifests may still hold their original default.
    for flag, suffixes in (("--gpt", {".ckpt"}), ("--sovits", {".pth"}),
                           ("--reference", {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"})):
        value = Path(str(values[flag] or "")).expanduser()
        if not value.is_absolute() or not value.is_file() or value.suffix.lower() not in suffixes:
            raise OSError(f"所选V2音色文件不可用：{flag[2:]}。")
        values[flag] = str(value.resolve())
    if not isinstance(values["--reference-text"], str) or not values["--reference-text"].strip() or len(values["--reference-text"]) > 1000:
        raise OSError("所选V2音色需要参考音频的准确原文（1–1000字）。")
    if values["--reference-language"] not in {"zh", "ja", "en", "all_zh", "all_ja"}:
        raise OSError("所选V2音色参考语言不受支持。")
    values["--voice-profile"] = selected["voice_profile_id"]
    args = list(entry.get("args", []))
    for flag, value in values.items():
        positions = [index for index, item in enumerate(args) if item == flag]
        if len(positions) > 1 or positions and positions[0] + 1 >= len(args):
            raise OSError("本地V2语音启动参数重复或缺失。")
        if positions:
            args[positions[0] + 1] = str(value)
        else:
            args.extend([flag, str(value)])
    return {**entry, "args": args, "voice_profile_id": selected["voice_profile_id"]}


def _launch_key(entry: dict) -> str:
    return hashlib.sha256(json.dumps({key: entry.get(key) for key in ("python", "script", "cwd", "url", "args", "env")},
                                    sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def manifest_path() -> Path:
    configured = os.getenv("MIO_VOICE_ENGINES_MANIFEST", "").strip()
    return Path(configured) if configured else Path(sys.executable).resolve().parent / "local-voice-engines.json"


def load_entry(engine: str, config: dict) -> dict:
    if engine not in ENGINES:
        raise ValueError("不支持的本地语音引擎。")
    path = manifest_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError()
        entry = dict(data["engines"][engine])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OSError("此电脑尚未安装该引擎的本地启动配置。") from exc
    if data.get("schema") != 1:
        raise OSError("本地语音启动配置版本不匹配。")
    url = str(entry.get("url") or "").rstrip("/")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise OSError("本地语音启动地址或端口格式无效。") from exc
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not port or parts.path or parts.query or parts.fragment or parts.username:
        raise OSError("本地语音启动地址必须为127.0.0.1及明确端口。")
    selected_url = str(config.get(URL_KEYS[engine]) or url).rstrip("/")
    if selected_url != url:
        raise OSError("当前服务地址与本机安装配置不一致；自定义或远程服务请自行启动。")
    for key in ("python", "script", "cwd"):
        value = Path(str(entry.get(key) or ""))
        if not value.is_absolute() or not (value.is_dir() if key == "cwd" else value.is_file()):
            raise OSError(f"本地语音安装路径不可用：{key}。")
    if not isinstance(entry.get("args", []), list) or not all(isinstance(item, str) for item in entry.get("args", [])):
        raise OSError("本地语音启动参数无效。")
    environment = entry.get("env", {})
    if not isinstance(environment, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items()):
        raise OSError("本地语音环境配置无效。")
    entry["url"] = url
    if engine == "mio_v2":
        entry = _native_profile_entry(entry, config)
    return entry


def resource_check(root: Path) -> None:
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ("total", "free", "total_page", "free_page", "total_virtual", "free_virtual", "free_ext")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise OSError("无法检查可用内存，已停止本地语音进程。")
        if memory.free < 512 * 1024**2:
            raise OSError("可用内存低于512MiB，已停止本地语音进程。")
    if shutil.disk_usage(root).free < 8 * 1024**3:
        raise OSError("语音所在磁盘剩余空间低于8GiB，已停止本地语音进程。")
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or int(result.stdout.strip().splitlines()[0]) < 384:
            raise ValueError()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        raise OSError("显存不足384MiB或无法检查显卡，已停止本地语音进程。") from exc


def loopback_port_open(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    if parsed.hostname != "127.0.0.1" or not port:
        return False
    with socket.socket() as connection:
        connection.settimeout(.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


class OwnedProcess(ProbeProcess):
    def __init__(self, entry: dict):
        self.job = None
        self.error_type = ""
        environment = dict(os.environ)
        environment.update(entry.get("env", {}))
        environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        self.process = subprocess.Popen([entry["python"], "-u", entry["script"], *entry.get("args", [])], cwd=entry["cwd"], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, close_fds=True,
            creationflags=creation_flags(), start_new_session=os.name != "nt")
        if os.name == "nt":
            try:
                self._assign_job()
            except BaseException:
                self.close()
                raise
        threading.Thread(target=self._drain_diagnostics, daemon=True, name="mio-local-voice-diagnostics").start()

    def _drain_diagnostics(self):
        # Drain continuously, retain only Python exception class names. Model libraries
        # may print user text/reference paths; those are not copied to API diagnostics.
        with self.process.stderr as stream:
            for raw in iter(lambda: stream.read(4096), b""):
                matches = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))(?=:)", raw.decode("utf-8", errors="replace"))
                if matches:
                    self.error_type = matches[-1]


class LocalVoiceManager:
    def __init__(self):
        self.lifecycle_lock = threading.RLock()
        self.lock = threading.RLock()
        self.selection_reader = None
        self.child = None
        self.engine = ""
        self.url = ""
        self.loading = False
        self.error = ""
        self.closed = threading.Event()
        self.launch_key = ""
        self.last_successful_profile_id = ""

    def status(self, engine: str, config: dict) -> dict:
        try:
            entry = load_entry(engine, config)
            available, install_error = True, ""
        except (OSError, ValueError) as exc:
            entry, available, install_error = {}, False, str(exc)
        with self.lock:
            owned = self.engine == engine and self.child is not None
            running = owned and self.child.poll() is None
            error = self.error if self.engine == engine else ""
            return {"managed_available": available, "managed_running": running, "service_loading": bool(running and self.loading),
                    "managed_last_error": error, "managed_install_error": install_error,
                    "last_successful_profile_id": self.last_successful_profile_id if engine == "mio_v2" else "",
                    "experimental": bool(entry.get("experimental", engine == "aniflive")),
                    "numerical_verified": bool(entry.get("numerical_verified", False))}

    def _stop_locked(self):
        self.closed.set()
        self.loading = False
        if self.child is not None:
            try:
                self.child.close()
                # Windows can retire the Job before its TCP listener disappears.
                # Only wait for this manager's recorded owned URL, never a host
                # discovery result or every installed/external service port.
                deadline = time.monotonic() + 5
                while loopback_port_open(self.url):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Owned voice service port did not close")
                    time.sleep(.02)
            except (OSError, subprocess.SubprocessError) as exc:
                # Retain ownership so a retry can complete teardown. Starting a
                # second model must never hide an unfinished owned shutdown.
                self.error = "本地语音进程树或服务端口未能完全退出；请重试停止后再启动其他引擎。"
                raise OSError(self.error) from exc
            self.child = None
            self.launch_key = ""

    def stop_all(self):
        with self.lifecycle_lock, self.lock:
            self._stop_locked()

    def require_original_selected(self):
        """Automatic/late original requests never displace an explicit new-engine start."""
        with self.lock:
            if self.child is not None and self.child.poll() is None:
                raise OSError("另一个本地语音引擎正在运行；请从语音设置显式切换并启动。")
        if self.selection_reader is not None:
            current = self.selection_reader()
            if (current.get("voice_engine", "gpt_sovits") != "gpt_sovits"
                    or current.get("local_voice_runtime") == "mio_v2"):
                raise OSError("语音引擎已切换，已取消迟到的原音色加载。")

    def ensure_mio_v2(self, config: dict):
        """Late synthesis may start its selected runtime, never displace another model."""
        with self.lifecycle_lock, self.lock:
            current = self.selection_reader() if self.selection_reader is not None else config
            if (current.get("voice_engine", "gpt_sovits") != "gpt_sovits"
                    or current.get("local_voice_runtime") != "mio_v2"
                    or native_selection_key(current) != native_selection_key(config)):
                raise OSError("语音音色或运行时已切换，请重新开始语音回复。")
            if self.child is not None and self.child.poll() is None and self.engine != "mio_v2":
                raise OSError("另一个本地语音引擎正在运行；请从语音设置显式切换并启动。")
            if (self.child is not None and self.child.poll() is None and self.engine == "mio_v2"
                    and self.launch_key != _launch_key(load_entry("mio_v2", config))):
                raise OSError("运行中的V2音色不同；请保存并显式启动所选音色。")
            self.start("mio_v2", config)

    def stop(self, engine: str):
        with self.lifecycle_lock, self.lock:
            if self.engine == engine:
                self._stop_locked()

    def start(self, engine: str, config: dict):
        entry = load_entry(engine, config)
        with self.lifecycle_lock, self.lock:
            key = _launch_key(entry)
            if engine == "mio_v2" and self.selection_reader is not None:
                current = self.selection_reader()
                if (current.get("voice_engine", "gpt_sovits") != "gpt_sovits"
                        or current.get("local_voice_runtime") != "mio_v2"
                        or native_selection_key(current) != native_selection_key(config)):
                    raise OSError("语音音色或运行时已切换，请重新启动。")
            if self.engine == engine and self.child and self.child.poll() is None and not self.closed.is_set() and self.launch_key == key:
                return
            self._stop_locked()
            # No port ownership is inferred from /health or a PID discovered on the host.
            urls = {entry["url"]}
            try:
                installed = json.loads(manifest_path().read_text(encoding="utf-8-sig"))["engines"]
                urls.update(str(item.get("url", "")) for key, item in installed.items() if key in ENGINES and isinstance(item, dict))
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
            for url in urls:
                if loopback_port_open(url):
                    raise OSError("已安装引擎的端口存在独立服务；请先自行停止该服务，Mio不会接管或停止它。")
            resource_check(Path(entry["cwd"]))
            self.engine, self.url, self.error, self.loading = engine, entry["url"], "", True
            self.closed = threading.Event()
            try:
                child = OwnedProcess(entry)
            except Exception:
                self.loading = False
                raise
            self.child = child
            self.launch_key = key
            threading.Thread(target=self._monitor, args=(child, engine, entry, self.closed), daemon=True, name="mio-local-voice-monitor").start()

    def _monitor(self, child, engine, entry, stopped):
        started = time.monotonic()
        ready = False
        try:
            while not stopped.wait(1):
                if child.poll() is not None:
                    kind = f"，{child.error_type}" if child.error_type else ""
                    raise OSError(f"本地语音进程已退出（代码{child.poll()}{kind}）；请检查安装环境和模型文件。")
                resource_check(Path(entry["cwd"]))
                if not ready:
                    try:
                        with httpx.Client(timeout=1, trust_env=False) as client:
                            result = client.get(entry["url"] + "/health")
                        metadata = result.json() if result.is_success else {}
                        identity = (metadata.get("service") == "mio-v2-native" and metadata.get("engine") == "mio_v2" and metadata.get("version") == "v2" if engine == "mio_v2" else
                                    str(metadata.get("service") or "").startswith("aniflive") if engine == "aniflive" else
                                    str(metadata.get("model_version")) in {"v5", "v5dev", "v5turbo"} if engine == "gpt_sovits_v5" else
                                    metadata.get("protocol") == "mio-indextts2-v1" and str(metadata.get("model_version")) == str(entry.get("model_version", "2.5")))
                        ready = metadata.get("ready") is True and identity
                    except (httpx.HTTPError, ValueError, AttributeError):
                        pass
                    with self.lock:
                        if self.child is not child:
                            return
                        self.loading = not ready
                        if ready and engine == "mio_v2":
                            self.last_successful_profile_id = str(entry.get("voice_profile_id") or "")
                    if not ready and time.monotonic() - started > float(entry.get("startup_timeout", 180)):
                        raise OSError("本地语音模型加载超时，已停止本次启动。")
        except OSError as exc:
            with self.lock:
                if self.child is child:
                    self.error = str(exc)
                    self._stop_locked()


manager = LocalVoiceManager()
atexit.register(manager.stop_all)
