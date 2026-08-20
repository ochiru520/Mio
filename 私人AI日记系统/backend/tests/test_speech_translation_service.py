from __future__ import annotations

import unittest
from unittest.mock import patch

from app import speech_translation_service as service


class SpeechTranslationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        service.reset_for_tests()

    def tearDown(self) -> None:
        service.reset_for_tests()

    def test_whole_turn_translation_is_cached_by_dedicated_model(self) -> None:
        with patch(
            "app.speech_translation_service._run_async_blocking",
            return_value=("今日は早く休んでね", "translation-provider-model"),
        ) as request:
            first = service.translate(
                "今天早点休息",
                target_language="ja",
                model_id="dedicated-translation-model",
            )
            second = service.translate(
                "今天早点休息",
                target_language="ja",
                model_id="dedicated-translation-model",
            )

        self.assertEqual(first.text, "今日は早く休んでね")
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        request.assert_called_once()

    def test_chinese_output_is_rejected_for_japanese_tts(self) -> None:
        with patch(
            "app.speech_translation_service._run_async_blocking",
            return_value=("今天早点休息", "translation-provider-model"),
        ):
            with self.assertRaises(service.SpeechTranslationError) as raised:
                service.translate("今天早点休息", target_language="ja")

        self.assertEqual(raised.exception.category, "invalid_translation")
        self.assertEqual(service.status()["last_error_category"], "invalid_translation")

    def test_authentication_failure_is_reported_separately(self) -> None:
        class AuthenticationFailure(RuntimeError):
            http_status = 401

        with patch(
            "app.speech_translation_service._run_async_blocking",
            side_effect=AuthenticationFailure("HTTP 401"),
        ):
            with self.assertRaises(service.SpeechTranslationError) as raised:
                service.translate("今天早点休息", target_language="ja")

        self.assertEqual(raised.exception.category, "authentication")
        self.assertEqual(service.status()["last_error_category"], "authentication")

    def test_timeout_failure_is_reported_separately(self) -> None:
        with patch(
            "app.speech_translation_service._run_async_blocking",
            side_effect=TimeoutError("deadline"),
        ):
            with self.assertRaises(service.SpeechTranslationError) as raised:
                service.translate("今天早点休息", target_language="ja")

        self.assertEqual(raised.exception.category, "timeout")
        self.assertEqual(service.status()["last_error_category"], "timeout")


if __name__ == "__main__":
    unittest.main()
