from __future__ import annotations

import atexit
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
from typing import Any
import wave

from .config import settings


logger = logging.getLogger(__name__)
_worker_lock = threading.RLock()
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
            responses.put(payload)


def _read_stderr(process: subprocess.Popen[str]) -> None:
    assert process.stderr is not None
    for line in process.stderr:
        logger.info("Genie TTS: %s", line.rstrip())


def _start_worker() -> tuple[subprocess.Popen[str], queue.Queue[dict[str, Any]]]:
    global _worker, _responses, _last_request_monotonic, _worker_hot
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
    responses: queue.Queue[dict[str, Any]] = queue.Queue()
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


def _request(
    payload: dict[str, Any],
    *,
    timeout: float,
    idle_seconds: float = GENIE_IDLE_SECONDS,
) -> dict[str, Any]:
    global _last_error, _last_request_monotonic
    with _worker_lock:
        _last_request_monotonic = time.monotonic()
        process, responses = _start_worker()
        if process.stdin is None:
            raise OSError("Genie Worker 输入通道不可用。")
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()
            result = responses.get(timeout=timeout)
        except (BrokenPipeError, OSError, queue.Empty) as exc:
            stop_worker()
            if isinstance(exc, queue.Empty):
                _last_error = "Genie 合成超时。"
                raise TimeoutError(_last_error) from exc
            _last_error = "Genie Worker 已断开。"
            raise OSError(_last_error) from exc
        if not result.get("ok"):
            _last_error = str(result.get("error") or "Genie 合成失败。")
            raise OSError(_last_error)
        _last_error = ""
        _last_request_monotonic = time.monotonic()
        _schedule_idle_shutdown(idle_seconds)
        return result


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
