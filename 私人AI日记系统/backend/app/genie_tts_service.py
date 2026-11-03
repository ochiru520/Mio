from __future__ import annotations

import atexit
import base64
import functools
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterator
import uuid
import wave

from .config import settings


logger = logging.getLogger(__name__)
_worker_lock = threading.RLock()
_request_lock = threading.Lock()
_convert_lock = threading.Lock()
_worker: subprocess.Popen[str] | None = None
_responses: queue.Queue[dict[str, Any]] | None = None
_last_metrics: dict[str, Any] = {}
_last_error = ""
_worker_hot = False
_idle_timer: threading.Timer | None = None
_last_request_monotonic = 0.0
_idle_timeout_seconds = 180.0

# Genie keeps several ONNX sessions resident. Reclaim the worker after a
# short idle period so normal Mio use does not retain several GB of memory.
GENIE_IDLE_SECONDS = 180.0
PCM_SAMPLE_RATE = 32000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2

REQUIRED_CHARACTER_FILES = (
    "t2s_encoder_fp32.bin",
    "t2s_encoder_fp32.onnx",
    "t2s_first_stage_decoder_fp32.onnx",
    "t2s_shared_fp16.bin",
    "t2s_stage_decoder_fp32.onnx",
    "vits_fp16.bin",
    "vits_fp32.onnx",
)


def _paths() -> dict[str, Path]:
    root = settings.voice_training_dir
    return {
        "root": root,
        "python": root / ".genie-env" / "Scripts" / "python.exe",
        "site_packages": root / ".genie-env" / "Lib" / "site-packages",
        "data": root / "GenieData",
        "models": root / "models" / "genie",
        "mio_model": root / "models" / "genie" / "mio-v1",
        "default_model": root / "models" / "genie" / "default-v2",
        "worker": settings.agent_control_scripts_dir / "genie_tts_worker.py",
        "prepare": settings.agent_control_scripts_dir / "deps" / "prepare-genie-resources.py",
        "gpt_source": root / "GPT-SoVITS",
        "hubert_source": root / "cache" / "modelscope" / "AI-ModelScope--GPT-SoVITS" / "chinese-hubert-base",
    }


def _character_ready(path: Path) -> bool:
    try:
        return all((path / name).is_file() and (path / name).stat().st_size > 0 for name in REQUIRED_CHARACTER_FILES)
    except OSError:
        return False


def runtime_status() -> dict[str, Any]:
    paths = _paths()
    missing = []
    if not paths["python"].is_file():
        missing.append("Genie Python 环境")
    if not (paths["site_packages"] / "genie_tts" / "__init__.py").is_file():
        missing.append("genie-tts 2.0.2")
    if not (paths["site_packages"] / "jieba" / "__init__.py").is_file():
        missing.append("中文分词 jieba")
    if not (paths["data"] / "chinese-hubert-base" / "chinese-hubert-base.onnx").is_file():
        missing.append("中文语音编码器 ONNX")
    if not (paths["data"] / "G2P" / "ChineseG2P" / "opencpop-strict.txt").is_file():
        missing.append("中文发音字典")
    if not paths["worker"].is_file():
        missing.append("Mio Genie Worker")
    process_running = _worker is not None and _worker.poll() is None
    mio_model_ready = _character_ready(paths["mio_model"])
    return {
        "ready": not missing,
        "running": process_running,
        "hot": process_running and _worker_hot,
        "missing": missing,
        "runtime": "genie",
        "runtime_label": "Genie ONNX CPU",
        "runtime_dir": str(paths["python"].parent.parent),
        "genie_data": str(paths["data"]),
        "model_root": str(paths["models"]),
        "model_dir": str(paths["mio_model"]),
        "model_ready": mio_model_ready,
        "model_source": "Mio GPT-SoVITS V2 -> Genie ONNX",
        "last_metrics": dict(_last_metrics),
        "last_error": _last_error,
        "idle_timeout_seconds": _idle_timeout_seconds,
        "idle_seconds": (
            round(max(0.0, time.monotonic() - _last_request_monotonic), 1)
            if process_running and _last_request_monotonic
            else None
        ),
    }


def _read_stdout(process: subprocess.Popen[str], responses: queue.Queue[dict[str, Any]]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Genie Worker 输出了非 JSON 内容：%s", line.rstrip()[:500])
            continue
        if isinstance(payload, dict):
            # Bounded delivery applies pipe backpressure to long audio streams.
            # A cancelled worker must not leave its reader blocked on a full queue.
            while True:
                try:
                    responses.put(payload, timeout=0.1)
                    break
                except queue.Full:
                    if process.poll() is not None:
                        return


def _read_stderr(process: subprocess.Popen[str]) -> None:
    assert process.stderr is not None
    for line in process.stderr:
        logger.info("Genie TTS: %s", line.rstrip())


def _start_worker() -> tuple[subprocess.Popen[str], queue.Queue[dict[str, Any]]]:
    global _worker, _responses, _last_request_monotonic, _worker_hot
    from .local_voice_engines import manager as local_voice_manager
    local_voice_manager.require_original_selected()
    status = runtime_status()
    if not status["ready"]:
        raise OSError("Genie 本地音色未就绪：" + "、".join(status["missing"]))
    if _worker is not None and _worker.poll() is None and _responses is not None:
        _last_request_monotonic = time.monotonic()
        return _worker, _responses
    paths = _paths()
    environment = os.environ.copy()
    environment.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "MIO_GENIE_DATA_DIR": str(paths["data"]),
        # 中文和日语共用同一套 Mio 权重；只保留当前语言实例，避免两套
        # ONNX session 常驻把 16 GB 内存吃满。切换语言时按需重载。
        "Max_Cached_Character_Models": "1",
        "Max_Cached_Reference_Audio": "4",
    })
    process = subprocess.Popen(
        [str(paths["python"]), "-u", str(paths["worker"])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(paths["root"]),
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=16)
    threading.Thread(target=_read_stdout, args=(process, responses), name="mio-genie-output", daemon=True).start()
    threading.Thread(target=_read_stderr, args=(process,), name="mio-genie-errors", daemon=True).start()
    _worker = process
    _responses = responses
    _worker_hot = False
    _last_request_monotonic = time.monotonic()
    return process, responses


def _schedule_idle_shutdown(idle_seconds: float = GENIE_IDLE_SECONDS) -> None:
    global _idle_timer, _idle_timeout_seconds
    with _worker_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None
        _idle_timeout_seconds = max(0.0, float(idle_seconds))
        if _idle_timeout_seconds <= 0:
            return
        timer = threading.Timer(_idle_timeout_seconds, _stop_if_idle)
        timer.daemon = True
        _idle_timer = timer
        timer.start()


def _stop_if_idle() -> None:
    with _worker_lock:
        if _worker is None or _worker.poll() is not None:
            return
        if _idle_timeout_seconds <= 0:
            return
        if time.monotonic() - _last_request_monotonic < _idle_timeout_seconds - 1:
            _schedule_idle_shutdown(_idle_timeout_seconds)
            return
    logger.info("Genie TTS worker idle; releasing ONNX memory")
    stop_worker()


def _abort_worker(process: subprocess.Popen[str]) -> None:
    """Discard this process and its queue immediately after a cancelled request."""
    global _worker, _responses, _worker_hot, _idle_timer
    with _worker_lock:
        if _worker is process:
            _worker = None
            _responses = None
            _worker_hot = False
            if _idle_timer is not None:
                _idle_timer.cancel()
                _idle_timer = None
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def _request_events(
    payload: dict[str, Any],
    *,
    timeout: float,
    idle_seconds: float = GENIE_IDLE_SECONDS,
    cancel_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    global _last_error, _last_request_monotonic
    deadline = time.monotonic() + timeout
    process = None
    completed = False
    acquired = False
    try:
        while not acquired:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Genie 语音合成已取消。")
            if time.monotonic() >= deadline:
                raise TimeoutError("Genie 合成排队超时。")
            acquired = _request_lock.acquire(timeout=0.1)
        request_id = uuid.uuid4().hex
        from .local_voice_engines import manager as local_voice_manager
        with local_voice_manager.lifecycle_lock, _worker_lock:
            _last_request_monotonic = time.monotonic()
            process, responses = _start_worker()
            if _idle_timer is not None:
                _idle_timer.cancel()
            if process.stdin is None:
                raise OSError("Genie Worker 输入通道不可用。")
            process.stdin.write(json.dumps({**payload, "request_id": request_id}, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Genie 语音合成已取消。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Genie 合成超时。")
            try:
                result = responses.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    raise OSError("Genie Worker 已断开。")
                continue
            if result.get("fatal"):
                raise OSError(str(result.get("error") or "Genie Worker 加载失败。"))
            if result.get("request_id") != request_id:
                # Every protocol reply is scoped to one request. Never reuse an
                # old audio chunk or completion as the next request's response.
                continue
            if not result.get("ok"):
                raise OSError(str(result.get("error") or "Genie 合成失败。"))
            terminal = result.get("event") in {None, "done"}
            if terminal:
                completed = True
                _last_error = ""
                _last_request_monotonic = time.monotonic()
                _schedule_idle_shutdown(idle_seconds)
            yield result
            if terminal:
                return
    except (BrokenPipeError, OSError, TimeoutError) as exc:
        _last_error = str(exc)
        raise
    finally:
        if process is not None and not completed:
            _abort_worker(process)
        if acquired:
            _request_lock.release()


def _request(
    payload: dict[str, Any],
    *,
    timeout: float,
    idle_seconds: float = GENIE_IDLE_SECONDS,
) -> dict[str, Any]:
    events = _request_events(payload, timeout=timeout, idle_seconds=idle_seconds)
    try:
        for result in events:
            return result
        raise OSError("Genie Worker 没有返回结果。")
    finally:
        events.close()


def start_worker() -> dict[str, Any]:
    _request({"action": "probe"}, timeout=30)
    return runtime_status()


@functools.lru_cache(maxsize=64)
def _file_digest(path_text: str, size: int, modified_ns: int) -> str:
    del size, modified_ns
    digest = hashlib.sha256()
    with Path(path_text).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(path: Path) -> str:
    stat = path.stat()
    return _file_digest(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def _converter_python(paths: dict[str, Path]) -> tuple[Path, dict[str, str]]:
    genie_python = paths["python"]
    if (paths["site_packages"] / "torch" / "__init__.py").is_file():
        return genie_python, {}
    legacy = paths["root"] / ".voice-env" / "Scripts" / "python.exe"
    legacy_site = paths["root"] / ".voice-env" / "Lib" / "site-packages"
    if legacy.is_file() and (legacy_site / "torch" / "__init__.py").is_file():
        return legacy, {"PYTHONPATH": str(paths["site_packages"])}
    raise OSError("音色需要首次转换，但没有找到带 PyTorch 的转换环境；请在环境与模型中心重新安装本地音色。")


def _resolve_character_model(config: dict[str, Any]) -> Path:
    from .voice_asset_service import genie_model_override
    pinned = genie_model_override(config)
    if pinned is not None:
        return pinned
    paths = _paths()
    gpt = Path(str(config.get("gpt_sovits_gpt_weights") or "")).expanduser()
    sovits = Path(str(config.get("gpt_sovits_sovits_weights") or "")).expanduser()
    if not gpt.is_file() or not sovits.is_file():
        if _character_ready(paths["mio_model"]):
            return paths["mio_model"]
        if _character_ready(paths["default_model"]):
            return paths["default_model"]
        raise FileNotFoundError("当前音色缺少 GPT/SoVITS V2 权重，且没有可用的默认 Genie 音色。")
    gpt_digest = _digest(gpt)
    sovits_digest = _digest(sovits)
    for marker in paths["models"].glob("**/mio-genie-v2.json"):
        try:
            manifest = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if manifest.get("gpt_sha256") == gpt_digest and manifest.get("sovits_sha256") == sovits_digest:
            if _character_ready(marker.parent):
                return marker.parent
    key = hashlib.sha256(f"{gpt_digest}:{sovits_digest}".encode("ascii")).hexdigest()[:24]
    target = paths["models"] / "voices" / key
    if _character_ready(target):
        return target
    with _convert_lock:
        if _character_ready(target):
            return target
        python, extra_environment = _converter_python(paths)
        hubert_source = paths["hubert_source"]
        if not hubert_source.is_dir():
            hubert_source = paths["gpt_source"] / "GPT_SoVITS" / "pretrained_models" / "chinese-hubert-base"
        command = [
            str(python), str(paths["prepare"]),
            "--genie-data", str(paths["data"]),
            "--hubert-source", str(hubert_source),
            "--gpt-weights", str(gpt.resolve()),
            "--sovits-weights", str(sovits.resolve()),
            "--character-output", str(target),
        ]
        if paths["gpt_source"].is_dir():
            command.extend(["--gpt-source", str(paths["gpt_source"])])
        environment = os.environ.copy()
        environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **extra_environment})
        completed = subprocess.run(
            command,
            cwd=str(paths["root"]),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not _character_ready(target):
            detail = (completed.stderr or completed.stdout or "转换进程没有返回详情").strip()[-1200:]
            raise OSError(f"GPT-SoVITS V2 转换 Genie ONNX 失败：{detail}")
    return target


def _validate_wav(content: bytes) -> None:
    if len(content) < 44 or not content.startswith(b"RIFF") or content[8:12] != b"WAVE":
        raise OSError("Genie 没有生成标准 WAV。")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as stream:
        stream.write(content)
        name = stream.name
    try:
        with wave.open(name, "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 32000:
                raise OSError("Genie WAV 不是 32 kHz / 16-bit / 单声道。")
            if wav.getnframes() <= 0:
                raise OSError("Genie WAV 没有音频帧。")
    finally:
        Path(name).unlink(missing_ok=True)


def synthesize_wav(text: str, config: dict[str, Any], *, timeout: float = 120) -> bytes:
    global _last_metrics, _worker_hot
    model_dir = _resolve_character_model(config)
    reference = Path(str(config.get("gpt_sovits_ref_audio") or "")).expanduser()
    prompt_text = str(config.get("gpt_sovits_prompt_text") or "").strip()
    if not reference.is_file():
        raise FileNotFoundError(f"参考音频不存在：{reference}")
    if not prompt_text:
        raise ValueError("参考音频准确原文不能为空。")
    settings.companion_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="mio-genie-", suffix=".wav", dir=settings.companion_dir)
    os.close(fd)
    output = Path(name)
    try:
        result = _request({
            "action": "synthesize",
            "model_dir": str(model_dir),
            "reference_audio": str(reference.resolve()),
            "prompt_text": prompt_text,
            "prompt_language": str(config.get("gpt_sovits_prompt_language") or "zh"),
            "text_language": str(config.get("gpt_sovits_text_language") or "zh"),
            "text": text,
            "output_path": str(output),
        }, timeout=timeout, idle_seconds=float(config.get("voice_idle_timeout_seconds", 180)))
        content = output.read_bytes()
        _validate_wav(content)
        _last_metrics = {
            "model_dir": str(model_dir),
            "first_audio_ms": result.get("first_audio_ms"),
            "total_ms": result.get("total_ms"),
            "duration_seconds": result.get("duration_seconds"),
        }
        _worker_hot = True
        return content
    finally:
        output.unlink(missing_ok=True)


def iter_speech_pcm(
    text: str,
    config: dict[str, Any],
    *,
    timeout: float = 120,
    cancel_event: threading.Event | None = None,
) -> Iterator[bytes]:
    """Yield generated mono 32kHz PCM16 chunks without waiting for a WAV file.

    Closing this generator before completion kills only its worker, which also
    discards any queued output from the cancelled request. Callers that may
    cancel while waiting for the first chunk should also set ``cancel_event``.
    ``first_pcm_received_ms`` measures the backend receipt, never speaker output.
    """
    global _last_metrics, _worker_hot
    model_dir = _resolve_character_model(config)
    reference = Path(str(config.get("gpt_sovits_ref_audio") or "")).expanduser()
    prompt_text = str(config.get("gpt_sovits_prompt_text") or "").strip()
    if not reference.is_file():
        raise FileNotFoundError(f"参考音频不存在：{reference}")
    if not prompt_text:
        raise ValueError("参考音频准确原文不能为空。")
    started = time.monotonic()
    first_pcm_ms: float | None = None
    received_bytes = 0
    metadata_received = False
    events = _request_events({
        "action": "stream",
        "model_dir": str(model_dir),
        "reference_audio": str(reference.resolve()),
        "prompt_text": prompt_text,
        "prompt_language": str(config.get("gpt_sovits_prompt_language") or "zh"),
        "text_language": str(config.get("gpt_sovits_text_language") or "zh"),
        "text": text,
    }, timeout=timeout, idle_seconds=float(config.get("voice_idle_timeout_seconds", 180)), cancel_event=cancel_event)
    try:
        for event in events:
            kind = event.get("event")
            if kind == "start":
                if metadata_received or (event.get("sample_rate"), event.get("channels"), event.get("sample_width")) != (PCM_SAMPLE_RATE, PCM_CHANNELS, PCM_SAMPLE_WIDTH):
                    raise OSError("Genie PCM 流不是 32 kHz / 16-bit / 单声道。")
                metadata_received = True
            elif kind == "audio":
                if not metadata_received:
                    raise OSError("Genie PCM 流缺少音频格式信息。")
                try:
                    pcm = base64.b64decode(str(event.get("pcm_base64") or ""), validate=True)
                except ValueError as exc:
                    raise OSError("Genie PCM 数据编码无效。") from exc
                if not pcm or len(pcm) % PCM_SAMPLE_WIDTH:
                    raise OSError("Genie PCM 流包含无效的音频帧。")
                if first_pcm_ms is None:
                    first_pcm_ms = (time.monotonic() - started) * 1000
                received_bytes += len(pcm)
                _last_metrics = {
                    "model_dir": str(model_dir),
                    "streaming": True,
                    "first_pcm_received_ms": round(first_pcm_ms, 1),
                    "received_bytes": received_bytes,
                    "complete": False,
                }
                yield pcm
            elif kind == "done":
                if not metadata_received or received_bytes <= 0:
                    raise OSError("Genie 没有生成可播放的 PCM 音频。")
                if event.get("frames") != received_bytes // PCM_SAMPLE_WIDTH:
                    raise OSError("Genie PCM 流的音频帧数量不完整。")
                _worker_hot = True
                _last_metrics = {
                    "model_dir": str(model_dir),
                    "streaming": True,
                    "first_audio_ms": event.get("first_audio_ms"),
                    "first_pcm_received_ms": round(first_pcm_ms, 1),
                    "total_ms": event.get("total_ms"),
                    "duration_seconds": received_bytes / (PCM_SAMPLE_RATE * PCM_SAMPLE_WIDTH),
                    "received_bytes": received_bytes,
                    "complete": True,
                }
            else:
                raise OSError("Genie PCM 流收到未知事件。")
    finally:
        events.close()


def stop_worker() -> None:
    global _worker, _responses, _idle_timer, _worker_hot
    with _worker_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None
        process = _worker
        _worker = None
        _responses = None
        _worker_hot = False
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write('{"action":"shutdown"}\n')
                process.stdin.flush()
            process.wait(timeout=8)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()


atexit.register(stop_worker)
