"""公开版"环境与模型中心"：依赖检测、一键安装与进度查询。

设计目标（方案 v3）：
- 首次启动向导与设置页共用同一份清单，一键检查哪些依赖已有、哪些缺失。
- 缺失项默认用命令行安装（国内源优先），也可以跳到官方页面手动安装。
- 安装脚本在独立控制台窗口运行（用户看得见进度），同时把进度写入 status
  文件供界面轮询，下载不阻塞向导后续步骤。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from . import environment_check_service
from . import companion_service, genie_tts_service
from .config import settings
from .model_registry import list_model_profiles

STATUS_DIR_NAME = "dependency-install"

# 安装脚本位于 agent_control_scripts_dir/deps 下（随程序发布）。
DEPS_SCRIPTS_DIR_NAME = "deps"

# 各依赖的文案：是什么 / 没有会怎样 / 怎么装。
_DEPENDENCY_DEFS: tuple[dict[str, Any], ...] = (
    {
        "id": "cloud_model",
        "label": "聊天模型",
        "kind": "configure",
        "size_label": "无下载",
        "what": "让 Mio 能聊天的大脑，用你自己的 API Key 接入。",
        "missing_effect": "没有它就不能对话，但不会影响其他功能。",
        "how": "在首次向导或「模型与 API」设置里填写供应商与 Key。",
    },
    {
        "id": "cloud_tts",
        "label": "云端语音（豆包）",
        "kind": "configure",
        "size_label": "无下载",
        "what": "让 Mio 开口说话，火山引擎豆包音色，填一个 API Key 就能用。",
        "missing_effect": "没有它，回复朗读和桌宠声音都不会出声。",
        "how": "在语音设置里选择「云端语音」并填写语音 API Key。",
    },
    {
        "id": "genie_runtime",
        "label": "Genie 本地语音运行引擎",
        "kind": "script",
        "script": "install-genie-runtime.ps1",
        "size_label": "运行环境按需安装；引擎包约 0.4 GB",
        "what": "本地语音推理底座，负责 Genie ONNX CPU 运行环境和基础 G2P/Hubert 数据。",
        "missing_effect": "没有它就不能运行本地音色；云端语音和聊天不受影响。",
        "how": "先安装这个运行引擎。完成后再单独安装 Mio 音色包；两个安装项互不重复下载。",
    },
    {
        "id": "gpt_sovits",
        "label": "Mio 本地原声音色",
        "kind": "script",
        "script": "install-gpt-sovits.ps1",
        "size_label": "音色包约 0.1 GB；需先安装 Genie 引擎",
        "what": "Mio 的角色音色、参考音频和情绪素材；只负责“声音是谁”，不包含推理引擎。",
        "missing_effect": "没有它就不能使用 Mio 本地原声音色，但 Genie 引擎和云端语音仍可用。",
        "how": "先完成 Genie 引擎，再点击这里下载或查找音色包；安装后会注册、预热并播放试听。",
    },
    {
        "id": "napcat",
        "label": "QQ 通道（NapCat）",
        "kind": "script",
        "script": "install-napcat.ps1",
        "size_label": "约 50 MB",
        "what": "连接 QQ 私聊和群聊的通道，需要电脑上已安装官方 QQ 并登录过。",
        "missing_effect": "没有它就不能在 QQ 里和 Mio 聊天，其他功能不受影响。",
        "how": "点击「一键安装」自动下载（优先国内通道）；下载失败时可打开官方文档页手动安装。",
        "manual_url": "https://napneko.pages.dev",
        "manual_label": "官方文档页（国内可访问）",
    },
    {
        "id": "ollama_vision",
        "label": "本地视觉（Ollama + Qwen2.5-VL）",
        "kind": "script",
        "script": "install-ollama-vision.ps1",
        "size_label": "运行器约 1.36 GiB，视觉模型约 3 GB",
        "what": "让 Mio 在你自己电脑上看懂屏幕画面，不占云端额度。",
        "missing_effect": "没有它，屏幕观察会优先使用云端视觉模型；不影响聊天。",
        "how": "点击「一键安装」；运行器支持断点续传和 SHA-256 校验，再自动拉取视觉模型。",
        "manual_url": "https://ollama.com/download/windows",
        "manual_label": "手动安装：Ollama 官网",
    },
    {
        "id": "whisper",
        "label": "系统声音理解（faster-whisper）",
        "kind": "script",
        "script": "install-whisper.ps1",
        "size_label": "约 1 GB",
        "what": "让 Mio 听懂电脑正在播放的声音（游戏、视频等）。",
        "missing_effect": "没有它，屏幕观察就只看画面、不听声音。",
        "how": "点击「一键安装」自动下载安装。",
        "manual_url": "https://github.com/SYSTRAN/faster-whisper",
        "manual_label": "手动安装：项目页",
    },
    {
        "id": "screen_capture",
        "label": "屏幕观察与截图",
        "kind": "builtin",
        "size_label": "已内置",
        "what": "观察屏幕画面的基础能力，随程序自带。",
        "missing_effect": "",
        "how": "无需安装，开箱即用。",
    },
)

_install_lock = threading.Lock()
_voice_finalize_lock = threading.Lock()
_running_installs: dict[str, int] = {}


@contextmanager
def _cross_process_install_lock(dep_id: str):
    """Serialize installer launches across multiple Mio backend processes on Windows."""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    name = f"Local\\MioAgent.DependencyInstaller.{dep_id}"
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise OSError("无法创建安装任务互斥锁。")
    acquired = False
    try:
        wait_result = kernel32.WaitForSingleObject(handle, 10_000)
        if wait_result not in {0x00000000, 0x00000080}:
            raise TimeoutError("另一个 Mio 实例正在准备安装，请稍后重试。")
        acquired = True
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _install_path(dep_id: str) -> str:
    paths = {
        "genie_runtime": settings.voice_training_dir,
        "gpt_sovits": settings.voice_training_dir,
        "napcat": settings.napcat_dir,
        "ollama_vision": settings.local_vision_dir,
        "whisper": settings.voice_training_dir / "cache" / "faster-whisper",
    }
    path = paths.get(str(dep_id))
    return str(path.resolve()) if path is not None else ""


def _progress_payload(progress: dict[str, Any], *, installing: bool) -> dict[str, Any]:
    return {
        "installing": installing,
        "stage": str(progress.get("stage") or ("running" if installing else "idle")),
        "percent": int(progress.get("percent") or 0),
        "message": str(progress.get("message") or ""),
        "file_name": str(progress.get("file_name") or ""),
        "downloaded_bytes": int(progress.get("downloaded_bytes") or 0),
        "total_bytes": int(progress.get("total_bytes") or 0),
        "download_percent": int(progress.get("download_percent") or 0),
        "target_path": str(progress.get("target_path") or ""),
        "speed_mb_s": float(progress.get("speed_mb_s") or 0),
    }


def _status_dir() -> Path:
    path = settings.data_dir / STATUS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _status_path(dep_id: str) -> Path:
    safe = "".join(ch for ch in str(dep_id) if ch.isalnum() or ch in "-_") or "unknown"
    return _status_dir() / f"{safe}.json"


def _read_status(dep_id: str) -> dict[str, Any]:
    path = _status_path(dep_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _detect_status(
    dep: dict[str, Any],
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dep_id = str(dep["id"])
    kind = str(dep.get("kind") or "builtin")
    status = {"id": dep_id, "status": "ready", "detail": ""}

    if dep_id in {"genie_runtime", "gpt_sovits"}:
        voice_root = settings.voice_training_dir
        genie_python = voice_root / ".genie-env" / "Scripts" / "python.exe"
        genie_data = voice_root / "GenieData"
        engine_ready = genie_python.is_file() and all(
            path.is_file() and path.stat().st_size > 0
            for path in (
                genie_data / "chinese-hubert-base" / "chinese-hubert-base.onnx",
                genie_data / "G2P" / "ChineseG2P" / "opencpop-strict.txt",
                genie_data / "G2P" / "ChineseG2P" / "polyphonic.pickle",
            )
        )
        if dep_id == "genie_runtime":
            status["status"] = "ready" if engine_ready else "missing"
            status["detail"] = "Genie ONNX CPU 引擎已就绪" if engine_ready else "尚未安装 Genie 运行引擎"
            return status
        voice_ready = engine_ready and all(
            path.is_file() and path.stat().st_size > 0
            for path in (
                voice_root / "models" / "genie" / "mio-v1" / "mio-genie-v2.json",
                *(
                    voice_root / "models" / "genie" / "mio-v1" / name
                    for name in genie_tts_service.REQUIRED_CHARACTER_FILES
                ),
                voice_root / "materials" / "prepared" / "wav32k_v2" / "mio_v2_00.wav",
                voice_root / "emotion-references.json",
            )
        )
        status["status"] = "ready" if voice_ready else "missing"
        status["detail"] = "Mio 音色已就绪" if voice_ready else ("请先安装 Genie 引擎" if not engine_ready else "尚未安装 Mio 音色包")
        return status

    if kind == "builtin":
        status["status"] = "ready"
        status["detail"] = dep.get("size_label") or "已内置"
        return status

    if kind == "configure":
        if dep_id == "cloud_model":
            profiles = list_model_profiles()
            status["status"] = "configured" if profiles else "unconfigured"
            status["detail"] = (
                f"已配置 {len(profiles)} 个模型" if profiles else "还没有配置聊天模型"
            )
        elif dep_id == "cloud_tts":
            config = companion_service.load_config()
            from .cloud_tts import cloud_tts_configured

            if cloud_tts_configured(config):
                status["status"] = "configured"
                status["detail"] = "已经填写语音 API Key"
            else:
                status["status"] = "unconfigured"
                status["detail"] = "还没有填写语音 API Key"
        return status

    environment = environment or environment_check_service.environment_status()
    optional = {item["id"]: item for item in environment.get("optional", [])}
    # 依赖中心的 id 与 environment_check 的可选项 id 不同，这里做映射。
    env_id_map = {
        "napcat": "qq",
        "ollama_vision": "local_vision",
        "whisper": "system_audio",
    }
    env_id = env_id_map.get(dep_id, dep_id)
    item = optional.get(env_id)
    if item is None:
        status["status"] = "unconfigured"
        status["detail"] = "暂未检测到"
        return status
    env_status = str(item.get("status") or "unconfigured")
    status["detail"] = str(item.get("detail") or "")
    if env_status in {"available", "configured"}:
        status["status"] = "ready"
    elif env_status == "missing":
        status["status"] = "missing"
    else:
        status["status"] = "unconfigured"
    return status


def list_dependencies() -> list[dict[str, Any]]:
    environment = environment_check_service.environment_status(refresh=True)
    result: list[dict[str, Any]] = []
    for dep in _DEPENDENCY_DEFS:
        entry = dict(dep)
        entry["install_path"] = _install_path(str(dep["id"]))
        detected = _detect_status(dep, environment)
        entry["status"] = detected["status"]
        entry["detail"] = detected["detail"]
        detected_ready = entry["status"] in {"ready", "configured"}
        entry["installing"] = False if detected_ready else _install_running(dep["id"])
        progress = _read_status(dep["id"])
        if entry["installing"]:
            entry["progress"] = _progress_payload(progress, installing=True)
        elif entry["status"] not in {"ready", "configured"} and progress.get("done") and progress.get("error"):
            entry["last_error"] = str(progress.get("error") or "")
        result.append(entry)
    return result


def _install_running(dep_id: str) -> bool:
    with _install_lock:
        pid = _running_installs.get(str(dep_id))
    if pid is None:
        try:
            pid = int(_read_status(dep_id).get("console_pid") or 0) or None
        except (TypeError, ValueError):
            pid = None
    if pid is None:
        return False
    if not _pid_alive(pid):
        # 进程已退出：清理脏 pid，避免 pid 复用导致的“永久安装中”。
        with _install_lock:
            _running_installs.pop(str(dep_id), None)
        return False
    return True


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except (AttributeError, OSError):
        return False


def _scripts_dir() -> Path:
    return settings.agent_control_scripts_dir / DEPS_SCRIPTS_DIR_NAME


def _write_status(dep_id: str, payload: dict[str, Any]) -> None:
    path = _status_path(dep_id)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _finalize_local_voice_install() -> dict[str, Any]:
    """Register the downloaded native voice, reload Genie, warm it, and audibly verify it."""
    from . import genie_tts_service

    runtime = genie_tts_service.runtime_status()
    if not runtime.get("ready"):
        raise OSError("Genie 运行环境未完整安装：" + "、".join(runtime.get("missing") or []))
    voice_root = settings.voice_training_dir
    model_manifest = voice_root / "models" / "genie" / "mio-v1" / "mio-genie-v2.json"
    reference = voice_root / "materials" / "prepared" / "wav32k_v2" / "mio_v2_00.wav"
    emotion_manifest = voice_root / "emotion-references.json"
    for required in (model_manifest, reference, emotion_manifest):
        if not required.is_file() or required.stat().st_size <= 0:
            raise OSError(f"Mio 原声音色缺少文件：{required}")

    config = companion_service.load_config()
    profiles = dict(config.get("voice_profiles") or {})
    profile = dict(profiles.get("mio") or {})
    profile.update({
        "name": "Mio 默认音色",
        "engine": "gpt_sovits",
        "gpt_sovits_ref_audio": str(reference.resolve()),
        "gpt_sovits_prompt_text": "つまらないものですが、いや、ありがとうございます。",
        "gpt_sovits_prompt_language": "ja",
        "gpt_sovits_text_language": "auto",
        "gpt_sovits_translate_to_japanese": False,
        "gpt_sovits_gpt_weights": "",
        "gpt_sovits_sovits_weights": "",
        "use_emotion_references": True,
    })
    profiles["mio"] = profile
    companion_service.save_config({
        "voice_enabled": True,
        "voice_engine": "gpt_sovits",
        "local_voice_runtime": "genie",
        "default_voice_profile_id": "mio",
        "voice_profiles": profiles,
    })
    genie_tts_service.stop_worker()
    genie_tts_service.start_worker()
    warmup = companion_service.warm_voice_runtime()
    if warmup.get("warmup_state") != "ready":
        raise OSError(str(warmup.get("warmup_error") or "Mio 原声音色预热失败。"))
    spoken = companion_service.speak_text(
        "你好，我是 Mio。本地原声音色已经安装好了。",
        context="环境与模型中心安装完成试听",
        wait=True,
        language="zh",
    )
    if not spoken:
        raise OSError("试听没有完成播放。")
    return {
        "registered_profile_id": "mio",
        "voice_engine": "gpt_sovits",
        "local_voice_runtime": "genie",
        "warmup_seconds": warmup.get("warmup_seconds"),
        "preview_played": True,
    }


def _ensure_install_finalized(dep_id: str) -> dict[str, Any]:
    status = _read_status(dep_id)
    if dep_id != "gpt_sovits" or not status.get("done") or status.get("error"):
        return status
    if status.get("finalized"):
        return status
    with _voice_finalize_lock:
        status = _read_status(dep_id)
        if status.get("finalized") or status.get("error"):
            return status
        status.update({
            "stage": "finalizing",
            "percent": 99,
            "message": "正在注册 Mio 默认音色、预热并播放试听…",
            # Keep the download phase terminal while the named lock serializes
            # activation. A status poll can wait for the same final result.
            "done": True,
        })
        _write_status(dep_id, status)
        try:
            verification = _finalize_local_voice_install()
        except Exception as exc:
            status.update({
                "stage": "error",
                "percent": 0,
                "message": f"模型文件已下载，但自动启用失败：{exc}",
                "error": f"模型文件已下载，但自动启用失败：{exc}",
                "done": True,
                "finalized": False,
            })
        else:
            status.update({
                "stage": "done",
                "percent": 100,
                "message": "Mio 本地原声音色已安装、切换、预热并完成试听",
                "error": "",
                "done": True,
                "finalized": True,
                "verified": True,
                "verification": verification,
            })
        _write_status(dep_id, status)
        environment_check_service.refresh_detection_cache()
        return status


def _watch_install(dep_id: str, process: subprocess.Popen[Any]) -> None:
    try:
        process.wait()
        _ensure_install_finalized(dep_id)
    finally:
        with _install_lock:
            if _running_installs.get(dep_id) == process.pid:
                _running_installs.pop(dep_id, None)


def _dependency_def(dep_id: str) -> dict[str, Any] | None:
    for dep in _DEPENDENCY_DEFS:
        if dep["id"] == dep_id:
            return dep
    return None


def install_dependency(dep_id: str) -> dict[str, Any]:
    dep = _dependency_def(dep_id)
    if dep is None:
        raise ValueError("不认识的依赖项目。")
    if str(dep.get("kind") or "") != "script":
        raise ValueError("这个项目不需要安装，请按界面引导操作。")
    script = _scripts_dir() / str(dep.get("script") or "")
    if not script.is_file():
        raise FileNotFoundError(f"找不到安装脚本：{script.name}")

    status_path = _status_path(dep_id)
    initial_status = {
        "id": dep_id,
        "stage": "starting",
        "percent": 0,
        "message": (
            "正在启动后台安装…"
            if dep_id in {"gpt_sovits"}
            else "正在启动安装窗口…"
        ),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "done": False,
        "error": "",
    }
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["MIO_WORKSPACE_ROOT"] = str(settings.workspace_root.resolve())
    env["MIO_DATA_DIR"] = str(settings.data_dir.resolve())
    env["MIO_PROGRAM_DIR"] = str(settings.agent_control_scripts_dir.parent.parent.resolve())
    env["MIO_VOICE_TRAINING_DIR"] = str(settings.voice_training_dir.resolve())
    env["MIO_LOCAL_VISION_DIR"] = str(settings.local_vision_dir.resolve())
    env["MIO_NAPCAT_DIR"] = str(settings.napcat_dir.resolve())
    env["MIO_STATUS_FILE"] = str(status_path.resolve())
    # 线程锁与命名 Mutex 同时保护“检查 -> 启动 -> 持久化 pid”，可覆盖多后端实例。
    with _cross_process_install_lock(dep_id), _install_lock:
        existing_pid = _running_installs.get(str(dep_id))
        if existing_pid is None:
            try:
                existing_pid = int(_read_status(dep_id).get("console_pid") or 0) or None
            except (TypeError, ValueError):
                existing_pid = None
        if existing_pid is not None and _pid_alive(existing_pid):
            raise ValueError("这个项目正在安装，请稍候。")
        status_path.write_text(
            json.dumps(initial_status, ensure_ascii=False),
            encoding="utf-8",
        )
        creation_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if dep_id in {"gpt_sovits", "genie_runtime"}
            else getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        )
        process = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=str(script.parent),
            env=env,
            creationflags=creation_flags,
        )
        _running_installs[dep_id] = process.pid
        status = _read_status(dep_id)
        status["console_pid"] = process.pid
        status_path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        threading.Thread(
            target=_watch_install,
            args=(dep_id, process),
            name=f"mio-dependency-{dep_id}",
            daemon=True,
        ).start()
    return {
        "id": dep_id,
        "installing": True,
        "install_path": _install_path(dep_id),
        "console_pid": process.pid,
        "stage": "starting",
        "percent": 0,
        "message": (
            "正在后台安装，下载与校验进度会持续显示在这里。"
            if dep_id in {"gpt_sovits", "genie_runtime"}
            else "安装窗口已打开，可以在窗口里看到下载进度。"
        ),
    }


def install_status(dep_id: str) -> dict[str, Any]:
    dep = _dependency_def(dep_id)
    if dep is None:
        raise ValueError("不认识的依赖项目。")
    progress = _read_status(dep_id)
    installing = _install_running(dep_id)
    if progress.get("done") and not installing and not progress.get("error"):
        progress = _ensure_install_finalized(dep_id)
    if progress.get("done") and not installing:
        environment_check_service.refresh_detection_cache()
    return {
        "id": dep_id,
        **_progress_payload(progress, installing=installing),
        "install_path": _install_path(dep_id),
        "done": bool(progress.get("done")),
        "error": str(progress.get("error") or ""),
        "started_at": str(progress.get("started_at") or ""),
        "finalized": bool(progress.get("finalized")),
        "verified": bool(progress.get("verified")),
        "verification": progress.get("verification") if isinstance(progress.get("verification"), dict) else {},
    }


__all__ = ["list_dependencies", "install_dependency", "install_status"]
