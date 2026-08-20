from __future__ import annotations

from collections import deque
from datetime import datetime
import atexit
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any
import uuid

from . import environment_check_service
from .config import settings


_lock = threading.RLock()
_process: subprocess.Popen[str] | None = None
_reader_thread: threading.Thread | None = None
_transcripts: deque[dict[str, Any]] = deque(maxlen=12)
_ready = False
_last_error = ""
_last_event_at = ""
_model = ""
_requested_model = ""
_model_fallback_reason = ""
_engine = "faster-whisper"
_device = ""
_compute_type = ""
_speaker = ""
_phase = "stopped"
_desired_running = False
_worker_engine = ""
_quality_requests: dict[str, dict[str, Any]] = {}
_quality_requested = 0
_quality_completed = 0
_quality_timeouts = 0
_quality_errors = 0
_quality_last_error = ""
_quality_last_latency_ms = 0.0
_capture_stats: dict[str, Any] = {
    "captured_blocks": 0,
    "queued_windows": 0,
    "dropped_windows": 0,
    "completed_windows": 0,
}
_capture_config: dict[str, float] = {}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalized_transcript(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+", "", text).lower()


def _runtime_paths(engine: str = "whisper") -> tuple[Path, Path, Path]:
    root = settings.voice_training_dir
    if _canonical_engine(engine) == "whisper":
        detected = environment_check_service.find_whisper_runtime()
        if detected is not None:
            root = Path(detected["root"])
    python = root / ".voice-env" / "Scripts" / "python.exe"
    worker = settings.agent_control_scripts_dir / "system_audio_worker.py"
    cache = root / "cache" / "faster-whisper"
    return python, worker, cache


def _canonical_engine(value: object) -> str:
    engine = str(value or "whisper").strip().lower()
    if engine == "auto":
        return "whisper"
    return engine if engine in {"whisper", "sensevoice", "paraformer"} else "whisper"


def _local_whisper_model_path(cache: Path, model: str) -> Path | None:
    normalized = str(model or "base").strip().replace("/", "--")
    repositories = [
        cache / f"models--Systran--faster-whisper-{normalized}",
        cache / f"models--{normalized}",
    ]
    for repository in repositories:
        snapshots = repository / "snapshots"
        if not snapshots.is_dir():
            continue
        candidates = sorted(
            (
                path for path in snapshots.iterdir()
                if path.is_dir() and (path / "model.bin").is_file() and (path / "config.json").is_file()
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if candidates:
            return candidates[0]
    bundled = (
        settings.voice_training_dir
        / "GPT-SoVITS"
        / "tools"
        / "asr"
        / "models"
        / f"faster-whisper-{normalized}"
    )
    if (bundled / "model.bin").is_file() and (bundled / "config.json").is_file():
        return bundled
    return None


def _resolve_local_whisper_model(cache: Path, requested_model: str) -> tuple[Path | None, str, str]:
    requested = str(requested_model or "base").strip() or "base"
    preferred = [requested, "large-v3-turbo", "small", "base", "tiny"]
    seen: set[str] = set()
    for model in preferred:
        normalized = model.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        local_model = _local_whisper_model_path(cache, model)
        if local_model is None:
            continue
        fallback_reason = ""
        if model != requested:
            fallback_reason = f"请求的 {requested} 未安装，已使用本机完整的 {model} 模型。"
        return local_model, model, fallback_reason
    return None, requested, ""


def _local_modelscope_model_path(
    repository: str,
    *,
    required_files: tuple[str, ...] = ("config.yaml", "model.pt"),
) -> Path | None:
    snapshots = (
        settings.voice_training_dir
        / "cache"
        / "modelscope"
        / "models"
        / repository
        / "snapshots"
    )
    if not snapshots.is_dir():
        return None
    candidates = sorted(
        (
            path for path in snapshots.iterdir()
            if path.is_dir() and all((path / name).is_file() for name in required_files)
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _worker_launch(config: dict[str, Any], whisper_cache: Path) -> dict[str, str] | None:
    engine = _canonical_engine(config.get("asr_engine"))
    if engine == "whisper":
        requested_model = str(config.get("asr_model") or config.get("screen_audio_model") or "base")
        local_model, model_label, fallback_reason = _resolve_local_whisper_model(
            whisper_cache,
            requested_model,
        )
        return {
            "engine": engine,
            "model": str(local_model) if local_model is not None else model_label,
            "model_label": model_label,
            "requested_model_label": requested_model,
            "fallback_reason": fallback_reason,
            "vad_model": "",
        }

    vad_model = _local_modelscope_model_path("iic--speech_fsmn_vad_zh-cn-16k-common-pytorch")
    if engine == "sensevoice":
        model = _local_modelscope_model_path(
            "iic--SenseVoiceSmall",
            required_files=("config.yaml", "model.pt", "chn_jpn_yue_eng_ko_spectok.bpe.model"),
        )
        model_label = "SenseVoiceSmall"
    else:
        model = _local_modelscope_model_path(
            "iic--speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        )
        model_label = "Paraformer-large-zh"
    if model is None or vad_model is None:
        return None
    return {
        "engine": engine,
        "model": str(model),
        "model_label": model_label,
        "vad_model": str(vad_model),
    }


def _reader() -> None:
    global _ready, _last_error, _last_event_at, _model, _engine, _device, _compute_type, _speaker, _phase
    global _capture_stats, _capture_config
    process = _process
    if process is None or process.stdout is None:
        return
    try:
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = str(payload.get("type") or "")
            with _lock:
                _last_event_at = _now_iso()
                if event_type == "ready":
                    _ready = True
                    _phase = "ready"
                    _last_error = ""
                    _model = str(payload.get("model") or "")
                    _engine = str(payload.get("engine") or "faster-whisper")
                    _device = str(payload.get("device") or "")
                    _compute_type = str(payload.get("compute_type") or "")
                    _speaker = str(payload.get("speaker") or "")
                    _capture_config = {
                        "capture_block_seconds": float(payload.get("capture_block_seconds") or 0),
                        "analysis_window_seconds": float(payload.get("analysis_window_seconds") or 0),
                        "analysis_hop_seconds": float(payload.get("analysis_hop_seconds") or 0),
                    }
                elif event_type == "transcript":
                    text = " ".join(str(payload.get("text") or "").split()).strip()
                    if text:
                        transcript = {
                            "text": text[:1000],
                            "language": str(payload.get("language") or ""),
                            "probability": float(payload.get("probability") or 0),
                            "latency_seconds": float(payload.get("latency_seconds") or 0),
                            "received_monotonic": time.monotonic(),
                            "received_at": _last_event_at,
                        }
                        previous = _transcripts[-1] if _transcripts else None
                        previous_text = str(previous.get("text") or "") if previous else ""
                        previous_normalized = _normalized_transcript(previous_text)
                        current_normalized = _normalized_transcript(text)
                        extends_recent = bool(
                            previous
                            and time.monotonic() - float(previous.get("received_monotonic") or 0) < 10
                            and previous_normalized
                            and previous_normalized in current_normalized
                        )
                        if extends_recent:
                            _transcripts[-1] = transcript
                        elif not previous or previous_normalized != current_normalized:
                            _transcripts.append(transcript)
                elif event_type == "quality_transcript":
                    request_id = str(payload.get("request_id") or "")
                    request = _quality_requests.get(request_id)
                    if request is not None:
                        error = str(payload.get("error") or "")[:300]
                        request["result"] = {
                            "text": " ".join(str(payload.get("text") or "").split()).strip()[:1000],
                            "language": str(payload.get("language") or ""),
                            "probability": float(payload.get("probability") or 0),
                            "accepted": bool(payload.get("accepted", True)),
                            "rejection_reason": str(payload.get("rejection_reason") or "")[:100],
                            "audio": dict(payload.get("audio") or {}),
                            "asr": dict(payload.get("asr") or {}),
                            "engine": str(payload.get("engine") or ""),
                            "model": str(payload.get("model") or ""),
                            "device": str(payload.get("device") or ""),
                            "compute_type": str(payload.get("compute_type") or ""),
                            "requested_language": str(payload.get("requested_language") or ""),
                            "error": error,
                        }
                        request["event"].set()
                elif event_type == "capture_stats":
                    _capture_stats = {
                        key: max(0, int(payload.get(key) or 0))
                        for key in _capture_stats
                    }
                elif event_type == "background_error":
                    _last_error = str(payload.get("message") or "后台系统声音转写失败")[:500]
                elif event_type in {"loading_runtime", "loading_model", "speaker_detected", "microphone_detected"}:
                    _phase = event_type
                elif event_type == "error":
                    _phase = "error"
                    _last_error = str(payload.get("message") or "系统声音转写失败")[:500]
    finally:
        with _lock:
            # A model switch can replace the worker before the old reader
            # thread unwinds. Only the reader that still owns the active
            # process may change its readiness state.
            if _process is process:
                _ready = False
                if _phase != "error":
                    _phase = "stopped"


def start(config: dict[str, Any]) -> dict[str, Any]:
    global _process, _reader_thread, _ready, _last_error, _model, _requested_model
    global _model_fallback_reason, _speaker, _phase, _desired_running
    global _capture_stats, _capture_config, _worker_engine
    if not bool(config.get("screen_audio_enabled", True)):
        stop()
        return status()
    requested_engine = _canonical_engine(config.get("asr_engine"))
    python, worker, cache = _runtime_paths(requested_engine)
    launch = _worker_launch(config, cache)
    if launch is None:
        with _lock:
            _last_error = (
                "缺少 SenseVoice/Paraformer 或 FSMN-VAD 的完整本地模型，"
                "请先完成模型安装后再选择该电话识别引擎。"
            )
            _phase = "error"
        return status()
    requested_model = launch["model_label"]
    with _lock:
        _desired_running = True
        restart_for_identity = (
            _process is not None
            and _process.poll() is None
            and (
                (_worker_engine and _worker_engine != requested_engine)
                or (_model and _model != requested_model)
            )
        )
        if _process is not None and _process.poll() is None and not restart_for_identity:
            return status()
    if restart_for_identity:
        stop()
        with _lock:
            _desired_running = True
    with _lock:
        missing = [str(path) for path in (python, worker) if not path.is_file()]
        if missing:
            _last_error = f"缺少系统声音转写运行文件：{missing[0]}"
            return status()
        cache.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["HF_HOME"] = str(cache)
        env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        env["HF_ENDPOINT"] = (
            str(os.getenv("MIO_HF_ENDPOINT") or os.getenv("HF_ENDPOINT") or "").strip()
            or "https://hf-mirror.com"
        )
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            _process = subprocess.Popen(
                [
                    str(python),
                    str(worker),
                    "--engine",
                    requested_engine,
                    "--model",
                    launch["model"],
                    "--model-label",
                    requested_model,
                    "--vad-model",
                    launch["vad_model"],
                    "--language",
                    str(config.get("screen_audio_language") or "auto"),
                    "--chunk-seconds",
                    str(int(config.get("screen_audio_chunk_seconds") or 6)),
                    "--cache-dir",
                    str(cache),
                ],
                cwd=str(settings.voice_training_dir),
                env=env,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            _process = None
            _last_error = str(exc)[:500]
            return status()
        _ready = False
        _phase = "starting"
        _last_error = ""
        _model = requested_model
        _requested_model = str(launch.get("requested_model_label") or requested_model)
        _model_fallback_reason = str(launch.get("fallback_reason") or "")[:300]
        _worker_engine = requested_engine
        _speaker = ""
        _capture_stats = {
            "captured_blocks": 0,
            "queued_windows": 0,
            "dropped_windows": 0,
            "completed_windows": 0,
        }
        _capture_config = {}
        _reader_thread = threading.Thread(target=_reader, name="mio-system-audio-reader", daemon=True)
        _reader_thread.start()
    return status()


def stop() -> dict[str, Any]:
    global _process, _ready, _phase, _desired_running, _worker_engine
    with _lock:
        _desired_running = False
        process = _process
        _process = None
        _ready = False
        _phase = "stopped"
        _worker_engine = ""
        _transcripts.clear()
        pending_requests = list(_quality_requests.values())
        _quality_requests.clear()
        for request in pending_requests:
            request["result"] = {"text": "", "error": "系统声音服务已停止"}
            request["event"].set()
    if process is not None and process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
    return status()


def recent_transcript(*, max_age_seconds: float = 24.0) -> str:
    now = time.monotonic()
    with _lock:
        rows = [row for row in _transcripts if now - float(row["received_monotonic"]) <= max_age_seconds]
    return " / ".join(str(row["text"]) for row in rows[-3:])[:1800]


def transcribe_wav_for_quality(
    wav_content: bytes,
    *,
    language: str = "auto",
    purpose: str = "quality",
    timeout_seconds: float = 18.0,
) -> dict[str, Any] | None:
    """Use the already-loaded local ASR worker without persisting the WAV."""
    global _quality_requested, _quality_completed, _quality_timeouts, _quality_errors
    global _quality_last_error, _quality_last_latency_ms
    if not wav_content:
        return None
    request_id = uuid.uuid4().hex
    event = threading.Event()
    request: dict[str, Any] = {
        "event": event,
        "result": None,
        "started_monotonic": time.monotonic(),
    }
    with _lock:
        process = _process
        if not _ready or process is None or process.poll() is not None or process.stdin is None:
            return None
        _quality_requests[request_id] = request
        _quality_requested += 1
        payload = json.dumps(
            {
                "type": "transcribe_wav",
                "request_id": request_id,
                "language": str(language or "auto"),
                "purpose": str(purpose or "quality"),
                "wav_base64": base64.b64encode(wav_content).decode("ascii"),
            },
            ensure_ascii=False,
        )
        try:
            process.stdin.write(payload + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            _quality_requests.pop(request_id, None)
            return None

    completed = event.wait(timeout=max(0.1, float(timeout_seconds)))
    with _lock:
        saved = _quality_requests.pop(request_id, None)
    if not completed or saved is None:
        if not completed:
            with _lock:
                _quality_timeouts += 1
                _quality_last_error = f"WAV 转写在 {float(timeout_seconds):.1f} 秒内没有完成"
        return None
    result = saved.get("result")
    if not isinstance(result, dict):
        return None
    error = str(result.get("error") or "")[:300]
    with _lock:
        _quality_completed += 1
        _quality_last_latency_ms = round(
            max(0.0, time.monotonic() - float(saved["started_monotonic"])) * 1000,
            2,
        )
        if error:
            _quality_errors += 1
            _quality_last_error = error
        else:
            _quality_last_error = ""
    return dict(result)


def chat_context() -> str:
    current = status()
    if current["last_error"]:
        return (
            "【系统声音能力】当前系统声音转写发生错误，暂时听不到电脑播放的内容。"
            f"错误：{current['last_error']}"
        )
    if not current["running"]:
        return (
            "【系统声音能力】当前没有启动系统声音监听。只有桌宠的屏幕或窗口观察正在运行时，"
            "才会在本地监听扬声器声音；此刻不要声称自己能听到。"
        )
    if not current["ready"]:
        return "【系统声音能力】系统声音监听正在加载，还没有准备好；此刻只能说正在加载。"
    transcript = recent_transcript(max_age_seconds=90)
    if transcript:
        return (
            "【系统声音能力】当前正在本地监听电脑扬声器，能够听到并转写较清晰的台词或语音。"
            f"最近听到：{transcript}。不要把视频或游戏台词误认为用户对你说的话。"
        )
    return (
        "【系统声音能力】当前正在本地监听电脑扬声器，但最近没有识别到清晰台词。"
        "这表示正在听但暂时没有可用转写，不要回答成完全没有听觉能力。"
    )


def status() -> dict[str, Any]:
    with _lock:
        running = _process is not None and _process.poll() is None
        latest = dict(_transcripts[-1]) if _transcripts else {}
        latest.pop("received_monotonic", None)
        return {
            "enabled": running,
            "desired_running": _desired_running,
            "running": running,
            "ready": _ready,
            "phase": _phase,
            "engine": _engine,
            "requested_engine": _worker_engine,
            "model": _model,
            "requested_model": _requested_model,
            "model_fallback_reason": _model_fallback_reason,
            "device": _device,
            "compute_type": _compute_type,
            "speaker": _speaker,
            "last_error": _last_error,
            "last_event_at": _last_event_at,
            "latest_transcript": latest,
            "audio_persisted": False,
            "quality_requests": {
                "requested": _quality_requested,
                "completed": _quality_completed,
                "timeouts": _quality_timeouts,
                "errors": _quality_errors,
                "pending": len(_quality_requests),
                "last_error": _quality_last_error,
                "last_latency_ms": _quality_last_latency_ms,
            },
            "capture": {
                **_capture_config,
                **_capture_stats,
            },
        }


atexit.register(stop)
