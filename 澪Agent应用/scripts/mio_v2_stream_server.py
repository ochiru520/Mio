"""Owner-scoped CUDA streaming bridge for selected GPT-SoVITS V2 voices."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
import types
import uuid

LANGUAGES = {"zh": "zh", "ja": "ja", "en": "en", "all_zh": "all_zh", "all_ja": "all_ja"}
MODEL = "mio-v2-native"
VOICE_PROFILE = "gpt-sovits-v2-selected"
SETTINGS = dict(top_k=10, top_p=.88, temperature=.68, speed_factor=1., seed=6101,
                repetition_penalty=1.35, text_split_method="cut5", batch_size=1,
                split_bucket=False, return_fragment=False, streaming_mode=True,
                parallel_infer=False, fragment_interval=.16, fixed_length_chunk=True,
                min_chunk_length=32, overlap_length=2)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def synthesis_payload(text, language, reference, reference_text, reference_language="ja"):
    if not isinstance(text, str) or not text.strip() or len(text) > 600 or language not in LANGUAGES:
        raise ValueError("语音文字为空、过长或语言不支持。")
    return {**SETTINGS, "text": text, "text_lang": LANGUAGES[language],
            "ref_audio_path": str(reference), "prompt_text": reference_text,
            "prompt_lang": reference_language, "aux_ref_audio_paths": []}


class _CheckpointParams:
    """Data-only replacement for old checkpoints' utils.HParams pickles."""
    pass


def _mapping(value):
    return value if isinstance(value, dict) else vars(value) if isinstance(value, _CheckpointParams) else {}


def validate_v2_metadata(sovits, gpt):
    """Inspect tensor/config metadata, never trusting the chosen filename."""
    weight, config = sovits.get("weight", {}), _mapping(sovits.get("config"))
    if not isinstance(weight, dict):
        raise ValueError("SoVITS权重结构无效。")
    versions = [sovits.get("version"), config.get("version"), _mapping(config.get("model")).get("version")]
    if any(value not in (None, "", "v2") for value in versions):
        raise ValueError("此流式运行方式仅支持GPT-SoVITS V2，不支持Pro/Plus或其他版本。")
    embedding = weight.get("enc_p.text_embedding.weight")
    if (getattr(embedding, "shape", None) is None or tuple(embedding.shape) != (732, 192)
            or any(key.startswith(("sv_emb", "cfm.", "sv_encoder", "vocoder.")) for key in weight)):
        raise ValueError("SoVITS模型结构不是受支持的V2。")
    gpt_weight = gpt.get("weight", {})
    if not isinstance(gpt_weight, dict) or "model.ar_text_embedding.word_embeddings.weight" not in gpt_weight:
        raise ValueError("GPT语义权重结构无效。")
    gpt_config = _mapping(gpt.get("config"))
    if gpt.get("version") not in (None, "", "v2") or gpt_config.get("version") not in (None, "", "v2"):
        raise ValueError("GPT权重版本与V2运行方式不兼容。")


def validate_checkpoint_files(torch, sovits_path, gpt_path):
    """Restricted unpickling on meta tensors before upstream native loading."""
    def load(path, sovits=False):
        with path.open("rb") as stream:
            prefix = stream.read(2)
        if prefix not in ({b"PK", b"01"} if sovits else {b"PK"}):
            raise ValueError("权重文件标识不属于支持的V2检查点。")
        if prefix == b"01":
            content = bytearray(path.read_bytes()); content[:2] = b"PK"
            target, mmap = BytesIO(content), False
        else:
            target, mmap = path, True
        with torch.serialization.safe_globals([(_CheckpointParams, "utils.HParams")]):
            data = torch.load(target, map_location="meta", weights_only=True, mmap=mmap)
        if not isinstance(data, dict):
            raise ValueError("模型检查点需要权重与配置。")
        return data
    sovits, gpt = load(sovits_path, True), load(gpt_path)
    validate_v2_metadata(sovits, gpt)


def install_pypinyin_frontend(source):
    """Select the shipped pypinyin implementation only in this isolated process."""
    import text as package
    path = source / "GPT_SoVITS/text/chinese2.py"
    content = path.read_text(encoding="utf-8")
    marker = "is_g2pw = True  #"
    if content.count(marker) != 1:
        raise RuntimeError("原生中文前端版本不符合已验证版本。")
    module = types.ModuleType("text.chinese2")
    module.__file__, module.__package__ = str(path), "text"
    sys.modules["text.chinese2"] = module
    exec(compile(content.replace(marker, "is_g2pw = False  #", 1), str(path), "exec"), module.__dict__)
    package.chinese2 = module
    if module.is_g2pw is not False:
        raise RuntimeError("未启用隔离pypinyin前端。")


def release_startup_pages():
    """One startup-only trim of this worker's inactive Windows resident pages."""
    import gc
    gc.collect()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        psapi.EmptyWorkingSet.restype = wintypes.BOOL
        if not psapi.EmptyWorkingSet(kernel.GetCurrentProcess()):
            raise OSError(ctypes.get_last_error(), "Unable to trim own startup pages")


def warm_voice(voice):
    """Consume fixed public samples before readiness, without playing or caching."""
    started = time.perf_counter()
    languages = []
    for text, language in (("你好。", "zh"), ("こんにちは。", "ja")):
        pieces = iter(voice.generate((text,), language, threading.Event()))
        received = 0
        try:
            for pcm in pieces:
                if not isinstance(pcm, bytes) or len(pcm) % 2:
                    raise RuntimeError("原Mio预热未产生有效PCM。")
                received += len(pcm)
        finally:
            close = getattr(pieces, "close", None)
            if close:
                close()
        if not received:
            raise RuntimeError("原Mio预热未产生音频。")
        languages.append(language)
    return {"warmup_seconds": time.perf_counter() - started, "warmup_languages": languages}


class NativeVoice:
    def __init__(self, args):
        started = time.perf_counter()
        source = args.source.resolve(strict=True)
        self.gpt, self.sovits = args.gpt.resolve(strict=True), args.sovits.resolve(strict=True)
        self.reference = args.reference.resolve(strict=True)
        self.reference_text, self.reference_language = args.reference_text, args.reference_language
        self.voice_profile = getattr(args, "voice_profile", VOICE_PROFILE)
        runtime = args.runtime_dir.resolve()
        if runtime == source or source in runtime.parents:
            raise ValueError("运行配置目录必须位于原源码目录之外。")
        runtime.mkdir(parents=True, exist_ok=True)
        sys.path[:0] = [str(source), str(source / "GPT_SoVITS")]
        # Upstream prints normalized request text and tracebacks. This dedicated
        # worker never persists those diagnostics; errors expose only their type.
        with open(os.devnull, "w", encoding="utf-8") as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
            import torch
            validate_checkpoint_files(torch, self.sovits, self.gpt)
            if not torch.cuda.is_available():
                raise RuntimeError("GPT-SoVITS V2流式语音需要CUDA。")
            install_pypinyin_frontend(source)
            from TTS_infer_pack.TTS import TTS, TTS_Config
            cfg = TTS_Config({"custom": {"device": "cuda", "is_half": True, "version": "v2",
                "t2s_weights_path": str(self.gpt), "vits_weights_path": str(self.sovits),
                "bert_base_path": str(source / "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"),
                "cnhuhbert_base_path": str(source / "GPT_SoVITS/pretrained_models/chinese-hubert-base")}})
            cfg.configs_path = str(runtime / "mio-v2-infer.yaml")
            self.tts = TTS(cfg)
        loaded = self.tts.configs
        if (str(loaded.device) != "cuda" or loaded.version != "v2" or not loaded.is_half
                or getattr(self.tts, "is_v2pro", False) or loaded.sampling_rate != 32000
                or Path(loaded.t2s_weights_path).resolve() != self.gpt
                or Path(loaded.vits_weights_path).resolve() != self.sovits):
            raise RuntimeError("实际加载模型与所选V2 CUDA FP16身份不符。")
        release_startup_pages()
        self.startup = {"load_seconds": time.perf_counter() - started}
        # Trimming again here would discard the frontend pages we just warmed.
        self.startup.update(warm_voice(self))

    def metadata(self):
        return {"active_weights": {"gpt": str(self.gpt), "sovits": str(self.sovits)},
                "weight_sha256": {"gpt": file_sha256(self.gpt), "sovits": file_sha256(self.sovits)},
                "reference_audio": str(self.reference), "reference_path": str(self.reference),
                "reference_language": self.reference_language,
                "reference_text_sha256": hashlib.sha256(self.reference_text.encode("utf-8")).hexdigest(),
                "voice_profile": self.voice_profile,
                "device": "cuda", "precision": "float16", "chinese_frontend": "native-pypinyin", **self.startup}

    def generate(self, segments, language, cancelled):
        import numpy as np
        for text in segments:
            if cancelled.is_set():
                return
            payload = synthesis_payload(text, language, self.reference, self.reference_text, self.reference_language)
            pieces = self.tts.run(payload)
            try:
                while not cancelled.is_set():
                    with open(os.devnull, "w", encoding="utf-8") as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
                        part = next(pieces, None)
                    if part is None:
                        break
                    if cancelled.is_set():
                        return
                    sample_rate, audio = part
                    values = np.asarray(audio)
                    if sample_rate != 32000 or values.dtype != np.int16 or not np.isfinite(values).all():
                        raise RuntimeError("原生推理未产生有效32000Hz int16音频。")
                    pcm = values.reshape(-1).astype("<i2").tobytes()
                    if pcm:
                        yield pcm
            finally:
                with open(os.devnull, "w", encoding="utf-8") as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
                    pieces.close()


class Sessions:
    MAX_SESSIONS = 16
    def __init__(self):
        self.lock, self.items = threading.RLock(), {}

    def create(self):
        with self.lock:
            now = time.monotonic()
            for key, item in list(self.items.items()):
                if item["state"] != "streaming" and now - item["created"] > 120:
                    self.items.pop(key)
            if len(self.items) >= self.MAX_SESSIONS:
                raise ValueError("语音等待会话已满。")
            key = uuid.uuid4().hex
            self.items[key] = {"created": now, "segments": [], "segment_ids": set(),
                               "language": "zh", "state": "open", "cancelled": threading.Event()}
            return key


def handler_for(generate, *, metadata=None):
    """No audio queue: HTTP backpressure holds at most one native PCM chunk."""
    sessions, inference = Sessions(), threading.Lock()
    health = {"ready": True, "service": "mio-v2-native", "engine": "mio_v2", "version": "v2",
              "model_family": "GPT-SoVITS V2", "model": MODEL, "voice_profile": VOICE_PROFILE,
              "sample_rate": 32000, "languages": list(LANGUAGES), "streaming_mode": "audio_chunks",
              "min_chunk_length": 32, "overlap_length": 2, "generation": dict(SETTINGS), **(metadata or {})}
    voice_profile = health["voice_profile"]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connection.settimeout(30)

        def log_message(self, *_args):
            pass

        def answer(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384 or not self.headers.get("Content-Type", "").startswith("application/json"):
                    self.close_connection = True
                    return self.answer(413, {"detail": "需要大小受限的JSON请求。"})
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("需要JSON对象。")
                if self.path in {"/v1/sessions", "/tts"}:
                    if payload.get("model") not in (None, "", MODEL) or payload.get("voice_profile") not in (None, "", voice_profile):
                        raise ValueError("音色身份不符。")
                    if self.path == "/tts":
                        text, language = payload.get("text"), payload.get("language", payload.get("text_lang", "zh"))
                        synthesis_payload(text, language, "", "")
                    key = sessions.create()
                    if self.path == "/v1/sessions":
                        return self.answer(200, {"id": key, "model": MODEL, "voice_profile": voice_profile})
                    with sessions.lock:
                        item = sessions.items[key]
                        item.update(segments=[text], language=language, state="flushed")
                    return self.stream(key)
                parts = self.path.strip("/").split("/")
                with sessions.lock:
                    item = sessions.items.get(parts[2]) if len(parts) == 4 and parts[:2] == ["v1", "sessions"] else None
                    if item is None:
                        return self.answer(404, {"detail": "语音会话不存在。"})
                    action = parts[3]
                    if action == "cancel":
                        item["cancelled"].set()
                        if item["state"] != "streaming":
                            sessions.items.pop(parts[2], None)
                    elif action == "segments":
                        if item["state"] != "open":
                            raise ValueError("语音已提交。")
                        text, language = payload.get("text"), payload.get("language", "zh")
                        synthesis_payload(text, language, "", "")
                        segment_id = payload.get("segment_id")
                        if not isinstance(segment_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", segment_id) or segment_id in item["segment_ids"]:
                            raise ValueError("分段标识无效。")
                        if len(item["segments"]) >= 8 or sum(map(len, item["segments"])) + len(text) > 600:
                            raise ValueError("语音内容过长。")
                        if item["segments"] and language != item["language"]:
                            raise ValueError("会话语言不一致。")
                        item["segments"].append(text)
                        item["segment_ids"].add(segment_id)
                        item["language"] = language
                    elif action == "flush":
                        if item["state"] != "open" or not item["segments"]:
                            raise ValueError("会话为空或已提交。")
                        item["state"] = "flushed"
                    else:
                        return self.answer(404, {"detail": "接口不存在。"})
                self.answer(200, {"ok": True})
            except (ValueError, TypeError, KeyError, UnicodeError):
                self.answer(400, {"detail": "语音请求、身份或会话状态无效。"})

        def do_GET(self):
            if self.path == "/health":
                return self.answer(200, health)
            parts = self.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "audio":
                return self.stream(parts[2])
            return self.answer(404, {"detail": "接口不存在。"})

        def stream(self, key):
            with sessions.lock:
                item = sessions.items.get(key)
                if item is None:
                    return self.answer(404, {"detail": "语音会话不存在。"})
                if item["state"] != "flushed":
                    return self.answer(409, {"detail": "会话未提交或已读取。"})
                item["state"] = "streaming"
            acquired, sent_headers, pieces = False, False, None
            try:
                deadline = time.monotonic() + 30
                while not acquired:
                    if item["cancelled"].is_set() or time.monotonic() > deadline:
                        return self.answer(409, {"detail": "会话取消或等待推理超时。"})
                    acquired = inference.acquire(timeout=.1)
                if item["cancelled"].is_set():
                    return self.answer(409, {"detail": "会话已取消。"})
                pieces = iter(generate(tuple(item["segments"]), item["language"], item["cancelled"]))
                first = next(pieces, None)
                if not first:
                    raise RuntimeError("没有有效音频。")
                if item["cancelled"].is_set():
                    return self.answer(409, {"detail": "会话已取消。"})
                self.send_response(200)
                for name, value in {"Content-Type": "application/octet-stream", "X-TTS-Sample-Rate": "32000",
                    "X-TTS-Channels": "1", "X-TTS-Sample-Format": "s16le", "Transfer-Encoding": "chunked"}.items():
                    self.send_header(name, value)
                self.end_headers()
                sent_headers = True
                data = first
                while data is not None:
                    if item["cancelled"].is_set():
                        raise InterruptedError("语音已取消。")
                    if not isinstance(data, bytes) or len(data) % 2:
                        raise RuntimeError("PCM格式无效。")
                    if data:
                        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()
                    data = next(pieces, None)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                if sent_headers:
                    self.close_connection = True
                else:
                    self.answer(500, {"detail": "V2语音推理失败，请检查运行资源与所选模型。"})
            finally:
                item["cancelled"].set()
                try:
                    if pieces is not None:
                        close = getattr(pieces, "close", None)
                        if close:
                            close()
                finally:
                    if acquired:
                        inference.release()
                    with sessions.lock:
                        sessions.items.pop(key, None)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "gpt", "sovits", "reference", "runtime-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--reference-text", required=True)
    parser.add_argument("--reference-language", choices=LANGUAGES, default="ja")
    parser.add_argument("--voice-profile", default=VOICE_PROFILE)
    parser.add_argument("--port", type=int, default=9880)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口无效。")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.voice_profile):
        parser.error("音色标识无效。")
    voice = NativeVoice(args)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(voice.generate, metadata=voice.metadata()))
    server.daemon_threads = True
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Mio V2 worker failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
