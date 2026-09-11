from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .llm import CompletionResult
from .ollama_model_files import manifest_complete


DEFAULT_PORT = 11435
_server_url = ""
DEFAULT_MODEL = "qwen2.5vl:3b"
_lock = threading.Lock()
_lifecycle_lock = threading.RLock()
_server_process: subprocess.Popen | None = None
_pull_process: subprocess.Popen | None = None
_last_error = ""
_probe_at = 0.0
_probe_cache: dict[str, Any] = {}
_server_desired_running = False
_inference_probe_at = 0.0
_inference_probe_result: dict[str, Any] = {}


def _ollama_executable() -> Path:
    return settings.local_vision_dir / "Ollama" / "ollama.exe"


def _models_dir() -> Path:
    return settings.local_vision_dir / "models"


def _home_dir() -> Path:
    return settings.local_vision_dir / "home"


def _model_manifest_path(model: str = DEFAULT_MODEL) -> Path:
    name, _, tag = model.partition(":")
    return _models_dir() / "manifests" / "registry.ollama.ai" / "library" / name / (tag or "latest")


def _runtime_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("OLLAMA_")}
    _home_dir().mkdir(parents=True, exist_ok=True)
    env["OLLAMA_MODELS"] = str(_models_dir())
    env["OLLAMA_HOST"] = _server_url.removeprefix("http://")
    env["OLLAMA_KEEP_ALIVE"] = "5m"
    env["HOME"] = str(_home_dir())
    env["USERPROFILE"] = str(_home_dir())
    no_proxy = [item.strip() for item in env.get("NO_PROXY", "").split(",") if item.strip()]
    for host in ("127.0.0.1", "localhost", "::1"):
        if host not in no_proxy:
            no_proxy.append(host)
    env["NO_PROXY"] = ",".join(no_proxy)
    return env


def _invalidate_probe() -> None:
    global _probe_at
    _probe_at = 0.0


def installation_status() -> dict[str, bool]:
    executable = _ollama_executable()
    try:
        runtime_installed = executable.is_file() and executable.stat().st_size > 0
    except OSError:
        runtime_installed = False
    return {"runtime_installed": runtime_installed,
            "model_installed": manifest_complete(_model_manifest_path(), _models_dir())}


def _owned_server_url() -> str:
    with _lock:
        if _server_process is None or _server_process.poll() is not None or not _server_url:
            raise OSError("Mio 本地视觉服务未启动。")
        return _server_url


def _available_server_url() -> str:
    # An occupied preferred port is never treated as an existing Mio service.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", DEFAULT_PORT))
        except OSError:
            listener.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{listener.getsockname()[1]}"


def _request_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 4.0) -> dict[str, Any]:
    base_url = _owned_server_url()
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.request(method, f"{base_url}{path}", json=payload)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else {}


def _model_names(payload: dict[str, Any], key: str) -> list[str]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("model") or "").strip()
        if name:
            names.append(name)
    return names


def _matches_model(name: str, model: str) -> bool:
    clean = str(name or "").strip().lower()
    target = str(model or "").strip().lower()
    return clean == target or clean == f"{target}:latest"


def status(*, force: bool = False) -> dict[str, Any]:
    global _probe_at, _probe_cache, _last_error
    now = time.monotonic()
    with _lock:
        if not force and _probe_cache and now - _probe_at < 3.0:
            return dict(_probe_cache)
        server_process = _server_process
        pull_process = _pull_process
    installed = installation_status()
    runtime_installed = installed["runtime_installed"]
    server_running = False
    installed_models: list[str] = []
    loaded_models: list[str] = []
    probe_error = ""
    try:
        version = _request_json("/api/version")
        server_running = bool(version.get("version"))
        if server_running:
            installed_models = _model_names(_request_json("/api/tags"), "models")
            loaded_models = _model_names(_request_json("/api/ps"), "models")
    except (OSError, ValueError, httpx.HTTPError) as exc:
        probe_error = str(exc)
    model = DEFAULT_MODEL
    pulling = bool(pull_process is not None and pull_process.poll() is None)
    owned_server = bool(server_process is not None and server_process.poll() is None)
    if server_process is not None and not owned_server and _server_desired_running:
        probe_error = f"Mio 本地视觉服务已退出，退出码：{server_process.returncode}。"
    result = {
        "runtime_installed": runtime_installed,
        "server_running": server_running,
        "owned_server": owned_server,
        "model": model,
        "model_installed": installed["model_installed"],
        "model_available": any(_matches_model(name, model) for name in installed_models),
        "base_url": _server_url if owned_server else "",
        "model_loaded": any(_matches_model(name, model) for name in loaded_models),
        "inference_ready": owned_server and server_running and bool(_inference_probe_result.get("ready")),
        "inference_state": str(_inference_probe_result.get("state") or "unverified"),
        "inference_error": str(_inference_probe_result.get("error") or ""),
        "inference_probe_at": _inference_probe_result.get("checked_at"),
        "pulling": pulling,
        "root": str(settings.local_vision_dir),
        "models_dir": str(_models_dir()),
        "last_error": _last_error or (probe_error if runtime_installed and server_process is not None else ""),
    }
    with _lock:
        _probe_at = now
        _probe_cache = dict(result)
    return result


def probe_inference(*, force: bool = False, timeout: float = 90.0) -> dict[str, Any]:
    """Run a bounded real Ollama inference probe, without starting the server."""
    global _inference_probe_at, _inference_probe_result, _last_error
    current = status(force=True)
    now = time.monotonic()
    with _lock:
        if current["server_running"] and not force and _inference_probe_result and now - _inference_probe_at < 15.0:
            return dict(_inference_probe_result)
    checked_at = time.time()
    if not current.get("server_running"):
        result = {"ready": False, "state": "unverified", "error": "Ollama 服务未运行，尚未执行真实推理探针", "checked_at": checked_at}
    elif not current.get("model_installed") or not current.get("model_available"):
        result = {"ready": False, "state": "missing", "error": f"未找到本地模型 {DEFAULT_MODEL}", "checked_at": checked_at}
    else:
        try:
            data = _request_json(
                "/api/generate", method="POST",
                payload={"model": DEFAULT_MODEL, "prompt": "Reply with OK.", "stream": False, "keep_alive": 0,
                         "options": {"num_ctx": 128, "num_predict": 2, "temperature": 0}},
                timeout=timeout,
            )
            result = ({"ready": True, "state": "ready", "error": "", "checked_at": checked_at}
                      if str(data.get("response") or "").strip()
                      else {"ready": False, "state": "failed", "error": "本地视觉模型返回了空结果", "checked_at": checked_at})
        except (OSError, ValueError, httpx.HTTPError) as exc:
            message = str(exc).strip() or type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    body = exc.response.json()
                    message = str(body.get("error") or message) if isinstance(body, dict) else message
                except ValueError:
                    pass
            lowered = message.lower()
            state = "oom" if any(token in lowered for token in ("out of memory", "oom", "memory", "commit")) else "failed"
            result = {"ready": False, "state": state, "error": message[:500], "checked_at": checked_at}
            _last_error = message[:500]
    with _lock:
        _inference_probe_at = now
        _inference_probe_result = dict(result)
        if result.get("ready"):
            _last_error = ""
    return result


def dependency_status() -> dict[str, Any]:
    current = status(force=True)
    if not current["runtime_installed"]:
        state, detail = "missing", "Mio 本地视觉运行器尚未安装。"
    elif not current["model_installed"]:
        state, detail = "missing", f"运行器已安装，但 {DEFAULT_MODEL} 模型文件缺失或不完整。"
    elif not current["server_running"]:
        state = "degraded" if current["last_error"] else "installed"
        detail = current["last_error"] or "模型文件已完整安装，Mio 本地视觉服务尚未启动。"
    elif current["inference_ready"]:
        state, detail = "available", "Mio 独立本地视觉服务已通过推理验证。"
    elif current["inference_state"] in {"failed", "oom", "missing"}:
        state, detail = "degraded", current["inference_error"] or "模型已安装，但推理失败。"
    else:
        state, detail = "unverified", "服务已启动，尚未验证模型推理。"
    return {"status": state, "detail": detail, **current}


def passive_status() -> dict[str, Any]:
    """Return cached local-vision health without contacting Ollama."""
    now = time.monotonic()
    with _lock:
        cached = dict(_probe_cache)
        probe_at = _probe_at
        server_process = _server_process
        pull_process = _pull_process
        last_error = _last_error
        desired_running = _server_desired_running
    owned_server = bool(server_process is not None and server_process.poll() is None)
    pulling = bool(pull_process is not None and pull_process.poll() is None)
    probe_age = max(0.0, now - probe_at) if probe_at else None
    observed_running = (
        bool(cached.get("server_running"))
        if probe_age is not None and probe_age <= 10
        else (True if owned_server else None)
    )
    return {
        "runtime_installed": _ollama_executable().is_file(),
        "model": DEFAULT_MODEL,
        "model_installed": installation_status()["model_installed"],
        "inference_ready": owned_server and bool(_inference_probe_result.get("ready")),
        "inference_state": str(_inference_probe_result.get("state") or cached.get("inference_state") or "unverified"),
        "inference_error": str(_inference_probe_result.get("error") or cached.get("inference_error") or ""),
        "model_loaded": bool(cached.get("model_loaded")) if observed_running is not None else None,
        "owned_server": owned_server,
        "desired_running": desired_running,
        "observed_running": observed_running,
        "pulling": pulling,
        "probe_age_seconds": round(probe_age, 3) if probe_age is not None else None,
        "probe_stale": observed_running is None,
        "last_error": str(last_error or cached.get("last_error") or ""),
    }


def start_server() -> dict[str, Any]:
    global _server_process, _server_url, _last_error, _server_desired_running
    global _inference_probe_at, _inference_probe_result
    with _lifecycle_lock:
        with _lock:
            _server_desired_running = True
            process = _server_process
        if process is not None and process.poll() is None:
            return status(force=True)
        executable = _ollama_executable()
        if not installation_status()["runtime_installed"]:
            raise FileNotFoundError(f"本地视觉运行器不存在：{executable}")
        settings.local_vision_dir.mkdir(parents=True, exist_ok=True)
        _models_dir().mkdir(parents=True, exist_ok=True)
        _server_url = _available_server_url()
        _inference_probe_at = 0.0
        _inference_probe_result = {}
        log_path = settings.local_vision_dir / "ollama.log"
        try:
            with log_path.open("ab") as log_handle:
                process = subprocess.Popen(
                    [str(executable), "serve"], cwd=str(executable.parent), env=_runtime_env(),
                    stdout=log_handle, stderr=log_handle,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            with _lock:
                _server_process = process
                _last_error = ""
            _invalidate_probe()
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise OSError(f"本地视觉服务启动失败，退出码：{process.returncode}。请查看 {log_path}")
                current = status(force=True)
                if current["server_running"] and process.poll() is None:
                    return current
                time.sleep(0.25)
            raise TimeoutError(f"本地视觉服务启动超时，请查看 {log_path}")
        except (OSError, RuntimeError) as exc:
            _last_error = str(exc)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            with _lock:
                _server_process = None
            _server_url = ""
            _invalidate_probe()
            raise


def unload_model() -> dict[str, Any]:
    global _inference_probe_at, _inference_probe_result
    with _lifecycle_lock:
        current = status(force=True)
        if current.get("owned_server") and current.get("server_running") and current.get("model_available"):
            try:
                _request_json("/api/generate", method="POST",
                              payload={"model": DEFAULT_MODEL, "prompt": "", "keep_alive": 0, "stream": False},
                              timeout=20.0)
            except (OSError, ValueError, httpx.HTTPError):
                pass
        _inference_probe_at = 0.0
        _inference_probe_result = {}
        _invalidate_probe()
        return status(force=True)


def stop_server() -> dict[str, Any]:
    global _server_process, _pull_process, _server_url, _last_error, _server_desired_running
    with _lifecycle_lock:
        unload_model()
        with _lock:
            _server_desired_running = False
            processes = (_pull_process, _server_process)
            _pull_process = None
            _server_process = None
        for process in processes:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        _server_url = ""
        _last_error = ""
        _invalidate_probe()
        return status(force=True)


def restart_server() -> dict[str, Any]:
    with _lifecycle_lock:
        stop_server()
        return start_server()


def start_model_pull() -> dict[str, Any]:
    global _pull_process, _last_error
    with _lifecycle_lock:
        start_server()
        with _lock:
            pulling = _pull_process is not None and _pull_process.poll() is None
        if pulling:
            return status(force=True)
        log_path = settings.local_vision_dir / "模型下载.log"
        with log_path.open("ab") as log_handle:
            process = subprocess.Popen(
                [str(_ollama_executable()), "pull", DEFAULT_MODEL],
                cwd=str(_ollama_executable().parent), env=_runtime_env(),
                stdout=log_handle, stderr=log_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        with _lock:
            _pull_process = process
            _last_error = ""
        _invalidate_probe()
        return status(force=True)


def activate() -> dict[str, Any]:
    with _lifecycle_lock:
        installed = installation_status()
        if not all(installed.values()):
            raise ValueError("本地视觉文件尚未完整安装，请先安装或修复缺失文件。")
        current = status(force=True)
        if current["owned_server"] and not current["server_running"]:
            restart_server()
        else:
            start_server()
        probe_inference(force=True, timeout=90.0)
        return dependency_status()


async def ensure_ready() -> dict[str, Any]:
    current = await asyncio.to_thread(status, force=True)
    if not current.get("server_running"):
        current = await asyncio.to_thread(start_server)
    if not current.get("model_installed"):
        raise RuntimeError(
            f"本地视觉模型 {DEFAULT_MODEL} 尚未下载。整屏画面不会转发云端，请先下载模型。"
        )
    probe = await asyncio.to_thread(probe_inference)
    if not probe.get("ready"):
        detail = str(probe.get("error") or "真实推理探针未通过")
        raise RuntimeError(f"本地视觉模型当前不可推理（{probe.get('state') or 'unverified'}）：{detail}")
    return current


async def analyze_image(*, prompt: str, image: bytes, system_prompt: str) -> CompletionResult:
    global _last_error
    await ensure_ready()
    payload = {
        "model": DEFAULT_MODEL,
        "stream": False,
        "keep_alive": "90s",
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image).decode("ascii")],
            },
        ],
        # The perception response is a compact JSON object. A smaller context
        # and bounded output materially reduce CPU latency on qwen2.5-vl:3b.
        "options": {"temperature": 0.1, "num_ctx": 2048, "num_predict": 220},
    }
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            response = await client.post(f"{_owned_server_url()}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
    except (OSError, ValueError, httpx.HTTPError) as exc:
        _last_error = str(exc)
        _invalidate_probe()
        raise RuntimeError(f"本地视觉识别失败：{exc}") from exc
    message = data.get("message") if isinstance(data, dict) else None
    content = str(message.get("content") or "") if isinstance(message, dict) else ""
    if not content.strip():
        raise RuntimeError("本地视觉模型没有返回识别结果")
    _last_error = ""
    _invalidate_probe()
    return CompletionResult(
        content=content,
        model=f"本地 · {DEFAULT_MODEL}",
        prompt_tokens=max(0, int(data.get("prompt_eval_count") or 0)),
        cached_prompt_tokens=0,
        completion_tokens=max(0, int(data.get("eval_count") or 0)),
        reasoning_tokens=0,
        cost_yuan=0.0,
        cost_source="local",
    )


__all__ = [
    "DEFAULT_MODEL",
    "analyze_image",
    "ensure_ready",
    "passive_status",
    "probe_inference",
    "restart_server",
    "start_model_pull",
    "start_server",
    "status",
    "stop_server",
    "unload_model",
]
