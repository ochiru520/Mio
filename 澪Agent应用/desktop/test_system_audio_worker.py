from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


WORKER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "system_audio_worker.py"
SPEC = importlib.util.spec_from_file_location("mio_system_audio_worker", WORKER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法加载系统声音 worker：{WORKER_PATH}")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class PhoneTranscriptGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = {"voiced_ms": 1200.0, "trimmed_duration_ms": 1800.0}

    def test_chinese_transcript_passes_script_gate(self) -> None:
        reason, diagnostics = worker.phone_text_rejection(
            "你好，这是中文电话测试。",
            self.audio,
            "zh",
        )

        self.assertEqual(reason, "")
        self.assertGreater(diagnostics["han_ratio"], 0.9)

    def test_english_drift_is_rejected_for_chinese_call(self) -> None:
        reason, diagnostics = worker.phone_text_rejection(
            "hello this is an unrelated subtitle",
            self.audio,
            "zh",
        )

        self.assertEqual(reason, "non_chinese_drift")
        self.assertEqual(diagnostics["han_count"], 0)

    def test_short_boilerplate_hallucination_is_rejected(self) -> None:
        reason, diagnostics = worker.phone_text_rejection(
            "感谢观看，请点赞订阅并转发",
            self.audio,
            "zh",
        )

        self.assertEqual(reason, "boilerplate_hallucination")
        self.assertGreaterEqual(diagnostics["hallucination_marker_count"], 2)

    def test_long_boilerplate_hallucination_is_rejected(self) -> None:
        reason, _diagnostics = worker.phone_text_rejection(
            "请不吝点赞订阅转发打赏支持明镜与点点栏目",
            {"voiced_ms": 6500.0, "trimmed_duration_ms": 9000.0},
            "zh",
        )

        self.assertEqual(reason, "boilerplate_hallucination")

    def test_whisper_instruction_leak_is_rejected(self) -> None:
        reason, diagnostics = worker.phone_text_rejection(
            "请准确转写数字。",
            self.audio,
            "zh",
        )

        self.assertEqual(reason, "prompt_leak_hallucination")
        self.assertEqual(diagnostics["prompt_leak_pattern"], "请准确转写数字")


if __name__ == "__main__":
    unittest.main()
