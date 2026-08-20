from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.call_session_service import CallSessionConflict, CallSessionManager
from app import system_audio_service
from app.routes import companion as companion_route


class CallSessionManagerTests(unittest.TestCase):
    def test_new_session_invalidates_old_turn(self) -> None:
        manager = CallSessionManager()
        first = manager.start()
        old_turn = manager.begin_turn(first["call_session_id"], 1)

        second = manager.start()

        self.assertNotEqual(first["call_session_id"], second["call_session_id"])
        self.assertFalse(manager.is_current(old_turn))
        with self.assertRaisesRegex(CallSessionConflict, "已结束或轮次已被替换"):
            manager.require_current(old_turn)

    def test_stale_stop_cannot_close_new_session(self) -> None:
        manager = CallSessionManager()
        first = manager.start()
        second = manager.start()

        self.assertFalse(manager.stop(first["call_session_id"]))
        self.assertTrue(manager.is_active_session(second["call_session_id"]))

    def test_voice_ack_only_updates_matching_response(self) -> None:
        manager = CallSessionManager()
        session = manager.start()
        turn = manager.begin_turn(session["call_session_id"], 1)

        self.assertFalse(manager.record_voice_started("old-response", {"mode": "stream"}))
        self.assertEqual(manager.status()["stage"], "asr")
        self.assertTrue(manager.record_voice_started(turn.response_id, {"mode": "stream"}))
        self.assertEqual(manager.status()["stage"], "speaking")
        self.assertFalse(manager.record_voice_ended("old-response", {"reason": "finished"}))
        self.assertEqual(manager.status()["stage"], "speaking")
        self.assertTrue(manager.record_voice_ended(turn.response_id, {"reason": "finished"}))
        self.assertEqual(manager.status()["stage"], "listening")

    def test_interrupt_only_accepts_current_response(self) -> None:
        manager = CallSessionManager()
        session = manager.start()
        turn = manager.begin_turn(session["call_session_id"], 1)
        manager.update_turn(turn, "awaiting_voice", reply="这段声音正在播放")

        self.assertEqual(manager.interrupt(session["call_session_id"], "old-response"), "")
        self.assertEqual(manager.status()["stage"], "awaiting_voice")
        self.assertEqual(
            manager.interrupt(session["call_session_id"], turn.response_id),
            turn.response_id,
        )
        self.assertEqual(manager.echo_reference(turn), "这段声音正在播放")


class CallAsrSelectionTests(unittest.TestCase):
    def test_auto_engine_resolves_to_local_whisper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            snapshot = root / "cache" / "faster-whisper" / "models--Systran--faster-whisper-large-v3-turbo" / "snapshots" / "revision"
            snapshot.mkdir(parents=True)
            (snapshot / "model.bin").write_bytes(b"model")
            (snapshot / "config.json").write_text("{}", encoding="utf-8")
            with patch(
                "app.system_audio_service.settings",
                SimpleNamespace(voice_training_dir=root),
            ):
                launch = system_audio_service._worker_launch(
                    {"asr_engine": "auto", "asr_model": "large-v3-turbo"},
                    root / "cache" / "faster-whisper",
                )

        self.assertIsNotNone(launch)
        self.assertEqual(launch["engine"], "whisper")
        self.assertEqual(launch["model_label"], "large-v3-turbo")
        self.assertEqual(launch["requested_model_label"], "large-v3-turbo")
        self.assertEqual(launch["fallback_reason"], "")
        self.assertEqual(Path(launch["model"]), snapshot)

    def test_missing_large_model_falls_back_to_complete_local_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            snapshot = root / "cache" / "faster-whisper" / "models--Systran--faster-whisper-base" / "snapshots" / "revision"
            snapshot.mkdir(parents=True)
            (snapshot / "model.bin").write_bytes(b"model")
            (snapshot / "config.json").write_text("{}", encoding="utf-8")
            with patch(
                "app.system_audio_service.settings",
                SimpleNamespace(voice_training_dir=root),
            ):
                launch = system_audio_service._worker_launch(
                    {"asr_engine": "auto", "asr_model": "large-v3-turbo"},
                    root / "cache" / "faster-whisper",
                )

        self.assertIsNotNone(launch)
        self.assertEqual(launch["model_label"], "base")
        self.assertEqual(launch["requested_model_label"], "large-v3-turbo")
        self.assertIn("已使用本机完整的 base 模型", launch["fallback_reason"])
        self.assertEqual(Path(launch["model"]), snapshot)

    def test_sensevoice_selection_requires_complete_local_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_root = root / "cache" / "modelscope" / "models"
            sensevoice = model_root / "iic--SenseVoiceSmall" / "snapshots" / "master"
            vad = model_root / "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch" / "snapshots" / "master"
            for directory in (sensevoice, vad):
                directory.mkdir(parents=True)
                (directory / "config.yaml").write_text("model: test", encoding="utf-8")
                (directory / "model.pt").write_bytes(b"model")
            (sensevoice / "chn_jpn_yue_eng_ko_spectok.bpe.model").write_bytes(b"tokenizer")
            with patch(
                "app.system_audio_service.settings",
                SimpleNamespace(voice_training_dir=root),
            ):
                launch = system_audio_service._worker_launch(
                    {"asr_engine": "sensevoice"},
                    root / "cache" / "faster-whisper",
                )

        self.assertIsNotNone(launch)
        self.assertEqual(launch["engine"], "sensevoice")
        self.assertEqual(Path(launch["model"]), sensevoice)
        self.assertEqual(Path(launch["vad_model"]), vad)

    def test_stale_resource_release_does_not_touch_new_call_owner(self) -> None:
        previous_owner = companion_route._pet_call_resource_owner
        companion_route._pet_call_resource_owner = "new-session"
        try:
            with (
                patch("app.routes.companion.companion_service.window_observer.stop") as stop_observer,
                patch("app.routes.companion.system_audio_service.stop") as stop_audio,
                patch("app.routes.companion.system_audio_service.start") as start_audio,
            ):
                companion_route._release_call_resources_sync("old-session")
        finally:
            companion_route._pet_call_resource_owner = previous_owner

        stop_observer.assert_not_called()
        stop_audio.assert_not_called()
        start_audio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
