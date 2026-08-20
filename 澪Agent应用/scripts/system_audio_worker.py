from __future__ import annotations

import argparse
import atexit
import base64
import io
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
import wave


_emit_lock = threading.Lock()


PHONE_FRAME_MS = 20
PHONE_EDGE_PADDING_MS = 180
PHONE_MIN_VOICED_MS = 240
PHONE_MIN_CONTIGUOUS_VOICED_MS = 100
PHONE_MIN_PEAK_RMS = 0.006
LANGUAGE_TAG_RE = re.compile(r"<\|(?P<language>zh|en|yue|ja|ko|nospeech)\|>", re.IGNORECASE)
PHONE_HALLUCINATION_MARKERS = (
    "点赞",
    "订阅",
    "转发",
    "打赏",
    "字幕",
    "栏目",
    "谢谢观看",
    "感谢观看",
    "amara.org",
)
PHONE_PROMPT_LEAK_PATTERNS = (
    "请准确转写数字",
    "准确转写数字和标点",
    "普通话简体中文对话",
    "人名可能包括澪",
)

def emit(event_type: str, **payload: object) -> None:
    with _emit_lock:
        print(json.dumps({"type": event_type, **payload}, ensure_ascii=False), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("whisper", "sensevoice", "paraformer"), default="whisper")
    parser.add_argument("--model", default="base")
    parser.add_argument("--model-label", default="")
    parser.add_argument("--vad-model", default="")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--chunk-seconds", type=float, default=6.0)
    parser.add_argument("--cache-dir", required=True)
    return parser.parse_args()


def prepare_phone_samples(samples, sample_rate: int) -> tuple[object, dict[str, object], str]:
    """Trim quiet edges and reject clips without enough acoustic speech evidence."""
    sample_count = len(samples)
    duration_ms = sample_count / max(1, sample_rate) * 1000
    frame_size = max(1, round(sample_rate * PHONE_FRAME_MS / 1000))
    frame_rms: list[float] = []
    for start in range(0, sample_count, frame_size):
        frame = samples[start:start + frame_size]
        if len(frame):
            frame_rms.append(math.sqrt(sum(float(value) ** 2 for value in frame) / len(frame)))
    sorted_rms = sorted(frame_rms)
    noise_floor = sorted_rms[min(len(sorted_rms) - 1, len(sorted_rms) // 5)] if sorted_rms else 0.0
    peak_rms = max(frame_rms, default=0.0)
    speech_threshold = max(0.004, min(0.018, max(noise_floor * 2.8, peak_rms * 0.12)))
    voiced_frames = [index for index, rms in enumerate(frame_rms) if rms >= speech_threshold]
    voiced_ms = len(voiced_frames) * PHONE_FRAME_MS
    longest_voiced_run = 0
    current_voiced_run = 0
    voiced_frame_set = set(voiced_frames)
    for index in range(len(frame_rms)):
        if index in voiced_frame_set:
            current_voiced_run += 1
            longest_voiced_run = max(longest_voiced_run, current_voiced_run)
        else:
            current_voiced_run = 0
    longest_voiced_ms = longest_voiced_run * PHONE_FRAME_MS
    stats: dict[str, object] = {
        "duration_ms": round(duration_ms, 1),
        "peak_rms": round(peak_rms, 6),
        "noise_floor_rms": round(noise_floor, 6),
        "speech_threshold_rms": round(speech_threshold, 6),
        "voiced_ms": round(voiced_ms, 1),
        "longest_voiced_ms": round(longest_voiced_ms, 1),
        "voiced_ratio": round(voiced_ms / max(PHONE_FRAME_MS, duration_ms), 4),
        "trimmed_duration_ms": 0.0,
    }
    if peak_rms < PHONE_MIN_PEAK_RMS:
        return samples[:0], stats, "low_energy"
    if (
        voiced_ms < PHONE_MIN_VOICED_MS
        or longest_voiced_ms < PHONE_MIN_CONTIGUOUS_VOICED_MS
        or not voiced_frames
    ):
        return samples[:0], stats, "too_little_speech"

    padding_frames = math.ceil(PHONE_EDGE_PADDING_MS / PHONE_FRAME_MS)
    first_frame = max(0, voiced_frames[0] - padding_frames)
    last_frame = min(len(frame_rms), voiced_frames[-1] + padding_frames + 1)
    trimmed = samples[first_frame * frame_size:min(sample_count, last_frame * frame_size)]
    stats["trimmed_duration_ms"] = round(len(trimmed) / max(1, sample_rate) * 1000, 1)
    return trimmed, stats, ""


def phone_text_rejection(
    text: str,
    audio_stats: dict[str, object],
    requested_language: str,
) -> tuple[str, dict[str, object]]:
    normalized = re.sub(r"\s+", "", str(text or "")).lower()
    marker_count = sum(marker in normalized for marker in PHONE_HALLUCINATION_MARKERS)
    prompt_leak = next((pattern for pattern in PHONE_PROMPT_LEAK_PATTERNS if pattern in normalized), "")
    symbol_count = len(re.findall(r"[0-9a-z\u3040-\u30ff\u3400-\u9fff]", normalized))
    han_count = len(re.findall(r"[\u3400-\u9fff]", normalized))
    latin_count = len(re.findall(r"[a-z]", normalized))
    kana_count = len(re.findall(r"[\u3040-\u30ff]", normalized))
    script_count = han_count + latin_count + kana_count
    han_ratio = han_count / script_count if script_count else 0.0
    voiced_seconds = max(0.08, float(audio_stats.get("voiced_ms") or 0) / 1000)
    symbols_per_voiced_second = symbol_count / voiced_seconds
    diagnostics: dict[str, object] = {
        "symbol_count": symbol_count,
        "han_count": han_count,
        "latin_count": latin_count,
        "kana_count": kana_count,
        "han_ratio": round(han_ratio, 4),
        "symbols_per_voiced_second": round(symbols_per_voiced_second, 2),
        "hallucination_marker_count": marker_count,
        "prompt_leak_pattern": prompt_leak,
    }
    if not normalized:
        return "empty_transcript", diagnostics
    if symbol_count == 0:
        return "nonlexical_transcript", diagnostics
    if requested_language == "zh" and latin_count >= 3 and han_ratio < 0.35:
        return "non_chinese_drift", diagnostics
    if requested_language == "zh" and kana_count >= 3 and han_ratio < 0.35:
        return "non_chinese_drift", diagnostics
    if prompt_leak:
        return "prompt_leak_hallucination", diagnostics
    if marker_count >= 2:
        return "boilerplate_hallucination", diagnostics
    if symbol_count >= 10 and symbols_per_voiced_second > 14:
        return "implausible_transcript_rate", diagnostics
    return "", diagnostics


def whisper_phone_rejection(
    text: str,
    segments: list[object],
    audio_stats: dict[str, object],
    requested_language: str,
) -> tuple[str, dict[str, object]]:
    weighted_ms = 0.0
    weighted_logprob = 0.0
    weighted_no_speech = 0.0
    max_compression_ratio = 0.0
    for segment in segments:
        segment_ms = max(20.0, (float(segment.end) - float(segment.start)) * 1000)
        weighted_ms += segment_ms
        weighted_logprob += float(segment.avg_logprob) * segment_ms
        weighted_no_speech += float(segment.no_speech_prob) * segment_ms
        max_compression_ratio = max(max_compression_ratio, float(segment.compression_ratio))
    avg_logprob = weighted_logprob / weighted_ms if weighted_ms else -99.0
    no_speech_prob = weighted_no_speech / weighted_ms if weighted_ms else 1.0
    text_rejection, diagnostics = phone_text_rejection(text, audio_stats, requested_language)
    diagnostics.update({
        "segment_count": len(segments),
        "avg_logprob": round(avg_logprob, 4),
        "no_speech_prob": round(no_speech_prob, 4),
        "max_compression_ratio": round(max_compression_ratio, 4),
    })
    if text_rejection:
        return text_rejection, diagnostics
    if not segments:
        return "empty_transcript", diagnostics
    if no_speech_prob >= 0.75:
        return "whisper_no_speech", diagnostics
    if avg_logprob < -1.0 and (no_speech_prob >= 0.45 or diagnostics["symbol_count"] <= 3):
        return "low_confidence", diagnostics
    if max_compression_ratio > 2.4:
        return "repetitive_transcript", diagnostics
    return "", diagnostics


def phone_transcript_rejection(
    text: str,
    segments: list[object],
    audio_stats: dict[str, object],
    requested_language: str = "zh",
) -> tuple[str, dict[str, object]]:
    """Compatibility entry point for phone ASR gate tests and callers."""
    return whisper_phone_rejection(text, segments, audio_stats, requested_language)


def ascii_sentencepiece_copy(model_dir: Path) -> Path:
    source = model_dir / "chn_jpn_yue_eng_ko_spectok.bpe.model"
    if not source.is_file():
        raise FileNotFoundError(f"SenseVoice tokenizer 不存在：{source}")
    temporary_dir = Path(tempfile.mkdtemp(prefix="mio-sensevoice-"))
    atexit.register(shutil.rmtree, temporary_dir, True)
    target = temporary_dir / "tokenizer.bpe.model"
    shutil.copyfile(source, target)
    return target


def main() -> None:
    args = parse_args()
    emit("loading_runtime")
    import numpy as np
    import soundcard as sc
    warnings.filterwarnings("ignore", message="data discontinuity in recording")

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_dir))
    model_label = args.model_label or args.model
    emit("loading_model", model=model_label)
    model = None
    model_device = ""
    model_compute_type = ""
    engine_name = ""
    rich_transcription_postprocess = lambda text: text
    if args.engine == "whisper":
        from faster_whisper import WhisperModel

        runtime_attempts = (
            [("cuda", "int8_float16"), ("cpu", "int8")]
            if model_label == "large-v3-turbo"
            else [("cpu", "int8")]
        )
        runtime_errors = []
        for device, compute_type in runtime_attempts:
            try:
                model = WhisperModel(
                    args.model,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=max(1, min(4, (os.cpu_count() or 4) // 2)),
                    num_workers=1,
                    download_root=str(cache_dir),
                    local_files_only=Path(args.model).is_dir(),
                )
                model_device = device
                model_compute_type = compute_type
                break
            except Exception as exc:
                runtime_errors.append(f"{device}/{compute_type}: {exc}")
        if model is None:
            raise RuntimeError("；".join(runtime_errors) or "语音识别模型加载失败")
        engine_name = "faster-whisper"
    else:
        from funasr import AutoModel

        if not Path(args.model).is_dir() or not Path(args.vad_model).is_dir():
            raise FileNotFoundError("FunASR 本地模型或 VAD 模型不完整")
        tokenizer_conf = {}
        if args.engine == "sensevoice":
            from funasr.utils.postprocess_utils import rich_transcription_postprocess

            tokenizer_conf = {"bpemodel": str(ascii_sentencepiece_copy(Path(args.model)))}
        runtime_errors = []
        for device in ("cuda:0", "cpu"):
            try:
                model = AutoModel(
                    model=str(args.model),
                    vad_model=str(args.vad_model),
                    vad_kwargs={"max_single_segment_time": 30000},
                    tokenizer_conf=tokenizer_conf,
                    device=device,
                    disable_update=True,
                    disable_pbar=True,
                )
                model_device = device
                model_compute_type = "pytorch"
                break
            except Exception as exc:
                runtime_errors.append(f"{device}: {exc}")
        if model is None:
            raise RuntimeError("；".join(runtime_errors) or "FunASR 模型加载失败")
        engine_name = "sensevoice-small" if args.engine == "sensevoice" else "paraformer-zh"
    model_lock = threading.Lock()
    quality_pending = threading.Event()
    try:
        from opencc import OpenCC
        to_simplified = OpenCC("t2s").convert
    except Exception:
        to_simplified = lambda text: text

    def transcribe_samples(
        samples,
        language: str | None = None,
        *,
        phone_turn: bool = False,
    ) -> tuple[str, str, float, dict[str, object]]:
        audio_stats: dict[str, object] = {}
        if phone_turn:
            samples, audio_stats, acoustic_rejection = prepare_phone_samples(samples, 16000)
            if acoustic_rejection:
                return "", str(language or ""), 0.0, {
                    "accepted": False,
                    "rejection_reason": acoustic_rejection,
                    "audio": audio_stats,
                    "asr": {},
                }
        requested_language = str(language or "auto")
        segment_rows: list[object] = []
        engine_diagnostics: dict[str, object] = {}
        with model_lock:
            if args.engine == "whisper":
                segments, info = model.transcribe(
                    samples,
                    language=language,
                    beam_size=5 if phone_turn else (3 if language in {"zh", "ja"} else 1),
                    best_of=1,
                    vad_filter=not phone_turn,
                    condition_on_previous_text=False,
                    temperature=0.0,
                    initial_prompt=None,
                )
                # faster-whisper performs most inference while its segment
                # generator is consumed, so iteration must remain under the lock.
                segment_rows = list(segments)
                text = " ".join(
                    segment.text.strip() for segment in segment_rows if segment.text.strip()
                ).strip()
                detected_language = str(info.language or "")
                probability = float(info.language_probability or 0)
            else:
                if args.engine == "paraformer" and language not in {None, "zh"}:
                    raise ValueError("Paraformer 中文模型只支持中文电话识别")
                generate_kwargs = {
                    "input": np.asarray(samples, dtype=np.float32),
                    "cache": {},
                    "batch_size_s": 60,
                    "merge_vad": True,
                    "merge_length_s": 15,
                    "fs": 16000,
                }
                if args.engine == "sensevoice":
                    generate_kwargs.update({"language": requested_language, "use_itn": True})
                rows = model.generate(**generate_kwargs)
                raw_text = " ".join(str(row.get("text") or "").strip() for row in rows).strip()
                if args.engine == "sensevoice":
                    match = LANGUAGE_TAG_RE.search(raw_text)
                    detected_language = match.group("language").lower() if match else requested_language
                    text = rich_transcription_postprocess(raw_text).strip()
                else:
                    detected_language = "zh"
                    text = raw_text
                probability = 1.0 if text else 0.0
                engine_diagnostics = {
                    "row_count": len(rows),
                    "raw_text": raw_text[:1000],
                }
        if requested_language == "zh" or (language is None and detected_language == "zh"):
            text = to_simplified(text)
        diagnostics: dict[str, object] = {"accepted": True, "rejection_reason": "", "audio": {}, "asr": {}}
        if phone_turn:
            if args.engine == "whisper":
                rejection_reason, asr_stats = whisper_phone_rejection(
                    text,
                    segment_rows,
                    audio_stats,
                    requested_language,
                )
            else:
                rejection_reason, asr_stats = phone_text_rejection(
                    text,
                    audio_stats,
                    requested_language,
                )
                asr_stats.update(engine_diagnostics)
            if requested_language == "zh" and detected_language not in {"", "zh", "yue"}:
                rejection_reason = rejection_reason or "unexpected_language"
                asr_stats["detected_language"] = detected_language
            diagnostics = {
                "accepted": not rejection_reason,
                "rejection_reason": rejection_reason,
                "audio": audio_stats,
                "asr": asr_stats,
            }
            if rejection_reason:
                text = ""
        return text, detected_language, probability, diagnostics

    def command_reader() -> None:
        for line in sys.stdin:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(payload.get("type") or "") != "transcribe_wav":
                continue
            request_id = str(payload.get("request_id") or "")
            quality_pending.set()
            try:
                raw = base64.b64decode(str(payload.get("wav_base64") or ""), validate=True)
                with wave.open(io.BytesIO(raw), "rb") as source:
                    channels = source.getnchannels()
                    sample_width = source.getsampwidth()
                    sample_rate = source.getframerate()
                    frames = source.readframes(source.getnframes())
                if sample_width != 2 or channels not in {1, 2}:
                    raise ValueError("只支持 16 位单声道或双声道 WAV")
                samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
                if channels == 2:
                    samples = samples.reshape(-1, 2).mean(axis=1)
                if sample_rate != 16000 and samples.size:
                    output_size = max(1, round(samples.size * 16000 / sample_rate))
                    samples = np.interp(
                        np.linspace(0, samples.size - 1, output_size),
                        np.arange(samples.size),
                        samples,
                    ).astype(np.float32)
                language = str(payload.get("language") or "").strip()
                purpose = str(payload.get("purpose") or "quality").strip().lower()
                text, detected_language, probability, diagnostics = transcribe_samples(
                    samples,
                    None if language in {"", "auto"} else language,
                    phone_turn=purpose == "phone",
                )
                emit(
                    "quality_transcript",
                    request_id=request_id,
                    text=text[:1000],
                    language=detected_language,
                    probability=round(probability, 4),
                    engine=engine_name,
                    model=model_label,
                    device=model_device,
                    compute_type=model_compute_type,
                    requested_language=language or "auto",
                    **diagnostics,
                )
            except Exception as exc:
                emit("quality_transcript", request_id=request_id, text="", error=str(exc)[:300])
            finally:
                quality_pending.clear()

    threading.Thread(target=command_reader, name="mio-quality-asr", daemon=True).start()
    speaker = sc.default_speaker()
    emit("speaker_detected", speaker=str(speaker.name if speaker else ""))
    if speaker is None:
        raise RuntimeError("没有找到默认扬声器")
    microphone = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    emit("microphone_detected", microphone=str(microphone))
    sample_rate = 16000
    window_seconds = min(15.0, max(4.0, float(args.chunk_seconds)))
    window_frames = int(sample_rate * window_seconds)
    capture_block_frames = int(sample_rate * 0.5)
    analysis_hop_frames = int(sample_rate * min(2.0, max(1.0, window_seconds / 2)))
    minimum_analysis_frames = int(sample_rate * min(3.0, window_seconds))
    background_windows: queue.Queue[tuple[object, float]] = queue.Queue(maxsize=2)
    background_stats = {
        "captured_blocks": 0,
        "queued_windows": 0,
        "dropped_windows": 0,
        "completed_windows": 0,
    }
    last_emitted_text = ""
    last_emitted_at = 0.0

    def normalized_transcript(text: str) -> str:
        return re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+", "", text).lower()

    def emit_background_transcript(samples, rms: float) -> None:
        nonlocal last_emitted_text, last_emitted_at
        started = time.monotonic()
        text, language, probability, _diagnostics = transcribe_samples(
            samples,
            None if args.language == "auto" else args.language,
        )
        background_stats["completed_windows"] += 1
        if not text:
            return
        now = time.monotonic()
        current_normalized = normalized_transcript(text)
        previous_normalized = normalized_transcript(last_emitted_text)
        if (
            current_normalized
            and previous_normalized
            and now - last_emitted_at < max(8.0, window_seconds * 2)
            and (
                current_normalized == previous_normalized
                or current_normalized in previous_normalized
            )
        ):
            return
        last_emitted_text = text
        last_emitted_at = now
        emit(
            "transcript",
            text=text[:1000],
            language=language,
            probability=round(probability, 4),
            rms=round(rms, 6),
            latency_seconds=round(time.monotonic() - started, 3),
        )

    def background_transcriber() -> None:
        while True:
            samples, rms = background_windows.get()
            try:
                if quality_pending.is_set():
                    continue
                emit_background_transcript(samples, rms)
            except Exception as exc:
                emit("background_error", message=str(exc)[:300])
            finally:
                background_windows.task_done()

    threading.Thread(
        target=background_transcriber,
        name="mio-loopback-asr",
        daemon=True,
    ).start()
    emit(
        "ready",
        engine=engine_name,
        model=model_label,
        device=model_device,
        compute_type=model_compute_type,
        speaker=speaker.name,
        sample_rate=sample_rate,
        capture_block_seconds=0.5,
        analysis_window_seconds=window_seconds,
        analysis_hop_seconds=analysis_hop_frames / sample_rate,
    )

    with microphone.recorder(samplerate=sample_rate, channels=1) as recorder:
        rolling_samples = np.empty(0, dtype=np.float32)
        frames_since_analysis = 0
        while True:
            block = np.asarray(
                recorder.record(numframes=capture_block_frames),
                dtype=np.float32,
            ).reshape(-1)
            background_stats["captured_blocks"] += 1
            if not block.size:
                continue
            rolling_samples = np.concatenate((rolling_samples, block))[-window_frames:]
            frames_since_analysis += block.size
            if rolling_samples.size < minimum_analysis_frames or frames_since_analysis < analysis_hop_frames:
                continue
            frames_since_analysis = 0
            rms = math.sqrt(float(np.mean(np.square(rolling_samples)))) if rolling_samples.size else 0.0
            if rms < 0.0025:
                emit("silence", rms=round(rms, 6))
                continue
            if quality_pending.is_set():
                emit("quality_priority", rms=round(rms, 6))
                continue
            snapshot = rolling_samples.copy()
            if background_windows.full():
                try:
                    background_windows.get_nowait()
                    background_windows.task_done()
                    background_stats["dropped_windows"] += 1
                except queue.Empty:
                    pass
            background_windows.put_nowait((snapshot, rms))
            background_stats["queued_windows"] += 1
            emit("capture_stats", **background_stats)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        emit("error", message=str(exc)[:500])
        sys.exit(1)
