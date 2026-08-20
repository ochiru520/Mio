from __future__ import annotations

from array import array
from types import SimpleNamespace
import unittest

from scripts import system_audio_worker as worker


def _segment(**overrides: float) -> SimpleNamespace:
    values = {
        "start": 0.0,
        "end": 0.6,
        "avg_logprob": -0.25,
        "no_speech_prob": 0.05,
        "compression_ratio": 1.1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PhoneAsrGateTests(unittest.TestCase):
    def test_silence_is_rejected_before_transcription(self) -> None:
        _trimmed, stats, reason = worker.prepare_phone_samples(array("f", [0.0] * 16000), 16000)

        self.assertEqual(reason, "low_energy")
        self.assertEqual(stats["voiced_ms"], 0)

    def test_short_voiced_clip_is_kept_with_edge_padding(self) -> None:
        samples = array("f", [0.0] * 3200 + [0.05] * 4800 + [0.0] * 9600)

        trimmed, stats, reason = worker.prepare_phone_samples(samples, 16000)

        self.assertEqual(reason, "")
        self.assertGreater(len(trimmed), 4800)
        self.assertEqual(stats["voiced_ms"], 300)
        self.assertGreaterEqual(stats["longest_voiced_ms"], 300)

    def test_short_hello_is_accepted(self) -> None:
        reason, _diagnostics = worker.phone_transcript_rejection(
            "你好",
            [_segment(avg_logprob=-0.78, no_speech_prob=0.0)],
            {"voiced_ms": 700.0, "trimmed_duration_ms": 900.0},
        )

        self.assertEqual(reason, "")

    def test_video_outro_hallucination_is_rejected(self) -> None:
        reason, diagnostics = worker.phone_transcript_rejection(
            "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
            [_segment()],
            {"voiced_ms": 300.0, "trimmed_duration_ms": 900.0},
        )

        self.assertIn(reason, {"implausible_transcript_rate", "boilerplate_hallucination"})
        self.assertGreaterEqual(diagnostics["hallucination_marker_count"], 4)

    def test_long_video_outro_hallucination_is_rejected(self) -> None:
        reason, _diagnostics = worker.phone_transcript_rejection(
            "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
            [_segment(end=8.0)],
            {"voiced_ms": 6200.0, "trimmed_duration_ms": 8000.0},
        )

        self.assertEqual(reason, "boilerplate_hallucination")

    def test_prompt_leak_hallucination_is_rejected(self) -> None:
        reason, diagnostics = worker.phone_transcript_rejection(
            "请准确转写数字。",
            [_segment()],
            {"voiced_ms": 700.0, "trimmed_duration_ms": 1000.0},
        )

        self.assertEqual(reason, "prompt_leak_hallucination")
        self.assertEqual(diagnostics["prompt_leak_pattern"], "请准确转写数字")

    def test_nonlexical_noise_transcript_is_rejected(self) -> None:
        reason, _diagnostics = worker.phone_transcript_rejection(
            "♪♪♪♪♪",
            [_segment(avg_logprob=-1.1, no_speech_prob=0.0)],
            {"voiced_ms": 1000.0, "trimmed_duration_ms": 1000.0},
        )

        self.assertEqual(reason, "nonlexical_transcript")


if __name__ == "__main__":
    unittest.main()
