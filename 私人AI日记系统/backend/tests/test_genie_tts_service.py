from __future__ import annotations

from io import BytesIO
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import wave

from app import genie_tts_service


def _wav_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(32000)
        stream.writeframes(b"\x00\x00" * 3200)
    return output.getvalue()


class GenieTtsServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        genie_tts_service.stop_worker()
        genie_tts_service._file_digest.cache_clear()

    def test_existing_character_is_reused_by_weight_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            models = root / "models" / "genie"
            character = models / "named-character"
            character.mkdir(parents=True)
            for name in genie_tts_service.REQUIRED_CHARACTER_FILES:
                (character / name).write_bytes(b"model")
            gpt = root / "voice.ckpt"
            sovits = root / "voice.pth"
            gpt.write_bytes(b"gpt")
            sovits.write_bytes(b"sovits")
            (character / "mio-genie-v2.json").write_text(json.dumps({
                "gpt_sha256": hashlib.sha256(b"gpt").hexdigest(),
                "sovits_sha256": hashlib.sha256(b"sovits").hexdigest(),
            }), encoding="utf-8")
            paths = {
                "root": root,
                "models": models,
                "default_model": models / "default-v2",
            }

            with mock.patch("app.genie_tts_service._paths", return_value=paths):
                selected = genie_tts_service._resolve_character_model({
                    "gpt_sovits_gpt_weights": str(gpt),
                    "gpt_sovits_sovits_weights": str(sovits),
                })

            self.assertEqual(selected, character)

    def test_runtime_status_reports_the_actual_mio_onnx_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "models" / "genie" / "mio-v1"
            model.mkdir(parents=True)
            for name in genie_tts_service.REQUIRED_CHARACTER_FILES:
                (model / name).write_bytes(b"model")
            paths = {
                "python": root / ".genie-env" / "Scripts" / "python.exe",
                "site_packages": root / ".genie-env" / "Lib" / "site-packages",
                "data": root / "GenieData",
                "models": root / "models" / "genie",
                "mio_model": model,
                "worker": root / "genie_tts_worker.py",
            }
            with mock.patch.object(genie_tts_service, "_paths", return_value=paths):
                status = genie_tts_service.runtime_status()

        self.assertTrue(status["model_ready"])
        self.assertEqual(status["model_dir"], str(model))
        self.assertIn("Mio", status["model_source"])

    def test_synthesize_writes_and_validates_standard_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(_wav_bytes())
            character = root / "character"
            character.mkdir()
            for name in genie_tts_service.REQUIRED_CHARACTER_FILES:
                (character / name).write_bytes(b"model")

            def fake_request(payload, *, timeout, idle_seconds):
                del timeout, idle_seconds
                Path(payload["output_path"]).write_bytes(_wav_bytes())
                return {"ok": True, "first_audio_ms": 120.0, "total_ms": 300.0, "duration_seconds": 0.1}

            with (
                mock.patch("app.genie_tts_service._resolve_character_model", return_value=character),
                mock.patch("app.genie_tts_service._request", side_effect=fake_request),
            ):
                content = genie_tts_service.synthesize_wav("你好", {
                    "gpt_sovits_ref_audio": str(reference),
                    "gpt_sovits_prompt_text": "参考文本",
                    "gpt_sovits_prompt_language": "zh",
                    "gpt_sovits_text_language": "zh",
                })

            self.assertTrue(content.startswith(b"RIFF"))
            self.assertEqual(genie_tts_service.runtime_status()["last_metrics"]["first_audio_ms"], 120.0)

    def test_idle_worker_schedules_memory_reclamation(self) -> None:
        timer = mock.Mock()
        with mock.patch("app.genie_tts_service.threading.Timer", return_value=timer) as timer_type:
            genie_tts_service._schedule_idle_shutdown()

        timer_type.assert_called_once_with(
            genie_tts_service.GENIE_IDLE_SECONDS,
            genie_tts_service._stop_if_idle,
        )
        timer.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
