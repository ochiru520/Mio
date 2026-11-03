"""Experimental Mio loopback bridge. Uses existing, explicitly unverified TRT engines."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
import uuid

POLICY = {"ANIFLIVE_TTS_PIPELINE_OVERLAP": "0", "ANIFLIVE_TTS_PERSISTENT_GPT_CONTEXTS": "0",
    "ANIFLIVE_TTS_SAMPLE_CUDA_GRAPH": "0", "ANIFLIVE_TTS_CUDA_GRAPH": "0", "ANIFLIVE_TTS_ENABLE_G2PW": "0",
    "ANIFLIVE_TTS_SEMANTIC_BACKEND": "transformer", "ANIFLIVE_TTS_SEMANTIC_SAMPLING": "native-v2proplus-v1"}
LANGUAGES = {"zh": "all_zh", "ja": "all_ja", "en": "en", "yue": "all_yue", "ko": "all_ko"}


def speech_segments(segments, language, engine, streamer, cancelled):
    """Fit the actual per-machine phoneme profiles without dropping input text."""
    reference = streamer.reference_bank.identity
    encoder = engine.model_gpt_enc.input_max_shapes
    limits = [int(engine.sovits_max_text_len),
              int(encoder['phoneme_ids'][-1]) - len(reference.phones),
              int(encoder['bert_feature'][-1]) - len(reference.phones)]
    maximum = min(limits)
    if maximum < 1:
        raise ValueError('参考音频超出当前引擎的文字容量。')

    def split(text, bound):
        # Preserve every character; prefer clause/word boundaries over a hard cut.
        low = max(1, bound // 2)
        candidates = [index + 1 for index, char in enumerate(text[:bound])
                      if index + 1 >= low and (char in '，、。！？!?；;：:\n' or char.isspace())]
        at = candidates[-1] if candidates else bound
        # Keep trailing punctuation with its spoken text, never alone in a call.
        if not any(char.isalnum() for char in text[at:]):
            return None
        return text[:at], text[at:]

    def fit(text):
        if cancelled.is_set():
            raise InterruptedError('语音已取消。')
        if not any(char.isalnum() for char in text):
            raise ValueError('没有可朗读的文字。')
        # The fitted and small Anif profiles use a 32-character planning bound;
        # digits and Japanese can expand further, so also check actual phonemes.
        if len(text) > 32:
            pair = split(text, 32)
            if pair is not None:
                return fit(pair[0]) + fit(pair[1])
        phones, features, _ = engine.get_phones_and_bert(text, LANGUAGES[language], engine.version, default_lang='all_zh')
        count = len(phones)
        del features
        if not count:
            raise ValueError('没有可朗读的文字。')
        if count <= maximum:
            return [text]
        pair = split(text, max(1, len(text) // 2))
        if pair is None or len(text) < 2:
            raise ValueError('文字超出当前引擎的音素容量。')
        return fit(pair[0]) + fit(pair[1])

    result = []
    for text in segments:
        result.extend(fit(text))
    return tuple(result)


def generation_settings(*, temperature=1., noise_scale=.5):
    """Bounded startup choices; preserve the prior baseline unless selected."""
    if not math.isfinite(temperature) or not .1 <= temperature <= 2.:
        raise ValueError("采样温度必须为0.1至2之间的有限数。")
    if not math.isfinite(noise_scale) or not 0. <= noise_scale <= 1.:
        raise ValueError("声学噪声系数必须为0至1之间的有限数。")
    return {"top_k": 5, "top_p": 1., "temperature": temperature,
            "noise_scale": noise_scale, "speed": 1., "pause_length": 0., "request_seed": 42}


class FrontendProfiler:
    """Opt-in timing of existing calls; retain counts/times, never text or paths."""
    def __init__(self, engine, runtime_module):
        self.engine = engine
        self.runtime = runtime_module
        self.local = threading.local()

    @contextmanager
    def capture(self, phase):
        record = {"phase": phase, "stages": {}}
        self.local.record = record
        started = time.perf_counter()
        try:
            yield record
        finally:
            record["elapsed_seconds"] = time.perf_counter() - started
            self.local.record = None

    def timed(self, name, callback, *args, **kwargs):
        record = getattr(self.local, "record", None)
        if record is None:
            return callback(*args, **kwargs)
        started = time.perf_counter()
        try:
            return callback(*args, **kwargs)
        finally:
            row = record["stages"].setdefault(name, {"calls": 0, "wall_seconds": 0.0})
            row["calls"] += 1
            row["wall_seconds"] += time.perf_counter() - started

    def wrap(self, target, attribute, label):
        original = getattr(target, attribute)
        def measured(*args, **kwargs):
            return self.timed(label, original, *args, **kwargs)
        setattr(target, attribute, measured)

    def install(self):
        self.wrap(self.engine, "get_phones_and_bert", "phones_and_bert_total")
        self.wrap(self.engine, "get_bert_feature", "bert_feature_host_wall")
        self.wrap(self.runtime.LangSegmenter, "getTexts", "language_segmentation")
        original = self.runtime.clean_text
        def clean(text, language, version=None):
            if getattr(self.local, "record", None) is None:
                return original(text, language, version)
            # cleaner.py imports these same modules lazily. Separate import/init
            # time from their normalize/G2P calls without changing their inputs.
            modules = {"zh": "chinese" if version == "v1" else "chinese2",
                       "ja": "japanese", "en": "english", "ko": "korean", "yue": "cantonese"}
            name = modules.get(language, "english")
            module = self.timed("frontend_import_" + name, importlib.import_module, "text." + name)
            originals = {}
            for attribute in ("text_normalize", "g2p"):
                if hasattr(module, attribute):
                    originals[attribute] = getattr(module, attribute)
                    self.wrap(module, attribute, language + "_" + attribute)
            try:
                return self.timed("clean_text_total", original, text, language, version)
            finally:
                for attribute, callback in originals.items():
                    setattr(module, attribute, callback)
        self.runtime.clean_text = clean


class TimingSink:
    MAX_RECORDS = 256
    def __init__(self, path):
        self.path = path
        self.records = 0
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation avoids overwriting or mixing prior evidence.
            with path.open("x", encoding="utf-8"):
                pass

    def write(self, record):
        if self.path is not None and self.records < self.MAX_RECORDS:
            self.records += 1
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ordinal": self.records, **record}, ensure_ascii=False) + "\n")


def warm_text_frontends(engine):
    """Fixed public samples initialize existing language paths; no request cache."""
    for text, language in (("文件3.wav，重试1次。", "all_zh"),
                           ("テスト3です。", "all_ja")):
        phones, features, _ = engine.get_phones_and_bert(text, language, engine.version, default_lang="all_zh")
        if not phones or features is None:
            raise RuntimeError("文字前端预热未产生有效特征。")
        # Features are discarded; subsequent requests still process their own text.
        del phones, features


def release_startup_pages():
    """Release this worker's inactive resident pages once, before accepting work."""
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
            raise OSError(ctypes.get_last_error(), "Unable to release startup resident pages")


class Sessions:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = {}

    def create(self):
        with self.lock:
            now = time.monotonic()
            for key, item in list(self.items.items()):
                if item["state"] != "streaming" and now - item["created"] > 300:
                    del self.items[key]
            if len(self.items) >= 32:
                raise ValueError("语音会话已满，请稍后再试。")
            key = uuid.uuid4().hex
            self.items[key] = {"created": now, "segments": [], "segment_ids": set(), "language": "zh", "state": "open", "cancelled": threading.Event()}
            return key


def handler_for(generate, *, sample_rate=32000, voice_profile="default",
                reference_language="zh", reference_kind="unspecified",
                model_id="mio-v2proplus-experimental", runtime_metadata=None):
    if sample_rate != 32000:
        raise ValueError("Mio Anif实验桥接要求32000Hz。")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", voice_profile):
        raise ValueError("音色标识必须为1至64位英文字母、数字、下划线或短横线。")
    if reference_language not in LANGUAGES or reference_kind not in {"unspecified", "public-synthetic", "user-provided"}:
        raise ValueError("参考音频语言或来源无效。")
    sessions = Sessions()
    inference = threading.Lock()

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
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    self.close_connection = True
                    return self.answer(413, {"detail": "请求为空或过大。"})
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self.close_connection = True
                    return self.answer(415, {"detail": "需要JSON请求。"})
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("需要JSON对象。")
                parts = self.path.strip("/").split("/")
                if parts == ["v1", "sessions"]:
                    if payload.get("model") not in (None, "", model_id) or payload.get("voice_profile") not in (None, "", voice_profile):
                        raise ValueError("模型或音色与本服务启动时加载的配置不符。")
                    return self.answer(200, {"id": sessions.create(), "experimental": True})
                with sessions.lock:
                    item = sessions.items.get(parts[2]) if len(parts) == 4 and parts[:2] == ["v1", "sessions"] else None
                    if item is None:
                        return self.answer(404, {"detail": "语音会话不存在。"})
                    action = parts[3]
                    if action == "cancel":
                        item["cancelled"].set()
                        if item["state"] != "streaming":
                            del sessions.items[parts[2]]
                    elif action == "segments":
                        if item["state"] != "open":
                            raise ValueError("会话已提交，不能继续修改。")
                        text = str(payload.get("text") or "").strip()
                        language = str(payload.get("language") or "zh")
                        segment_id = str(payload.get("segment_id") or "")
                        if language not in LANGUAGES or not text or len(text) + sum(len(t) for t in item["segments"]) > 600 or len(item["segments"]) >= 8:
                            raise ValueError("语言、文字长度或分段数量无效。")
                        if not segment_id or len(segment_id) > 160 or segment_id in item["segment_ids"]:
                            raise ValueError("分段ID为空、过长或重复。")
                        if item["segments"] and language != item["language"]:
                            raise ValueError("单个实验会话必须使用同一种语言。")
                        item["segments"].append(text)
                        item["segment_ids"].add(segment_id)
                        item["language"] = language
                    elif action == "flush":
                        if item["state"] != "open" or not item["segments"]:
                            raise ValueError("会话为空或已提交。")
                        item["state"] = "flushed"
                    else:
                        return self.answer(404, {"detail": "接口不存在。"})
                self.answer(200, {"ok": True, "experimental": True})
            except (ValueError, TypeError, KeyError):
                self.answer(400, {"detail": "无效语音请求、会话状态或未加载的模型/音色。"})

        def do_GET(self):
            if self.path == "/health":
                return self.answer(200, {"ready": True, "service": "aniflive-mio-experimental", "version": "mio-bridge-1",
                    "model_family": "GPT-SoVITS V2ProPlus", "model": model_id, "voice_profile": voice_profile,
                    "experimental": True, "numerical_verified": False, "formal_numerical_gate": "pending; original GPTEncoder validation failed",
                    "sample_rate": sample_rate, "languages": list(LANGUAGES),
                    "reference_language": reference_language, "reference_kind": reference_kind,
                    "public_synthetic_reference": reference_kind == "public-synthetic", **(runtime_metadata or {})})
            parts = self.path.strip("/").split("/")
            with sessions.lock:
                item = sessions.items.get(parts[2]) if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "audio" else None
                if item is None:
                    return self.answer(404, {"detail": "语音会话不存在。"})
                if item["state"] != "flushed":
                    return self.answer(409, {"detail": "会话未提交或已经读取。"})
                item["state"] = "streaming"
            acquired = False
            pieces = None
            sent_headers = False
            try:
                deadline = time.monotonic() + 30
                while not acquired:
                    if item["cancelled"].is_set() or time.monotonic() > deadline:
                        return self.answer(409, {"detail": "会话已取消或等待推理超时。"})
                    acquired = inference.acquire(timeout=.1)
                pieces = iter(generate(tuple(item["segments"]), item["language"], item["cancelled"]))
                first = next(pieces, b"")
                if not first:
                    raise RuntimeError("没有有效音频。")
                self.send_response(200)
                for name, value in {"Content-Type": "application/octet-stream", "X-TTS-Sample-Rate": str(sample_rate),
                        "X-TTS-Channels": "1", "X-TTS-Sample-Format": "s16le", "Transfer-Encoding": "chunked"}.items():
                    self.send_header(name, value)
                self.end_headers()
                sent_headers = True
                for data in _with_first(first, pieces):
                    if item["cancelled"].is_set():
                        raise InterruptedError("会话已取消。")
                    if not isinstance(data, bytes) or len(data) % 2:
                        raise RuntimeError("无效PCM。")
                    if data:
                        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception as exc:
                if sent_headers:
                    self.close_connection = True  # A truncated stream must fail at the Mio adapter.
                else:
                    self.answer(422 if isinstance(exc, ValueError) else 500,
                                {"detail": "文字不适合当前引擎。" if isinstance(exc, ValueError)
                                 else "Anif实验推理失败，请检查模型与运行资源。"})
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
                        sessions.items.pop(parts[2], None)

    return Handler


def _with_first(first, rest):
    yield first
    yield from rest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--engines", type=Path, required=True)
    parser.add_argument("--bert", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-text", required=True)
    parser.add_argument("--reference-language", choices=LANGUAGES, default="zh",
                        help="参考录音原文的实际语言，与输出语言独立。")
    parser.add_argument("--voice-profile", default="default",
                        help="本进程唯一加载的音色标识；更换参考需重新启动进程。")
    parser.add_argument("--reference-kind", choices=["unspecified", "public-synthetic", "user-provided"], default="unspecified")
    parser.add_argument("--temperature", type=float, default=1., help="固定采样温度，0.1至2；默认保持1。")
    parser.add_argument("--noise-scale", type=float, default=.5, help="固定声学噪声系数，0至1；默认保持0.5。")
    parser.add_argument("--port", type=int, default=9882)
    parser.add_argument("--profile-jsonl", type=Path,
                        help="显式写入无原文的分阶段计时；最多256条，路径必须尚不存在。")
    parser.add_argument("--warmup-text-frontends", action=argparse.BooleanOptionalAction, default=True,
                        help="默认在ready前使用固定公开样本预热混合中英及日语文字前端；对照测试可显式关闭。")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.voice_profile):
        parser.error("音色标识必须为1至64位英文字母、数字、下划线或短横线。")
    try:
        generation = generation_settings(temperature=args.temperature, noise_scale=args.noise_scale)
    except ValueError as exc:
        parser.error(str(exc))
    os.environ.update(POLICY)
    source = args.source.resolve(strict=True)
    sys.path[:0] = [str(source / "src"), str(source / "minimal_inference"), str(source / "minimal_inference/GPT_SoVITS")]
    import numpy as np
    import torch
    import run_trt_inference as runtime_module
    from run_trt_inference import GPTSoVITS_TRT_Inference
    from aniflive_tts.streaming import TensorRTFixedReferenceStreamer
    engine = GPTSoVITS_TRT_Inference(str(args.engines.resolve(strict=True)), str(args.bert.resolve(strict=True)))
    sink = TimingSink(args.profile_jsonl)
    profiler = FrontendProfiler(engine, runtime_module) if args.profile_jsonl else None
    if profiler:
        profiler.install()
    if args.warmup_text_frontends:
        if profiler:
            with profiler.capture("fixed_text_frontend_warmup") as record:
                warm_text_frontends(engine)
            sink.write(record)
        else:
            warm_text_frontends(engine)
    streamer = TensorRTFixedReferenceStreamer(engine, reference_wav=args.reference.resolve(strict=True), reference_text=args.reference_text,
                                              reference_language=LANGUAGES[args.reference_language])
    streamer.prepare_reference()
    # Language dictionaries/imports and TRT deserialization create a large host
    # working set. Trim only our own process at this loading boundary, never per
    # request or in a timer; tensor values/devices and resource guards stay intact.
    release_startup_pages()

    def generate(segments, language, cancelled):
        started = time.perf_counter()
        first_pcm = None
        chunks = 0
        completed = False
        capture = profiler.capture("synthesis") if profiler else None
        if capture:
            record = capture.__enter__()
        bounded, pieces = (), None
        try:
            with torch.cuda.stream(engine.stream):
                bounded = speech_segments(segments, language, engine, streamer, cancelled)
            pieces = streamer.iter_audio(segments=bounded, text_language=LANGUAGES[language], **generation, cancelled=cancelled)
            for chunk in pieces:
                values = np.asarray(chunk, dtype=np.float32).reshape(-1)
                if not np.all(np.isfinite(values)):
                    raise RuntimeError("Nonfinite acoustic output")
                pcm = (np.clip(values, -1, 1) * 32767).astype("<i2").tobytes()
                if pcm:
                    chunks += 1
                    if first_pcm is None:
                        first_pcm = time.perf_counter() - started
                yield pcm
            completed = True
        finally:
            if pieces is not None:
                pieces.close()
            if capture:
                capture.__exit__(None, None, None)
                # Only explicitly allowlisted numeric fields; streamer profiles
                # can evolve to contain data unsuitable for diagnostics.
                record.update(language=language, segment_count=len(bounded),
                              first_pcm_seconds=first_pcm, chunks=chunks,
                              completed=completed, cancelled=cancelled.is_set())
                if completed:
                    native = getattr(streamer, "last_profile", {})
                    record["native_pipeline"] = {key: native[key] for key in (
                        "text_processing_seconds", "gpt_encoder_seconds", "gpt_decode_seconds",
                        "sovits_seconds", "first_audio_seconds", "total_pipeline_seconds",
                        "host_sync_seconds", "semantic_tokens", "sovits_invocations")
                        if isinstance(native.get(key), (int, float))}
                sink.write(record)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(generate, sample_rate=streamer.sample_rate,
        voice_profile=args.voice_profile, reference_language=args.reference_language, reference_kind=args.reference_kind))
    server.daemon_threads = True
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
