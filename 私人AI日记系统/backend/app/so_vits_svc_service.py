from __future__ import annotations

import atexit
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
from typing import Any

from .config import settings


logger = logging.getLogger(__name__)
_worker_lock = threading.RLock()
_worker: subprocess.Popen[str] | None = None
_responses: queue.Queue[dict[str, Any]] | None = None


def _runtime_paths() -> tuple[Path, Path, Path, Path, Path]:
    voice_root = settings.voice_training_dir
    configured = str(os.getenv("MIO_SO_VITS_SVC_DIR") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        voice_root / "so-vits-svc-ghfast",
        voice_root / "so-vits-svc",
    ]
    runtime_dir = next((path for path in candidates if path and (path / "inference" / "infer_tool.py").is_file()), voice_root / "so-vits-svc")
    python = voice_root / ".voice-env" / "Scripts" / "python.exe"
    dependency_dir = voice_root / "so-vits-svc-runtime"
    encoder = runtime_dir / "pretrain" / "checkpoint_best_legacy_500.pt"
    worker = settings.agent_control_scripts_dir / "so_vits_svc_worker.py"
    return python, runtime_dir, dependency_dir, encoder, worker


def runtime_status() -> dict[str, Any]:
    python, runtime_dir, dependency_dir, encoder, worker = _runtime_paths()
    missing = []
    if not python.is_file():
        missing.append("Python 语音环境")
    if not (runtime_dir / "inference" / "infer_tool.py").is_file():
        missing.append("So-VITS-SVC 4.1 运行代码")
    if not (dependency_dir / "fairseq").is_dir():
        missing.append("So-VITS-SVC 推理依赖")
    if not encoder.is_file() or encoder.stat().st_size < 100 * 1024 * 1024:
        missing.append("ContentVec 编码器")
    if not worker.is_file():
        missing.append("Mio 音色转换 Worker")
    return {
        "ready": not missing,
        "missing": missing,
        "runtime_dir": str(runtime_dir),
        "dependency_dir": str(dependency_dir),
        "encoder_path": str(encoder),
    }


def _read_stdout(process: subprocess.Popen[str], responses: queue.Queue[dict[str, Any]]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("So-VITS-SVC Worker 输出了非 JSON 内容：%s", line.rstrip()[:500])
            continue
        if isinstance(payload, dict):
            responses.put(payload)


def _read_stderr(process: subprocess.Popen[str]) -> None:
    assert process.stderr is not None
    for line in process.stderr:
        logger.info("So-VITS-SVC: %s", line.rstrip())


def _start_worker() -> tuple[subprocess.Popen[str], queue.Queue[dict[str, Any]]]:
    global _worker, _responses
    status = runtime_status()
    if not status["ready"]:
        raise OSError("第三方音色引擎未就绪：" + "、".join(status["missing"]))
    if _worker is not None and _worker.poll() is None and _responses is not None:
        return _worker, _responses
    python, runtime_dir, dependency_dir, _, worker_script = _runtime_paths()
    environment = os.environ.copy()
    environment.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "MIO_SO_VITS_SVC_DIR": str(runtime_dir),
        "MIO_SO_VITS_SVC_SITE": str(dependency_dir),
    })
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [str(python), "-u", str(worker_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(runtime_dir),
        env=environment,
        creationflags=creationflags,
    )
    responses: queue.Queue[dict[str, Any]] = queue.Queue()
    threading.Thread(target=_read_stdout, args=(process, responses), name="mio-svc-output", daemon=True).start()
    threading.Thread(target=_read_stderr, args=(process,), name="mio-svc-errors", daemon=True).start()
    _worker = process
    _responses = responses
    return process, responses


def _request(payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    global _worker, _responses
    with _worker_lock:
        process, responses = _start_worker()
        if process.stdin is None:
            raise OSError("第三方音色 Worker 输入通道不可用。")
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
            result = responses.get(timeout=timeout)
        except (BrokenPipeError, OSError, queue.Empty) as exc:
            stop_worker()
            if isinstance(exc, queue.Empty):
                raise TimeoutError("第三方音色转换超时。") from exc
            raise OSError("第三方音色 Worker 已断开。") from exc
        if not result.get("ok"):
            raise OSError(str(result.get("error") or "第三方音色转换失败。"))
        return result


def probe_profile(
    config: dict[str, Any],
    *,
    timeout: float = 60,
    device: str = "cuda",
) -> dict[str, Any]:
    return _request({
        "action": "probe",
        "model_path": str(config.get("so_vits_svc_model_path") or ""),
        "config_path": str(config.get("so_vits_svc_config_path") or ""),
        "speaker": str(config.get("so_vits_svc_speaker") or ""),
        "device": "cpu" if str(device).lower() == "cpu" else "cuda",
    }, timeout=timeout)


def convert_wav(content: bytes, config: dict[str, Any], *, timeout: float = 120) -> bytes:
    settings.companion_dir.mkdir(parents=True, exist_ok=True)
    input_fd, input_name = tempfile.mkstemp(prefix="mio-svc-input-", suffix=".wav", dir=settings.companion_dir)
    output_fd, output_name = tempfile.mkstemp(prefix="mio-svc-output-", suffix=".wav", dir=settings.companion_dir)
    os.close(input_fd)
    os.close(output_fd)
    input_path = Path(input_name)
    output_path = Path(output_name)
    try:
        input_path.write_bytes(content)
        _request({
            "action": "convert",
            "model_path": str(config.get("so_vits_svc_model_path") or ""),
            "config_path": str(config.get("so_vits_svc_config_path") or ""),
            "speaker": str(config.get("so_vits_svc_speaker") or ""),
            "pitch": int(config.get("so_vits_svc_pitch") or 0),
            "auto_predict_f0": bool(config.get("so_vits_svc_auto_predict_f0", True)),
            "noise_scale": float(config.get("so_vits_svc_noise_scale") or 0.4),
            "device": "cuda",
            "input_path": str(input_path),
            "output_path": str(output_path),
        }, timeout=timeout)
        converted = output_path.read_bytes()
        if len(converted) < 44 or not converted.startswith(b"RIFF"):
            raise OSError("第三方音色没有生成有效的 WAV。")
        return converted
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def stop_worker() -> None:
    global _worker, _responses
    with _worker_lock:
        process = _worker
        _worker = None
        _responses = None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write('{"action":"shutdown"}\n')
                process.stdin.flush()
            process.wait(timeout=5)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()


atexit.register(stop_worker)
