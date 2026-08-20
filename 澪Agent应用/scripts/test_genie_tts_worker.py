from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


WORKER = Path(__file__).with_name("genie_tts_worker.py")
SPEC = importlib.util.spec_from_file_location("mio_genie_tts_worker", WORKER)
assert SPEC is not None and SPEC.loader is not None
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class GenieWorkerTextTests(unittest.TestCase):
    def test_chinese_g2p_text_removes_unsupported_symbols(self) -> None:
        cleaned = worker._safe_g2p_text("你好\u200b🙂〔测试〕", "Chinese", fallback="你好。")
        self.assertEqual(cleaned, "你好测试")

    def test_punctuation_only_text_uses_language_fallback(self) -> None:
        self.assertEqual(worker._safe_g2p_text("……！！！", "Chinese", fallback="你好。"), "你好。")
        self.assertEqual(worker._safe_g2p_text("……", "Japanese", fallback="こんにちは。"), "こんにちは。")

    def test_japanese_text_is_preserved(self) -> None:
        text = "つまらないものですが、ありがとうございます。"
        self.assertEqual(worker._safe_g2p_text(text, "Japanese", fallback="こんにちは。"), text)

    def test_reference_prompt_g2p_failure_retries_with_safe_text(self) -> None:
        genie = mock.Mock()
        genie.set_reference_audio.side_effect = [IndexError("tone"), None]

        retried = worker._set_reference_audio_safely(
            genie,
            "mio",
            Path("reference.wav"),
            "异常提示文本",
            "Chinese",
            "你好。",
        )

        self.assertTrue(retried)
        self.assertEqual(genie.set_reference_audio.call_count, 2)
        self.assertEqual(genie.set_reference_audio.call_args_list[-1].args[-2:], ("你好。", "Chinese"))


if __name__ == "__main__":
    unittest.main()
