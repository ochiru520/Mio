"""Companion audio processing; dependencies are explicitly injected."""
from __future__ import annotations

from typing import Any, Callable
from io import BytesIO
from pathlib import Path
from difflib import SequenceMatcher
from array import array
import functools
import json
import math
import re
import struct
import sys
import wave


class AudioProcessingService:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_settings: Callable[..., Any],
                 ) -> None:
        self._dep_settings = dep_settings

    def _split_genie_stream_text(self, text: str, *, max_chars: int = 18) -> list[str]:
        return [segment for segment, _ in self._split_genie_stream_segments(text, max_chars=max_chars)]


    def _split_genie_stream_segments(self,
        text: str, *, max_chars: int = 18
    ) -> list[tuple[str, bool]]:
        """Split speech into synthesis chunks and retain explicit line boundaries."""
        clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not clean:
            return []
        lines = [line.strip() for line in clean.split("\n") if line.strip()]
        result: list[tuple[str, bool]] = []
        for line_index, line in enumerate(lines):
            pieces = re.findall(r"[^。！？!?；;，,、…]+[。！？!?；;，,、…]*", line)
            segments: list[str] = []
            pending = ""
            for piece in pieces or [line]:
                piece = piece.strip()
                if not piece:
                    continue
                if pending and len(pending) + len(piece) <= max_chars:
                    pending += piece
                    continue
                if pending:
                    segments.append(pending)
                    pending = ""
                while len(piece) > max_chars:
                    segments.append(piece[:max_chars])
                    piece = piece[max_chars:]
                pending = piece
            if pending:
                segments.append(pending)
            if not segments:
                segments = [line]
            # Unsupported symbols can leave a punctuation-only tail (for example
            # an emoji followed by ``。``). Keep that tail with the preceding
            # spoken text instead of sending an empty segment to the TTS worker.
            spoken_segments: list[str] = []
            for segment in segments:
                if re.search(r"[0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]", segment):
                    spoken_segments.append(segment)
                elif spoken_segments:
                    spoken_segments[-1] += segment
            segments = spoken_segments or [line]
            for segment_index, segment in enumerate(segments):
                is_line_break = line_index < len(lines) - 1 and segment_index == len(segments) - 1
                result.append((segment, is_line_break))
        return result


    def _streaming_pcm_wav_header(self) -> bytes:
        data_size = 0x7FFFFFF0
        return struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + data_size,
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            32000,
            64000,
            2,
            16,
            b"data",
            data_size,
        )


    def _wav_pcm_payload(self, content: bytes) -> bytes:
        with wave.open(BytesIO(content), "rb") as stream:
            if (
                stream.getnchannels() != 1
                or stream.getsampwidth() != 2
                or stream.getframerate() != 32000
            ):
                raise OSError("Genie 流式音频不是 32 kHz / 16-bit / 单声道。")
            frames = stream.readframes(stream.getnframes())
        if not frames:
            raise OSError("Genie 流式音频没有 PCM 数据。")
        return frames


    def _wav_duration_seconds(self, content: bytes) -> float:
        with wave.open(BytesIO(content), "rb") as stream:
            frame_rate = stream.getframerate()
            if frame_rate <= 0:
                raise OSError("Genie WAV 采样率无效。")
            return stream.getnframes() / frame_rate


    def _short_speech_duration_limit(self, text: str) -> float | None:
        spoken = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]", "", str(text or ""))
        if not spoken or len(spoken) > 10:
            return None
        # Genie occasionally expands a short acknowledgement into a complete
        # reference clip. Naturalized two/three-word replies also need protection.
        # Formal Mio-Genie measurements put normal 5-10 character Japanese
        # acknowledgements around 1.4-3.0 s, while leaked references cluster near
        # 4.5-4.9 s. Keep the first-pass gate below that observed failure band.
        if len(spoken) <= 3:
            return 2.4
        if len(spoken) <= 6:
            return 3.0
        return 3.4


    def _short_speech_recovery_text(self, text: str, language: str) -> str:
        if language in {"ja", "all_ja"}:
            return "うん、わかったよ。続けて話してね。"
        return "好的，我明白了，你继续说吧。"


    def _recovery_speech_duration_limit(self, text: str) -> float:
        """Allow a natural recovery sentence without accepting a full reference clip."""
        spoken = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]", "", str(text or ""))
        # The old fixed four-second gate rejected valid Japanese recovery phrases
        # around five seconds long and turned the whole reply silent.
        return min(10.0, max(4.0, 1.2 + 0.48 * len(spoken)))


    def _wav_acoustic_features(self, source: bytes | Path, *, buckets: int = 96) -> tuple[list[list[float]], float] | None:
        """Return dependency-free rhythm features for conservative reference-leak checks."""
        try:
            handle = BytesIO(source) if isinstance(source, bytes) else source.open("rb")
            with handle:
                with wave.open(handle, "rb") as stream:
                    channels = stream.getnchannels()
                    sample_width = stream.getsampwidth()
                    sample_rate = stream.getframerate()
                    frames = stream.getnframes()
                    if channels <= 0 or sample_width != 2 or sample_rate <= 0 or frames <= 0:
                        return None
                    pcm = array("h")
                    pcm.frombytes(stream.readframes(frames))
        except (OSError, EOFError, wave.Error, ValueError):
            return None
        if sys.byteorder != "little":
            pcm.byteswap()
        if channels > 1:
            mono = [
                sum(int(pcm[index + channel]) for channel in range(channels)) / channels
                for index in range(0, len(pcm) - channels + 1, channels)
            ]
        else:
            mono = [float(value) for value in pcm]
        if len(mono) < max(64, sample_rate // 20):
            return None
        peak = max(abs(value) for value in mono)
        if peak < 32:
            return None
        threshold = max(96.0, peak * 0.035)
        voiced = [index for index, value in enumerate(mono) if abs(value) >= threshold]
        if not voiced:
            return None
        context = max(1, sample_rate // 20)
        start = max(0, voiced[0] - context)
        end = min(len(mono), voiced[-1] + context + 1)
        mono = mono[start:end]
        duration = len(mono) / sample_rate
        if len(mono) < buckets:
            return None
        energy: list[float] = []
        movement: list[float] = []
        crossings: list[float] = []
        for bucket in range(buckets):
            left = bucket * len(mono) // buckets
            right = max(left + 1, (bucket + 1) * len(mono) // buckets)
            chunk = mono[left:right]
            energy.append(math.sqrt(sum(value * value for value in chunk) / len(chunk)))
            if len(chunk) > 1:
                movement.append(sum(abs(chunk[index] - chunk[index - 1]) for index in range(1, len(chunk))) / (len(chunk) - 1))
                crossings.append(sum(1 for index in range(1, len(chunk)) if (chunk[index] >= 0) != (chunk[index - 1] >= 0)) / (len(chunk) - 1))
            else:
                movement.append(0.0)
                crossings.append(0.0)

        def normalize(values: list[float]) -> list[float]:
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            scale = math.sqrt(variance)
            if scale <= 1e-9:
                return [0.0 for _ in values]
            return [(value - mean) / scale for value in values]

        return [normalize(energy), normalize(movement), normalize(crossings)], duration


    def _reference_audio_leak_score_from_features(self,
        generated: tuple[list[list[float]], float] | None,
        original: tuple[list[list[float]], float] | None,
    ) -> float | None:
        if generated is None or original is None:
            return None
        generated_features, generated_duration = generated
        original_features, original_duration = original
        duration_ratio = generated_duration / max(0.001, original_duration)
        if not 0.76 <= duration_ratio <= 1.32:
            return 0.0

        def correlation(left: list[float], right: list[float]) -> float:
            numerator = sum(a * b for a, b in zip(left, right))
            denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
            if denominator <= 1e-9:
                return 1.0 if left == right else 0.0
            return max(-1.0, min(1.0, numerator / denominator))

        correlations = [
            correlation(generated_feature, original_feature)
            for generated_feature, original_feature in zip(generated_features, original_features)
        ]
        return max(0.0, 0.60 * correlations[0] + 0.25 * correlations[1] + 0.15 * correlations[2])


    @functools.lru_cache(maxsize=64)
    def _cached_reference_audio_features(self,
        path_text: str,
        size: int,
        modified_ns: int,
    ) -> tuple[list[list[float]], float] | None:
        del size, modified_ns
        return self._wav_acoustic_features(Path(path_text))


    def _reference_audio_features(self, reference: Path) -> tuple[list[list[float]], float] | None:
        try:
            resolved = reference.resolve()
            stat = resolved.stat()
        except OSError:
            return None
        return self._cached_reference_audio_features(str(resolved), stat.st_size, stat.st_mtime_ns)


    def _reference_audio_leak_score(self, content: bytes, reference: Path) -> float | None:
        """Score near-copying of a reference clip without importing a heavy ASR stack."""
        return self._reference_audio_leak_score_from_features(
            self._wav_acoustic_features(content),
            self._reference_audio_features(reference),
        )


    def _looks_like_reference_audio(self, content: bytes, reference: Path) -> tuple[bool, float | None]:
        score = self._reference_audio_leak_score(content, reference)
        # Real leaked clips from the ONNX runtime are not byte-identical to the
        # conditioning WAV; their dependency-free rhythm score measured 0.86-0.89.
        # A short result must stay clearly below that band to be accepted.
        return bool(score is not None and score >= 0.84), score


    def _reference_audio_candidates(self, config: dict[str, Any], primary: Path) -> list[Path]:
        """Return every runtime/training reference that Genie must never reproduce."""
        candidates: dict[str, Path] = {}

        def add_path(value: object, *, relative_to: Path | None = None) -> None:
            raw = str(value or "").strip()
            if not raw:
                return
            path = Path(raw).expanduser()
            if not path.is_absolute() and relative_to is not None:
                path = relative_to / path
            try:
                path = path.resolve()
                if path.is_file() and path.suffix.lower() == ".wav":
                    candidates[str(path).casefold()] = path
            except OSError:
                return

        add_path(primary)
        add_path(config.get("gpt_sovits_ref_audio"), relative_to=self._dep_settings().companion_dir)

        mapping_path = self._dep_settings().voice_training_dir / "emotion-references.json"
        try:
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            mapping = {}

        def visit(value: object) -> None:
            if isinstance(value, dict):
                add_path(value.get("audio"), relative_to=mapping_path.parent)
                for nested in value.values():
                    if isinstance(nested, (dict, list)):
                        visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(mapping)
        for directory in (
            self._dep_settings().voice_training_dir / "materials" / "prepared" / "wav32k_v2",
            self._dep_settings().voice_training_dir / "materials" / "emotion-references-zh",
            self._dep_settings().companion_dir / "默认参考音频",
        ):
            try:
                for path in directory.glob("*.wav"):
                    add_path(path)
            except OSError:
                continue
        return list(candidates.values())


    def _looks_like_any_reference_audio(self,
        content: bytes,
        references: list[Path],
    ) -> tuple[bool, float | None, Path | None]:
        generated = self._wav_acoustic_features(content)
        best_score: float | None = None
        best_reference: Path | None = None
        for reference in references:
            score = self._reference_audio_leak_score_from_features(
                generated,
                self._reference_audio_features(reference),
            )
            if score is not None and (best_score is None or score > best_score):
                best_score = score
                best_reference = reference
        return bool(best_score is not None and best_score >= 0.84), best_score, best_reference


    def _postprocess_speech_wav(self, content: bytes, volume: int) -> bytes:
        try:
            with wave.open(BytesIO(content), "rb") as source:
                params = source.getparams()
                if params.sampwidth != 2 or params.nchannels not in {1, 2}:
                    return content
                frames = source.readframes(params.nframes)
        except (EOFError, OSError, wave.Error):
            return content

        samples = array("h")
        samples.frombytes(frames)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return content

        peak = max(abs(sample) for sample in samples)
        if peak <= 0:
            return content
        threshold = max(96, int(peak * 0.012))
        active = [index for index, sample in enumerate(samples) if abs(sample) >= threshold]
        if active:
            padding = int(params.framerate * 0.06) * params.nchannels
            start = max(0, active[0] - padding)
            end = min(len(samples), active[-1] + 1 + padding)
            start -= start % params.nchannels
            remainder = end % params.nchannels
            if remainder:
                end = min(len(samples), end + params.nchannels - remainder)
            minimum = int(params.framerate * 0.12) * params.nchannels
            if end - start >= minimum:
                samples = samples[start:end]

        gain = max(0.0, min(1.0, volume / 100.0))
        if gain < 0.999:
            for index, sample in enumerate(samples):
                samples[index] = max(-32768, min(32767, round(sample * gain)))

        if sys.byteorder != "little":
            samples.byteswap()
        output = BytesIO()
        try:
            with wave.open(output, "wb") as target:
                target.setparams(params)
                target.writeframes(samples.tobytes())
        except (OSError, wave.Error):
            return content
        return output.getvalue()


    def _speech_text_for_comparison(self, text: str) -> str:
        return "".join(
            character.casefold()
            for character in str(text or "")
            if character.isalnum() or "\u3040" <= character <= "\u30ff" or "\u3400" <= character <= "\u9fff"
        )


    def _speech_text_similarity(self, expected: str, actual: str) -> float:
        left = self._speech_text_for_comparison(expected)
        right = self._speech_text_for_comparison(actual)
        if not left or not right:
            return 0.0
        sequence_score = SequenceMatcher(None, left, right).ratio()
        left_chars = set(left)
        overlap_score = len(left_chars.intersection(right)) / max(1, len(left_chars))
        return round(max(sequence_score, overlap_score * 0.8), 4)


    def _wav_quality_metrics(self, content: bytes) -> dict[str, float | int]:
        try:
            with wave.open(BytesIO(content), "rb") as source:
                channels = source.getnchannels()
                sample_width = source.getsampwidth()
                sample_rate = source.getframerate()
                frame_count = source.getnframes()
                frames = source.readframes(frame_count)
        except (EOFError, OSError, wave.Error) as exc:
            raise ValueError(f"音频不是有效的 WAV：{exc}") from exc
        if sample_width != 2 or channels not in {1, 2} or sample_rate <= 0:
            raise ValueError("语音质量检查只支持 16 位单声道或双声道 WAV。")

        samples = array("h")
        samples.frombytes(frames)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples or frame_count <= 0:
            raise ValueError("生成的 WAV 没有可播放采样。")

        normalized_square_sum = sum(float(sample) * float(sample) for sample in samples)
        rms = math.sqrt(normalized_square_sum / len(samples)) / 32768.0
        clipping_ratio = sum(1 for sample in samples if abs(sample) >= 32700) / len(samples)

        window_frames = max(1, round(sample_rate * 0.02))
        silent_windows = 0
        total_windows = 0
        for start_frame in range(0, frame_count, window_frames):
            start = start_frame * channels
            end = min(len(samples), (start_frame + window_frames) * channels)
            window = samples[start:end]
            if not window:
                continue
            window_rms = math.sqrt(sum(float(sample) * float(sample) for sample in window) / len(window)) / 32768.0
            total_windows += 1
            if window_rms < 0.0035:
                silent_windows += 1

        return {
            "duration_seconds": round(frame_count / sample_rate, 3),
            "sample_rate": sample_rate,
            "channels": channels,
            "rms": round(rms, 6),
            "silence_ratio": round(silent_windows / max(1, total_windows), 4),
            "clipping_ratio": round(clipping_ratio, 6),
        }
