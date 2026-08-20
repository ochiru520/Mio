from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import unittest
import wave
import zipfile
import sys
import threading
import httpx
from array import array
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import (
    call_session_service,
    companion_service,
    db,
    pet_event_service,
    screen_behavior_service,
    screen_observation_service,
    so_vits_svc_service,
    speech_translation_service,
    system_audio_service,
)
from app.desktop_pet import MioDesktopPet
from app.chat_service import ChatResult
from app.routes.companion import (
    CompanionCallTurnRequest,
    CompanionChatSettingsRequest,
    CompanionChatRequest,
    CompanionChatWindowStateRequest,
    ScreenObservationRequest,
    SpeechRequest,
    SpriteSheetRequest,
    VoiceReferenceRequest,
    companion_chat,
    companion_chat_window_state,
    companion_chat_settings,
    companion_chat_history,
    companion_agent_show,
    companion_feed,
    companion_start,
    companion_stop,
    companion_screen_preview,
    companion_screen_analyze,
    companion_game_analyze,
    companion_sprite,
    companion_sprite_sheet_save,
    companion_voice_audio,
    companion_voice_reference,
    companion_voice_runtime,
    companion_voice_test,
    companion_call_interrupt,
    companion_call_start,
    companion_call_stop,
    companion_call_turn,
    get_companion_chat_settings,
)
from app.routes import companion as companion_routes


def _png_data_url(color: str = "#7fa4b0") -> str:
    buffer = BytesIO()
    Image.new("RGB", (24, 24), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _tone_wav_bytes(*, seconds: float = 1.0, amplitude: int = 5000) -> bytes:
    sample_rate = 16000
    sample_count = max(1, round(sample_rate * seconds))
    samples = array("h", [amplitude if index % 32 < 16 else -amplitude for index in range(sample_count)])
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples.tobytes())
    return output.getvalue()


def _genie_wav_bytes(*, frames: int = 3200) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(32000)
        stream.writeframes(b"\x01\x00" * frames)
    return output.getvalue()


class StubScreenCapture:
    def __init__(self, color: str = "navy") -> None:
        self.color = color

    def capture(self, *, all_screens: bool = False) -> Image.Image:
        return Image.new("RGB", (640, 360), self.color)

    def release(self) -> None:
        return None


class CompanionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        active_call = call_session_service.manager.status()
        if active_call.get("active"):
            call_session_service.manager.stop(str(active_call.get("call_session_id") or ""))
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_paths = {
            "companion_dir": companion_service.settings.companion_dir,
            "companion_config_path": companion_service.settings.companion_config_path,
            "companion_avatar_path": companion_service.settings.companion_avatar_path,
            "companion_sprite_dir": companion_service.settings.companion_sprite_dir,
            "companion_game_preview_path": companion_service.settings.companion_game_preview_path,
            "agent_frontend_dir": companion_service.settings.agent_frontend_dir,
            "voice_training_dir": companion_service.settings.voice_training_dir,
            "db_path": db.settings.db_path,
        }
        root = Path(self.temp_dir.name)
        object.__setattr__(companion_service.settings, "companion_dir", root)
        object.__setattr__(companion_service.settings, "companion_config_path", root / "settings.json")
        object.__setattr__(companion_service.settings, "companion_avatar_path", root / "avatar.png")
        object.__setattr__(companion_service.settings, "companion_sprite_dir", root / "sprites")
        object.__setattr__(companion_service.settings, "companion_game_preview_path", root / "preview.jpg")
        object.__setattr__(companion_service.settings, "agent_frontend_dir", root / "frontend")
        object.__setattr__(companion_service.settings, "voice_training_dir", root / "voice-training")
        object.__setattr__(db.settings, "db_path", root / "test.db")
        db.init_db()
        self.original_capture_preference = companion_service.window_observer._prefer_native
        companion_service.window_observer._prefer_native = False
        companion_service.window_observer._native_capture = None
        companion_service.window_observer._screen_capture = StubScreenCapture()
        screen_observation_service._last_analyzed_frame_id = 0
        screen_observation_service._last_analysis_monotonic = 0.0
        screen_observation_service._last_analyzed_at = ""
        screen_observation_service._last_reply = ""
        screen_observation_service._last_error = ""
        screen_observation_service._last_model = ""
        screen_observation_service._last_cost_yuan = None
        screen_observation_service._capture_only = False
        screen_observation_service._in_progress = False
        screen_observation_service._session_request_count = 0
        screen_observation_service._session_attempt_count = 0
        screen_observation_service._session_failure_count = 0
        screen_observation_service._session_cost_yuan = 0.0
        screen_observation_service._session_unknown_cost_count = 0
        screen_observation_service._cloud_profile_health.clear()
        companion_service._gpt_sovits_last_error = ""
        companion_service._speech_owner_generation = 0
        companion_service._speech_owner_priority = 0
        companion_service._speech_owner_source = ""
        speech_translation_service.reset_for_tests()
        companion_service._speech_translation_last_error = ""
        companion_service._speech_translation_last_model = ""
        # Existing streaming/API tests exercise the legacy HTTP runtime explicitly.
        # Genie has separate coverage and remains the product default.
        companion_service.save_config({"local_voice_runtime": "gpt_sovits"})
        companion_service.set_pet_activity("idle")

    def tearDown(self) -> None:
        companion_service.game_observer.stop()
        companion_service.window_observer._prefer_native = self.original_capture_preference
        for name, value in self.original_paths.items():
            object.__setattr__(companion_service.settings, name, value)
        self.temp_dir.cleanup()

    def test_settings_are_normalized_and_saved(self) -> None:
        saved = companion_service.save_config({
            "voice_volume": -1,
            "voice_engine": "system",
            "chat_model_id": "deepseek-v4-flash",
            "chat_reasoning_level": "high",
            "pet_chat_model_id": "gpt-5.6-sol",
            "pet_chat_reasoning_level": "medium",
            "voice_startup_enabled": True,
            "qq_startup_enabled": True,
            "speak_proactive": True,
            "qq_voice_mode": "always",
            "screen_daily_cost_limit_yuan": 0,
            "screen_request_timeout_seconds": 999,
            "pet_renderer": "live2d",
            "live2d_model_id": "hiyori",
            "live2d_scale": 9,
            "live2d_vertical_offset": -2,
            "live2d_click_through_locked": True,
            "live2d_speech_bubble_enabled": False,
            "live2d_expression_slots": {"hiyori": {"cheerful": "Happy"}},
            "live2d_keep_visible": True,
            "gpt_sovits_translate_to_japanese": True,
            "speech_translation_model_id": "deepseek-v4-flash",
        })
        self.assertEqual(saved["voice_volume"], 0)
        self.assertEqual(saved["voice_engine"], "gpt_sovits")
        self.assertEqual(saved["chat_model_id"], "deepseek-v4-flash")
        self.assertEqual(saved["chat_reasoning_level"], "high")
        self.assertEqual(saved["pet_chat_model_id"], "gpt-5.6-sol")
        self.assertEqual(saved["pet_chat_reasoning_level"], "medium")
        self.assertTrue(saved["voice_startup_enabled"])
        self.assertTrue(saved["qq_startup_enabled"])
        self.assertTrue(companion_service.load_config()["speak_proactive"])
        self.assertEqual(companion_service.load_config()["qq_voice_mode"], "always")
        self.assertEqual(saved["screen_daily_cost_limit_yuan"], 0.1)
        self.assertEqual(saved["screen_request_timeout_seconds"], 60)
        self.assertTrue(saved["speak_game_observations"])
        self.assertEqual(saved["pet_renderer"], "live2d")
        self.assertEqual(saved["live2d_model_id"], "hiyori")
        self.assertEqual(saved["live2d_scale"], 1.55)
        self.assertEqual(saved["live2d_vertical_offset"], -0.35)
        self.assertTrue(saved["live2d_click_through_locked"])
        self.assertFalse(saved["live2d_speech_bubble_enabled"])
        self.assertEqual(saved["live2d_expression_slots"], {"hiyori": {"cheerful": "Happy"}})
        self.assertTrue(saved["live2d_keep_visible"])
        self.assertTrue(saved["gpt_sovits_translate_to_japanese"])
        self.assertEqual(saved["speech_translation_model_id"], "deepseek-v4-flash")

    def test_screen_request_timeout_defaults_to_twenty_five_seconds(self) -> None:
        saved = companion_service.save_config({})

        self.assertEqual(saved["screen_request_timeout_seconds"], 25)

    def test_legacy_screen_request_timeout_is_migrated_once(self) -> None:
        companion_service.settings.companion_config_path.write_text(
            json.dumps({"screen_request_timeout_seconds": 12}),
            encoding="utf-8",
        )

        migrated = companion_service.load_config()
        self.assertEqual(migrated["screen_request_timeout_seconds"], 25)
        self.assertEqual(migrated["config_schema_version"], 2)

        saved = companion_service.save_config({"screen_request_timeout_seconds": 12})
        self.assertEqual(saved["screen_request_timeout_seconds"], 12)
        self.assertEqual(saved["config_schema_version"], 2)

    def test_versioned_custom_screen_timeout_is_preserved(self) -> None:
        companion_service.settings.companion_config_path.write_text(
            json.dumps({
                "config_schema_version": 2,
                "screen_request_timeout_seconds": 12,
            }),
            encoding="utf-8",
        )

        loaded = companion_service.load_config()
        self.assertEqual(loaded["screen_request_timeout_seconds"], 12)

    def test_phone_input_language_defaults_to_chinese(self) -> None:
        self.assertEqual(companion_service.load_config()["pet_call_input_language"], "zh")

    def test_legacy_phone_auto_language_is_normalized_to_chinese(self) -> None:
        normalized = companion_service.load_normalized_config({
            "pet_call_input_language": "auto",
        })

        self.assertEqual(normalized["pet_call_input_language"], "zh")

    def test_chat_settings_persist_voice_language(self) -> None:
        updated = asyncio.run(companion_chat_settings(CompanionChatSettingsRequest(
            model_id="deepseek-v4-flash",
            reasoning_level="high",
            voice_language="ja",
        )))
        loaded = asyncio.run(get_companion_chat_settings())

        self.assertEqual(updated["voice_language"], "ja")
        self.assertEqual(loaded, {
            "model_id": "deepseek-v4-flash",
            "reasoning_level": "high",
            "voice_language": "ja",
        })

        legacy_update = asyncio.run(companion_chat_settings(CompanionChatSettingsRequest(
            model_id="auto",
            reasoning_level="auto",
        )))
        self.assertEqual(legacy_update["voice_language"], "ja")

    def test_custom_live2d_model_can_be_imported_selected_and_deleted(self) -> None:
        root = Path(self.temp_dir.name)
        source = root / "sample-live2d"
        source.mkdir()
        (source / "sample.moc3").write_bytes(b"moc3")
        (source / "texture.png").write_bytes(b"texture")
        (source / "preview.png").write_bytes(b"preview")
        (source / "sample.model3.json").write_text(
            json.dumps({
                "Version": 3,
                "FileReferences": {
                    "Moc": "sample.moc3",
                    "Textures": ["texture.png"],
                    "Motions": {"Idle": [], "TapBody": []},
                },
            }),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"MIO_DESKTOP_STATE_DIR": str(root / "desktop-state")}):
            model = companion_service.import_live2d_model_directory(source, "测试模型")
            self.assertTrue(model["imported"])
            self.assertEqual(model["name"], "测试模型")
            self.assertTrue(model["preview_url"].endswith("/preview"))
            self.assertIn(model["id"], {item["id"] for item in companion_service.available_live2d_models()})

            selected = companion_service.select_live2d_model(model["id"])
            self.assertEqual(selected, model["id"])
            runtime = json.loads((companion_service.live2d_state_dir() / "runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(runtime["selectedModelId"], model["id"])

            companion_service.save_config({"live2d_model_id": model["id"]})
            self.assertTrue(companion_service.delete_live2d_model(model["id"]))
            self.assertEqual(companion_service.load_config()["live2d_model_id"], "hiyori")
            self.assertNotIn(model["id"], {item["id"] for item in companion_service.available_live2d_models()})

    def test_custom_live2d_import_rejects_missing_model_files(self) -> None:
        root = Path(self.temp_dir.name)
        source = root / "invalid-live2d"
        source.mkdir()
        (source / "invalid.model3.json").write_text(
            json.dumps({"Version": 3, "FileReferences": {"Moc": "missing.moc3", "Textures": []}}),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"MIO_DESKTOP_STATE_DIR": str(root / "desktop-state")}):
            with self.assertRaisesRegex(ValueError, "moc3"):
                companion_service.import_live2d_model_directory(source)

    def test_live2d_import_registers_unlisted_expressions_and_uses_character_cover(self) -> None:
        root = Path(self.temp_dir.name)
        source = root / "expressive-live2d"
        source.mkdir()
        (source / "model.moc3").write_bytes(b"moc3")
        (source / "texture.png").write_bytes(b"texture")
        (source / "开心.exp3.json").write_text('{"Type":"Live2D Expression"}', encoding="utf-8")
        (source / "角色封面.png").write_bytes(b"cover")
        (source / "model.model3.json").write_text(
            json.dumps({
                "Version": 3,
                "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]},
            }),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"MIO_DESKTOP_STATE_DIR": str(root / "desktop-state")}):
            model = companion_service.import_live2d_model_directory(source)
            self.assertEqual(model["capabilities"]["expressions"], [
                {"Name": "开心", "File": "开心.exp3.json"}
            ])
            self.assertEqual(model["preview_path"], "角色封面.png")
            imported_document = json.loads(
                (companion_service.live2d_state_dir() / "models" / model["id"] / "model.model3.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(imported_document["FileReferences"]["Expressions"][0]["Name"], "开心")

    def test_existing_live2d_model_metadata_is_refreshed_without_reimport(self) -> None:
        root = Path(self.temp_dir.name)
        model_root = root / "desktop-state" / "Live2D桌宠" / "models" / "old-model"
        model_root.mkdir(parents=True)
        (model_root / "model.moc3").write_bytes(b"moc3")
        (model_root / "texture.png").write_bytes(b"texture")
        (model_root / "害羞.exp3.json").write_text('{"Type":"Live2D Expression"}', encoding="utf-8")
        (model_root / "avatar.webp").write_bytes(b"cover")
        (model_root / "model.model3.json").write_text(json.dumps({
            "Version": 3,
            "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]},
        }), encoding="utf-8")
        (model_root / "mio-model.json").write_text(json.dumps({
            "name": "旧模型",
            "modelPath": "model.model3.json",
            "previewPath": "",
            "capabilities": {},
        }), encoding="utf-8")

        with patch.dict(os.environ, {"MIO_DESKTOP_STATE_DIR": str(root / "desktop-state")}):
            model = next(item for item in companion_service.available_live2d_models() if item["id"] == "old-model")
            self.assertEqual(model["preview_path"], "avatar.webp")
            self.assertEqual(model["capabilities"]["expressions"][0]["Name"], "害羞")

    def test_screen_audio_settings_are_normalized(self) -> None:
        saved = companion_service.save_config({
            "screen_audio_enabled": True,
            "screen_audio_model": "invalid",
            "screen_audio_language": "invalid",
            "screen_audio_chunk_seconds": 99,
        })

        self.assertTrue(saved["screen_audio_enabled"])
        self.assertEqual(saved["screen_audio_model"], "base")
        self.assertEqual(saved["screen_audio_language"], "auto")
        self.assertEqual(saved["screen_audio_chunk_seconds"], 15)

    def test_voice_package_export_import_roundtrip(self) -> None:
        companion_service.save_config({
            "voice_profiles": {
                "mio": {"name": "默认角色", "gpt_sovits_ref_audio": ""},
                "bright": {"name": "明快", "gpt_sovits_ref_audio": ""},
            },
            "default_voice_profile_id": "mio",
        })
        ref = companion_service.settings.companion_dir / "音色参考-bright.wav"
        ref.write_bytes(b"RIFF-fake-wav-bytes-for-roundtrip")
        companion_service.save_config({
            "voice_profiles": {
                "bright": {
                    "name": "明快",
                    "gpt_sovits_ref_audio": str(ref.resolve()),
                    "gpt_sovits_prompt_text": "今天天气不错。",
                    "gpt_sovits_prompt_language": "zh",
                    "gpt_sovits_gpt_weights": str(Path("C:/模型/bright.ckpt")),
                },
            },
            "default_voice_profile_id": "bright",
        })
        package = companion_service.export_voice_package("bright")
        self.assertGreater(len(package), 0)

        result = companion_service.import_voice_package(package)
        self.assertIn("id", result)
        config = companion_service.load_config()
        imported = config["voice_profiles"][result["id"]]
        self.assertEqual(imported["name"], "明快")
        self.assertEqual(imported["gpt_sovits_prompt_text"], "今天天气不错。")
        self.assertTrue(Path(imported["gpt_sovits_ref_audio"]).is_file())
        self.assertEqual(Path(imported["gpt_sovits_ref_audio"]).suffix, ".wav")
        # 权重只记录文件名引用，不随包复制
        self.assertEqual(imported["gpt_sovits_gpt_weights"], "")

    def test_voice_package_rejects_invalid_inputs(self) -> None:
        companion_service.save_config({"voice_profiles": {"mio": {"name": "默认角色"}}})
        with self.assertRaises(ValueError):
            companion_service.import_voice_package(b"")
        with self.assertRaises(ValueError):
            companion_service.import_voice_package(b"not a zip at all")
        with self.assertRaises(ValueError):
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps({"format": "other-tool", "name": "外来卡"}),
                )
            companion_service.import_voice_package(buffer.getvalue())
        with self.assertRaises(ValueError):
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps({"format": "mio-voice-package", "name": ""}),
                )
            companion_service.import_voice_package(buffer.getvalue())
        # 路径穿越
        with self.assertRaises(ValueError):
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("../escape.txt", "x")
                archive.writestr(
                    "manifest.json",
                    json.dumps({"format": "mio-voice-package", "name": "穿越"}),
                )
            companion_service.import_voice_package(buffer.getvalue())

    def test_so_vits_svc_package_imports_model_and_becomes_default(self) -> None:
        companion_service.save_config({
            "voice_profiles": {
                "mio": {
                    "name": "基础音色",
                    "engine": "gpt_sovits",
                    "gpt_sovits_ref_audio": "C:/voice/base.wav",
                },
            },
            "default_voice_profile_id": "mio",
        })
        model_config = {
            "data": {"sampling_rate": 44100},
            "model": {
                "speech_encoder": "vec768l12",
                "n_speakers": 1,
                "use_automatic_f0_prediction": True,
            },
            "spk": {"huahuo": 0},
        }
        package = BytesIO()
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("config.json", json.dumps(model_config))
            archive.writestr("G_800.pth", b"model" * 220_000)
            archive.writestr("feature_and_index.pkl", b"this must never be loaded or extracted")

        with (
            patch("app.so_vits_svc_service.runtime_status", return_value={"ready": True, "missing": []}),
            patch("app.so_vits_svc_service.probe_profile", return_value={"ok": True, "speakers": ["huahuo"]}) as probe,
        ):
            result = companion_service.import_voice_package(package.getvalue(), "花火.zip")

        self.assertEqual(result["engine"], "so_vits_svc")
        self.assertTrue(result["activated"])
        self.assertEqual(result["speaker"], "huahuo")
        config = companion_service.load_config()
        self.assertEqual(config["default_voice_profile_id"], result["id"])
        imported = config["voice_profiles"][result["id"]]
        self.assertEqual(imported["engine"], "so_vits_svc")
        self.assertEqual(imported["so_vits_svc_base_profile_id"], "mio")
        self.assertTrue(Path(imported["so_vits_svc_model_path"]).is_file())
        self.assertTrue(Path(imported["so_vits_svc_config_path"]).is_file())
        self.assertFalse((Path(imported["so_vits_svc_model_path"]).parent / "feature_and_index.pkl").exists())
        probe.assert_called_once_with(unittest.mock.ANY, device="cpu")

    def test_so_vits_svc_package_without_runtime_keeps_working_default(self) -> None:
        companion_service.save_config({
            "voice_profiles": {"mio": {"name": "基础音色", "engine": "gpt_sovits"}},
            "default_voice_profile_id": "mio",
        })
        package = BytesIO()
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("config.json", json.dumps({
                "model": {"speech_encoder": "vec768l12"},
                "spk": {"huahuo": 0},
            }))
            archive.writestr("G_800.pth", b"model" * 220_000)

        with (
            patch("app.so_vits_svc_service.runtime_status", return_value={"ready": False, "missing": ["ContentVec 编码器"]}),
            patch("app.so_vits_svc_service.probe_profile") as probe,
        ):
            result = companion_service.import_voice_package(package.getvalue(), "花火.zip")

        self.assertFalse(result["activated"])
        self.assertEqual(result["runtime"], "missing")
        self.assertEqual(companion_service.load_config()["default_voice_profile_id"], "mio")
        probe.assert_not_called()

    def test_voice_package_file_import_reports_streaming_progress(self) -> None:
        companion_service.save_config({
            "voice_profiles": {"mio": {"name": "基础音色", "engine": "gpt_sovits"}},
            "default_voice_profile_id": "mio",
        })
        package_path = companion_service.settings.companion_dir / "progress-voice.zip"
        package_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("config.json", json.dumps({
                "model": {"speech_encoder": "vec768l12"},
                "spk": {"huahuo": 0},
            }))
            archive.writestr("G_800.pth", b"model" * 220_000)
        progress: list[dict[str, object]] = []

        with patch("app.so_vits_svc_service.runtime_status", return_value={"ready": False, "missing": ["runtime"]}):
            result = companion_service.import_voice_package_file(package_path, progress=progress.append)

        self.assertEqual(result["engine"], "so_vits_svc")
        extracting = [item for item in progress if item["phase"] == "extracting"]
        self.assertTrue(extracting)
        self.assertEqual(extracting[-1]["processed_bytes"], extracting[-1]["total_bytes"])
        self.assertLessEqual(extracting[-1]["percent"], 99)

    def test_so_vits_svc_package_import_creates_fresh_companion_directory(self) -> None:
        companion_service.save_config({
            "voice_profiles": {"mio": {"name": "基础音色", "engine": "gpt_sovits"}},
            "default_voice_profile_id": "mio",
        })
        fresh_companion_dir = Path(self.temp_dir.name) / "全新音色数据目录"
        object.__setattr__(companion_service.settings, "companion_dir", fresh_companion_dir)
        package = BytesIO()
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("config.json", json.dumps({
                "model": {"speech_encoder": "vec768l12"},
                "spk": {"huahuo": 0},
            }))
            archive.writestr("G_800.pth", b"model" * 220_000)

        self.assertFalse(fresh_companion_dir.exists())
        with patch("app.so_vits_svc_service.runtime_status", return_value={"ready": False, "missing": ["runtime"]}):
            result = companion_service.import_voice_package(package.getvalue(), "花火.zip")

        self.assertEqual(result["engine"], "so_vits_svc")
        self.assertTrue(fresh_companion_dir.is_dir())
        self.assertTrue(Path(companion_service.load_config()["voice_profiles"][result["id"]]["so_vits_svc_model_path"]).is_file())

    def test_voice_package_rejects_corrupt_audio_hash(self) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps({
                    "format": "mio-voice-package",
                    "name": "坏音频",
                    "reference_audio": "ref.wav",
                    "sha256": "0" * 64,
                }),
            )
            archive.writestr("reference_audio/ref.wav", b"fake")
        with self.assertRaises(ValueError):
            companion_service.import_voice_package(buffer.getvalue())

    def test_voice_package_export_import_endpoints(self) -> None:
        from app.routes import companion as companion_route

        companion_service.save_config({
            "voice_profiles": {"mio": {"name": "默认角色", "gpt_sovits_ref_audio": ""}},
            "default_voice_profile_id": "mio",
        })
        app = FastAPI()
        app.include_router(companion_route.router)
        with TestClient(app) as client:
            exported = client.post("/api/companion/voice/profiles/export", json={"profile_id": "mio"})
            self.assertEqual(exported.status_code, 200)
            self.assertEqual(exported.headers["content-type"], "application/zip")
            self.assertGreater(len(exported.content), 0)

            rejected = client.post("/api/companion/voice/profiles/import-package", content=b"not a zip")
            self.assertEqual(rejected.status_code, 400)

            imported = client.post(
                "/api/companion/voice/profiles/import-package",
                content=exported.content,
            )
            self.assertEqual(imported.status_code, 200)
            body = imported.json()
            profiles = body["pet"]["settings"]["voice_profiles"]
            self.assertEqual(len(profiles), 2)
            self.assertIn("默认角色", [item["name"] for item in profiles.values()])

    def test_multiple_voice_profiles_always_use_the_selected_default(self) -> None:
        normalized = companion_service.load_normalized_config({
            "voice_profiles": {
                "mio": {"name": "澪", "gpt_sovits_gpt_weights": "mio.ckpt"},
                "bright": {"name": "明快", "gpt_sovits_gpt_weights": "bright.ckpt"},
            },
            "default_voice_profile_id": "mio",
            "model_voice_bindings": {"deepseek-v4-flash": "bright"},
        })

        profile_id, selected = companion_service.resolve_voice_profile(
            "deepseek-v4-flash",
            normalized,
        )
        self.assertEqual(profile_id, "mio")
        self.assertEqual(selected["voice_profile_name"], "澪")
        self.assertEqual(selected["gpt_sovits_gpt_weights"], "mio.ckpt")
        self.assertEqual(set(normalized["voice_profiles"]), {"mio", "bright"})
        self.assertNotIn("model_voice_bindings", normalized)

        fallback_id, fallback = companion_service.resolve_voice_profile("gpt-5.6-sol", normalized)
        self.assertEqual(fallback_id, "mio")
        self.assertEqual(fallback["voice_profile_name"], "澪")

    def test_voice_weights_accept_character_names_but_reject_wrong_suffixes(self) -> None:
        normalized = companion_service.load_normalized_config({
            "gpt_sovits_gpt_weights": "narrator.ckpt",
            "gpt_sovits_sovits_weights": "narrator.pth",
            "voice_profiles": {
                "mio": {
                    "gpt_sovits_gpt_weights": "narrator.ckpt",
                    "gpt_sovits_sovits_weights": "narrator.pth",
                },
                "invalid": {
                    "gpt_sovits_gpt_weights": "wrong.pth",
                    "gpt_sovits_sovits_weights": "wrong.ckpt",
                },
            },
        })

        self.assertEqual(normalized["gpt_sovits_gpt_weights"], "narrator.ckpt")
        self.assertEqual(normalized["gpt_sovits_sovits_weights"], "narrator.pth")
        self.assertEqual(normalized["voice_profiles"]["mio"]["gpt_sovits_gpt_weights"], "narrator.ckpt")
        self.assertEqual(normalized["voice_profiles"]["mio"]["gpt_sovits_sovits_weights"], "narrator.pth")
        self.assertEqual(normalized["voice_profiles"]["invalid"]["gpt_sovits_gpt_weights"], "")
        self.assertEqual(normalized["voice_profiles"]["invalid"]["gpt_sovits_sovits_weights"], "")

    def test_pet_language_override_keeps_profile_but_changes_spoken_language(self) -> None:
        normalized = companion_service.load_normalized_config({
            "voice_profiles": {
                "mio": {
                    "name": "澪",
                    "gpt_sovits_text_language": "auto",
                    "gpt_sovits_translate_to_japanese": False,
                },
            },
        })

        _, chinese = companion_service.resolve_voice_profile("", normalized, speech_language="zh")
        _, japanese = companion_service.resolve_voice_profile("", normalized, speech_language="ja")

        self.assertEqual(chinese["gpt_sovits_text_language"], "zh")
        self.assertFalse(chinese["gpt_sovits_translate_to_japanese"])
        self.assertEqual(japanese["gpt_sovits_text_language"], "ja")
        self.assertTrue(japanese["gpt_sovits_translate_to_japanese"])

    def test_voice_reference_upload_updates_only_the_selected_profile(self) -> None:
        companion_service.save_config({
            "voice_profiles": {
                "calm": {"name": "沉静", "gpt_sovits_ref_audio": ""},
                "bright": {"name": "明快", "gpt_sovits_ref_audio": ""},
            },
            "default_voice_profile_id": "calm",
        })
        audio = _tone_wav_bytes(seconds=0.1)

        asyncio.run(companion_voice_reference(VoiceReferenceRequest(
            name="bright.wav",
            data_url="data:audio/wav;base64," + base64.b64encode(audio).decode("ascii"),
            profile_id="bright",
        )))

        saved = companion_service.load_config()
        self.assertEqual(saved["voice_profiles"]["calm"]["gpt_sovits_ref_audio"], "")
        reference = Path(saved["voice_profiles"]["bright"]["gpt_sovits_ref_audio"])
        self.assertEqual(reference.name, "音色参考-bright.wav")
        self.assertEqual(reference.read_bytes(), audio)

    def test_voice_preview_uses_japanese_sentence_when_japanese_is_selected(self) -> None:
        config = companion_service.load_normalized_config({
            "pet_speech_language": "ja",
            "voice_profiles": {"calm": {"name": "沉静"}},
            "default_voice_profile_id": "calm",
        })

        with (
            patch("app.routes.companion.companion_service.load_config", return_value=config),
            patch("app.routes.companion.companion_service.speak_text", return_value=True) as speak,
        ):
            result = asyncio.run(companion_voice_test())

        self.assertTrue(result["spoken"])
        self.assertIn("日本語", speak.call_args.args[0])
        self.assertEqual(speak.call_args.kwargs["language"], "ja")
        self.assertTrue(speak.call_args.kwargs["wait"])

    def test_show_agent_window_launches_installed_app_when_event_is_unavailable(self) -> None:
        executable = Path(self.temp_dir.name) / "澪.exe"
        executable.write_bytes(b"test")
        process = SimpleNamespace()
        with (
            patch.dict(os.environ, {"MIO_AGENT_EXE": str(executable)}),
            patch("app.companion_service.signal_agent_window", return_value=False),
            patch("app.companion_service.subprocess.Popen", return_value=process) as popen,
        ):
            result = companion_service.show_agent_window()

        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "launch")
        self.assertEqual(popen.call_args.args[0], [str(executable)])

    def test_agent_show_route_returns_desktop_wakeup_result(self) -> None:
        with patch(
            "app.routes.companion.companion_service.show_agent_window",
            return_value={"ok": True, "method": "event"},
        ):
            result = asyncio.run(companion_agent_show())

        self.assertEqual(result, {"ok": True, "method": "event"})

        saved = companion_service.save_config({"startup_greeting_enabled": False})
        self.assertFalse(saved["startup_greeting_enabled"])
        self.assertFalse(companion_service.load_config()["startup_greeting_enabled"])

    def test_live2d_motion_slots_are_saved_per_model_and_filtered(self) -> None:
        saved = companion_service.save_config(
            {
                "live2d_motion_slots": {
                    "hiyori": {
                        "idle": "Idle",
                        "touch": "TapBody",
                        "unknown": "ShouldNotPersist",
                    },
                    "custom-model": {"speak": "Talk", "think": "Think"},
                    "invalid": "not-a-slot-map",
                }
            }
        )

        self.assertEqual(
            saved["live2d_motion_slots"],
            {
                "hiyori": {"idle": "Idle", "touch": "TapBody"},
                "custom-model": {"speak": "Talk", "think": "Think"},
            },
        )

    def test_live2d_expression_slots_are_saved_per_model_and_filtered(self) -> None:
        saved = companion_service.save_config({
            "live2d_expression_slots": {
                "custom-model": {
                    "neutral": "普通",
                    "cheerful": "开心",
                    "unknown": "ShouldNotPersist",
                },
                "invalid": "not-a-slot-map",
            }
        })

        self.assertEqual(saved["live2d_expression_slots"], {
            "custom-model": {"neutral": "普通", "cheerful": "开心"},
        })

    def test_pet_chat_window_state_is_forwarded_to_live2d_renderer(self) -> None:
        with patch.object(pet_event_service, "publish") as publish:
            result = asyncio.run(
                companion_chat_window_state(CompanionChatWindowStateRequest(open=True))
            )

        self.assertEqual(result, {"ok": True, "open": True})
        publish.assert_called_once_with("chat_window_state", {"open": True})

    def test_reaction_prompt_includes_current_foreground_window(self) -> None:
        observation = screen_observation_service.Observation(
            event_type="gameplay",
            summary="角色正在探索",
            confidence=0.9,
            game_name="测试游戏",
            details={},
            tags=(),
        )
        decision = SimpleNamespace(reason="出现了新场景", repeat_count=1)
        with patch(
            "app.screen_observation_service.pet_event_service.status",
            return_value={"foreground": {"title": "测试游戏 - 主窗口"}},
        ):
            messages = screen_observation_service._reaction_messages(
                observation,
                decision,
                {},
                "desktop_pet",
            )

        self.assertIn("当前前台窗口：测试游戏 - 主窗口", messages[1]["content"])

    def test_combined_screen_prompt_supports_daily_activity_and_speech_interval(self) -> None:
        frame = {
            "content": b"test-image",
            "mode": "screen",
            "title": "主屏幕",
            "captured_at": "2026-08-10T15:30:00+08:00",
            "change_percent": 12.5,
        }
        with patch(
            "app.screen_observation_service._foreground_context",
            return_value="Visual Studio Code",
        ):
            messages = screen_observation_service._combined_analysis_messages(
                frame,
                [],
                {},
                {"width": 960, "height": 540},
                "desktop_pet",
                seconds_since_last_speech=125,
            )

        prompt = messages[1]["content"][0]["text"]
        self.assertIn("日常桌面陪伴", prompt)
        self.assertIn("工作、学习、写作、画画", prompt)
        self.assertIn("距离上次开口：约 125 秒", prompt)

    def test_activity_context_distinguishes_video_platform_and_code_editor(self) -> None:
        with patch(
            "app.screen_observation_service._foreground_details",
            return_value={
                "process_name": "msedge.exe",
                "title": "哔哩哔哩 - 游戏实况",
                "process_id": 1,
            },
        ):
            video = screen_observation_service._activity_context({"title": "主屏幕"})
        with patch(
            "app.screen_observation_service._foreground_details",
            return_value={
                "process_name": "Code.exe",
                "title": "screen_observation_service.py - Visual Studio Code",
                "process_id": 2,
            },
        ):
            editor = screen_observation_service._activity_context({"title": "主屏幕"})

        self.assertEqual(video["kind"], "video_platform")
        self.assertEqual(editor["kind"], "code_editor")

    def test_game_picture_inside_bilibili_is_corrected_to_watching_video(self) -> None:
        observation = screen_observation_service.Observation(
            event_type="gameplay",
            summary="玩家正在操作角色战斗",
            confidence=0.94,
            game_name="测试游戏",
            details={},
            tags=("战斗",),
        )
        with patch(
            "app.screen_observation_service._foreground_details",
            return_value={
                "process_name": "msedge.exe",
                "title": "哔哩哔哩 - 游戏实况",
                "process_id": 1,
            },
        ):
            corrected = screen_observation_service._correct_observation_for_activity(
                observation,
                {"title": "主屏幕"},
            )

        self.assertEqual(corrected.event_type, "watching_game_video")
        self.assertEqual(corrected.game_name, "")
        self.assertEqual(corrected.details["activity_kind"], "video_platform")

    def test_screen_prompt_combines_recent_system_audio_without_persisting_audio(self) -> None:
        frame = {
            "content": b"test-image",
            "mode": "screen",
            "title": "主屏幕",
            "captured_at": "2026-08-10T15:30:00+08:00",
            "change_percent": 12.5,
        }
        with patch(
            "app.screen_observation_service.system_audio_service.recent_transcript",
            return_value="我们先去找那个失踪的人吧",
        ):
            messages = screen_observation_service._combined_analysis_messages(
                frame,
                [],
                {},
                {"width": 960, "height": 540},
                "desktop_pet",
            )

        prompt = messages[1]["content"][0]["text"]
        self.assertIn("最近系统声音台词：我们先去找那个失踪的人吧", prompt)
        self.assertIn("不要把视频台词当成用户对你说话", prompt)

    def test_system_audio_context_keeps_transcript_only_in_memory(self) -> None:
        with system_audio_service._lock:
            system_audio_service._transcripts.clear()
            system_audio_service._transcripts.append({
                "text": "这一幕的台词",
                "received_monotonic": __import__("time").monotonic(),
            })

        self.assertEqual(system_audio_service.recent_transcript(), "这一幕的台词")
        self.assertFalse(system_audio_service.status()["audio_persisted"])

    def test_system_audio_chat_context_reports_active_hearing(self) -> None:
        with (
            patch.object(
                system_audio_service,
                "status",
                return_value={
                    "running": True,
                    "ready": True,
                    "last_error": "",
                },
            ),
            patch.object(
                system_audio_service,
                "recent_transcript",
                return_value="我们先去找那个人吧",
            ),
        ):
            context = system_audio_service.chat_context()

        self.assertIn("当前正在本地监听电脑扬声器", context)
        self.assertIn("我们先去找那个人吧", context)
        self.assertIn("不要把视频或游戏台词误认为用户", context)

    def test_system_audio_quality_transcription_uses_loaded_worker(self) -> None:
        original_process = system_audio_service._process
        original_ready = system_audio_service._ready
        before = system_audio_service.status()["quality_requests"]

        class FakeStdin:
            def write(self, value: str) -> int:
                payload = json.loads(value)
                request = system_audio_service._quality_requests[payload["request_id"]]
                request["result"] = {
                    "text": "你好，小落",
                    "language": "zh",
                    "probability": 0.99,
                    "error": "",
                }
                request["event"].set()
                return len(value)

            def flush(self) -> None:
                return None

        try:
            system_audio_service._process = SimpleNamespace(stdin=FakeStdin(), poll=lambda: None)
            system_audio_service._ready = True
            result = system_audio_service.transcribe_wav_for_quality(_tone_wav_bytes(), language="zh")
            diagnostics = system_audio_service.status()["quality_requests"]
            self.assertEqual(result["text"], "你好，小落")
            self.assertEqual(diagnostics["requested"], before["requested"] + 1)
            self.assertEqual(diagnostics["completed"], before["completed"] + 1)
            self.assertEqual(diagnostics["pending"], 0)
        finally:
            system_audio_service._process = original_process
            system_audio_service._ready = original_ready
            system_audio_service._quality_requests.clear()

    def test_system_audio_quality_timeout_is_counted_for_diagnostics(self) -> None:
        original_process = system_audio_service._process
        original_ready = system_audio_service._ready

        class SilentStdin:
            def write(self, _payload):
                return None

            def flush(self):
                return None

        try:
            system_audio_service._process = SimpleNamespace(stdin=SilentStdin(), poll=lambda: None)
            system_audio_service._ready = True
            before = system_audio_service.status()["quality_requests"]["timeouts"]

            result = system_audio_service.transcribe_wav_for_quality(
                _tone_wav_bytes(),
                language="zh",
                timeout_seconds=0.1,
            )

            diagnostics = system_audio_service.status()["quality_requests"]
            self.assertIsNone(result)
            self.assertEqual(diagnostics["timeouts"], before + 1)
            self.assertEqual(diagnostics["pending"], 0)
            self.assertIn("0.1", diagnostics["last_error"])
        finally:
            system_audio_service._process = original_process
            system_audio_service._ready = original_ready
            system_audio_service._quality_requests.clear()

    def test_system_audio_stop_terminates_windows_process_tree(self) -> None:
        process = SimpleNamespace(pid=1234, poll=lambda: None)
        with (
            system_audio_service._lock,
            patch("app.system_audio_service.os.name", "nt"),
            patch("app.system_audio_service.subprocess.run") as run,
        ):
            system_audio_service._process = process
            system_audio_service.stop()

        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "1234", "/T", "/F"])

    def test_daily_activity_events_can_speak_after_cooldown(self) -> None:
        for event_type in ("activity_progress", "activity_pause", "interesting_content"):
            with self.subTest(event_type=event_type):
                observation = screen_behavior_service.Observation(
                    event_type=event_type,
                    summary=f"日常活动事件 {event_type}",
                    confidence=0.95,
                    game_name="",
                    details={},
                    tags=(),
                )
                decision = screen_behavior_service.decide_behavior(
                    observation,
                    {"event_counts": {event_type: 1}},
                    recent_summaries=[],
                    seconds_since_last_speech=120,
                    cooldown_seconds=5,
                    minimum_priority=0.62,
                )
                self.assertTrue(decision.should_speak)

    def test_pet_activity_is_normalized_and_expires(self) -> None:
        with patch("app.companion_service.time.monotonic", return_value=100.0):
            active = companion_service.set_pet_activity(
                "thinking",
                emotion="cheerful",
                source="desktop",
                ttl_seconds=5,
            )
        self.assertEqual(active["state"], "thinking")
        self.assertEqual(active["label"], "正在想")
        self.assertEqual(active["emotion"], "cheerful")
        self.assertEqual(active["remaining_ms"], 5000)

        with patch("app.companion_service.time.monotonic", return_value=106.0):
            expired = companion_service.pet_activity_status()
        self.assertEqual(expired["state"], "idle")
        self.assertEqual(expired["emotion"], "neutral")
        self.assertEqual(expired["remaining_ms"], 0)

    def test_pet_emotion_sprite_wins_while_speaking(self) -> None:
        pet = MioDesktopPet.__new__(MioDesktopPet)
        pet.speaking = True
        pet.current_emotion = "cheerful"
        pet.current_activity = "speaking"
        pet.activity_deadline = 999.0
        pet.observing = False
        pet.sprites = {"idle": object(), "speaking": object(), "cheerful": object()}
        pet.blink_until = 0.0
        pet.next_blink_at = 999.0
        self.assertEqual(pet._sprite_state(10.0), "cheerful")

    def test_pet_thinking_uses_quiet_blink_sprite(self) -> None:
        pet = MioDesktopPet.__new__(MioDesktopPet)
        pet.speaking = False
        pet.current_emotion = "neutral"
        pet.current_activity = "thinking"
        pet.activity_deadline = 999.0
        pet.observing = False
        pet.sprites = {"idle": object(), "blink": object()}
        pet.blink_until = 0.0
        pet.next_blink_at = 999.0
        self.assertEqual(pet._sprite_state(10.0), "blink")

    def test_screen_session_is_reused_only_while_recent(self) -> None:
        with patch("app.db.now_iso", side_effect=[
            "2026-08-07T13:00:00+08:00",
            "2026-08-07T13:20:00+08:00",
            "2026-08-07T13:31:00+08:00",
        ]):
            first = db.start_screen_session("window", "测试游戏")
            recent = db.start_screen_session("window", "测试游戏")
            stale = db.start_screen_session("window", "测试游戏")

        self.assertEqual(recent, first)
        self.assertNotEqual(stale, first)
        with db.get_conn() as conn:
            old = conn.execute("SELECT status, ended_at FROM game_sessions WHERE id = ?", (first,)).fetchone()
        self.assertEqual(old["status"], "ended")
        self.assertEqual(old["ended_at"], "2026-08-07T13:31:00+08:00")

    def test_screen_session_accepts_legacy_timestamp_without_timezone(self) -> None:
        self.assertTrue(
            db._screen_session_is_recent(
                "2026-08-07T13:00:00",
                "2026-08-07T13:20:00+08:00",
            )
        )

    def test_switching_observed_window_starts_a_new_game_session(self) -> None:
        with patch("app.db.now_iso", side_effect=[
            "2026-08-07T13:00:00+08:00",
            "2026-08-07T13:01:00+08:00",
        ]):
            first = db.start_screen_session("window", "游戏 A")
            second = db.start_screen_session("window", "游戏 B")

        self.assertNotEqual(second, first)

    def test_reopened_window_carries_recent_game_state_into_new_session(self) -> None:
        with patch("app.db.now_iso", side_effect=[
            "2026-08-07T13:00:00+08:00",
            "2026-08-07T13:05:00+08:00",
            "2026-08-07T13:31:00+08:00",
        ]):
            first = db.start_screen_session("window", "测试游戏")
            db.upsert_game_session_state(
                first,
                "测试游戏",
                {"game_name": "测试游戏", "boss": "旧 Boss", "death_count": 2},
            )
            reopened = db.start_screen_session("window", "测试游戏")

        self.assertNotEqual(reopened, first)
        self.assertEqual(db.get_game_session_state(reopened)["boss"], "旧 Boss")
        self.assertEqual(db.get_game_session_state(reopened)["death_count"], 2)

    def test_screen_history_cleanup_does_not_touch_chat_or_diary_rows(self) -> None:
        old = "2026-07-01T12:00:00+08:00"
        current = "2026-08-08T12:00:00+08:00"
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO messages (role, content, source, conversation_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("user", "保留的聊天", "test", "default", old),
            )
            conn.execute(
                "INSERT INTO diaries (date, title, markdown_content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("2026-07-01", "保留的日记", "正文", old, old),
            )
            session = conn.execute(
                "INSERT INTO game_sessions (mode, title, status, started_at, ended_at) VALUES (?, ?, ?, ?, ?)",
                ("window", "旧窗口", "ended", old, old),
            ).lastrowid
            conn.execute(
                "INSERT INTO game_session_states (session_id, game_name, state_json, updated_at) VALUES (?, ?, ?, ?)",
                (session, "旧游戏", "{}", old),
            )
            event = conn.execute(
                "INSERT INTO screen_events (session_id, occurred_at, created_at) VALUES (?, ?, ?)",
                (session, old, old),
            ).lastrowid
            conn.execute(
                "INSERT INTO observations (session_id, occurred_at, created_at) VALUES (?, ?, ?)",
                (session, old, old),
            )
            conn.execute(
                "INSERT INTO companion_reactions (screen_event_id, text, created_at) VALUES (?, ?, ?)",
                (event, "旧观察回复", old),
            )

        deleted = db.cleanup_screen_observation_history(
            retention_days=30,
            current_at=current,
        )

        self.assertGreaterEqual(deleted["observations"], 1)
        self.assertGreaterEqual(deleted["screen_events"], 1)
        self.assertGreaterEqual(deleted["companion_reactions"], 1)
        with db.get_conn() as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM messages").fetchone())
            self.assertIsNotNone(conn.execute("SELECT id FROM diaries").fetchone())
            self.assertIsNone(conn.execute("SELECT id FROM game_sessions WHERE id = ?", (session,)).fetchone())

    def test_pet_size_is_normalized_and_saved(self) -> None:
        saved = companion_service.save_pet_size(999)

        self.assertEqual(saved["pet_size_percent"], 240)
        self.assertEqual(companion_service.load_config()["pet_size_percent"], 240)
        self.assertEqual(MioDesktopPet._normalize_size(79), 80)
        self.assertEqual(MioDesktopPet._normalize_size("bad"), 150)

    def test_pet_status_reports_bundled_live2d_models(self) -> None:
        live2d_dir = companion_service.settings.agent_frontend_dir / "live2d-pet"
        live2d_dir.mkdir(parents=True)
        (live2d_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")

        with patch.dict(
            os.environ,
            {"MIO_DESKTOP_STATE_DIR": str(Path(self.temp_dir.name) / "desktop-state")},
        ):
            status = companion_service.pet_status()

        self.assertTrue(status["live2d"]["available"])
        self.assertEqual(
            [model["id"] for model in status["live2d"]["models"]],
            ["hiyori"],
        )
        self.assertIn("THIRD_PARTY_NOTICES", status["live2d"]["notices_url"])

    def test_qq_voice_mode_and_emotion_are_selected_locally(self) -> None:
        companion_service.save_config({"qq_voice_mode": "explicit"})
        self.assertFalse(companion_service.should_use_qq_voice("晚安"))
        self.assertTrue(companion_service.should_use_qq_voice("普通消息", explicitly_requested=True))

        companion_service.save_config({"qq_voice_mode": "adaptive"})
        self.assertTrue(companion_service.should_use_qq_voice("晚安"))
        self.assertEqual(companion_service.infer_speech_emotion("晚安，早点休息"), "gentle")
        self.assertEqual(companion_service.infer_speech_emotion("你终于做到了！"), "cheerful")
        self.assertEqual(companion_service.infer_speech_emotion("你听起来很累，先别硬撑，好吗？"), "concerned")
        self.assertEqual(companion_service.infer_speech_emotion("……你突然这么说，我有点不好意思"), "shy")
        self.assertEqual(companion_service.infer_speech_emotion("先停一下，别再否定自己"), "serious")

    def test_speech_emotion_uses_conversation_context(self) -> None:
        self.assertEqual(
            companion_service.infer_speech_emotion("嗯，我知道了", "我终于把项目做完了"),
            "cheerful",
        )
        self.assertEqual(
            companion_service.infer_speech_emotion("先休息一会儿吧", "我今天疼得有点撑不住"),
            "concerned",
        )

    def test_explicit_speech_style_overrides_reply_wording(self) -> None:
        self.assertEqual(
            companion_service.infer_speech_emotion("はじめまして、澪です。", "用温柔一点的语气说"),
            "gentle",
        )
        self.assertEqual(
            companion_service.infer_speech_emotion("太好了，你做到了！", "用自然的语气说"),
            "neutral",
        )

    def test_pet_position_is_saved_without_overwriting_voice_settings(self) -> None:
        companion_service.save_config({"voice_volume": 70})
        saved = companion_service.save_pet_position(-120, 360)

        self.assertEqual(saved["position_x"], -120)
        self.assertEqual(saved["position_y"], 360)
        self.assertEqual(saved["voice_volume"], 70)

    def test_pet_sprite_manifest_tracks_all_six_states(self) -> None:
        sprite_dir = companion_service.settings.companion_sprite_dir
        sprite_dir.mkdir(parents=True, exist_ok=True)
        for filename in companion_service.PET_SPRITE_FILES.values():
            Image.new("RGBA", (12, 18), "#7fa4b0").save(sprite_dir / filename)

        manifest = companion_service.pet_sprite_manifest()

        self.assertTrue(manifest["ready"])
        self.assertEqual(manifest["states"], list(companion_service.PET_SPRITE_FILES))
        self.assertTrue(manifest["version"])
        self.assertEqual(companion_service.pet_sprite_path("speaking"), sprite_dir / "说话.png")

    def test_pet_sprite_endpoint_and_transparent_full_body_preparation(self) -> None:
        sprite_dir = companion_service.settings.companion_sprite_dir
        sprite_dir.mkdir(parents=True, exist_ok=True)
        source = Image.new("RGBA", (120, 240), (0, 0, 0, 0))
        for y in range(20, 230):
            for x in range(30, 90):
                source.putpixel((x, y), (127, 164, 176, 255))
        source.save(sprite_dir / "待机.png")

        response = asyncio.run(companion_sprite("idle"))
        prepared = MioDesktopPet._prepare_sprite(source)

        self.assertEqual(Path(response.path), sprite_dir / "待机.png")
        self.assertEqual(prepared.size, (512, 640))
        self.assertIsNotNone(prepared.getchannel("A").getbbox())
        resized = prepared.resize((198, 248), Image.Resampling.LANCZOS)
        hardened = MioDesktopPet._harden_alpha(resized)
        self.assertLessEqual(set(hardened.getchannel("A").getdata()), {0, 255})
        with self.assertRaises(HTTPException) as context:
            asyncio.run(companion_sprite("missing"))
        self.assertEqual(context.exception.status_code, 404)

    def test_sprite_sheet_upload_splits_all_six_states_in_order(self) -> None:
        colors = ("#102030", "#203040", "#304050", "#405060", "#506070", "#607080")
        sheet = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
        for index, color in enumerate(colors):
            x = (index % 3) * 100
            y = (index // 3) * 100
            sheet.paste(Image.new("RGBA", (100, 100), color), (x, y))
        buffer = BytesIO()
        sheet.save(buffer, format="PNG")
        payload = SpriteSheetRequest(
            data_url="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        )

        with patch("app.routes.companion._status_payload", return_value={"ok": True}):
            response = asyncio.run(companion_sprite_sheet_save(payload))

        self.assertEqual(response, {"ok": True})
        self.assertTrue(companion_service.pet_sprite_manifest()["ready"])
        for filename, color in zip(companion_service.PET_SPRITE_FILES.values(), colors, strict=True):
            with Image.open(companion_service.settings.companion_sprite_dir / filename) as cell:
                self.assertEqual(cell.size, (100, 100))
                self.assertEqual(cell.convert("RGB").getpixel((50, 50)), Image.new("RGB", (1, 1), color).getpixel((0, 0)))

    def test_portrait_sprite_sheet_upload_splits_2x3_in_order(self) -> None:
        colors = ("#102030", "#203040", "#304050", "#405060", "#506070", "#607080")
        sheet = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
        for index, color in enumerate(colors):
            x = (index % 2) * 100
            y = (index // 2) * 100
            sheet.paste(Image.new("RGBA", (100, 100), color), (x, y))
        buffer = BytesIO()
        sheet.save(buffer, format="PNG")

        with patch("app.routes.companion._status_payload", return_value={"ok": True}):
            response = asyncio.run(
                companion_sprite_sheet_save(
                    SpriteSheetRequest(
                        data_url="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
                    )
                )
            )

        self.assertEqual(response, {"ok": True})
        for filename, color in zip(companion_service.PET_SPRITE_FILES.values(), colors, strict=True):
            with Image.open(companion_service.settings.companion_sprite_dir / filename) as cell:
                self.assertEqual(cell.size, (100, 100))
                self.assertEqual(cell.convert("RGB").getpixel((50, 50)), Image.new("RGB", (1, 1), color).getpixel((0, 0)))

    def test_sprite_sheet_upload_rejects_tiny_images_without_replacing_assets(self) -> None:
        sprite_dir = companion_service.settings.companion_sprite_dir
        sprite_dir.mkdir(parents=True, exist_ok=True)
        existing = sprite_dir / "待机.png"
        Image.new("RGBA", (20, 20), "#123456").save(existing)

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(companion_sprite_sheet_save(SpriteSheetRequest(data_url=_png_data_url())))

        self.assertEqual(raised.exception.status_code, 400)
        with Image.open(existing) as preserved:
            self.assertEqual(preserved.getpixel((10, 10))[:3], (18, 52, 86))

    def test_speech_audio_is_generated_in_memory(self) -> None:
        companion_service.save_config({"voice_engine": "gpt_sovits"})
        wav = _tone_wav_bytes()
        with (
            patch("app.companion_service._ensure_gpt_sovits_service"),
            patch("app.companion_service._synthesize_gpt_sovits_wav", return_value=wav) as synthesize,
            patch("app.companion_service.system_audio_service.transcribe_wav_for_quality", return_value=None),
        ):
            response = asyncio.run(companion_voice_audio(SpeechRequest(text="你好")))

        self.assertEqual(response.media_type, "audio/wav")
        self.assertTrue(response.body.startswith(b"RIFF"))
        self.assertEqual(response.headers["x-mio-emotion"], "neutral")
        self.assertEqual(synthesize.call_args.args[0], "你好")
        self.assertFalse(any(Path(self.temp_dir.name).rglob("*.wav")))

    def test_waiting_speech_reports_real_playback_result(self) -> None:
        played: list[bytes] = []
        fake_winsound = SimpleNamespace(
            SND_MEMORY=1,
            SND_NODEFAULT=2,
            PlaySound=lambda content, _flags: played.append(content),
        )
        with (
            patch("app.companion_service.synthesize_speech_wav", return_value=b"RIFFvoice"),
            patch.dict(sys.modules, {"winsound": fake_winsound}),
        ):
            spoken = companion_service.speak_text("你好", wait=True)

        self.assertTrue(spoken)
        self.assertEqual(played, [b"RIFFvoice"])

    def test_screen_speech_cannot_interrupt_active_chat_speech(self) -> None:
        companion_service._speech_owner_generation = 7
        companion_service._speech_owner_priority = companion_service.SPEECH_SOURCE_PRIORITIES["chat"]
        companion_service._speech_owner_source = "chat"

        spoken = companion_service.speak_text("screen event", source="screen")

        self.assertFalse(spoken)
        self.assertEqual(companion_service._speech_owner_generation, 7)

    def test_waiting_speech_exposes_playback_failure(self) -> None:
        def fail_playback(_content, _flags):
            raise OSError("没有可用的输出设备")

        fake_winsound = SimpleNamespace(
            SND_MEMORY=1,
            SND_NODEFAULT=2,
            PlaySound=fail_playback,
        )
        with (
            patch("app.companion_service.synthesize_speech_wav", return_value=b"RIFFvoice"),
            patch.dict(sys.modules, {"winsound": fake_winsound}),
        ):
            spoken = companion_service.speak_text("你好", wait=True)

        self.assertFalse(spoken)
        self.assertIn("输出设备", companion_service._gpt_sovits_last_error)

    def test_waiting_speech_uses_streaming_player_and_reports_first_audio(self) -> None:
        first_audio: list[float] = []

        def play_stream(_text, _config, _generation, **kwargs):
            kwargs["on_audio_started"](0.18)
            return True

        with (
            patch("app.companion_service._ensure_gpt_sovits_service"),
            patch("app.companion_service._play_gpt_sovits_stream", side_effect=play_stream) as stream,
        ):
            spoken = companion_service.speak_text(
                "你好",
                wait=True,
                streaming=True,
                on_audio_started=first_audio.append,
            )

        self.assertTrue(spoken)
        stream.assert_called_once()
        self.assertEqual(first_audio, [0.18])

    def test_low_latency_vision_selection_prefers_luna_over_sol(self) -> None:
        profiles = [
            SimpleNamespace(
                id="sol",
                model="gpt-5.6-sol",
                variant_name="Sol",
                input_price_cny_per_million=0,
                output_price_cny_per_million=0,
            ),
            SimpleNamespace(
                id="luna",
                model="gpt-5.6-luna",
                variant_name="Luna",
                input_price_cny_per_million=0,
                output_price_cny_per_million=0,
            ),
        ]

        selected = screen_observation_service._selected_vision_profile(
            {"screen_vision_model_id": "auto-fast"},
            profiles,
        )

        self.assertEqual(selected.id, "luna")

    def test_gpt_sovits_request_uses_reference_and_returns_memory_audio(self) -> None:
        reference = companion_service.save_voice_reference_data_url(
            "data:audio/mpeg;base64," + base64.b64encode(b"ID3reference").decode("ascii"),
            "日语参考.mp3",
        )
        companion_service.save_config({
            "voice_engine": "gpt_sovits",
            "gpt_sovits_prompt_text": "おはようございます",
            "gpt_sovits_prompt_language": "ja",
            "gpt_sovits_text_language": "zh",
        })
        wav = b"RIFF" + (b"\0" * 60)
        recorded: dict[str, object] = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json):
                recorded["url"] = url
                recorded["json"] = json
                return SimpleNamespace(is_success=True, content=wav)

        with patch("app.companion_service.httpx.Client", FakeClient):
            content = companion_service._synthesize_gpt_sovits_wav("你好", companion_service.load_config())

        self.assertEqual(content, wav)
        self.assertEqual(recorded["json"]["ref_audio_path"], str(reference.resolve()))
        self.assertEqual(recorded["json"]["prompt_lang"], "ja")
        self.assertEqual(recorded["json"]["text_lang"], "zh")
        self.assertEqual(recorded["json"]["text_split_method"], "cut5")
        self.assertEqual(recorded["json"]["text"], "你好。")
        self.assertEqual(recorded["json"]["aux_ref_audio_paths"], [])
        self.assertEqual(recorded["json"]["speed_factor"], 1.0)
        self.assertEqual(recorded["json"]["temperature"], 0.68)
        self.assertFalse(any(path.name.startswith("生成") for path in Path(self.temp_dir.name).rglob("*.wav")))

    def test_gpt_sovits_request_applies_inferred_emotion_style(self) -> None:
        companion_service.save_voice_reference_data_url(
            "data:audio/mpeg;base64," + base64.b64encode(b"ID3reference").decode("ascii"),
            "日语参考.mp3",
        )
        companion_service.save_config({"gpt_sovits_prompt_text": "おはようございます"})
        wav = b"RIFF" + (b"\0" * 60)
        payloads: list[dict[str, object]] = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json):
                payloads.append(json)
                return SimpleNamespace(is_success=True, content=wav)

        with patch("app.companion_service.httpx.Client", FakeClient):
            companion_service._synthesize_gpt_sovits_wav("太好了，你终于做到了！", companion_service.load_config())
            companion_service._synthesize_gpt_sovits_wav("晚安，早点休息", companion_service.load_config())

        self.assertEqual(payloads[0]["speed_factor"], 1.09)
        self.assertEqual(payloads[0]["temperature"], 0.80)
        self.assertEqual(payloads[0]["seed"], 3103)
        self.assertEqual(payloads[1]["speed_factor"], 0.91)
        self.assertEqual(payloads[1]["temperature"], 0.60)
        self.assertEqual(payloads[1]["seed"], 3102)

    def test_gpt_sovits_automatically_uses_japanese_for_kana_text(self) -> None:
        self.assertEqual(companion_service.speech_text_language("こんばんは、澪です"), "ja")
        self.assertEqual(companion_service.speech_text_language("今晚说こんばんは给我听"), "auto")
        self.assertEqual(companion_service.speech_text_language("晚上好"), "zh")

    def test_manual_speech_language_overrides_automatic_detection(self) -> None:
        self.assertEqual(companion_service.speech_text_language("今天辛苦了", "ja"), "ja")

    def test_chinese_speech_can_be_translated_to_japanese_before_tts(self) -> None:
        companion_service.save_voice_reference_data_url(
            "data:audio/mpeg;base64," + base64.b64encode(b"ID3reference").decode("ascii"),
            "日语参考.mp3",
        )
        config = companion_service.save_config({
            "gpt_sovits_prompt_text": "おはようございます",
            "gpt_sovits_prompt_language": "ja",
            "gpt_sovits_text_language": "auto",
            "gpt_sovits_translate_to_japanese": True,
        })

        with patch(
            "app.speech_translation_service._run_async_blocking",
            return_value=("今日は早く休んでね", "translation-model"),
        ) as translate:
            _, payload = companion_service._gpt_sovits_request_payload("今天早点休息", config)
            _, cached_payload = companion_service._gpt_sovits_request_payload("今天早点休息", config)

        self.assertEqual(payload["text"], "今日は早く休んでね")
        self.assertEqual(payload["text_lang"], "ja")
        self.assertEqual(payload["text_split_method"], "cut1")
        self.assertEqual(cached_payload["text"], payload["text"])
        translate.assert_called_once()
        self.assertEqual(companion_service._speech_translation_last_model, "translation-model")

    def test_short_japanese_speech_translation_does_not_call_model(self) -> None:
        with patch("app.speech_translation_service._run_async_blocking") as translate:
            result = companion_service._translate_speech_to_japanese("嗯？", {})

        self.assertEqual(result, "うん、どうしたの？")
        self.assertEqual(companion_service._speech_translation_last_model, "local-quick-translation")
        translate.assert_not_called()

    def test_short_acknowledgements_are_expanded_after_language_selection(self) -> None:
        self.assertEqual(companion_service._naturalize_short_speech_text("好。", "zh"), "好的。")
        self.assertEqual(companion_service._naturalize_short_speech_text("嗯……", "zh"), "嗯嗯……")
        self.assertEqual(companion_service._naturalize_short_speech_text("好啊！", "zh"), "好啊，我知道了！")
        self.assertEqual(companion_service._naturalize_short_speech_text("好的呀", "zh"), "好的呀，我知道了")
        self.assertEqual(companion_service._naturalize_short_speech_text("嗯好。", "zh"), "嗯嗯，好的。")
        self.assertEqual(companion_service._naturalize_short_speech_text("行啊", "zh"), "可以啊，我知道了")
        self.assertEqual(companion_service._naturalize_short_speech_text("知道了", "zh"), "知道了，我会记住的")
        self.assertEqual(companion_service._naturalize_short_speech_text("うん。", "ja"), "うんうん、わかったよ。")
        self.assertEqual(companion_service._naturalize_short_speech_text("好消息。", "zh"), "好消息。")

    def test_common_short_acknowledgements_translate_locally_without_failure_prompt(self) -> None:
        sources = ("好啊", "好的。", "好呀！", "好的呀", "嗯好", "行啊", "可以啊", "知道了。")
        with patch("app.speech_translation_service._run_async_blocking") as translate:
            translated = [
                companion_service._translate_speech_to_japanese(source, {})
                for source in sources
            ]

        self.assertEqual(len(translated), len(sources))
        self.assertTrue(all("ごめん" not in item for item in translated))
        self.assertTrue(all(item for item in translated))
        translate.assert_not_called()

    def test_japanese_translation_failure_prompt_is_not_a_short_apology_segment(self) -> None:
        segments = companion_service._split_genie_stream_segments(
            companion_service.SPEECH_JAPANESE_TRANSLATION_FAILURE_TEXT
        )

        self.assertNotIn("ごめん", companion_service.SPEECH_JAPANESE_TRANSLATION_FAILURE_TEXT)
        self.assertGreaterEqual(len(segments[0][0]), 12)

    def test_common_japanese_greeting_uses_local_translation(self) -> None:
        with patch("app.speech_translation_service._run_async_blocking") as translate:
            result = companion_service._translate_speech_to_japanese("哈喽大家好，我是澪~", {})

        self.assertEqual(result, "みんな、こんにちは。Mioだよ")
        self.assertEqual(companion_service._speech_translation_last_model, "local-quick-translation")
        translate.assert_not_called()

    def test_japanese_speech_auto_translation_uses_deepseek_flash(self) -> None:
        async def fake_completion(*_args, **_kwargs):
            return SimpleNamespace(content="自動翻訳モデルを固定します", model="deepseek-v4-flash")

        with (
            patch("app.llm.resolve_model_id", return_value="deepseek-v4-flash") as resolve,
            patch("app.llm.call_chat_completion_result", side_effect=fake_completion),
        ):
            result = companion_service._translate_speech_to_japanese(
                "自动翻译模型必须保持稳定",
                {"chat_model_id": "gpt-5.6-sol", "speech_translation_model_id": "deepseek-v4-flash"},
            )

        self.assertEqual(result, "自動翻訳モデルを固定します")
        resolve.assert_called_once_with("deepseek-v4-flash")

    def test_japanese_translation_has_short_deadline_and_never_falls_back_to_chinese(self) -> None:
        speech_translation_service.reset_for_tests()
        with patch(
            "app.speech_translation_service._run_async_blocking",
            side_effect=TimeoutError("translation deadline"),
        ) as translate:
            first = companion_service._translate_speech_to_japanese("今天早点休息", {})
            second = companion_service._translate_speech_to_japanese("明天再继续", {})

        self.assertEqual(first, "")
        self.assertEqual(second, "")
        translate.assert_called_once()
        self.assertEqual(translate.call_args.args[1], 4.0)
        self.assertIn("不会回退为中文", companion_service._speech_translation_last_error)
        self.assertEqual(speech_translation_service.status()["last_error_category"], "timeout")
        speech_translation_service.reset_for_tests()

    def test_japanese_speech_can_be_translated_to_chinese_for_one_playback(self) -> None:
        _, config = companion_service.resolve_voice_profile(speech_language="zh")
        with patch(
            "app.speech_translation_service._run_async_blocking",
            return_value=("今天一起回去吧", "translation-model"),
        ) as translate:
            prepared, language, translated = companion_service._prepare_speech_input(
                "今日は一緒に帰ろうね",
                config,
            )

        self.assertEqual(prepared, "今天一起回去吧")
        self.assertEqual(language, "zh")
        self.assertTrue(translated)
        translate.assert_called_once()

    def test_invalid_japanese_translation_stops_instead_of_speaking_chinese(self) -> None:
        config = companion_service.load_normalized_config({
            "gpt_sovits_translate_to_japanese": True,
            "gpt_sovits_text_language": "ja",
        })
        config["gpt_sovits_translate_to_japanese"] = True
        with patch(
            "app.speech_translation_service._run_async_blocking",
            return_value=("Hmm?", "translation-model"),
        ):
            with self.assertRaisesRegex(ValueError, "日语朗读准备失败.*不会回退为中文"):
                companion_service._prepare_speech_input("嗯哼一下", config)
        speech_translation_service.reset_for_tests()

    def test_voice_health_uses_dedicated_translation_service_status(self) -> None:
        companion_service._speech_translation_last_error = "旧错误不应继续暴露"
        with (
            patch("app.companion_service._gpt_sovits_process_running", return_value=True),
            patch(
                "app.companion_service.speech_translation_service.status",
                return_value={
                    "last_error": "独立翻译服务错误",
                    "last_error_category": "timeout",
                    "last_model": "deepseek-v4-flash",
                    "retry_after_seconds": 12.5,
                },
            ),
        ):
            status = companion_service.voice_runtime_health()

        self.assertEqual(status["translation_last_error"], "独立翻译服务错误")
        self.assertEqual(status["translation_last_error_category"], "timeout")
        self.assertEqual(status["translation_last_model"], "deepseek-v4-flash")
        self.assertEqual(status["translation_retry_after_seconds"], 12.5)

    def test_speech_stream_proxies_gpt_sovits_streaming_mode(self) -> None:
        companion_service.save_voice_reference_data_url(
            "data:audio/mpeg;base64," + base64.b64encode(b"ID3reference").decode("ascii"),
            "日语参考.mp3",
        )
        companion_service.save_config({
            "gpt_sovits_prompt_text": "おはようございます",
            "voice_streaming_enabled": True,
        })
        wav = b"RIFF" + (b"\0" * 84)
        recorded: dict[str, object] = {}

        class FakeResponse:
            is_success = True

            def iter_bytes(self, chunk_size=0):
                yield wav[:20]
                yield wav[20:]

        class FakeStream:
            def __enter__(self):
                return FakeResponse()

            def __exit__(self, *args):
                return False

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, method, url, json):
                recorded["method"] = method
                recorded["url"] = url
                recorded["json"] = json
                return FakeStream()

        with (
            patch("app.companion_service._ensure_gpt_sovits_service"),
            patch("app.companion_service._apply_gpt_sovits_weights"),
            patch("app.companion_service.httpx.Client", FakeClient),
        ):
            content = b"".join(companion_service.iter_speech_wav_stream("晚上好"))

        self.assertEqual(content, wav)
        self.assertEqual(recorded["method"], "POST")
        self.assertEqual(recorded["json"]["streaming_mode"], 2)
        self.assertIsInstance(
            companion_service._voice_runtime_metrics["last_first_audio_ms"],
            float,
        )
        self.assertEqual(companion_service.speech_text_language("今天辛苦了", "auto"), "zh")
        self.assertEqual(companion_service.speech_text_language("こんばんは", "zh"), "zh")

    def test_speech_stream_falls_back_before_first_audio_chunk(self) -> None:
        class FailingClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                raise OSError("stream unavailable")

        with (
            patch("app.companion_service._ensure_gpt_sovits_service"),
            patch(
                "app.companion_service._gpt_sovits_request_payload",
                return_value=("http://127.0.0.1:9880", {"streaming_mode": 2}),
            ),
            patch("app.companion_service.httpx.Client", FailingClient),
            patch(
                "app.companion_service.synthesize_speech_wav",
                return_value=b"RIFFfallback",
            ) as fallback,
        ):
            content = b"".join(companion_service.iter_speech_wav_stream("晚上好"))

        self.assertEqual(content, b"RIFFfallback")
        fallback.assert_called_once()

    def test_speech_stream_never_appends_fallback_after_audio_started(self) -> None:
        class PartialResponse:
            is_success = True

            def iter_bytes(self, chunk_size=0):
                yield b"RIFFpartial"
                raise OSError("stream disconnected")

        class PartialStream:
            def __enter__(self):
                return PartialResponse()

            def __exit__(self, *args):
                return False

        class PartialClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                return PartialStream()

        with (
            patch("app.companion_service._ensure_gpt_sovits_service"),
            patch(
                "app.companion_service._gpt_sovits_request_payload",
                return_value=("http://127.0.0.1:9880", {"streaming_mode": 2}),
            ),
            patch("app.companion_service._apply_gpt_sovits_weights"),
            patch("app.companion_service.httpx.Client", PartialClient),
            patch("app.companion_service.synthesize_speech_wav") as fallback,
        ):
            with self.assertRaisesRegex(OSError, "stream disconnected"):
                b"".join(companion_service.iter_speech_wav_stream("晚上好"))

        fallback.assert_not_called()

    def test_genie_stream_merges_punctuation_tail_after_unsupported_symbol(self) -> None:
        segments = companion_service._split_genie_stream_segments(
            "第二句话包含特殊符号：测试【继续】🙂。\n第三句话。"
        )

        self.assertEqual(
            segments,
            [
                ("第二句话包含特殊符号：测试【继续】🙂。", True),
                ("第三句话。", False),
            ],
        )

    def test_speech_stream_accepts_incomplete_chunked_end_after_audio_started(self) -> None:
        class PartialResponse:
            is_success = True

            def iter_bytes(self, chunk_size=0):
                yield b"RIFFpartial"
                raise httpx.RemoteProtocolError(
                    "peer closed connection without sending complete message body "
                    "(incomplete chunked read)"
                )

        class PartialStream:
            def __enter__(self):
                return PartialResponse()

            def __exit__(self, *args):
                return False

        class PartialClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                return PartialStream()

        with (
            patch("app.companion_service._ensure_gpt_sovits_service"),
            patch(
                "app.companion_service._gpt_sovits_request_payload",
                return_value=("http://127.0.0.1:9880", {"streaming_mode": 2}),
            ),
            patch("app.companion_service._apply_gpt_sovits_weights"),
            patch("app.companion_service.httpx.Client", PartialClient),
            patch("app.companion_service.synthesize_speech_wav") as fallback,
        ):
            content = b"".join(companion_service.iter_speech_wav_stream("晚上好"))

        self.assertEqual(content, b"RIFFpartial")
        fallback.assert_not_called()

    def test_speech_stream_generator_can_be_closed_from_another_thread(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingResponse:
            is_success = True

            def iter_bytes(self, chunk_size=0):
                yield b"RIFFpartial"
                entered.set()
                release.wait(2)
                yield b"tail"

        class BlockingStream:
            def __enter__(self):
                return BlockingResponse()

            def __exit__(self, *args):
                return False

        class BlockingClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                return BlockingStream()

        errors: list[BaseException] = []
        generator = companion_service.iter_speech_wav_stream("晚上好")

        with (
            patch("app.companion_service._ensure_gpt_sovits_service"),
            patch(
                "app.companion_service._gpt_sovits_request_payload",
                return_value=("http://127.0.0.1:9880", {"streaming_mode": 2}),
            ),
            patch("app.companion_service._apply_gpt_sovits_weights"),
            patch("app.companion_service.httpx.Client", BlockingClient),
        ):
            self.assertEqual(next(generator), b"RIFFpartial")

            def close_generator() -> None:
                try:
                    generator.close()
                except BaseException as exc:
                    errors.append(exc)

            closer = threading.Thread(target=close_generator)
            closer.start()
            closer.join(timeout=2)

        release.set()
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])

    def test_chinese_prosody_uses_emotion_appropriate_endings(self) -> None:
        self.assertEqual(companion_service.prepare_speech_prosody("你终于做到了。", "cheerful", "zh"), "你终于做到了！")
        self.assertEqual(companion_service.prepare_speech_prosody("突然这么说。", "shy", "zh"), "突然这么说……")
        self.assertEqual(companion_service.prepare_speech_prosody("おやすみ。", "gentle", "ja"), "おやすみ。")

    def test_chinese_prosody_preserves_each_line_as_separate_dialogue(self) -> None:
        self.assertEqual(
            companion_service.prepare_speech_prosody("第一句\n第二句", "neutral", "zh"),
            "第一句。\n第二句。",
        )

    def test_chinese_prosody_shapes_existing_clause_breaks(self) -> None:
        self.assertEqual(
            companion_service.prepare_speech_prosody("太好了，你做到了。", "cheerful", "zh"),
            "太好了！你做到了！",
        )
        self.assertEqual(
            companion_service.prepare_speech_prosody("听起来很难受，先休息一下。", "concerned", "zh"),
            "听起来很难受，先休息一下。",
        )
        self.assertEqual(
            companion_service.prepare_speech_prosody("先停一下，别这样否定自己。", "serious", "zh"),
            "先停一下。别这样否定自己。",
        )

    def test_chinese_prosody_does_not_overuse_ellipsis_for_shy_speech(self) -> None:
        self.assertEqual(
            companion_service.prepare_speech_prosody("……你突然这么说，我有点不好意思。", "shy", "zh"),
            "…你突然这么说，我有点不好意思……",
        )

    def test_wav_postprocess_trims_edge_silence_and_applies_volume(self) -> None:
        source = BytesIO()
        samples = array("h", [0] * 160 + [1000] * 400 + [0] * 160)
        with wave.open(source, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(1000)
            audio.writeframes(samples.tobytes())

        processed = companion_service._postprocess_speech_wav(source.getvalue(), 50)
        with wave.open(BytesIO(processed), "rb") as audio:
            output_samples = array("h")
            output_samples.frombytes(audio.readframes(audio.getnframes()))
            frame_count = audio.getnframes()

        self.assertEqual(frame_count, 520)
        self.assertEqual(max(output_samples), 500)

    def test_voice_quality_gate_accepts_clear_matching_audio(self) -> None:
        with patch(
            "app.companion_service.system_audio_service.transcribe_wav_for_quality",
            return_value={"text": "你好小落", "language": "zh", "probability": 0.98, "error": ""},
        ):
            result = companion_service.inspect_speech_wav_quality(
                _tone_wav_bytes(),
                "你好，小落",
                language="zh",
            )

        self.assertTrue(result["passed"])
        self.assertEqual(result["semantic_check"], "passed")
        self.assertGreaterEqual(result["similarity"], 0.9)

    def test_voice_quality_gate_rejects_silence_and_mismatched_speech(self) -> None:
        silent = companion_service.inspect_speech_wav_quality(
            _tone_wav_bytes(amplitude=0),
            "你好，小落",
            language="zh",
            use_local_asr=False,
        )
        self.assertFalse(silent["passed"])
        self.assertIn("语音整体音量过低", silent["reasons"])

        with patch(
            "app.companion_service.system_audio_service.transcribe_wav_for_quality",
            return_value={"text": "今天天气完全不同", "language": "zh", "probability": 0.9, "error": ""},
        ):
            mismatch = companion_service.inspect_speech_wav_quality(
                _tone_wav_bytes(),
                "我们一起去看电影吧",
                language="zh",
            )
        self.assertFalse(mismatch["passed"])
        self.assertEqual(mismatch["semantic_check"], "failed")

    def test_gpt_sovits_uses_matching_emotion_reference(self) -> None:
        training_root = companion_service.settings.voice_training_dir
        training_root.mkdir(parents=True)
        gentle_audio = training_root / "gentle.wav"
        gentle_audio.write_bytes(b"RIFFgentle")
        (training_root / "emotion-references.json").write_text(
            json.dumps({
                "gentle": {
                    "audio": str(gentle_audio),
                    "text": "ゆっくり休んでね。",
                    "language": "ja",
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        fallback_audio = companion_service.save_voice_reference_data_url(
            "data:audio/mpeg;base64," + base64.b64encode(b"ID3fallback").decode("ascii"),
            "fallback.mp3",
        )
        companion_service.save_config({"gpt_sovits_prompt_text": "fallback"})
        wav = b"RIFF" + (b"\0" * 60)
        recorded: dict[str, object] = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json):
                recorded.update(json)
                return SimpleNamespace(is_success=True, content=wav)

        with patch("app.companion_service.httpx.Client", FakeClient):
            companion_service._synthesize_gpt_sovits_wav("晚安，早点休息", companion_service.load_config())

        self.assertEqual(recorded["ref_audio_path"], str(gentle_audio.resolve()))
        self.assertEqual(recorded["aux_ref_audio_paths"], [])
        self.assertEqual(recorded["prompt_text"], "ゆっくり休んでね。")
        self.assertEqual(recorded["prompt_lang"], "ja")

    def test_emotion_reference_can_be_selected_by_target_language(self) -> None:
        training_root = companion_service.settings.voice_training_dir
        training_root.mkdir(parents=True)
        zh_audio = training_root / "gentle-zh.wav"
        ja_audio = training_root / "gentle-ja.wav"
        zh_audio.write_bytes(b"RIFFzh")
        ja_audio.write_bytes(b"RIFFja")
        (training_root / "emotion-references.json").write_text(
            json.dumps({
                "zh": {
                    "gentle": {
                        "audio": str(zh_audio),
                        "text": "今天辛苦了。",
                        "language": "zh",
                    }
                },
                "ja": {
                    "gentle": {
                        "audio": str(ja_audio),
                        "text": "今日はお疲れさま。",
                        "language": "ja",
                    }
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        companion_service.save_voice_reference_data_url(
            "data:audio/mpeg;base64," + base64.b64encode(b"ID3fallback").decode("ascii"),
            "fallback.mp3",
        )
        config = companion_service.load_config()

        zh_reference = companion_service._emotion_reference(config, "gentle", "zh")
        ja_reference = companion_service._emotion_reference(config, "gentle", "ja")

        self.assertEqual(zh_reference, (zh_audio.resolve(), "今天辛苦了。", "zh"))
        self.assertEqual(ja_reference, (ja_audio.resolve(), "今日はお疲れさま。", "ja"))

    def test_chinese_reference_wins_over_original_japanese_training_clip(self) -> None:
        training_root = companion_service.settings.voice_training_dir
        training_root.mkdir(parents=True)
        original = training_root / "original-ja.wav"
        chinese = training_root / "generated-zh.wav"
        original.write_bytes(b"RIFFja")
        chinese.write_bytes(b"RIFFzh")
        (training_root / "emotion-references.json").write_text(
            json.dumps({
                "gentle": {"audio": str(original), "text": "Japanese reference", "language": "ja"},
                "zh": {
                    "gentle": {"audio": str(chinese), "text": "Chinese reference", "language": "zh"}
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        selected = companion_service._emotion_reference(
            companion_service.load_config(),
            "gentle",
            "zh",
        )

        self.assertEqual(selected, (chinese.resolve(), "Chinese reference", "zh"))

    def test_emotion_reference_does_not_require_manual_default_audio(self) -> None:
        training_root = companion_service.settings.voice_training_dir
        training_root.mkdir(parents=True)
        gentle_audio = training_root / "gentle-ja.wav"
        gentle_audio.write_bytes(b"RIFFgentle")
        (training_root / "emotion-references.json").write_text(
            json.dumps({
                "gentle": {
                    "audio": str(gentle_audio),
                    "text": "ゆっくり休んでね。",
                    "language": "ja",
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        reference = companion_service._emotion_reference(
            companion_service.load_config(),
            "gentle",
            "zh",
        )

        self.assertEqual(reference, (gentle_audio.resolve(), "ゆっくり休んでね。", "ja"))

    def test_voice_runtime_reports_emotion_library_and_started_weights(self) -> None:
        training_root = companion_service.settings.voice_training_dir
        source = training_root / "GPT-SoVITS"
        gpt_model = source / "GPT_weights_v2" / "mio.ckpt"
        sovits_model = source / "SoVITS_weights_v2" / "mio.pth"
        config_path = source / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        reference = training_root / "gentle.wav"
        for path, content in (
            (gpt_model, b"gpt"),
            (sovits_model, b"sovits"),
            (reference, b"RIFFreference"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "custom:\n"
            f"  t2s_weights_path: {json.dumps(str(gpt_model))}\n"
            f"  vits_weights_path: {json.dumps(str(sovits_model))}\n",
            encoding="utf-8",
        )
        (training_root / "emotion-references.json").write_text(
            json.dumps({
                "gentle": {
                    "audio": str(reference),
                    "text": "ゆっくり休んでね。",
                    "language": "ja",
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        with patch("app.companion_service._probe_gpt_sovits", return_value=True):
            status = companion_service.voice_runtime_status()

        self.assertTrue(status["emotion_reference_ready"])
        self.assertEqual(status["emotion_reference_count"], 1)
        self.assertEqual(status["active_weights"]["gpt"], str(gpt_model.resolve()))
        self.assertEqual(status["active_weights"]["sovits"], str(sovits_model.resolve()))

        training_status = companion_service.voice_training_status()
        self.assertTrue(training_status["trained_ready"])
        self.assertEqual(training_status["gpt_model"], str(gpt_model.resolve()))
        self.assertEqual(training_status["sovits_model"], str(sovits_model.resolve()))

    def test_voice_warmup_runs_discarded_synthesis_without_playing_audio(self) -> None:
        original_metrics = dict(companion_service._voice_runtime_metrics)
        companion_service._voice_runtime_metrics.update({
            "warmup_state": "idle",
            "warmup_seconds": None,
            "warmup_error": "",
        })
        try:
            with (
                patch("app.companion_service._uses_genie_runtime", return_value=True),
                patch("app.genie_tts_service.runtime_status", return_value={"hot": False}),
                patch("app.companion_service.synthesize_speech_wav") as synthesize,
            ):
                result = companion_service.warm_voice_runtime()
        finally:
            metrics = dict(companion_service._voice_runtime_metrics)
            companion_service._voice_runtime_metrics.clear()
            companion_service._voice_runtime_metrics.update(original_metrics)

        synthesize.assert_called_once_with(
            "你好呀",
            context="Mio 内部语音预热",
            emotion="gentle",
            language="zh",
        )
        self.assertEqual(result["warmup_state"], "ready")
        self.assertEqual(metrics["warmup_state"], "ready")

    def test_genie_stream_skips_a_failed_segment_and_continues(self) -> None:
        wav = _genie_wav_bytes()

        def synthesize(text, **kwargs):
            del kwargs
            if text.startswith("坏段"):
                raise OSError("G2P failed")
            return wav

        with (
            patch("app.companion_service.resolve_voice_profile", return_value=("mio", {"engine": "gpt_sovits"})),
            patch("app.companion_service._uses_genie_runtime", return_value=True),
            patch("app.companion_service.synthesize_speech_wav", side_effect=synthesize),
        ):
            chunks = list(companion_service.iter_speech_wav_stream("第一句。\n坏段。\n最后一句。"))

        self.assertEqual(chunks[0][:4], b"RIFF")
        self.assertEqual(len(chunks), 4)
        self.assertTrue(all(chunk for chunk in chunks))

    def test_genie_stream_translates_whole_reply_once_before_splitting(self) -> None:
        wav = _genie_wav_bytes(frames=320)
        source = "我是澪，是洛从小一起长大的青梅竹马，也是他现在的小女友。"
        translated = "私は澪だよ。洛とは幼いころから一緒に育った、今の彼女なんだ。"
        config = {
            "engine": "gpt_sovits",
            "gpt_sovits_translate_to_japanese": True,
        }
        with (
            patch("app.companion_service.resolve_voice_profile", return_value=("mio", config)),
            patch("app.companion_service._uses_genie_runtime", return_value=True),
            patch(
                "app.companion_service._prepare_speech_input",
                return_value=(translated, "ja", True),
            ) as prepare,
            patch(
                "app.companion_service.synthesize_speech_wav",
                return_value=wav,
            ) as synthesize,
        ):
            chunks = list(companion_service.iter_speech_wav_stream(source, language="ja"))

        prepare.assert_called_once_with(source, config)
        self.assertGreater(len(synthesize.call_args_list), 1)
        self.assertTrue(chunks[0].startswith(b"RIFF"))
        self.assertTrue(all(call.kwargs["_prepared_language"] == "ja" for call in synthesize.call_args_list))
        self.assertNotIn(source, [call.args[0] for call in synthesize.call_args_list])

    def test_genie_stream_translation_failure_speaks_japanese_status(self) -> None:
        wav = _genie_wav_bytes(frames=320)
        source = "这是一段需要翻译的中文回复。"
        config = {
            "engine": "gpt_sovits",
            "gpt_sovits_translate_to_japanese": True,
        }
        with (
            patch("app.companion_service.resolve_voice_profile", return_value=("mio", config)),
            patch("app.companion_service._uses_genie_runtime", return_value=True),
            patch(
                "app.companion_service._prepare_speech_input",
                side_effect=ValueError("翻译模型请求超时"),
            ) as prepare,
            patch(
                "app.companion_service.synthesize_speech_wav",
                return_value=wav,
            ) as synthesize,
        ):
            chunks = list(companion_service.iter_speech_wav_stream(source, language="ja"))

        prepare.assert_called_once_with(source, config)
        spoken = "".join(call.args[0] for call in synthesize.call_args_list)
        self.assertEqual(spoken, companion_service.SPEECH_JAPANESE_TRANSLATION_FAILURE_TEXT)
        self.assertNotIn(source, spoken)
        self.assertTrue(all(call.kwargs["_prepared_language"] == "ja" for call in synthesize.call_args_list))
        self.assertTrue(chunks[0].startswith(b"RIFF"))

    def test_genie_stream_raises_when_every_segment_fails(self) -> None:
        with (
            patch("app.companion_service.resolve_voice_profile", return_value=("mio", {"engine": "gpt_sovits"})),
            patch("app.companion_service._uses_genie_runtime", return_value=True),
            patch("app.companion_service.synthesize_speech_wav", side_effect=OSError("G2P failed")),
        ):
            stream = companion_service.iter_speech_wav_stream("坏段。")
            self.assertEqual(next(stream)[:4], b"RIFF")
            with self.assertRaisesRegex(OSError, "没有生成任何可播放片段"):
                next(stream)

    def test_voice_warmup_is_scheduled_only_once(self) -> None:
        original_metrics = dict(companion_service._voice_runtime_metrics)
        companion_service._voice_runtime_metrics["warmup_state"] = "idle"
        try:
            with patch("app.companion_service.threading.Thread") as thread:
                self.assertTrue(companion_service.warm_voice_runtime_async())
                self.assertFalse(companion_service.warm_voice_runtime_async())
            thread.return_value.start.assert_called_once_with()
        finally:
            companion_service._voice_runtime_metrics.clear()
            companion_service._voice_runtime_metrics.update(original_metrics)

    def test_genie_language_switch_warmup_discards_internal_synthesis(self) -> None:
        started = threading.Event()
        with (
            patch("app.companion_service._uses_genie_runtime", return_value=True),
            patch("app.genie_tts_service.runtime_status", return_value={"hot": False}),
            patch(
                "app.companion_service.synthesize_speech_wav",
                side_effect=lambda *_args, **_kwargs: started.set(),
            ) as synthesize,
        ):
            self.assertTrue(companion_service.warm_voice_language_async("ja"))
            self.assertTrue(started.wait(2))

        synthesize.assert_called_once_with(
            "こんにちは",
            context="Mio 内部语音预热",
            emotion="gentle",
            language="ja",
        )

    def test_voice_runtime_warmup_route_does_not_start_screen_observation(self) -> None:
        expected = {"warmup_state": "ready", "warmup_seconds": 1.25}
        with (
            patch(
                "app.routes.companion.companion_service.warm_voice_runtime",
                return_value=expected,
            ) as warmup,
            patch("app.routes.companion.screen_observation_service.analyze_once") as analyze,
        ):
            result = asyncio.run(companion_voice_runtime("warmup"))

        self.assertEqual(result, expected)
        warmup.assert_called_once_with()
        analyze.assert_not_called()

    def test_chinese_prefers_matching_reference_over_original_japanese_reference(self) -> None:
        training_root = companion_service.settings.voice_training_dir
        training_root.mkdir(parents=True)
        original_audio = training_root / "gentle-original.wav"
        generated_audio = training_root / "gentle-generated-zh.wav"
        original_audio.write_bytes(b"RIFForiginal")
        generated_audio.write_bytes(b"RIFFgenerated")
        (training_root / "emotion-references.json").write_text(
            json.dumps({
                "gentle": {
                    "audio": str(original_audio),
                    "text": "ゆっくり休んでね。",
                    "language": "ja",
                },
                "zh": {
                    "gentle": {
                        "audio": str(generated_audio),
                        "text": "今天辛苦了。",
                        "language": "zh",
                    }
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        companion_service.save_voice_reference_data_url(
            "data:audio/mpeg;base64," + base64.b64encode(b"ID3fallback").decode("ascii"),
            "fallback.mp3",
        )

        reference = companion_service._emotion_reference(
            companion_service.load_config(),
            "gentle",
            "zh",
        )

        self.assertEqual(reference[0], generated_audio.resolve())
        self.assertEqual(reference[2], "zh")

    def test_gpt_sovits_failure_never_falls_back_to_system_voice(self) -> None:
        with (
            patch("app.companion_service._ensure_gpt_sovits_service"),
            patch("app.companion_service._synthesize_gpt_sovits_wav", side_effect=OSError("service offline")),
        ):
            with self.assertRaisesRegex(OSError, "当前角色音色暂时不可用"):
                companion_service.synthesize_speech_wav("你好")

        self.assertIn("offline", companion_service._gpt_sovits_last_error)

    def test_synthesis_ensures_gpt_sovits_before_use(self) -> None:
        companion_service.save_config({"voice_engine": "gpt_sovits"})
        generated_wav = _tone_wav_bytes()
        with (
            patch("app.companion_service._ensure_gpt_sovits_service") as ensure,
            patch("app.companion_service._synthesize_gpt_sovits_wav", return_value=generated_wav) as synthesize,
            patch("app.companion_service.system_audio_service.transcribe_wav_for_quality", return_value=None),
        ):
            content = companion_service.synthesize_speech_wav("你好")

        self.assertEqual(
            content,
            companion_service._postprocess_speech_wav(
                generated_wav,
                int(companion_service.load_config().get("voice_volume", 85)),
            ),
        )
        ensure.assert_called_once_with()
        synthesize.assert_called_once()

    def test_genie_is_default_local_runtime_and_reuses_emotion_reference(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        generated_wav = _tone_wav_bytes()
        reference = Path(self.temp_dir.name) / "reference.wav"
        reference.write_bytes(_tone_wav_bytes(seconds=3.0))
        with (
            patch(
                "app.companion_service._emotion_reference",
                return_value=(reference, "参考原文", "ja"),
            ) as select_reference,
            patch("app.genie_tts_service.synthesize_wav", return_value=generated_wav) as synthesize,
            patch("app.companion_service.inspect_speech_wav_quality") as expensive_quality,
        ):
            content = companion_service.synthesize_speech_wav("你好", language="zh")

        self.assertTrue(content.startswith(b"RIFF"))
        select_reference.assert_called_once()
        genie_config = synthesize.call_args.args[1]
        self.assertEqual(genie_config["gpt_sovits_ref_audio"], str(reference))
        self.assertEqual(genie_config["gpt_sovits_prompt_text"], "参考原文")
        expensive_quality.assert_not_called()

    def test_genie_retries_abnormally_long_isolated_acknowledgement(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        long_reference_like_wav = _tone_wav_bytes(seconds=5.0)
        recovered_wav = _tone_wav_bytes(seconds=2.0)
        reference = Path(self.temp_dir.name) / "reference.wav"
        reference.write_bytes(long_reference_like_wav)
        with (
            patch(
                "app.companion_service._emotion_reference",
                return_value=(reference, "参考原文", "ja"),
            ),
            patch(
                "app.genie_tts_service.synthesize_wav",
                side_effect=[long_reference_like_wav, recovered_wav],
            ) as synthesize,
        ):
            content = companion_service.synthesize_speech_wav("うん", language="ja")

        self.assertTrue(content.startswith(b"RIFF"))
        self.assertEqual(synthesize.call_count, 2)
        self.assertIn("続けて話してね", synthesize.call_args.args[0])

    def test_genie_accepts_five_second_natural_recovery_phrase(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        long_reference_like_wav = _genie_wav_bytes(frames=32000 * 8)
        valid_recovery_wav = _genie_wav_bytes(frames=int(32000 * 4.88))
        reference = Path(self.temp_dir.name) / "reference.wav"
        reference.write_bytes(_genie_wav_bytes(frames=32000 * 2))
        with (
            patch(
                "app.companion_service._emotion_reference",
                return_value=(reference, "参考原文", "ja"),
            ),
            patch(
                "app.genie_tts_service.synthesize_wav",
                side_effect=[long_reference_like_wav, valid_recovery_wav],
            ) as synthesize,
        ):
            content = companion_service.synthesize_speech_wav("うん", language="ja")

        self.assertTrue(content.startswith(b"RIFF"))
        self.assertEqual(synthesize.call_count, 2)

    def test_genie_blocks_reference_audio_when_safe_retry_leaks_again(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        reference_wav = _tone_wav_bytes(seconds=5.0)
        reference = Path(self.temp_dir.name) / "reference.wav"
        reference.write_bytes(reference_wav)
        with (
            patch(
                "app.companion_service._emotion_reference",
                return_value=(reference, "参考原文", "ja"),
            ),
            patch("app.genie_tts_service.synthesize_wav", return_value=reference_wav) as synthesize,
        ):
            with self.assertRaisesRegex(OSError, "已阻止播放参考音频"):
                companion_service.synthesize_speech_wav("うん", language="ja")

        self.assertEqual(synthesize.call_count, 2)

    def test_reference_audio_leak_score_handles_gain_difference(self) -> None:
        reference_wav = _tone_wav_bytes(seconds=1.0, amplitude=5000)
        generated_wav = _tone_wav_bytes(seconds=1.0, amplitude=2500)
        reference = Path(self.temp_dir.name) / "reference.wav"
        reference.write_bytes(reference_wav)

        leaked, score = companion_service._looks_like_reference_audio(generated_wav, reference)

        self.assertTrue(leaked)
        self.assertIsNotNone(score)
        self.assertGreaterEqual(float(score), 0.84)

    def test_genie_checks_every_training_reference_not_only_selected_reference(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        primary = Path(self.temp_dir.name) / "primary.wav"
        primary.write_bytes(_tone_wav_bytes(seconds=2.0, amplitude=3000))
        other = companion_service.settings.voice_training_dir / "materials" / "prepared" / "wav32k_v2" / "other.wav"
        other.parent.mkdir(parents=True)
        leaked_other_reference = _tone_wav_bytes(seconds=1.0, amplitude=5000)
        other.write_bytes(leaked_other_reference)
        mapping = companion_service.settings.voice_training_dir / "emotion-references.json"
        mapping.parent.mkdir(parents=True, exist_ok=True)
        mapping.write_text(
            json.dumps({
                "neutral": {
                    "audio": "materials/prepared/wav32k_v2/other.wav",
                    "text": "別の参考音声です。",
                    "language": "ja",
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        valid_retry = _tone_wav_bytes(seconds=0.3, amplitude=1700)
        with (
            patch(
                "app.companion_service._emotion_reference",
                return_value=(primary, "現在の参考原文", "ja"),
            ),
            patch(
                "app.genie_tts_service.synthesize_wav",
                side_effect=[leaked_other_reference, valid_retry],
            ) as synthesize,
        ):
            content = companion_service.synthesize_speech_wav(
                "これは十分に長い通常の読み上げ文章です。",
                language="ja",
            )

        self.assertTrue(content.startswith(b"RIFF"))
        self.assertEqual(synthesize.call_count, 2)
        self.assertIn("これは十分に長い", synthesize.call_args_list[1].args[0])

    def test_genie_blocks_long_segment_when_another_training_reference_leaks_twice(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        primary = Path(self.temp_dir.name) / "primary.wav"
        primary.write_bytes(_tone_wav_bytes(seconds=2.0, amplitude=3000))
        other = companion_service.settings.voice_training_dir / "materials" / "prepared" / "wav32k_v2" / "other.wav"
        other.parent.mkdir(parents=True)
        leaked_other_reference = _tone_wav_bytes(seconds=1.0, amplitude=5000)
        other.write_bytes(leaked_other_reference)
        with (
            patch(
                "app.companion_service._emotion_reference",
                return_value=(primary, "現在の参考原文", "ja"),
            ),
            patch(
                "app.genie_tts_service.synthesize_wav",
                return_value=leaked_other_reference,
            ) as synthesize,
        ):
            with self.assertRaisesRegex(OSError, "已阻止播放参考音频"):
                companion_service.synthesize_speech_wav(
                    "これは十分に長い通常の読み上げ文章です。",
                    language="ja",
                )

        self.assertEqual(synthesize.call_count, 2)

    def test_prepared_genie_segment_does_not_translate_again(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        generated_wav = _genie_wav_bytes(frames=320)
        reference = Path(self.temp_dir.name) / "reference.wav"
        reference.write_bytes(generated_wav)
        with (
            patch(
                "app.companion_service._emotion_reference",
                return_value=(reference, "参考原文", "ja"),
            ),
            patch("app.companion_service._prepare_speech_input") as prepare,
            patch("app.genie_tts_service.synthesize_wav", return_value=generated_wav),
        ):
            content = companion_service.synthesize_speech_wav(
                "私は澪だよ。",
                language="ja",
                _prepared_language="ja",
            )

        self.assertTrue(content.startswith(b"RIFF"))
        prepare.assert_not_called()

    def test_genie_stream_splits_long_text_and_yields_pcm_after_one_wav_header(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        synthesized: list[str] = []

        def synthesize(text: str, **_kwargs) -> bytes:
            synthesized.append(text)
            return _genie_wav_bytes(frames=320)

        with (
            patch("app.companion_service.resolve_voice_profile", return_value=("mio", {"engine": "gpt_sovits"})),
            patch("app.companion_service.synthesize_speech_wav", side_effect=synthesize),
        ):
            chunks = list(companion_service.iter_speech_wav_stream(
                "第一段应该尽快开始播放，第二段继续在后台生成。",
                language="zh",
            ))

        self.assertGreaterEqual(len(synthesized), 2)
        self.assertTrue(chunks[0].startswith(b"RIFF"))
        self.assertEqual(chunks[0][8:12], b"WAVE")
        self.assertTrue(all(chunk == b"\x01\x00" * 320 for chunk in chunks[1:]))

    def test_genie_stream_synthesizes_lines_separately_with_audible_pause(self) -> None:
        companion_service.save_config({"local_voice_runtime": "genie"})
        synthesized: list[str] = []

        def synthesize(text: str, **_kwargs) -> bytes:
            synthesized.append(text)
            return _genie_wav_bytes(frames=320)

        with (
            patch("app.companion_service.resolve_voice_profile", return_value=("mio", {"engine": "gpt_sovits"})),
            patch("app.companion_service.synthesize_speech_wav", side_effect=synthesize),
        ):
            chunks = list(companion_service.iter_speech_wav_stream(
                "第一句\n第二句",
                language="zh",
            ))

        self.assertEqual(synthesized, ["第一句", "第二句"])
        self.assertEqual(chunks[1], b"\x01\x00" * 320)
        self.assertEqual(chunks[2], b"\x00\x00" * 9600)
        self.assertEqual(chunks[3], b"\x01\x00" * 320)

    def test_voice_weight_options_searches_known_external_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gpt_dir = root / "GPT-SoVITS" / "GPT_weights_v2"
            sovits_dir = root / "GPT-SoVITS" / "SoVITS_weights_v2"
            gpt_dir.mkdir(parents=True)
            sovits_dir.mkdir(parents=True)
            gpt = gpt_dir / "mio.ckpt"
            sovits = sovits_dir / "mio.pth"
            gpt.write_bytes(b"gpt")
            sovits.write_bytes(b"sovits")
            fake_settings = SimpleNamespace(
                voice_training_dir=root / "voice",
                workspace_root=root,
                project_root=root,
                source_workspace_root=root,
            )
            with patch.object(companion_service, "settings", fake_settings):
                options = companion_service._voice_weight_options()
        self.assertEqual(options["gpt"][0]["path"], str(gpt.resolve()))
        self.assertEqual(options["sovits"][0]["path"], str(sovits.resolve()))

    def test_observation_start_and_stop_bind_system_audio_without_stopping_phone_asr(self) -> None:
        observer = companion_service.window_observer
        with (
            patch.object(observer, "select_screen"),
            patch.object(observer, "start", return_value={"running": True}),
            patch.object(observer, "stop", return_value={"running": False}),
            patch.object(companion_service, "save_config") as save_config,
            patch("app.routes.companion.system_audio_service.start") as audio_start,
            patch("app.routes.companion.system_audio_service.stop") as audio_stop,
            patch("app.routes.companion.screen_observation_service.end_session"),
        ):
            companion_routes._pet_call_resource_owner = ""
            started = companion_routes._start_observation_runtime(
                interval_ms=1000,
                capture_only=False,
                screen_scope="primary",
            )
            companion_routes._pet_call_resource_owner = "call-active"
            stopped = companion_routes._stop_observation_runtime()
            companion_routes._pet_call_resource_owner = ""

        self.assertTrue(started["running"])
        self.assertFalse(stopped["running"])
        self.assertEqual(
            [call.args[0] for call in save_config.call_args_list],
            [{"screen_audio_enabled": True}, {"screen_audio_enabled": False}],
        )
        audio_start.assert_called_once()
        audio_stop.assert_not_called()

    def test_so_vits_svc_synthesis_converts_base_gpt_audio(self) -> None:
        companion_service.save_config({
            "voice_engine": "gpt_sovits",
            "voice_profiles": {
                "mio": {
                    "name": "基础音色",
                    "engine": "gpt_sovits",
                    "gpt_sovits_ref_audio": "C:/voice/base.wav",
                },
                "huahuo": {
                    "name": "花火",
                    "engine": "so_vits_svc",
                    "so_vits_svc_model_path": "C:/voice/G_800.pth",
                    "so_vits_svc_config_path": "C:/voice/config.json",
                    "so_vits_svc_speaker": "huahuo",
                    "so_vits_svc_base_profile_id": "mio",
                },
            },
            "default_voice_profile_id": "huahuo",
        })
        base_wav = _tone_wav_bytes(amplitude=4000)
        converted_wav = _tone_wav_bytes(amplitude=6000)
        with (
            patch("app.companion_service._ensure_gpt_sovits_service") as ensure,
            patch("app.companion_service._synthesize_gpt_sovits_wav", return_value=base_wav) as synthesize,
            patch("app.so_vits_svc_service.convert_wav", return_value=converted_wav) as convert,
            patch("app.companion_service.inspect_speech_wav_quality", return_value={"passed": True, "reasons": []}),
        ):
            content = companion_service.synthesize_speech_wav("你好", language="zh")

        self.assertEqual(
            content,
            companion_service._postprocess_speech_wav(
                converted_wav,
                int(companion_service.load_config().get("voice_volume", 85)),
            ),
        )
        ensure.assert_called_once_with()
        synthesis_config = synthesize.call_args.args[1]
        self.assertEqual(synthesis_config["voice_profile_id"], "mio")
        self.assertEqual(synthesis_config["gpt_sovits_text_language"], "zh")
        convert.assert_called_once()
        self.assertEqual(convert.call_args.args[0], base_wav)
        self.assertEqual(convert.call_args.args[1]["voice_profile_id"], "huahuo")

    def test_so_vits_svc_conversion_releases_temporary_wav_handles(self) -> None:
        generated = b"RIFF" + (b"\0" * 40)

        def fake_request(payload, *, timeout):
            Path(payload["output_path"]).write_bytes(generated)
            return {"ok": True}

        with patch("app.so_vits_svc_service._request", side_effect=fake_request):
            content = so_vits_svc_service.convert_wav(
                generated,
                {
                    "so_vits_svc_model_path": "C:/voice/G_800.pth",
                    "so_vits_svc_config_path": "C:/voice/config.json",
                    "so_vits_svc_speaker": "huahuo",
                },
            )

        self.assertEqual(content, generated)
        self.assertEqual(list(companion_service.settings.companion_dir.glob("mio-svc-*.wav")), [])

    def test_gpt_sovits_failure_is_also_strict_for_qq(self) -> None:
        with patch("app.companion_service._ensure_gpt_sovits_service", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "当前角色音色暂时不可用"):
                companion_service.synthesize_speech_wav("你好", require_configured_engine=True)

    def test_speech_text_removes_voice_prefixes_and_stage_directions(self) -> None:
        self.assertEqual(
            companion_service.clean_speech_text("语音消息（约3秒）：‘嗯，这次声音清楚吗？’"),
            "嗯，这次声音清楚吗？",
        )
        self.assertEqual(
            companion_service.clean_speech_text("然后轻轻笑了一声：\"我叫澪\""),
            "我叫澪",
        )
        self.assertEqual(
            companion_service.clean_speech_text("我会陪着你。（声音越来越小）"),
            "我会陪着你。",
        )
        self.assertEqual(
            companion_service.clean_speech_text("我是你的私人AI，也能看PDF"),
            "我是你的私人人工智能，也能看批迪艾弗",
        )
        self.assertEqual(
            companion_service.clean_speech_text("我是澪——安静，也认真——"),
            "我是澪，安静，也认真，",
        )
        self.assertEqual(companion_service.clean_speech_text("（这次应该能听到了吧？）"), "")
        self.assertEqual(companion_service.clean_speech_text("）"), "")

    def test_speech_text_preserves_newlines_between_dialogue_lines(self) -> None:
        self.assertEqual(
            companion_service.clean_speech_text("第一句\r\n（轻声）\r\n第二句"),
            "第一句\n第二句",
        )

    def test_gpt_sovits_start_prepares_runtime_dependencies(self) -> None:
        root = companion_service.settings.voice_training_dir
        source = root / "GPT-SoVITS"
        python = root / ".voice-env" / "Scripts" / "python.exe"
        api = source / "api_v2.py"
        tts_config = source / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        ffmpeg = root / "cache" / "bin" / "ffmpeg.exe"
        nltk_tagger = root / "cache" / "nltk_data" / "taggers" / "averaged_perceptron_tagger_eng"
        for path in (python, api, tts_config, ffmpeg):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        nltk_tagger.mkdir(parents=True)
        for filename, payload in zip(
            companion_service.GPT_SOVITS_TAGGER_FILES,
            ({}, {}, ["NN"]),
            strict=True,
        ):
            (nltk_tagger / filename).write_text(json.dumps(payload), encoding="utf-8")

        process = SimpleNamespace(poll=lambda: None)
        original_process = companion_service._gpt_sovits_process
        companion_service._gpt_sovits_process = None
        try:
            with (
                patch("app.companion_service._probe_gpt_sovits", return_value=False),
                patch("app.companion_service.voice_runtime_status", return_value={"service_running": False}),
                patch("app.companion_service.subprocess.Popen", return_value=process) as popen,
            ):
                companion_service.start_gpt_sovits_service()
        finally:
            companion_service._gpt_sovits_process = original_process

        cache_dir = source / "GPT_SoVITS" / "pretrained_models" / "fast_langdetect"
        self.assertTrue(cache_dir.is_dir())
        runtime_env = popen.call_args.kwargs["env"]
        self.assertEqual(runtime_env["PATH"].split(os.pathsep)[0], str(ffmpeg.parent))
        self.assertEqual(runtime_env["NLTK_DATA"], str(root / "cache" / "nltk_data"))

    def test_gpt_sovits_missing_nltk_dependency_is_downloaded_once(self) -> None:
        root = companion_service.settings.voice_training_dir
        python = root / ".voice-env" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
        tagger = root / "cache" / "nltk_data" / "taggers" / "averaged_perceptron_tagger_eng"

        def complete_download(*args, **kwargs):
            tagger.mkdir(parents=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("app.companion_service.subprocess.run", side_effect=complete_download) as run:
            first = companion_service._prepare_gpt_sovits_nltk_data(root, python)
            second = companion_service._prepare_gpt_sovits_nltk_data(root, python)

        self.assertEqual(first, root / "cache" / "nltk_data")
        self.assertEqual(second, first)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertIn("averaged_perceptron_tagger_eng", command)
        self.assertEqual(run.call_args.kwargs["env"]["NLTK_DATA"], str(first))

    def test_gpt_sovits_uses_offline_pos_fallback_when_download_fails(self) -> None:
        root = companion_service.settings.voice_training_dir
        python = root / ".voice-env" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")

        with patch(
            "app.companion_service.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="offline"),
        ):
            nltk_data = companion_service._prepare_gpt_sovits_nltk_data(root, python)

        tagger = nltk_data / "taggers" / "averaged_perceptron_tagger_eng"
        self.assertEqual(
            json.loads((tagger / "averaged_perceptron_tagger_eng.classes.json").read_text(encoding="utf-8")),
            ["NN"],
        )
        self.assertTrue((tagger / ".mio-offline-fallback").is_file())

    def test_gpt_sovits_repairs_an_incomplete_nltk_resource_directory(self) -> None:
        root = companion_service.settings.voice_training_dir
        python = root / ".voice-env" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
        tagger = root / "cache" / "nltk_data" / "taggers" / "averaged_perceptron_tagger_eng"
        tagger.mkdir(parents=True)
        (tagger / "averaged_perceptron_tagger_eng.weights.json").write_text("{}", encoding="utf-8")

        with patch(
            "app.companion_service.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="offline"),
        ) as run:
            companion_service._prepare_gpt_sovits_nltk_data(root, python)

        run.assert_called_once()
        self.assertTrue(all((tagger / name).is_file() for name in companion_service.GPT_SOVITS_TAGGER_FILES))

    def test_avatar_data_url_is_converted_to_png(self) -> None:
        path = companion_service.save_avatar_data_url(_png_data_url())
        self.assertTrue(path.is_file())
        with Image.open(path) as image:
            self.assertEqual(image.mode, "RGBA")

    def test_voice_training_requires_the_complete_v2_model_set(self) -> None:
        root = companion_service.settings.voice_training_dir
        (root / "GPT-SoVITS").mkdir(parents=True)
        (root / "GPT-SoVITS" / "webui.py").write_text("", encoding="utf-8")
        (root / ".voice-env" / "Scripts").mkdir(parents=True)
        (root / ".voice-env" / "Scripts" / "python.exe").write_bytes(b"")
        (root / ".voice-env" / ".setup-complete").write_bytes(b"")
        (root / ".pretrained-v2-complete").write_bytes(b"")
        model = root / "GPT-SoVITS" / "GPT_SoVITS" / "pretrained_models" / "chinese-hubert-base" / "pytorch_model.bin"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"partial")

        status = companion_service.voice_training_status()

        self.assertTrue(status["source_ready"])
        self.assertTrue(status["environment_ready"])
        self.assertFalse(status["pretrained_ready"])
        self.assertEqual(status["model_count"], 1)
        self.assertEqual(status["expected_model_count"], 9)

    def test_voice_training_status_reports_completed_voice(self) -> None:
        root = companion_service.settings.voice_training_dir
        gpt_model = root / "models" / "mio.ckpt"
        sovits_model = root / "models" / "mio.pth"
        gpt_model.parent.mkdir(parents=True)
        gpt_model.write_bytes(b"gpt")
        sovits_model.write_bytes(b"sovits")
        (root / "training-status.json").write_text(
            json.dumps({
                "stage": "complete",
                "message": "training complete",
                "success": True,
                "updated_at": "2026-08-05T19:56:12",
            }),
            encoding="utf-8",
        )
        (root / "training-result.json").write_text(
            json.dumps({"gpt_model": str(gpt_model), "sovits_model": str(sovits_model)}),
            encoding="utf-8",
        )

        status = companion_service.voice_training_status()

        self.assertTrue(status["trained_ready"])
        self.assertEqual(status["message"], "Mio 的第一版专属音色已训练完成")

    def test_feed_skips_history_on_initial_poll(self) -> None:
        conversation_id = "desktop_pet"
        message_id = db.save_message("assistant", "旧消息", source="desktop", conversation_id=conversation_id)
        initial = asyncio.run(companion_feed(None))
        self.assertEqual(initial["latest_id"], message_id)
        self.assertEqual(initial["messages"], [])

        db.save_message("assistant", "群聊消息", source="qq_group", conversation_id="qq_group_123")
        isolated = asyncio.run(companion_feed(message_id))
        self.assertEqual(isolated["messages"], [])
        self.assertEqual(isolated["latest_id"], message_id)

        new_id = db.save_message("assistant", "新消息", source="desktop", conversation_id=conversation_id)
        update = asyncio.run(companion_feed(message_id))
        self.assertEqual(update["latest_id"], new_id)
        self.assertEqual(update["messages"][0]["content"], "新消息")

    def test_desktop_pet_chat_uses_dedicated_conversation_and_auto_route(self) -> None:
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="standard")
        result = SimpleNamespace(
            reply="我在\n慢慢说",
            replies=["我在", "慢慢说"],
            request_id="pet-chat",
            model_id="deepseek-v4-flash",
            reasoning_level="standard",
            speech_emotion="gentle",
        )

        async def fake_chat(message: str, **kwargs):
            self.assertEqual(message, "陪我说会儿话")
            self.assertEqual(kwargs["conversation_id"], "desktop_pet")
            self.assertEqual(kwargs["source"], "desktop_pet")
            self.assertFalse(kwargs["agent_tools_enabled"])
            self.assertTrue(kwargs["fast_path"])
            return result

        with (
            patch("app.routes.companion.select_auto_route", return_value=route),
            patch("app.routes.companion.chat_with_ai", side_effect=fake_chat),
            patch("app.routes.companion.companion_service.speak_text", return_value=True) as speak,
            patch(
                "app.routes.companion.route_observation_service.record_completed_route"
            ) as record_route,
        ):
            response = asyncio.run(companion_chat(CompanionChatRequest(message="陪我说会儿话")))

        self.assertEqual(response["replies"], ["我在", "慢慢说"])
        self.assertEqual(response["model_id"], "deepseek-v4-flash")
        self.assertTrue(response["voice_attempted"])
        self.assertTrue(response["spoken"])
        self.assertEqual(response["voice_error"], "")
        record_route.assert_called_once()
        self.assertEqual(record_route.call_args.kwargs["source"], "desktop_pet")
        self.assertEqual(record_route.call_args.kwargs["mode"], "automatic")
        speak.assert_called_once_with(
            "我在\n慢慢说",
            context="陪我说会儿话",
            emotion="gentle",
            wait=False,
            model_id="deepseek-v4-flash",
            language="zh",
        )

    def test_desktop_pet_chat_accepts_agent_model_selection(self) -> None:
        result = SimpleNamespace(
            reply="我在",
            replies=["我在"],
            request_id="pet-chat-explicit-model",
            model_id="deepseek-v4-flash",
            reasoning_level="thinking",
        )

        with (
            patch("app.routes.companion.resolve_model_id", return_value="deepseek-v4-flash") as resolve,
            patch("app.routes.companion.select_auto_route") as auto_route,
            patch("app.routes.companion.chat_with_ai", return_value=result) as chat,
            patch("app.routes.companion.companion_service.speak_text", return_value=True),
        ):
            asyncio.run(
                companion_chat(
                    CompanionChatRequest(
                        message="继续说",
                        model_id="deepseek-flash",
                        reasoning_level="thinking",
                    )
                )
            )

        resolve.assert_called_once_with("deepseek-flash")
        auto_route.assert_not_called()
        self.assertEqual(chat.call_args.kwargs["model_id"], "deepseek-v4-flash")
        self.assertEqual(chat.call_args.kwargs["reasoning_level"], "thinking")
        self.assertFalse(chat.call_args.kwargs["agent_tools_enabled"])
        self.assertTrue(chat.call_args.kwargs["fast_path"])

    def test_desktop_pet_chat_continues_screen_context_with_fresh_analysis(self) -> None:
        db.save_message(
            "assistant",
            "现在是游戏主菜单。",
            source="screen",
            conversation_id="desktop_pet",
        )
        result = ChatResult(
            reply="现在已经进入游戏场景了。",
            replies=["现在已经进入游戏场景了。"],
            request_id="screen-follow-up",
            model_id="vision-model",
            reasoning_level="low",
        )
        with (
            patch(
                "app.routes.companion.screen_observation_service.analyze_screen_chat_follow_up",
                new=AsyncMock(return_value=result),
            ) as analyze,
            patch("app.routes.companion.chat_with_ai", new=AsyncMock()) as chat,
            patch("app.routes.companion.companion_service.speak_text", return_value=True),
        ):
            response = asyncio.run(companion_chat(CompanionChatRequest(message="现在呢")))

        analyze.assert_awaited_once()
        chat.assert_not_awaited()
        self.assertEqual(response["reply"], "现在已经进入游戏场景了。")
        self.assertEqual(response["model_id"], "vision-model")

    def test_screen_chat_follow_up_respects_disabled_privacy_switch(self) -> None:
        disabled_status = {
            "enabled": False,
            "capture_only": False,
            "vision_available": True,
            "vision_route": "cloud",
            "budget": {"paused": False},
            "last_error": "",
        }
        with (
            patch(
                "app.screen_observation_service.companion_service.window_observer.status",
                return_value={"running": False},
            ),
            patch("app.screen_observation_service.status", return_value=disabled_status),
            patch("app.screen_observation_service.analyze_once", new=AsyncMock()) as analyze,
            patch("app.screen_observation_service.companion_service.window_observer.select_screen") as select,
            patch("app.screen_observation_service.companion_service.window_observer.stop"),
            patch("app.screen_observation_service.end_session"),
        ):
            result = asyncio.run(screen_observation_service.analyze_screen_chat_follow_up(
                "现在呢",
                conversation_id="desktop_pet",
                request_id="disabled-screen-follow-up",
                source="desktop_pet",
            ))

        analyze.assert_not_awaited()
        select.assert_not_called()
        self.assertEqual(result.model_id, "local-status")
        self.assertIn("关闭", result.reply)

    def test_one_shot_screen_chat_follow_up_stops_temporary_capture_once(self) -> None:
        before = {
            "enabled": True,
            "capture_only": False,
            "vision_available": True,
            "vision_route": "cloud",
            "budget": {"paused": False},
        }
        after = {
            **before,
            "last_model": "vision-model",
            "last_emotion": "gentle",
            "pipeline_timings": {"first_token_seconds": 1.25},
        }

        async def fake_analyze(**kwargs):
            db.save_message(
                "assistant",
                "这是刚截取的新画面。",
                source="screen",
                conversation_id=kwargs["conversation_id"],
                request_id=kwargs["request_id"],
                model_id="vision-model",
                reasoning_level="low",
            )
            return True

        with (
            patch(
                "app.screen_observation_service.companion_service.window_observer.status",
                return_value={"running": False},
            ),
            patch("app.screen_observation_service.status", side_effect=[before, after]),
            patch(
                "app.screen_observation_service.local_vision_service.unload_model"
            ),
             patch(
                 "app.screen_observation_service.companion_service.window_observer.select_screen"
             ) as select,
             patch(
                 "app.screen_observation_service.companion_service.window_observer.capture"
             ),
             patch(
                 "app.screen_observation_service.companion_service.window_observer.stop"
             ) as stop,
            patch("app.screen_observation_service.end_session") as end,
            patch(
                "app.screen_observation_service.analyze_once",
                new=AsyncMock(side_effect=fake_analyze),
            ) as analyze,
        ):
            result = asyncio.run(screen_observation_service.analyze_screen_chat_follow_up(
                "现在呢",
                conversation_id="desktop_pet",
                request_id="one-shot-screen-follow-up",
                source="desktop_pet",
            ))

        select.assert_called_once_with("primary")
        stop.assert_called_once()
        end.assert_called_once()
        self.assertTrue(analyze.await_args.kwargs["interactive"])
        self.assertFalse(analyze.await_args.kwargs["allow_direct_speech"])
        self.assertEqual(result.reply, "这是刚截取的新画面。")
        self.assertEqual(result.first_token_latency_ms, 1250.0)

    def test_desktop_pet_chat_accepts_pasted_image(self) -> None:
        route = SimpleNamespace(model_id="vision-model", reasoning_level="default")
        result = SimpleNamespace(
            reply="我看到了",
            replies=["我看到了"],
            request_id="pet-chat-image",
            model_id="vision-model",
            reasoning_level="default",
        )

        with (
            patch("app.routes.companion.image_attachment_from_data_url", return_value=SimpleNamespace()) as decode,
            patch("app.routes.companion.select_auto_route", return_value=route) as auto_route,
            patch("app.routes.companion.chat_with_ai", return_value=result) as chat,
            patch("app.routes.companion.companion_service.speak_text", return_value=True),
        ):
            response = asyncio.run(companion_chat(CompanionChatRequest(
                images=[{"name": "截图.png", "data_url": "data:image/png;base64,dGVzdA=="}],
            )))

        decode.assert_called_once()
        self.assertEqual(auto_route.call_args.kwargs["image_count"], 1)
        self.assertEqual(len(chat.call_args.kwargs["image_attachments"]), 1)
        self.assertEqual(response["reply"], "我看到了")

    def test_desktop_pet_chat_uses_its_saved_model_without_changing_shared_chat(self) -> None:
        companion_service.save_config({
            "chat_model_id": "deepseek-v4-flash",
            "chat_reasoning_level": "default",
            "pet_chat_model_id": "gpt-5.6-sol",
            "pet_chat_reasoning_level": "medium",
        })
        result = SimpleNamespace(
            reply="我在",
            replies=["我在"],
            request_id="pet-chat-saved-model",
            model_id="gpt-5.6-sol",
            reasoning_level="medium",
        )

        with (
            patch("app.routes.companion.resolve_model_id", return_value="gpt-5.6-sol") as resolve,
            patch("app.routes.companion.select_auto_route") as auto_route,
            patch("app.routes.companion.chat_with_ai", return_value=result) as chat,
            patch("app.routes.companion.companion_service.speak_text", return_value=True),
        ):
            asyncio.run(companion_chat(CompanionChatRequest(message="继续说")))

        resolve.assert_called_once_with("gpt-5.6-sol")
        auto_route.assert_not_called()
        self.assertEqual(chat.call_args.kwargs["model_id"], "gpt-5.6-sol")
        self.assertEqual(chat.call_args.kwargs["reasoning_level"], "medium")
        saved = companion_service.load_config()
        self.assertEqual(saved["chat_model_id"], "deepseek-v4-flash")
        self.assertEqual(saved["chat_reasoning_level"], "default")

    def test_desktop_pet_chat_reports_real_voice_failure(self) -> None:
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="standard")
        result = SimpleNamespace(
            reply="我在",
            replies=["我在"],
            request_id="pet-chat-voice-failure",
            model_id="deepseek-v4-flash",
            reasoning_level="standard",
        )

        with (
            patch("app.routes.companion.select_auto_route", return_value=route),
            patch("app.routes.companion.chat_with_ai", return_value=result),
            patch("app.routes.companion.companion_service.speak_text", return_value=False),
            patch(
                "app.routes.companion.companion_service.voice_runtime_status",
                return_value={"last_error": "没有可用的输出设备"},
            ),
        ):
            response = asyncio.run(companion_chat(CompanionChatRequest(message="说句话")))

        self.assertTrue(response["voice_attempted"])
        self.assertFalse(response["spoken"])
        self.assertEqual(response["voice_error"], "没有可用的输出设备")

    def test_desktop_pet_chat_history_does_not_read_main_conversation(self) -> None:
        main_conversation_id = "qq_private_test_user"
        db.save_message("user", "主对话消息", source="desktop", conversation_id=main_conversation_id)
        db.save_message("user", "你好", source="desktop_pet", conversation_id="desktop_pet")
        db.save_message("assistant", "嗯，我在", source="desktop_pet", conversation_id="desktop_pet")

        response = asyncio.run(companion_chat_history(120))

        self.assertEqual(response["conversation_id"], "desktop_pet")
        self.assertEqual([item["role"] for item in response["messages"]], ["user", "assistant"])
        self.assertEqual(response["messages"][-1]["content"], "嗯，我在")

    def test_desktop_pet_call_requires_active_call(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(companion_call_turn(CompanionCallTurnRequest(
                wav_base64=base64.b64encode(_tone_wav_bytes()).decode("ascii"),
            )))
        self.assertEqual(raised.exception.status_code, 409)

    def test_desktop_pet_call_start_prepares_capture_asr_and_voice(self) -> None:
        with (
            patch("app.routes.companion.companion_service.window_observer.status", return_value={"running": False}),
            patch("app.routes.companion.companion_service.window_observer.select_screen") as select_screen,
            patch("app.routes.companion.companion_service.window_observer.start") as start_capture,
            patch("app.routes.companion.screen_observation_service.set_capture_only") as set_capture_only,
            patch("app.routes.companion.screen_observation_service.analyze_once", return_value=True),
            patch("app.routes.companion._ensure_call_asr_ready", return_value={"ready": True}) as ensure_asr,
            patch("app.routes.companion.companion_service.warm_voice_runtime_async") as warm_voice,
        ):
            response = asyncio.run(companion_call_start())

        self.assertTrue(response["active"])
        select_screen.assert_called_once_with("primary")
        start_capture.assert_called_once_with(1000)
        set_capture_only.assert_called_once_with(False)
        ensure_asr.assert_called_once_with()
        warm_voice.assert_called_once_with()

    def test_desktop_pet_call_start_reports_asr_failure(self) -> None:
        with (
            patch("app.routes.companion.companion_service.window_observer.status", return_value={"running": True}),
            patch("app.routes.companion.screen_observation_service.analyze_once", return_value=True),
            patch(
                "app.routes.companion._ensure_call_asr_ready",
                return_value={"ready": False, "running": False, "last_error": "模型文件损坏"},
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(companion_call_start())

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("模型文件损坏", raised.exception.detail)
        self.assertFalse(call_session_service.manager.status()["active"])

    def test_local_whisper_model_path_uses_completed_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = Path(temporary_directory)
            snapshot = cache / "models--Systran--faster-whisper-base" / "snapshots" / "revision"
            snapshot.mkdir(parents=True)
            (snapshot / "model.bin").write_bytes(b"model")
            (snapshot / "config.json").write_text("{}", encoding="utf-8")

            self.assertEqual(system_audio_service._local_whisper_model_path(cache, "base"), snapshot)

    def test_local_whisper_model_path_uses_bundled_large_v3_turbo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundled = root / "GPT-SoVITS" / "tools" / "asr" / "models" / "faster-whisper-large-v3-turbo"
            bundled.mkdir(parents=True)
            (bundled / "model.bin").write_bytes(b"model")
            (bundled / "config.json").write_text("{}", encoding="utf-8")

            with patch(
                "app.system_audio_service.settings",
                SimpleNamespace(voice_training_dir=root),
            ):
                selected = system_audio_service._local_whisper_model_path(
                    root / "cache" / "faster-whisper",
                    "large-v3-turbo",
                )

        self.assertEqual(selected, bundled)

    def test_whisper_model_resolution_prefers_requested_model_then_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cache = root / "cache" / "faster-whisper"
            base = cache / "models--Systran--faster-whisper-base" / "snapshots" / "revision"
            base.mkdir(parents=True)
            (base / "model.bin").write_bytes(b"model")
            (base / "config.json").write_text("{}", encoding="utf-8")

            with patch(
                "app.system_audio_service.settings",
                SimpleNamespace(voice_training_dir=root),
            ):
                path, actual, reason = system_audio_service._resolve_local_whisper_model(
                    cache,
                    "large-v3-turbo",
                )

        self.assertEqual(path, base)
        self.assertEqual(actual, "base")
        self.assertIn("large-v3-turbo", reason)
        self.assertIn("base", reason)

    def test_desktop_pet_call_uses_large_v3_turbo_asr(self) -> None:
        from app.routes import companion as companion_route

        with (
            patch("app.routes.companion.companion_service.load_config", return_value={"screen_audio_enabled": True, "screen_audio_model": "base"}),
            patch("app.routes.companion.system_audio_service.start", return_value={"ready": True}) as start,
        ):
            result = companion_route._ensure_call_asr_ready()

        self.assertTrue(result["ready"])
        self.assertEqual(start.call_args.args[0]["asr_engine"], "whisper")
        self.assertEqual(start.call_args.args[0]["asr_model"], "large-v3-turbo")

    def test_desktop_pet_call_start_failure_releases_its_observer_and_audio(self) -> None:
        from app.routes import companion as companion_route

        companion_route._pet_call_started_observer = False
        companion_route._pet_call_previous_audio_model = ""
        with (
            patch("app.routes.companion.system_audio_service.status", return_value={"running": False}),
            patch("app.routes.companion._ensure_call_asr_ready", return_value={
                "ready": False,
                "running": False,
                "last_error": "模型加载失败",
            }),
            patch("app.routes.companion.companion_service.window_observer.status", return_value={"running": False}),
            patch("app.routes.companion.companion_service.window_observer.select_screen"),
            patch("app.routes.companion.companion_service.window_observer.start"),
            patch("app.routes.companion.companion_service.window_observer.stop") as stop_observer,
            patch("app.routes.companion.screen_observation_service.set_capture_only"),
            patch("app.routes.companion.screen_observation_service.analyze_once", new=AsyncMock()),
            patch("app.routes.companion.screen_observation_service.end_session") as end_session,
            patch("app.routes.companion.companion_service.load_config", return_value={"screen_audio_enabled": True}),
            patch("app.routes.companion.system_audio_service.stop") as stop_audio,
        ):
            with self.assertRaisesRegex(HTTPException, "语音识别启动失败"):
                asyncio.run(companion_call_start())

        self.assertFalse(call_session_service.manager.status()["active"])
        self.assertFalse(companion_route._pet_call_started_observer)
        self.assertEqual(companion_route._pet_call_previous_audio_model, "")
        stop_observer.assert_called_once()
        end_session.assert_called_once()
        stop_audio.assert_called_once()

    def test_desktop_pet_call_turn_reuses_screen_context_and_low_latency_path(self) -> None:
        session = call_session_service.manager.start()
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="low", difficulty="simple")
        result = SimpleNamespace(
            reply="我看见了",
            replies=["我看见了"],
            request_id="pet-call",
            model_id="deepseek-v4-flash",
            reasoning_level="off",
            first_token_latency_ms=321.0,
        )

        async def fake_chat(message: str, **kwargs):
            self.assertEqual(message, "你看到什么了")
            self.assertEqual(kwargs["conversation_id"], "desktop_pet")
            self.assertEqual(kwargs["source"], "desktop_pet_call")
            self.assertFalse(kwargs["capture_follow_ups"])
            self.assertTrue(kwargs["voice_reply_requested"])
            self.assertTrue(kwargs["agent_tools_enabled"])
            self.assertIn("最近画面摘要：正在播放动画", kwargs["extra_system_context"])
            return result

        with (
            patch("app.routes.companion.system_audio_service.status", return_value={"ready": True}),
            patch(
                "app.routes.companion.system_audio_service.transcribe_wav_for_quality",
                return_value={"text": "你看到什么了", "language": "zh", "error": ""},
            ) as transcribe,
            patch("app.routes.companion.select_auto_route", return_value=route),
            patch("app.routes.companion.chat_with_ai", side_effect=fake_chat),
            patch("app.routes.companion.persist_generated_chat_result"),
            patch("app.routes.companion.screen_observation_service.status", return_value={
                "last_event_summary": "正在播放动画",
                "last_analyzed_at": "2026-08-13T12:00:00+08:00",
                "game_state": {},
            }),
            patch("app.routes.companion.pet_event_service.status", return_value={
                "foreground": {"title": "播放器", "process_name": "player.exe"},
            }),
            patch(
                "app.routes.companion.route_observation_service.record_completed_route"
            ) as record_route,
        ):
            response = asyncio.run(companion_call_turn(CompanionCallTurnRequest(
                wav_base64=base64.b64encode(_tone_wav_bytes()).decode("ascii"),
                language="zh",
                call_session_id=session["call_session_id"],
                turn_id=1,
            )))

        self.assertTrue(response["heard"])
        self.assertEqual(response["reply"], "我看见了")
        self.assertEqual(response["timings"]["model_first_token_ms"], 321.0)
        record_route.assert_called_once()
        self.assertEqual(record_route.call_args.kwargs["source"], "desktop_pet_call")
        transcribe.assert_called_once()

    def test_desktop_pet_call_small_talk_skips_agent_tools_and_deep_reasoning(self) -> None:
        from app.routes import companion as companion_route

        self.assertEqual(companion_route._phone_turn_policy("你好，今天过得怎么样", "medium"), (False, "off"))
        self.assertEqual(companion_route._phone_turn_policy("帮我看看屏幕上是什么", "medium"), (True, "medium"))

    def test_desktop_pet_call_auto_language_is_forced_to_chinese(self) -> None:
        session = call_session_service.manager.start()
        result = SimpleNamespace(
            reply="听清了",
            replies=["听清了"],
            request_id="pet-call-zh",
            model_id="deepseek-v4-flash",
            reasoning_level="off",
            first_token_latency_ms=100.0,
        )

        async def fake_chat(*_args, **_kwargs):
            return result

        with (
            patch("app.routes.companion.system_audio_service.status", return_value={"ready": True}),
            patch(
                "app.routes.companion.system_audio_service.transcribe_wav_for_quality",
                return_value={"text": "我说的是中文", "language": "zh", "error": ""},
            ) as transcribe,
            patch("app.routes.companion.select_auto_route", return_value=SimpleNamespace(
                model_id="deepseek-v4-flash",
                reasoning_level="off",
                difficulty="simple",
            )),
            patch("app.routes.companion.chat_with_ai", side_effect=fake_chat),
            patch("app.routes.companion.persist_generated_chat_result"),
        ):
            response = asyncio.run(companion_call_turn(CompanionCallTurnRequest(
                wav_base64=base64.b64encode(_tone_wav_bytes()).decode("ascii"),
                language="auto",
                call_session_id=session["call_session_id"],
                turn_id=1,
            )))

        self.assertTrue(response["heard"])
        self.assertEqual(transcribe.call_args.kwargs["language"], "zh")
        self.assertEqual(transcribe.call_args.kwargs["purpose"], "phone")

    def test_desktop_pet_call_rejects_unreliable_asr_before_chat(self) -> None:
        session = call_session_service.manager.start()
        with (
            patch("app.routes.companion.system_audio_service.status", return_value={"ready": True}),
            patch(
                "app.routes.companion.system_audio_service.transcribe_wav_for_quality",
                return_value={
                    "text": "",
                    "language": "zh",
                    "accepted": False,
                    "rejection_reason": "boilerplate_hallucination",
                    "audio": {"voiced_ms": 180.0},
                    "asr": {"hallucination_marker_count": 5},
                    "error": "",
                },
            ),
            patch("app.routes.companion.chat_with_ai") as chat,
        ):
            response = asyncio.run(companion_call_turn(CompanionCallTurnRequest(
                wav_base64=base64.b64encode(_tone_wav_bytes(seconds=0.6)).decode("ascii"),
                language="zh",
                call_session_id=session["call_session_id"],
                turn_id=1,
            )))

        self.assertFalse(response["heard"])
        self.assertEqual(response["rejection_reason"], "boilerplate_hallucination")
        self.assertEqual(response["diagnostics"]["audio"]["voiced_ms"], 180.0)
        chat.assert_not_called()

    def test_desktop_pet_call_rejects_recent_playback_echo_before_chat(self) -> None:
        session = call_session_service.manager.start()
        first_turn = call_session_service.manager.begin_turn(session["call_session_id"], 1)
        call_session_service.manager.update_turn(
            first_turn,
            "awaiting_voice",
            reply="今晚记得复测一下电话语音识别。",
        )
        call_session_service.manager.interrupt(
            session["call_session_id"],
            first_turn.response_id,
        )
        with (
            patch("app.routes.companion.system_audio_service.status", return_value={"ready": True}),
            patch(
                "app.routes.companion.system_audio_service.transcribe_wav_for_quality",
                return_value={
                    "text": "记得复测一下电话语音识别",
                    "language": "zh",
                    "accepted": True,
                    "audio": {"voiced_ms": 1200.0},
                    "asr": {"average_log_probability": -0.2},
                    "error": "",
                },
            ),
            patch("app.routes.companion.chat_with_ai") as chat,
        ):
            response = asyncio.run(companion_call_turn(CompanionCallTurnRequest(
                wav_base64=base64.b64encode(_tone_wav_bytes()).decode("ascii"),
                language="zh",
                call_session_id=session["call_session_id"],
                turn_id=2,
            )))

        self.assertFalse(response["heard"])
        self.assertEqual(response["rejection_reason"], "playback_echo")
        self.assertGreaterEqual(response["diagnostics"]["echo_similarity"], 0.72)
        chat.assert_not_called()

    def test_desktop_pet_call_stop_cancels_cooperative_model_run(self) -> None:
        async def scenario() -> None:
            session = call_session_service.manager.start()
            model_started = asyncio.Event()
            model_cancelled = asyncio.Event()

            async def cooperative_model(*_args, **_kwargs):
                model_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    model_cancelled.set()
                    raise

            with (
                patch("app.routes.companion.system_audio_service.status", return_value={"ready": True}),
                patch(
                    "app.routes.companion.system_audio_service.transcribe_wav_for_quality",
                    return_value={"text": "你好", "language": "zh", "accepted": True, "error": ""},
                ),
                patch("app.routes.companion.select_auto_route", return_value=SimpleNamespace(
                    model_id="deepseek-v4-flash",
                    reasoning_level="off",
                    difficulty="simple",
                )),
                patch("app.routes.companion._pet_call_screen_context", return_value=""),
                patch("app.chat_service._chat_with_ai_unlocked", side_effect=cooperative_model),
                patch("app.routes.companion._release_call_resources", new=AsyncMock()),
                patch("app.routes.companion.pet_event_service.publish"),
                patch("app.routes.companion.persist_generated_chat_result") as persist_result,
            ):
                turn_task = asyncio.create_task(companion_call_turn(CompanionCallTurnRequest(
                    wav_base64=base64.b64encode(_tone_wav_bytes()).decode("ascii"),
                    language="zh",
                    call_session_id=session["call_session_id"],
                    turn_id=1,
                )))
                await asyncio.wait_for(model_started.wait(), timeout=1)
                stopped = await companion_call_stop()
                with self.assertRaises(HTTPException) as raised:
                    await asyncio.wait_for(turn_task, timeout=1)

            self.assertTrue(stopped["stopped"])
            self.assertTrue(model_cancelled.is_set())
            self.assertEqual(raised.exception.status_code, 409)
            persist_result.assert_not_called()

        asyncio.run(scenario())

    def test_desktop_pet_call_late_model_result_cannot_write_history(self) -> None:
        async def scenario() -> None:
            session = call_session_service.manager.start()
            model_started = asyncio.Event()
            result = SimpleNamespace(
                reply="这是一条挂断后才到达的回复",
                replies=["这是一条挂断后才到达的回复"],
                request_id="late-call-result",
                model_id="deepseek-v4-flash",
                reasoning_level="off",
                first_token_latency_ms=100.0,
            )

            async def cancellation_ignoring_model(*_args, **_kwargs):
                model_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    return result

            with (
                patch("app.routes.companion.system_audio_service.status", return_value={"ready": True}),
                patch(
                    "app.routes.companion.system_audio_service.transcribe_wav_for_quality",
                    return_value={"text": "你好", "language": "zh", "accepted": True, "error": ""},
                ),
                patch("app.routes.companion.select_auto_route", return_value=SimpleNamespace(
                    model_id="deepseek-v4-flash",
                    reasoning_level="off",
                    difficulty="simple",
                )),
                patch("app.routes.companion._pet_call_screen_context", return_value=""),
                patch("app.chat_service._chat_with_ai_unlocked", side_effect=cancellation_ignoring_model),
                patch("app.routes.companion._release_call_resources", new=AsyncMock()),
                patch("app.routes.companion.pet_event_service.publish"),
                patch("app.routes.companion.persist_generated_chat_result") as persist_result,
            ):
                turn_task = asyncio.create_task(companion_call_turn(CompanionCallTurnRequest(
                    wav_base64=base64.b64encode(_tone_wav_bytes()).decode("ascii"),
                    language="zh",
                    call_session_id=session["call_session_id"],
                    turn_id=1,
                )))
                await asyncio.wait_for(model_started.wait(), timeout=1)
                stopped = await companion_call_stop()
                with self.assertRaises(HTTPException) as raised:
                    await asyncio.wait_for(turn_task, timeout=1)

            self.assertTrue(stopped["stopped"])
            self.assertEqual(raised.exception.status_code, 409)
            persist_result.assert_not_called()

        asyncio.run(scenario())

    def test_desktop_pet_call_http_route_uses_isolated_history(self) -> None:
        from app.routes import companion as companion_route

        app = FastAPI()
        app.include_router(companion_route.router)
        generated = ChatResult(
            reply="你好，我听清了",
            replies=["你好，我听清了"],
            request_id="isolated-http-call-1",
            model_id="deepseek-v4-flash",
            reasoning_level="off",
            first_token_latency_ms=123.0,
        )
        with (
            patch("app.routes.companion._prepare_call_runtime", return_value={"ready": True}),
            patch("app.routes.companion._ensure_call_asr_ready", return_value={"ready": True}),
            patch("app.routes.companion.system_audio_service.status", return_value={"ready": True}),
            patch(
                "app.routes.companion.system_audio_service.transcribe_wav_for_quality",
                return_value={"text": "你好", "language": "zh", "accepted": True, "error": ""},
            ),
            patch("app.routes.companion.screen_observation_service.analyze_once", new=AsyncMock()),
            patch("app.routes.companion.companion_service.warm_voice_runtime_async"),
            patch("app.routes.companion.select_auto_route", return_value=SimpleNamespace(
                model_id="deepseek-v4-flash",
                reasoning_level="off",
                difficulty="simple",
            )),
            patch("app.routes.companion._pet_call_screen_context", return_value=""),
            patch("app.routes.companion.chat_with_ai", new=AsyncMock(return_value=generated)),
            patch("app.routes.companion._release_call_resources", new=AsyncMock()),
            patch("app.routes.companion.pet_event_service.publish"),
        ):
            with TestClient(app) as client:
                started = client.post("/api/companion/call/start")
                self.assertEqual(started.status_code, 200)
                call_session_id = started.json()["call_session_id"]
                turn = client.post(
                    "/api/companion/call/turn",
                    json={
                        "wav_base64": base64.b64encode(_tone_wav_bytes()).decode("ascii"),
                        "language": "zh",
                        "call_session_id": call_session_id,
                        "turn_id": 1,
                    },
                )
                status = client.get("/api/companion/call/status")
                stopped = client.post(
                    "/api/companion/call/stop",
                    json={"call_session_id": call_session_id},
                )

        self.assertEqual(turn.status_code, 200)
        self.assertEqual(turn.json()["transcript"], "你好")
        self.assertEqual(turn.json()["reply"], "你好，我听清了")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["stage"], "awaiting_voice")
        self.assertTrue(stopped.json()["stopped"])
        rows = db.get_recent_messages(limit=10, conversation_id="desktop_pet")
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
        self.assertEqual([row["content"] for row in rows], ["你好", "你好，我听清了"])
        self.assertTrue(all(row["source"] == "desktop_pet_call" for row in rows))

    def test_desktop_pet_call_interrupt_stops_renderer_speech(self) -> None:
        session = call_session_service.manager.start()
        turn = call_session_service.manager.begin_turn(session["call_session_id"], 1)
        call_session_service.manager.update_turn(turn, "awaiting_voice", reply="正在播放的回复")
        with patch("app.routes.companion.pet_event_service.publish") as publish:
            response = asyncio.run(companion_call_interrupt())
        self.assertTrue(response["interrupted"])
        publish.assert_called_once_with(
            "speech_interrupt",
            {"reason": "user_started_speaking", "response_id": turn.response_id},
        )

    def test_desktop_pet_call_stop_does_not_stop_shared_audio_observer(self) -> None:
        from app.routes import companion as companion_route

        session = call_session_service.manager.start()
        companion_route._pet_call_started_observer = False
        companion_route._pet_call_previous_audio_model = "base"
        companion_route._pet_call_previous_audio_engine = "whisper"
        companion_route._pet_call_resource_owner = session["call_session_id"]
        with (
            patch("app.routes.companion.pet_event_service.publish"),
            patch("app.routes.companion.companion_service.window_observer.status", return_value={"running": True}),
            patch("app.routes.companion.companion_service.load_config", return_value={"screen_audio_enabled": True, "screen_audio_model": "base"}),
            patch("app.routes.companion.system_audio_service.stop") as stop_audio,
            patch("app.routes.companion.system_audio_service.start", return_value={"ready": True}) as start_audio,
        ):
            response = asyncio.run(companion_call_stop())
        self.assertFalse(response["active"])
        stop_audio.assert_not_called()
        self.assertEqual(start_audio.call_args.args[0]["screen_audio_model"], "base")
        self.assertEqual(start_audio.call_args.args[0]["asr_engine"], "whisper")

    def test_game_capture_updates_preview_and_change_rate(self) -> None:
        observer = companion_service.GameObserver(prefer_native=False)
        observer._hwnd = 123
        observer._title = "测试游戏"
        frames = [Image.new("RGB", (640, 360), "black"), Image.new("RGB", (640, 360), "white")]

        class FrameCapture:
            def capture(self, *, all_screens: bool = False) -> Image.Image:
                return frames.pop(0)

            def release(self) -> None:
                return None

        observer._screen_capture = FrameCapture()
        first = observer.capture()
        second = observer.capture()
        self.assertTrue(first["preview_available"])
        self.assertGreater(second["change_percent"], 90)
        self.assertFalse(companion_service.settings.companion_game_preview_path.exists())
        preview = observer.take_preview()
        self.assertIsNotNone(preview)
        self.assertGreater(len(preview or b""), 0)
        with Image.open(BytesIO(preview or b"")) as preview_image:
            self.assertLessEqual(preview_image.width, 800)
            self.assertLessEqual(preview_image.height, 450)
        analysis_frame = observer.claim_analysis_frame()
        self.assertIsNotNone(analysis_frame)
        with Image.open(BytesIO(analysis_frame["content"])) as analysis_image:
            self.assertLessEqual(analysis_image.width, 1280)
            self.assertLessEqual(analysis_image.height, 720)
        self.assertTrue(observer.status()["preview_available"])

    def test_capture_status_reports_dynamic_last_frame_age(self) -> None:
        observer = companion_service.WindowObserver(prefer_native=False)
        observer._thread = SimpleNamespace(is_alive=lambda: True)
        observer._frame_id = 1
        observer._preview_bytes = b"preview"
        observer._capture_valid = True
        observer._last_frame_monotonic = 100.0

        with patch("app.companion_observation_service.time.monotonic", return_value=103.456):
            status = observer.status()

        self.assertEqual(status["last_frame_age_seconds"], 3.456)
        self.assertEqual(status["capture_health"], "运行中，有可用画面")

    def test_capture_failure_invalidates_pending_analysis_frame(self) -> None:
        observer = companion_service.WindowObserver(prefer_native=False)
        observer._mode = "window"
        observer._hwnd = 123
        observer._frame_id = 7
        observer._analysis_bytes = b"old-frame"
        observer._capture_valid = True

        with patch.object(observer, "_client_bbox", side_effect=RuntimeError("目标窗口已最小化")):
            with self.assertRaises(RuntimeError):
                observer.capture()

        self.assertFalse(observer.status()["capture_valid"])
        self.assertTrue(observer.status()["preview_stale"] is False)
        self.assertIsNone(observer.claim_analysis_frame(after_frame_id=0))

    def test_native_capture_is_used_for_windows_and_screen_capture_is_compatible(self) -> None:
        class FakeNativeCapture:
            def __init__(self, image=None, error=None):
                self.image = image
                self.error = error
                self.regions = []

            def capture(self, *, region=None, all_screens=False):
                self.regions.append(region)
                if self.error:
                    raise RuntimeError(self.error)
                return self.image.copy()

            def release(self):
                return None

        native = FakeNativeCapture(Image.new("RGB", (640, 360), "purple"))
        observer = companion_service.WindowObserver(prefer_native=False)
        observer._native_capture = native
        observer._mode = "window"
        observer._hwnd = 123
        with patch("app.companion_observation_service.ImageGrab.grab") as legacy:
            with patch.object(observer, "_client_bbox", return_value=(0, 0, 640, 360)):
                result = observer.capture()
        legacy.assert_not_called()
        self.assertEqual(result["capture_backend"], "dxgi")
        self.assertEqual(native.regions, [(0, 0, 640, 360)])

        compatible = FakeNativeCapture(Image.new("RGB", (640, 360), "navy"))
        screen_observer = companion_service.WindowObserver(prefer_native=False)
        screen_observer._native_capture = native
        screen_observer._screen_capture = compatible
        screen_observer._mode = "screen"
        screen_observer._screen_scope = "primary"
        with patch("app.companion_observation_service.ImageGrab.grab") as legacy:
            result = screen_observer.capture()
        legacy.assert_not_called()
        self.assertEqual(result["capture_backend"], "mss (兼容模式)")
        self.assertEqual(compatible.regions, [None])

        failing = FakeNativeCapture(error="测试 DXGI 错误")
        observer._native_capture = failing
        observer._mode = "window"
        with patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "black")) as legacy:
            with patch.object(observer, "_client_bbox", return_value=(0, 0, 640, 360)):
                result = observer.capture()
        legacy.assert_called_once()
        self.assertEqual(result["capture_backend"], "imagegrab (降级)")
        self.assertIn("测试 DXGI 错误", result["capture_backend_error"])
        self.assertIsNone(observer._native_capture)
        self.assertTrue(observer._native_capture_failed)

        with (
            patch("app.companion_observation_service.create_native_capture") as create_native,
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "black")),
            patch.object(observer, "_client_bbox", return_value=(0, 0, 640, 360)),
        ):
            second_result = observer.capture()
        create_native.assert_not_called()
        self.assertEqual(second_result["capture_backend"], "imagegrab")

        recovered_native = FakeNativeCapture(Image.new("RGB", (640, 360), "green"))
        observer._prefer_native = True
        observer._native_capture_retry_at = 0.0
        with (
            patch("app.companion_observation_service.create_native_capture", return_value=recovered_native) as create_native,
            patch("app.companion_observation_service.ImageGrab.grab") as legacy,
            patch.object(observer, "_client_bbox", return_value=(0, 0, 640, 360)),
        ):
            recovered_result = observer.capture()
        create_native.assert_called_once()
        legacy.assert_not_called()
        self.assertEqual(recovered_result["capture_backend"], "dxgi")

    def test_screen_capture_uses_dxgi_before_imagegrab_and_retries_mss(self) -> None:
        class FakeCapture:
            def __init__(self, *, image=None, error=""):
                self.image = image
                self.error = error
                self.released = False

            def capture(self, **_kwargs):
                if self.error:
                    raise RuntimeError(self.error)
                return self.image.copy()

            def release(self):
                self.released = True

        failing_mss = FakeCapture(error="MSS 暂时失败")
        native = FakeCapture(image=Image.new("RGB", (640, 360), "purple"))
        observer = companion_service.WindowObserver(prefer_native=True)
        observer._screen_capture = failing_mss
        observer._native_capture = native
        observer._mode = "screen"

        with patch("app.companion_observation_service.ImageGrab.grab") as imagegrab:
            result = observer.capture()

        imagegrab.assert_not_called()
        self.assertEqual(result["capture_backend"], "dxgi (屏幕兼容)")
        self.assertIn("MSS 暂时失败", result["capture_backend_error"])
        self.assertTrue(failing_mss.released)
        self.assertTrue(observer._screen_capture_failed)

        recovered_mss = FakeCapture(image=Image.new("RGB", (640, 360), "navy"))
        observer._screen_capture_retry_at = 0.0
        with (
            patch("app.companion_observation_service.create_compatible_capture", return_value=recovered_mss),
            patch("app.companion_observation_service.ImageGrab.grab") as imagegrab,
        ):
            recovered = observer.capture()

        imagegrab.assert_not_called()
        self.assertEqual(recovered["capture_backend"], "mss (兼容模式)")
        self.assertEqual(recovered["capture_backend_error"], "")

    def test_obs_can_be_selected_and_falls_back_without_breaking_capture(self) -> None:
        class FakeObsCapture:
            def __init__(self, error: str = "") -> None:
                self.error = error
                self.released = False

            def capture(self, **_kwargs) -> Image.Image:
                if self.error:
                    raise RuntimeError(self.error)
                return Image.new("RGB", (640, 360), "purple")

            def release(self) -> None:
                self.released = True

        observer = companion_service.WindowObserver(prefer_native=False)
        observer._capture_preference = "obs"
        observer._obs_capture = FakeObsCapture()
        result = observer.capture()
        self.assertEqual(result["capture_backend"], "obs")

        failing = FakeObsCapture("OBS未连接")
        observer._obs_capture = failing
        observer._screen_capture = StubScreenCapture()
        result = observer.capture()
        self.assertEqual(result["capture_backend"], "mss (兼容模式)")
        self.assertTrue(failing.released)
        self.assertIn("OBS未连接", result["capture_backend_error"])

    def test_screen_preview_can_be_read_by_multiple_clients(self) -> None:
        with patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "navy")):
            companion_service.window_observer.select_screen("primary")

        response = asyncio.run(companion_screen_preview())
        self.assertGreater(len(response.body), 0)
        self.assertTrue(companion_service.window_observer.status()["preview_available"])
        second_response = asyncio.run(companion_screen_preview())
        self.assertEqual(second_response.body, response.body)

    def test_screen_analysis_saves_reply_without_writing_image(self) -> None:
        main_conversation_id = "qq_private_test_user"
        db.save_message("user", "主会话里的内容", source="qq", conversation_id=main_conversation_id)
        db.save_message("user", "桌宠会话里的内容", source="desktop_pet", conversation_id="desktop_pet")
        vision_completion = SimpleNamespace(
            content=(
                '{"event":"error","summary":"画面显示连接超时错误",'
                '"confidence":0.96,"game":"","state":{},"tags":["连接"]}'
            ),
            model="vision-test",
            prompt_tokens=120,
            cached_prompt_tokens=0,
            completion_tokens=18,
            reasoning_tokens=0,
            cost_yuan=0.003,
            cost_source="estimate",
        )
        reaction_completion = SimpleNamespace(
            content="这个报错像是连接超时，要不要先重试一次",
            model="chat-test",
            prompt_tokens=40,
            cached_prompt_tokens=0,
            completion_tokens=18,
            reasoning_tokens=0,
            cost_yuan=0.001,
            cost_source="estimate",
        )
        route = SimpleNamespace(model_id="vision-test", reasoning_level="low")
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "red")),
            patch("app.screen_observation_service.select_auto_route", return_value=route),
            patch(
                "app.screen_observation_service.local_vision_service.analyze_image",
                return_value=vision_completion,
            ) as local_vision_call,
            patch(
                "app.screen_observation_service.call_chat_completion_result",
                return_value=reaction_completion,
            ) as model_call,
        ):
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertTrue(replied)
        saved = db.get_last_message("desktop_pet")
        self.assertEqual(saved["source"], "screen")
        self.assertIn("连接超时", saved["content"])
        vision_prompt = local_vision_call.call_args.kwargs["prompt"]
        reaction_prompt = model_call.call_args.args[0][1]["content"]
        self.assertNotIn("桌宠会话里的内容", vision_prompt)
        self.assertIn("桌宠会话里的内容", reaction_prompt)
        self.assertNotIn("主会话里的内容", reaction_prompt)
        self.assertNotIn("连接超时", reaction_prompt)
        self.assertIn("遇到了问题", reaction_prompt)
        self.assertEqual(db.get_last_message(main_conversation_id)["content"], "主会话里的内容")
        self.assertFalse(any(Path(self.temp_dir.name).rglob("*.jpg")))
        self.assertFalse(companion_service.window_observer.status()["pending_change_percent"])

    def test_screen_reaction_timeout_uses_local_fallback_and_releases_lock(self) -> None:
        vision_completion = SimpleNamespace(
            content=(
                '{"event":"error","summary":"游戏画面显示连接错误",'
                '"confidence":0.95,"game":"测试游戏","state":{},"tags":["连接"]}'
            ),
            model="vision-test",
            prompt_tokens=80,
            cached_prompt_tokens=0,
            completion_tokens=10,
            reasoning_tokens=0,
            cost_yuan=0.002,
            cost_source="estimate",
        )
        route = SimpleNamespace(model_id="chat-test", reasoning_level="high")
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "navy")),
            patch("app.screen_observation_service.select_auto_route", return_value=route),
            patch(
                "app.screen_observation_service.local_vision_service.analyze_image",
                return_value=vision_completion,
            ),
            patch(
                "app.screen_observation_service.call_chat_completion_result",
                side_effect=asyncio.TimeoutError,
            ) as model_call,
        ):
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertTrue(replied)
        self.assertFalse(screen_observation_service.status()["in_progress"])
        self.assertIn("超时", screen_observation_service.status()["last_error"])
        saved = db.get_last_message("desktop_pet")
        self.assertEqual(screen_observation_service.status()["last_reaction_model"], "local-fallback")
        self.assertIn("问题", saved["content"])
        self.assertEqual(model_call.call_args.kwargs["reasoning_level"], "low")

    def test_full_screen_cloud_route_uses_cloud_vision(self) -> None:
        companion_service.save_config({"screen_vision_route": "cloud"})
        vision_completion = SimpleNamespace(
            content=(
                '{"event":"error","summary":"应用显示连接失败",'
                '"confidence":0.96,"game":"","state":{},"tags":["连接"],'
                '"should_reply":true,"importance":0.95,"emotion":"concerned",'
                '"reply":"好像断开了，先重试一下吧","reason":"发现连接错误"}'
            ),
            model="cloud-vision-test",
            prompt_tokens=80,
            cached_prompt_tokens=0,
            completion_tokens=12,
            reasoning_tokens=0,
            cost_yuan=0.002,
            cost_source="estimate",
        )
        route = SimpleNamespace(model_id="cloud-vision-test", reasoning_level="low")
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "red")),
            patch("app.screen_observation_service._vision_profiles", return_value=[SimpleNamespace(id="cloud-vision-test")]),
            patch("app.screen_observation_service.select_auto_route", return_value=route),
            patch("app.screen_observation_service.local_vision_service.analyze_image") as local_vision_call,
            patch(
                "app.screen_observation_service.call_chat_completion_result",
                return_value=vision_completion,
            ) as model_call,
        ):
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertTrue(replied)
        local_vision_call.assert_not_called()
        self.assertEqual(model_call.call_count, 1)
        self.assertEqual(model_call.call_args.kwargs["retry_attempts"], 1)
        vision_prompt = model_call.call_args_list[0].args[0][1]["content"][0]["text"]
        self.assertIn("云端整屏识别", vision_prompt)
        self.assertEqual(
            model_call.call_args_list[0].args[0][1]["content"][1]["image_url"]["detail"],
            "low",
        )
        self.assertEqual(screen_observation_service.status()["vision_route"], "cloud")
        self.assertIn("上传当前屏幕", screen_observation_service.status()["vision_route_label"])

    def test_cloud_vision_timeout_skips_stale_frame_and_releases_lock(self) -> None:
        companion_service.save_config({
            "screen_vision_route": "cloud",
            "screen_request_timeout_seconds": 8,
        })
        route = SimpleNamespace(model_id="cloud-vision-test", reasoning_level="low")
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "red")),
            patch("app.screen_observation_service._vision_profiles", return_value=[SimpleNamespace(id="cloud-vision-test")]),
            patch("app.screen_observation_service.select_auto_route", return_value=route),
            patch(
                "app.screen_observation_service.call_chat_completion_result",
                side_effect=asyncio.TimeoutError,
            ),
        ):
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        analysis = screen_observation_service.status()
        self.assertFalse(replied)
        self.assertFalse(analysis["in_progress"])
        self.assertEqual(analysis["request_timeout_seconds"], 8)
        self.assertIn("超过 8 秒", analysis["last_error"])
        self.assertTrue(analysis["pipeline_timings"]["timed_out"])

    def test_cloud_vision_uses_backup_profile_on_next_tick_after_primary_failure(self) -> None:
        companion_service.save_config({
            "screen_vision_route": "cloud",
            "screen_vision_model_id": "vision-primary",
        })
        primary = SimpleNamespace(
            id="vision-primary",
            display_name="Primary Vision",
            model="vision-primary",
            variant_name="Luna",
            input_price_cny_per_million=1.0,
            output_price_cny_per_million=1.0,
        )
        backup = SimpleNamespace(
            id="vision-backup",
            display_name="Backup Vision",
            model="vision-backup",
            variant_name="Sol",
            input_price_cny_per_million=2.0,
            output_price_cny_per_million=2.0,
        )
        completion = SimpleNamespace(
            content=(
                '{"event":"coding","summary":"编辑器中显示测试代码",'
                '"confidence":0.95,"game":"","state":{},"tags":["代码"],'
                '"should_reply":false,"importance":0.3,"emotion":"neutral",'
                '"reply":"","reason":"没有需要打断用户的事件"}'
            ),
            model="vision-backup",
            prompt_tokens=80,
            cached_prompt_tokens=0,
            completion_tokens=12,
            reasoning_tokens=0,
            cost_yuan=0.002,
            cost_source="estimate",
            first_token_latency_ms=350.0,
        )

        def route_for_profile(*_args, profiles, **_kwargs):
            return SimpleNamespace(model_id=profiles[0].id, reasoning_level="low")

        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "red")),
            patch("app.screen_observation_service._vision_profiles", return_value=[primary, backup]),
            patch("app.screen_observation_service.select_auto_route", side_effect=route_for_profile),
            patch(
                "app.screen_observation_service.call_chat_completion_result",
                side_effect=[RuntimeError("HTTP 503 no_available_account"), completion],
            ) as model_call,
        ):
            companion_service.window_observer.select_screen("primary")
            first = asyncio.run(screen_observation_service.analyze_once(force=True))
            companion_service.window_observer.select_screen("primary")
            second = asyncio.run(screen_observation_service.analyze_once(force=True))
            analysis = screen_observation_service.status()

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(model_call.call_count, 2)
        self.assertEqual(model_call.call_args_list[0].kwargs["model_id"], "vision-primary")
        self.assertEqual(model_call.call_args_list[1].kwargs["model_id"], "vision-backup")
        self.assertEqual(analysis["selected_vision_model_id"], "vision-backup")
        self.assertTrue(analysis["using_fallback_vision_model"])
        primary_health = next(
            item for item in analysis["cloud_profile_health"]
            if item["id"] == "vision-primary"
        )
        self.assertFalse(primary_health["available_now"])
        self.assertEqual(analysis["budget"]["session_attempt_count"], 2)
        self.assertEqual(analysis["budget"]["session_failure_count"], 1)

    def test_database_connections_use_wal_and_busy_timeout(self) -> None:
        with db.get_conn() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertEqual(busy_timeout, 15000)
        self.assertEqual(foreign_keys, 1)

    def test_pet_wake_observes_ordinary_screen_and_saves_voice_source(self) -> None:
        vision_completion = SimpleNamespace(
            content=(
                '{"event":"idle","summary":"桌面上打开着编辑器和浏览器",'
                '"confidence":0.93,"game":"","state":{},"tags":["桌面"]}'
            ),
            model="vision-test",
            prompt_tokens=90,
            cached_prompt_tokens=0,
            completion_tokens=12,
            reasoning_tokens=0,
            cost_yuan=0.002,
            cost_source="estimate",
        )
        reaction_completion = SimpleNamespace(
            content="还在忙这个呀，我陪你一会儿",
            model="chat-test",
            prompt_tokens=32,
            cached_prompt_tokens=0,
            completion_tokens=10,
            reasoning_tokens=0,
            cost_yuan=0.001,
            cost_source="estimate",
        )
        route = SimpleNamespace(model_id="vision-test", reasoning_level="low")
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "navy")),
            patch("app.screen_observation_service.select_auto_route", return_value=route),
            patch(
                "app.screen_observation_service.local_vision_service.analyze_image",
                return_value=vision_completion,
            ),
            patch(
                "app.screen_observation_service.call_chat_completion_result",
                return_value=reaction_completion,
            ) as model_call,
            patch("app.screen_observation_service.companion_service.pet_running", return_value=True),
        ):
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True, wake=True))

        self.assertTrue(replied)
        saved = db.get_last_message("desktop_pet")
        self.assertEqual(saved["source"], "desktop_pet_wake")
        self.assertIn("陪你", saved["content"])
        wake_prompt = model_call.call_args.args[0][1]["content"]
        self.assertIn("随桌宠窗口一起醒来", wake_prompt)

    def test_pet_start_and_stop_link_screen_observation(self) -> None:
        background_tasks = BackgroundTasks()
        with (
            patch("app.routes.companion.companion_service.pet_running", return_value=False),
            patch("app.routes.companion.companion_service.start_pet"),
            patch("app.routes.companion.companion_service.stop_pet"),
            patch("app.routes.companion.companion_service.window_observer.select_screen") as select_screen,
            patch("app.routes.companion.companion_service.window_observer.start") as start_observer,
            patch("app.routes.companion.companion_service.window_observer.stop") as stop_observer,
            patch("app.routes.companion.screen_observation_service.set_capture_only") as set_capture_only,
            patch("app.routes.companion.screen_observation_service.end_session") as end_session,
            patch("app.routes.companion.companion_service.set_pet_activity") as set_activity,
            patch("app.routes.companion.local_vision_service.unload_model") as unload_vision,
            patch("app.routes.companion._status_payload", return_value={"ok": True}),
        ):
            started = asyncio.run(companion_start(background_tasks))
            stopped = asyncio.run(companion_stop())

        self.assertEqual(started, {"ok": True})
        self.assertEqual(stopped, {"ok": True})
        select_screen.assert_called_once_with("primary")
        start_observer.assert_called_once_with(1000)
        set_capture_only.assert_called_once_with(False)
        set_activity.assert_called_once()
        self.assertEqual(len(background_tasks.tasks), 2)
        self.assertEqual(
            {task.func.__name__ for task in background_tasks.tasks},
            {"warm_voice_runtime_async", "_wake_pet_from_screen"},
        )
        stop_observer.assert_called_once_with()
        end_session.assert_called_once_with()
        unload_vision.assert_called_once_with()

    def test_game_analysis_uses_game_prompt_and_source(self) -> None:
        vision_completion = SimpleNamespace(
            content=(
                '{"event":"victory","summary":"玩家赢下了这一局",'
                '"confidence":0.95,"game":"测试游戏","state":{"outcome":"胜利"},"tags":["胜利"]}'
            ),
            model="本地 · qwen2.5vl:3b",
            prompt_tokens=90,
            cached_prompt_tokens=0,
            completion_tokens=9,
            reasoning_tokens=0,
            cost_yuan=0.002,
            cost_source="estimate",
        )
        reaction_completion = SimpleNamespace(
            content="这波赢得很漂亮",
            model="chat-test",
            prompt_tokens=30,
            cached_prompt_tokens=0,
            completion_tokens=8,
            reasoning_tokens=0,
            cost_yuan=0.001,
            cost_source="estimate",
        )
        route = SimpleNamespace(model_id="vision-test", reasoning_level="low")
        game_window = {"hwnd": 123, "title": "测试游戏", "width": 1280, "height": 720}
        with (
            patch.object(companion_service.window_observer, "list_windows", return_value=[game_window]),
            patch.object(companion_service.window_observer, "_client_bbox", return_value=(0, 0, 1280, 720)),
            patch(
                "app.companion_observation_service.ImageGrab.grab",
                return_value=Image.effect_noise((1280, 720), 24).convert("RGB"),
            ),
            patch("app.screen_observation_service._vision_profiles", return_value=[SimpleNamespace(id="vision-test")]),
            patch("app.screen_observation_service.select_auto_route", return_value=route),
            patch(
                "app.screen_observation_service.local_vision_service.analyze_image",
                return_value=vision_completion,
            ) as local_vision_call,
            patch(
                "app.screen_observation_service.call_chat_completion_result",
                return_value=reaction_completion,
            ) as model_call,
            patch("app.screen_observation_service.pet_event_service.publish") as publish_event,
            patch("app.screen_observation_service.companion_service.speak_text", return_value=True) as speak,
        ):
            companion_service.window_observer.select(123)
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertTrue(replied)
        saved = db.get_last_message("desktop_pet")
        self.assertEqual(saved["source"], "game")
        self.assertEqual(saved["content"], "这波赢得很漂亮")
        vision_prompt = local_vision_call.call_args.kwargs["prompt"]
        self.assertIn("指定窗口", vision_prompt)
        self.assertIn("测试游戏", vision_prompt)
        self.assertEqual(model_call.call_count, 1)
        visual_call = next(
            call for call in publish_event.call_args_list if call.args[0] == "visual_event"
        )
        self.assertEqual(visual_call.args[1]["event_type"], "victory")
        self.assertEqual(visual_call.args[1]["motion_hint"], "celebrate")
        self.assertEqual(visual_call.args[1]["emotion"], "cheerful")
        speak.assert_called_once()
        self.assertEqual(speak.call_args.kwargs["source"], "screen")
        local_vision_call.assert_called_once()
        with db.get_conn() as conn:
            observation = conn.execute(
                "SELECT event_type, game_name, confidence FROM observations ORDER BY id DESC LIMIT 1"
            ).fetchone()
            state = conn.execute(
                "SELECT state_json FROM game_session_states ORDER BY session_id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(observation["event_type"], "victory")
        self.assertEqual(observation["game_name"], "测试游戏")
        self.assertGreater(float(observation["confidence"]), 0.9)
        self.assertIn("胜利", state["state_json"])
        self.assertFalse(any(Path(self.temp_dir.name).rglob("*.jpg")))

    def test_non_game_window_analysis_uses_screen_source(self) -> None:
        vision_completion = SimpleNamespace(
            content=(
                '{"event":"activity_progress","summary":"文档新增了一段内容",'
                '"confidence":0.94,"game":"","state":{},"tags":["写作"]}'
            ),
            model="本地 · qwen2.5vl:3b",
            prompt_tokens=80,
            cached_prompt_tokens=0,
            completion_tokens=10,
            reasoning_tokens=0,
            cost_yuan=0.002,
            cost_source="estimate",
        )
        reaction_completion = SimpleNamespace(
            content="这一段写顺了不少",
            model="chat-test",
            prompt_tokens=20,
            cached_prompt_tokens=0,
            completion_tokens=8,
            reasoning_tokens=0,
            cost_yuan=0.001,
            cost_source="estimate",
        )
        route = SimpleNamespace(model_id="vision-test", reasoning_level="low")
        editor_window = {"hwnd": 456, "title": "文档编辑器", "width": 1280, "height": 720}
        with (
            patch.object(companion_service.window_observer, "list_windows", return_value=[editor_window]),
            patch.object(companion_service.window_observer, "_client_bbox", return_value=(0, 0, 1280, 720)),
            patch(
                "app.companion_observation_service.ImageGrab.grab",
                return_value=Image.effect_noise((1280, 720), 24).convert("RGB"),
            ),
            patch("app.screen_observation_service._vision_profiles", return_value=[SimpleNamespace(id="vision-test")]),
            patch("app.screen_observation_service.select_auto_route", return_value=route),
            patch(
                "app.screen_observation_service.local_vision_service.analyze_image",
                return_value=vision_completion,
            ) as local_call,
            patch(
                "app.screen_observation_service.call_chat_completion_result",
                return_value=reaction_completion,
            ) as model_call,
        ):
            companion_service.window_observer.select(456)
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertTrue(replied)
        saved = db.get_last_message("desktop_pet")
        self.assertEqual(saved["source"], "screen")
        prompt = local_call.call_args.kwargs["prompt"]
        self.assertIn("指定窗口", prompt)
        self.assertIn("原生游戏才用 gameplay", prompt)
        self.assertEqual(model_call.call_count, 1)

    def test_screen_analysis_no_reply_is_not_saved(self) -> None:
        completion = SimpleNamespace(
            content="NO_REPLY",
            model="vision-test",
            prompt_tokens=20,
            cached_prompt_tokens=0,
            completion_tokens=1,
            reasoning_tokens=0,
            cost_yuan=0.0,
            cost_source="estimate",
        )
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "black")),
            patch(
                "app.screen_observation_service.local_vision_service.analyze_image",
                return_value=completion,
            ),
            patch("app.screen_observation_service.call_chat_completion_result") as cloud_call,
        ):
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertFalse(replied)
        self.assertIsNone(db.get_last_message("desktop_pet"))
        usage = db.get_screen_analysis_usage()
        self.assertEqual(usage["request_count"], 1)
        self.assertEqual(usage["prompt_tokens"], 20)
        cloud_call.assert_not_called()

    def test_screen_analysis_usage_is_aggregated_without_images(self) -> None:
        db.record_screen_analysis_usage(prompt_tokens=80, completion_tokens=12, cost_yuan=0.003)
        db.record_screen_analysis_usage(prompt_tokens=40, completion_tokens=2, cost_yuan=None)

        usage = db.get_screen_analysis_usage()

        self.assertEqual(usage["request_count"], 2)
        self.assertEqual(usage["prompt_tokens"], 120)
        self.assertEqual(usage["completion_tokens"], 14)
        self.assertEqual(usage["priced_request_count"], 1)
        self.assertEqual(usage["unknown_cost_count"], 1)
        self.assertAlmostEqual(usage["total_cost_yuan"], 0.003)
        self.assertFalse(any(Path(self.temp_dir.name).rglob("*.jpg")))

    def test_screen_analysis_usage_exposes_only_confirmed_provider_cost(self) -> None:
        db.record_screen_analysis_usage(
            prompt_tokens=80,
            completion_tokens=12,
            cost_yuan=0.003,
            request_id="screen-confirmed",
            model_id="vision-test",
            cost_source="provider_reported",
        )
        db.record_screen_analysis_usage(
            prompt_tokens=40,
            completion_tokens=2,
            cost_yuan=0.01,
            request_id="screen-pending",
            model_id="vision-test",
            cost_source="provider_reconciliation_pending",
        )

        usage = db.get_screen_analysis_usage()

        self.assertEqual(usage["confirmed_request_count"], 1)
        self.assertAlmostEqual(usage["confirmed_cost_yuan"], 0.003)
        self.assertEqual(usage["pending_request_count"], 1)
        self.assertAlmostEqual(usage["budget_cost_yuan"], 0.013)

    def test_legacy_screen_estimates_do_not_consume_the_current_budget(self) -> None:
        db.record_screen_analysis_usage(
            prompt_tokens=100,
            completion_tokens=20,
            cost_yuan=0.5,
        )

        usage = db.get_screen_analysis_usage()

        self.assertEqual(usage["legacy_unconfirmed_count"], 1)
        self.assertEqual(usage["budget_cost_yuan"], 0)

    def test_capture_only_mode_never_calls_the_vision_model(self) -> None:
        screen_observation_service.set_capture_only(True)
        with patch("app.screen_observation_service.call_chat_completion_result") as model_call:
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertFalse(replied)
        self.assertTrue(screen_observation_service.status()["capture_only"])
        model_call.assert_not_called()

    def test_disabled_screen_ai_rejects_manual_analysis_without_model_call(self) -> None:
        with (
            patch.object(companion_service.window_observer, "status", return_value={"running": False}),
            patch.object(companion_service.window_observer, "stop"),
            patch.object(companion_service, "load_config", return_value={"screen_ai_enabled": False}),
            patch("app.routes.companion.screen_observation_service.analyze_once") as model_path,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(companion_screen_analyze(ScreenObservationRequest(capture_only=False)))

        self.assertEqual(raised.exception.status_code, 409)
        model_path.assert_not_called()

    def test_disabled_screen_ai_background_analysis_has_zero_side_effects_for_five_ticks(self) -> None:
        with (
            patch.object(companion_service, "load_config", return_value={"screen_ai_enabled": False}),
            patch.object(companion_service.window_observer, "claim_analysis_frame") as claim_frame,
            patch("app.screen_observation_service.call_chat_completion_result") as cloud_model,
            patch.object(screen_observation_service.local_vision_service, "analyze_image") as local_model,
            patch.object(db, "record_screen_analysis_usage") as record_usage,
            patch.object(db, "save_observation") as save_observation,
        ):
            results = [
                asyncio.run(screen_observation_service.analyze_once())
                for _ in range(5)
            ]

        self.assertEqual(results, [False] * 5)
        claim_frame.assert_not_called()
        cloud_model.assert_not_called()
        local_model.assert_not_called()
        record_usage.assert_not_called()
        save_observation.assert_not_called()

    def test_one_shot_screen_analysis_releases_idle_observer(self) -> None:
        with (
            patch.object(companion_service.window_observer, "status", return_value={"running": False}),
            patch.object(companion_service.window_observer, "select_screen"),
            patch.object(companion_service.window_observer, "stop") as stop,
            patch("app.routes.companion.screen_observation_service.analyze_once", return_value=False),
            patch("app.routes.companion.screen_observation_service.end_session") as end_session,
            patch("app.routes.companion.screen_observation_service.set_capture_only") as set_capture_only,
        ):
            response = asyncio.run(companion_screen_analyze(ScreenObservationRequest(capture_only=True)))

        self.assertFalse(response["replied"])
        set_capture_only.assert_called_once_with(True)
        stop.assert_called_once_with()
        end_session.assert_called_once_with()

    def test_one_shot_game_analysis_keeps_running_observer(self) -> None:
        with (
            patch.object(
                companion_service.game_observer,
                "status",
                return_value={"running": True, "mode": "window", "hwnd": 123},
            ),
            patch.object(companion_service.game_observer, "stop") as stop,
            patch("app.routes.companion.screen_observation_service.analyze_once", return_value=True),
        ):
            response = asyncio.run(companion_game_analyze())

        self.assertTrue(response["replied"])
        stop.assert_not_called()

    def test_screen_analysis_cost_budget_blocks_request_before_model_call(self) -> None:
        companion_service.save_config({"screen_daily_cost_limit_yuan": 0.1})
        db.record_screen_analysis_usage(
            prompt_tokens=10,
            completion_tokens=1,
            cost_yuan=0.1,
            request_id="confirmed-budget-cost",
            model_id="vision-test",
            cost_source="provider_reported",
        )
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "navy")),
            patch("app.screen_observation_service._vision_profiles", return_value=[SimpleNamespace(id="vision-test")]),
            patch("app.screen_observation_service.call_chat_completion_result") as completion,
        ):
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertFalse(replied)
        completion.assert_not_called()
        analysis_status = screen_observation_service.status()
        self.assertTrue(analysis_status["budget"]["paused"])
        self.assertIn("本地画面接收仍在运行", analysis_status["last_error"])

    def test_screen_analysis_count_does_not_pause_requests(self) -> None:
        completion = SimpleNamespace(
            content="NO_REPLY",
            model="vision-test",
            prompt_tokens=20,
            cached_prompt_tokens=0,
            completion_tokens=1,
            reasoning_tokens=0,
            cost_yuan=0.002,
            cost_source="estimate",
        )
        route = SimpleNamespace(model_id="vision-test", reasoning_level="low")
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "black")),
            patch("app.screen_observation_service.select_auto_route", return_value=route),
            patch(
                "app.screen_observation_service.local_vision_service.analyze_image",
                return_value=completion,
            ) as model_call,
            patch("app.screen_observation_service.call_chat_completion_result") as cloud_call,
        ):
            companion_service.window_observer.select_screen("primary")
            first = asyncio.run(screen_observation_service.analyze_once(force=True))
            companion_service.window_observer.select_screen("primary")
            second = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(model_call.call_count, 2)
        cloud_call.assert_not_called()
        self.assertEqual(screen_observation_service.status()["budget"]["session_request_count"], 2)

    def test_screen_analysis_local_failure_keeps_capture_without_cloud_fallback(self) -> None:
        with (
            patch("app.companion_observation_service.ImageGrab.grab", return_value=Image.new("RGB", (640, 360), "navy")),
            patch(
                "app.screen_observation_service.local_vision_service.analyze_image",
                side_effect=RuntimeError("本地视觉模型不可用"),
            ),
            patch("app.screen_observation_service.call_chat_completion_result") as completion,
        ):
            companion_service.window_observer.select_screen("primary")
            replied = asyncio.run(screen_observation_service.analyze_once(force=True))

        self.assertFalse(replied)
        completion.assert_not_called()
        self.assertIn("画面捕获会继续运行", screen_observation_service.status()["last_error"])
        self.assertIn("不会自动转发云端", screen_observation_service.status()["last_error"])


if __name__ == "__main__":
    unittest.main()
