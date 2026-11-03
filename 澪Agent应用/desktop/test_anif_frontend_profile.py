"""CPU-only checks for optional Anif timing and fixed public frontend warmup."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "anif_bridge_profile_tests", Path(__file__).resolve().parents[1] / "scripts/aniflive_experimental_server.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class SpeechSegmentTests(unittest.TestCase):
    def pipeline(self, expansion=3):
        calls = []
        def phones(text, *_args, **_kwargs):
            calls.append(text)
            return [1] * (sum(c.isalnum() for c in text) * expansion), object(), text
        engine = SimpleNamespace(version='v2ProPlus', sovits_max_text_len=100,
            get_phones_and_bert=phones, model_gpt_enc=SimpleNamespace(input_max_shapes={
                'phoneme_ids': (1, 256), 'bert_feature': (1, 1024, 256)}))
        streamer = SimpleNamespace(reference_bank=SimpleNamespace(identity=SimpleNamespace(phones=[1] * 170)))
        return engine, streamer, calls

    def test_long_chinese_japanese_and_digits_fit_actual_profiles_without_lost_text(self):
        for text in ('测试语音播放。' * 30, '今日はあなたと一緒に画面を見ながらゆっくりお話ししたいです。' * 4,
                     '1234567890123456789012345678901234567890。'):
            with self.subTest(language=text[:2]):
                engine, streamer, _ = self.pipeline(expansion=7)
                pieces = bridge.speech_segments((text,), 'ja', engine, streamer, threading.Event())
                self.assertEqual(''.join(pieces), text)
                self.assertGreater(len(pieces), 1)
                self.assertTrue(all(0 < sum(c.isalnum() for c in part) * 7 <= 86 for part in pieces))

    def test_short_text_keeps_single_segment_and_content(self):
        engine, streamer, calls = self.pipeline()
        text = 'こんにちは、澪です。'
        self.assertEqual(bridge.speech_segments((text,), 'ja', engine, streamer, threading.Event()), (text,))
        self.assertEqual(calls, [text])

    def test_punctuation_only_and_cancelled_never_start_frontend(self):
        engine, streamer, calls = self.pipeline()
        with self.assertRaises(ValueError):
            bridge.speech_segments(('……',), 'zh', engine, streamer, threading.Event())
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(InterruptedError):
            bridge.speech_segments(('你好。',), 'zh', engine, streamer, cancelled)
        self.assertEqual(calls, [])

    def test_input_that_cannot_fit_is_not_silently_dropped(self):
        engine, streamer, _ = self.pipeline(expansion=200)
        with self.assertRaises(ValueError):
            bridge.speech_segments(('好。',), 'zh', engine, streamer, threading.Event())


class FrontendProfileTests(unittest.TestCase):
    def fake_pipeline(self):
        language = SimpleNamespace(text_normalize=lambda text: text.strip(), g2p=lambda text: ([1], [1]))
        original_normalize = language.text_normalize
        original_g2p = language.g2p
        def clean(text, lang, version=None):
            return (*language.g2p(language.text_normalize(text)), text)
        runtime = SimpleNamespace(clean_text=clean, LangSegmenter=SimpleNamespace(getTexts=lambda text: [text]))
        engine = SimpleNamespace(version="v2ProPlus", get_bert_feature=lambda *_: object())
        def phones(text, language, version, default_lang=None):
            runtime.LangSegmenter.getTexts(text)
            cleaned = runtime.clean_text(text, "en", version)
            return cleaned[0], engine.get_bert_feature(text), cleaned[2]
        engine.get_phones_and_bert = phones
        return engine, runtime, language, original_normalize, original_g2p

    def test_profile_preserves_returns_and_does_not_retain_text(self):
        engine, runtime, language, normalize, g2p = self.fake_pipeline()
        profiler = bridge.FrontendProfiler(engine, runtime)
        profiler.install()
        private = " private-file-name.wav "
        with patch.object(bridge.importlib, "import_module", return_value=language):
            with profiler.capture("synthesis") as record:
                result = engine.get_phones_and_bert(private, "all_zh", engine.version)
        self.assertEqual(result[0], [1])
        self.assertEqual(result[2], private)
        self.assertNotIn("private-file-name", json.dumps(record))
        self.assertIs(language.text_normalize, normalize)
        self.assertIs(language.g2p, g2p)
        self.assertEqual(record["stages"]["en_g2p"]["calls"], 1)
        self.assertIn("frontend_import_english", record["stages"])
        self.assertIn("language_segmentation", record["stages"])
        self.assertIn("bert_feature_host_wall", record["stages"])

    def test_original_functions_restored_when_cleaner_raises(self):
        engine, runtime, language, normalize, g2p = self.fake_pipeline()
        runtime.clean_text = lambda *_: (_ for _ in ()).throw(ValueError("input omitted"))
        profiler = bridge.FrontendProfiler(engine, runtime)
        profiler.install()
        with patch.object(bridge.importlib, "import_module", return_value=language):
            with self.assertRaises(ValueError):
                with profiler.capture("synthesis"):
                    runtime.clean_text("secret", "en", "v2ProPlus")
        self.assertIs(language.text_normalize, normalize)
        self.assertIs(language.g2p, g2p)
        self.assertIsNone(profiler.local.record)

    def test_inactive_profiler_does_not_force_english_import(self):
        engine, runtime, *_ = self.fake_pipeline()
        profiler = bridge.FrontendProfiler(engine, runtime)
        profiler.install()
        with patch.object(bridge.importlib, "import_module") as imports:
            runtime.clean_text("hello", "en", "v2ProPlus")
        imports.assert_not_called()

    def test_sink_bounded_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "measurements.jsonl"
            sink = bridge.TimingSink(path)
            for _ in range(bridge.TimingSink.MAX_RECORDS + 5):
                sink.write({"phase": "synthesis", "first_pcm_seconds": .2})
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 256)
            self.assertEqual(rows[-1]["ordinal"], 256)
            with self.assertRaises(FileExistsError):
                bridge.TimingSink(path)

    def test_warmup_fixed_samples_and_no_result_cache(self):
        calls = []
        engine = SimpleNamespace(version="v2ProPlus")
        def run(text, language, version, default_lang=None):
            calls.append((text, language, version, default_lang))
            return [1], object(), text
        engine.get_phones_and_bert = run
        bridge.warm_text_frontends(engine)
        self.assertEqual(len(calls), 2)
        self.assertEqual([row[1] for row in calls], ["all_zh", "all_ja"])
        self.assertIn(".wav", calls[0][0])
        self.assertEqual(set(vars(engine)), {"version", "get_phones_and_bert"})

    def test_warmup_failure_is_not_hidden(self):
        engine = SimpleNamespace(version="v2ProPlus", get_phones_and_bert=lambda *_args, **_kwargs: ([], None, ""))
        with self.assertRaises(RuntimeError):
            bridge.warm_text_frontends(engine)

    def test_generation_rejects_nonfinite_and_out_of_range_before_inference(self):
        for temperature in (float("nan"), float("inf"), 0., 2.1):
            with self.subTest(temperature=temperature), self.assertRaises(ValueError):
                bridge.generation_settings(temperature=temperature)
        for noise in (float("nan"), float("-inf"), -.1, 1.1):
            with self.subTest(noise=noise), self.assertRaises(ValueError):
                bridge.generation_settings(noise_scale=noise)


if __name__ == "__main__":
    unittest.main()
