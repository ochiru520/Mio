"""Client for Mio's optional IndexTTS2 adapter (not an official REST API)."""
from __future__ import annotations

import base64
import binascii
import json
from io import BytesIO
from pathlib import Path
import uuid
import wave
from urllib.parse import urlsplit

import httpx

from . import tts_http_audio as audio

PROTOCOL = "mio-indextts2-v1"
DEFAULT_URL = "http://127.0.0.1:11996"
MAX_REFERENCE_BYTES = 40 * 1024 * 1024
MAX_AUDIO_BYTES = 64 * 1024 * 1024
REFERENCE_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"}


def service_url(config: dict) -> str:
    url = str(config.get("indextts2_url") or DEFAULT_URL).strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("IndexTTS2 服务地址需为 HTTP(S) 地址，不能包含账号、查询参数或片段。")
    return url


def validate_wav(content: bytes) -> bytes:
    try:
        if len(content) > MAX_AUDIO_BYTES:
            raise ValueError("WAV too large")
        with wave.open(BytesIO(content), "rb") as audio:
            expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
            if audio.getcomptype() != "NONE" or not 0 < expected <= MAX_AUDIO_BYTES or len(audio.readframes(audio.getnframes())) != expected:
                raise ValueError("empty, truncated or compressed WAV")
    except (wave.Error, EOFError, ValueError) as exc:
        raise OSError("IndexTTS2 没有返回可播放的 PCM WAV 音频；请检查适配服务版本与模型输出。") from exc
    return content


def synthesize_wav(text: str, config: dict, *, language: str = "", cancel_event=None) -> bytes:
    audio.check_cancel(cancel_event)
    reference = Path(str(config.get("indextts2_ref_audio") or "")).expanduser()
    if not str(config.get("indextts2_ref_audio") or "").strip() or not reference.is_file():
        raise ValueError("请先为 IndexTTS2 上传参考音频，或填写 Mio 所在电脑上存在的音频完整路径。")
    size = reference.stat().st_size
    if reference.suffix.lower() not in REFERENCE_EXTENSIONS or not 0 < size <= MAX_REFERENCE_BYTES:
        raise ValueError("IndexTTS2 参考音频需为 40 MB 以内的 WAV、MP3、FLAC 等音频。")
    payload = {"protocol": PROTOCOL, "text": text[:600], "reference_name": reference.name,
               "reference_audio_base64": base64.b64encode(reference.read_bytes()).decode("ascii")}
    if config.get("indextts2_version"):
        version = str(config["indextts2_version"])
        if version not in {"2", "2.5"}:
            raise ValueError("IndexTTS 模型版本应为2或2.5。")
        payload["model_version"] = version
    selected_language = language or str(config.get("indextts2_language") or "zh")
    if selected_language not in {"zh", "en", "ja", "es", "ar"}:
        raise ValueError("IndexTTS 不支持所选语言。")
    payload["language"] = selected_language
    try:
        with audio.cancellable_client(cancel_event, timeout=300) as client:
            with client.stream("POST", f"{service_url(config)}/synthesize", json=payload) as response:
                chunks = []
                received = 0
                for chunk in response.iter_bytes():
                    audio.check_cancel(cancel_event)
                    received += len(chunk)
                    if received > MAX_AUDIO_BYTES:
                        raise OSError("IndexTTS2 返回的音频超过 64 MB 限制。")
                    chunks.append(chunk)
                content = b"".join(chunks)
                audio.check_cancel(cancel_event)
    except httpx.HTTPError as exc:
        raise OSError(f"无法连接 IndexTTS2 适配服务或合成超时，请检查地址与独立服务是否已启动（{type(exc).__name__}）。") from exc
    if response.status_code != 200:
        detail = ""
        try:
            detail = str(json.loads(content).get("detail") or "")[:500]
        except (ValueError, AttributeError):
            pass
        raise OSError(f"IndexTTS2 适配服务合成失败（HTTP {response.status_code}）：{detail or '请检查独立服务终端'}")
    if not response.headers.get("content-type", "").lower().startswith("audio/wav"):
        raise OSError("IndexTTS2 服务返回了非音频内容；请使用 Mio 配套适配服务，不能直接填写 Gradio WebUI 地址。")
    return validate_wav(content)


def runtime_status(config: dict) -> dict:
    reference = str(config.get("indextts2_ref_audio") or "")
    running = False
    error = ""
    metadata = {}
    requested_version = str(config.get("indextts2_version") or "")
    actual_version = "unknown"
    try:
        url = service_url(config)
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f"{url}/health")
        metadata = response.json() if response.status_code == 200 else {}
        if not isinstance(metadata, dict):
            metadata = {}
        running = metadata.get("protocol") == PROTOCOL and metadata.get("ready") is True
        actual_version = str(metadata.get("model_version") or "unknown")
        if not running:
            error = "服务未就绪或协议不符，请使用 Mio 配套 IndexTTS2 适配服务。"
        elif requested_version and actual_version != requested_version:
            error = f"当前请求IndexTTS {requested_version}，服务实际版本为{actual_version}；请核对独立服务启动参数。"
            if actual_version != "unknown":
                running = False
    except (httpx.HTTPError, ValueError, AttributeError):
        error = "无法连接 IndexTTS2 适配服务，请先在 IndexTTS2 独立环境启动它。"
    return {"engine": "indextts2", "engine_label": "IndexTTS2（独立本地服务）", "service_url": config.get("indextts2_url", DEFAULT_URL),
            "service_running": running, "service_loading": False, "managed_running": False,
            "local_voice_runtime": "indextts2", "reference_ready": bool(reference and Path(reference).is_file()),
            "model_version": actual_version, "requested_model_version": requested_version,
            "version_verified": actual_version in {"2", "2.5"}, "languages": metadata.get("languages", []),
            "streaming_mode": "text_segments",
            "reference_name": Path(reference).name if reference else "", "prompt_ready": True,
            "last_error": error, "weights": {"gpt": [], "sovits": []}, "active_weights": {"gpt": "", "sovits": ""}}


def save_reference(data_url: str, name: str, directory: Path) -> Path:
    suffix = Path(name).suffix.lower()
    if suffix not in REFERENCE_EXTENSIONS:
        raise ValueError("请选择 WAV、MP3、FLAC 等参考音频。")
    try:
        if len(data_url) > 57 * 1024 * 1024:
            raise ValueError()
        header, encoded = data_url.split(",", 1)
        if not header.startswith("data:") or not header.endswith(";base64"):
            raise ValueError()
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("参考音频数据无效。") from exc
    if not 0 < len(content) <= MAX_REFERENCE_BYTES:
        raise ValueError("参考音频不能为空或超过 40 MB。")
    if suffix == ".wav":
        validate_wav(content)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"indextts2-{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)
    return target
