from __future__ import annotations

import argparse
import atexit
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
from statistics import mean
import subprocess
import shutil
import tempfile
import threading
import time
from typing import Any, Callable
import wave

import psutil


LANGUAGE_TAG_RE = re.compile(r"<\|(?P<language>zh|en|yue|ja|ko|nospeech)\|>", re.IGNORECASE)
LEXICAL_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "runs": 0,
            "average_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "maximum_ms": None,
        }
    return {
        "runs": len(values),
        "average_ms": round(mean(values), 2),
        "p50_ms": round(percentile(values, 0.50) or 0, 2),
        "p95_ms": round(percentile(values, 0.95) or 0, 2),
        "maximum_ms": round(max(values), 2),
    }


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def normalize(text: str) -> str:
    return "".join(LEXICAL_RE.findall(str(text or ""))).lower()


def wav_duration_ms(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / max(1, source.getframerate()) * 1000


def gpu_used_memory_mb() -> float | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3,
        )
        values = [float(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
        return sum(values) if values else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class ResourceSampler:
    def __init__(self, interval_seconds: float = 0.05) -> None:
        self._process = psutil.Process(os.getpid())
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.cpu_baseline_mb = self._rss_mb()
        self.cpu_peak_mb = self.cpu_baseline_mb
        self.gpu_baseline_mb = gpu_used_memory_mb()
        self.gpu_peak_mb = self.gpu_baseline_mb

    def _rss_mb(self) -> float:
        return self._process.memory_info().rss / (1024 * 1024)

    def sample(self) -> None:
        self.cpu_peak_mb = max(self.cpu_peak_mb, self._rss_mb())
        gpu_mb = gpu_used_memory_mb()
        if gpu_mb is not None:
            self.gpu_peak_mb = max(self.gpu_peak_mb or gpu_mb, gpu_mb)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self.sample()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="asr-resource-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.sample()

    def snapshot(self) -> dict[str, float | None]:
        current_cpu_mb = self._rss_mb()
        current_gpu_mb = gpu_used_memory_mb()
        return {
            "cpu_rss_mb": round(current_cpu_mb, 2),
            "cpu_rss_delta_mb": round(current_cpu_mb - self.cpu_baseline_mb, 2),
            "cpu_peak_rss_mb": round(self.cpu_peak_mb, 2),
            "cpu_peak_rss_delta_mb": round(self.cpu_peak_mb - self.cpu_baseline_mb, 2),
            "gpu_total_used_mb": round(current_gpu_mb, 2) if current_gpu_mb is not None else None,
            "gpu_total_used_delta_mb": (
                round(current_gpu_mb - self.gpu_baseline_mb, 2)
                if current_gpu_mb is not None and self.gpu_baseline_mb is not None
                else None
            ),
            "gpu_peak_total_used_mb": (
                round(self.gpu_peak_mb, 2) if self.gpu_peak_mb is not None else None
            ),
            "gpu_peak_total_used_delta_mb": (
                round(self.gpu_peak_mb - self.gpu_baseline_mb, 2)
                if self.gpu_peak_mb is not None and self.gpu_baseline_mb is not None
                else None
            ),
        }


Transcribe = Callable[[Path], tuple[str, str, dict[str, Any]]]


def ascii_sentencepiece_copy(model_dir: Path) -> Path:
    source = model_dir / "chn_jpn_yue_eng_ko_spectok.bpe.model"
    if not source.is_file():
        raise FileNotFoundError(f"Missing SenseVoice tokenizer: {source}")
    temporary_dir = Path(tempfile.mkdtemp(prefix="mio-sensevoice-"))
    atexit.register(shutil.rmtree, temporary_dir, True)
    target = temporary_dir / "tokenizer.bpe.model"
    shutil.copyfile(source, target)
    return target


def load_whisper(args: argparse.Namespace) -> tuple[Transcribe, dict[str, Any]]:
    from faster_whisper import WhisperModel

    device = "cuda" if args.device == "cuda" else "cpu"
    compute_type = "int8_float16" if device == "cuda" else "int8"
    model = WhisperModel(
        str(args.whisper_model),
        device=device,
        compute_type=compute_type,
        cpu_threads=max(1, int(args.cpu_threads)),
        num_workers=1,
        local_files_only=True,
    )

    def transcribe(path: Path) -> tuple[str, str, dict[str, Any]]:
        segments, info = model.transcribe(
            str(path),
            language="zh",
            beam_size=5,
            best_of=1,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0.0,
            initial_prompt="以下是普通话简体中文对话。人名可能包括澪。请准确转写数字和标点。",
        )
        rows = list(segments)
        text = " ".join(row.text.strip() for row in rows if row.text.strip()).strip()
        return text, str(info.language or ""), {
            "language_source": "model_detection_with_zh_constraint",
            "language_probability": round(float(info.language_probability or 0), 4),
            "segments": len(rows),
        }

    return transcribe, {
        "engine": "faster-whisper",
        "model_path": str(args.whisper_model),
        "device": device,
        "compute_type": compute_type,
        "vad": "disabled_for_pretrimmed_fixed_wav",
    }


def load_sensevoice(args: argparse.Namespace) -> tuple[Transcribe, dict[str, Any]]:
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    device = "cuda:0" if args.device == "cuda" else "cpu"
    tokenizer_path = ascii_sentencepiece_copy(args.sensevoice_model)
    model = AutoModel(
        model=str(args.sensevoice_model),
        vad_model=str(args.vad_model),
        vad_kwargs={"max_single_segment_time": 30000},
        tokenizer_conf={"bpemodel": str(tokenizer_path)},
        device=device,
        disable_update=True,
    )

    def transcribe(path: Path) -> tuple[str, str, dict[str, Any]]:
        rows = model.generate(
            input=str(path),
            cache={},
            language="zh",
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
        )
        raw_text = " ".join(str(row.get("text") or "").strip() for row in rows).strip()
        match = LANGUAGE_TAG_RE.search(raw_text)
        returned_language = match.group("language").lower() if match else "zh"
        text = rich_transcription_postprocess(raw_text).strip()
        return text, returned_language, {
            "language_source": "sensevoice_tag" if match else "forced_zh_request",
            "raw_text": raw_text[:1000],
            "segments": len(rows),
        }

    return transcribe, {
        "engine": "sensevoice-small",
        "model_path": str(args.sensevoice_model),
        "vad_model_path": str(args.vad_model),
        "device": device,
        "vad": "fsmn-vad",
    }


def load_paraformer(args: argparse.Namespace) -> tuple[Transcribe, dict[str, Any]]:
    from funasr import AutoModel

    device = "cuda:0" if args.device == "cuda" else "cpu"
    model = AutoModel(
        model=str(args.paraformer_model),
        vad_model=str(args.vad_model),
        vad_kwargs={"max_single_segment_time": 30000},
        device=device,
        disable_update=True,
    )

    def transcribe(path: Path) -> tuple[str, str, dict[str, Any]]:
        rows = model.generate(
            input=str(path),
            cache={},
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
        )
        text = " ".join(str(row.get("text") or "").strip() for row in rows).strip()
        return text, "zh", {
            "language_source": "zh_only_model_capability",
            "segments": len(rows),
        }

    return transcribe, {
        "engine": "paraformer-zh",
        "model_path": str(args.paraformer_model),
        "vad_model_path": str(args.vad_model),
        "device": device,
        "vad": "fsmn-vad",
        "punc": "not_loaded",
    }


def load_cases(ground_truth: Path) -> list[dict[str, Any]]:
    payload = json.loads(ground_truth.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for row in payload.get("runs") or []:
        path = Path(str(row.get("wav") or ""))
        if not path.is_file():
            path = ground_truth.parent / f"{row.get('id')}.wav"
        if not path.is_file():
            raise FileNotFoundError(f"Missing WAV for {row.get('id')}: {path}")
        cases.append(
            {
                "id": str(row.get("id") or path.stem),
                "wav": path,
                "expected": str(row.get("expected") or ""),
            }
        )
    if len(cases) != 5:
        raise ValueError(f"Expected exactly five benchmark cases, got {len(cases)}")
    return cases


def evaluate_case(
    case: dict[str, Any],
    transcribe: Transcribe,
    to_simplified: Callable[[str], str],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        transcript, returned_language, diagnostics = transcribe(case["wav"])
        error = ""
    except Exception as exc:
        transcript = ""
        returned_language = ""
        diagnostics = {}
        error = f"{type(exc).__name__}: {exc}"[:1000]
    elapsed_ms = (time.perf_counter() - started) * 1000
    expected = str(case["expected"])
    normalized_expected = normalize(expected)
    normalized_transcript = normalize(transcript)
    simplified_expected = normalize(to_simplified(expected))
    simplified_transcript = normalize(to_simplified(transcript))
    errors = edit_distance(normalized_expected, normalized_transcript)
    simplified_errors = edit_distance(simplified_expected, simplified_transcript)
    duration_ms = wav_duration_ms(case["wav"])
    return {
        "id": case["id"],
        "wav": str(case["wav"]),
        "duration_ms": round(duration_ms, 2),
        "expected": expected,
        "transcript": transcript,
        "requested_language": "zh",
        "returned_language": returned_language,
        "asr_ms": round(elapsed_ms, 2),
        "real_time_factor": round(elapsed_ms / max(1, duration_ms), 4),
        "character_errors": errors,
        "reference_characters": len(normalized_expected),
        "character_error_rate": round(errors / len(normalized_expected), 4)
        if normalized_expected
        else None,
        "simplified_character_errors": simplified_errors,
        "simplified_reference_characters": len(simplified_expected),
        "simplified_character_error_rate": round(simplified_errors / len(simplified_expected), 4)
        if simplified_expected
        else None,
        "passed_language": returned_language == "zh",
        "passed_nonempty": bool(normalized_transcript),
        "exact_match": normalized_expected == normalized_transcript,
        "simplified_exact_match": simplified_expected == simplified_transcript,
        "error": error,
        "diagnostics": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark fixed phone ASR WAV files without user data.")
    parser.add_argument("--engine", choices=("whisper", "sensevoice", "paraformer"), required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--whisper-model", type=Path)
    parser.add_argument("--sensevoice-model", type=Path)
    parser.add_argument("--paraformer-model", type=Path)
    parser.add_argument("--vad-model", type=Path)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()

    required_paths = {
        "whisper": (args.whisper_model,),
        "sensevoice": (args.sensevoice_model, args.vad_model),
        "paraformer": (args.paraformer_model, args.vad_model),
    }[args.engine]
    if any(path is None or not path.is_dir() for path in required_paths):
        parser.error(f"Missing local model path for {args.engine}")

    try:
        from opencc import OpenCC

        to_simplified = OpenCC("t2s").convert
    except Exception:
        to_simplified = lambda text: text

    cases = load_cases(args.ground_truth)
    sampler = ResourceSampler()
    sampler.start()
    loader = {
        "whisper": load_whisper,
        "sensevoice": load_sensevoice,
        "paraformer": load_paraformer,
    }[args.engine]
    cold_started = time.perf_counter()
    transcribe, engine = loader(args)
    cold_load_ms = (time.perf_counter() - cold_started) * 1000
    sampler.sample()
    after_load = sampler.snapshot()

    warmup = evaluate_case(cases[0], transcribe, to_simplified)
    cold_ready_to_result_ms = cold_load_ms + float(warmup["asr_ms"])
    runs = [evaluate_case(case, transcribe, to_simplified) for case in cases]
    sampler.stop()

    timings = [float(run["asr_ms"]) for run in runs]
    total_errors = sum(int(run["character_errors"]) for run in runs)
    total_references = sum(int(run["reference_characters"]) for run in runs)
    simplified_errors = sum(int(run["simplified_character_errors"]) for run in runs)
    simplified_references = sum(int(run["simplified_reference_characters"]) for run in runs)
    successes = sum(
        bool(run["passed_language"] and run["passed_nonempty"] and not run["error"])
        for run in runs
    )
    quality_passes = sum(
        bool(
            run["passed_language"]
            and run["passed_nonempty"]
            and not run["error"]
            and float(run["simplified_character_error_rate"] or 0) <= 0.20
        )
        for run in runs
    )
    payload = {
        "tested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "same five fixed WAV files; isolated local inference; no microphone, chat, TTS, or history write",
        "engine": engine,
        "cold": {
            "model_load_ms": round(cold_load_ms, 2),
            "first_inference_ms": warmup["asr_ms"],
            "ready_to_first_result_ms": round(cold_ready_to_result_ms, 2),
            "first_result": warmup,
        },
        "warm": {
            **summarize(timings),
            "successes": successes,
            "success_rate": round(successes / len(runs), 4),
            "quality_passes_at_cer_0_20": quality_passes,
            "quality_pass_rate_at_cer_0_20": round(quality_passes / len(runs), 4),
            "returned_zh": sum(run["returned_language"] == "zh" for run in runs),
            "exact_matches": sum(bool(run["exact_match"]) for run in runs),
            "simplified_exact_matches": sum(bool(run["simplified_exact_match"]) for run in runs),
            "aggregate_character_errors": total_errors,
            "aggregate_reference_characters": total_references,
            "aggregate_character_error_rate": round(total_errors / total_references, 4),
            "aggregate_simplified_character_errors": simplified_errors,
            "aggregate_simplified_reference_characters": simplified_references,
            "aggregate_simplified_character_error_rate": round(
                simplified_errors / simplified_references, 4
            ),
        },
        "resources": {
            "measurement": (
                "CPU RSS is process-specific. GPU memory is whole-device used-memory delta because "
                "Windows WDDM does not expose per-process memory through nvidia-smi."
            ),
            "baseline": {
                "cpu_rss_mb": round(sampler.cpu_baseline_mb, 2),
                "gpu_total_used_mb": (
                    round(sampler.gpu_baseline_mb, 2) if sampler.gpu_baseline_mb is not None else None
                ),
            },
            "after_model_load": after_load,
            "final_and_peak": sampler.snapshot(),
        },
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if successes == len(runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
