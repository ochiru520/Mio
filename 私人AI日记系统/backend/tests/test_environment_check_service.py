from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import environment_check_service, system_audio_service


class VoiceEnvironmentDetectionTests(unittest.TestCase):
    def test_complete_shell_elsewhere_does_not_hide_incomplete_configured_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            configured = base / "configured"
            alternate = base / "alternate"
            configured.mkdir()
            alternate.mkdir()
            (configured / "NapCatWinBootMain.exe").write_bytes(b"legacy")
            (configured / "NapCatWinBootHook.dll").write_bytes(b"hook")
            (alternate / "NapCatWinBootMain.exe").write_bytes(b"shell")
            (alternate / "NapCatWinBootHook.dll").write_bytes(b"hook")
            (alternate / "napcat.mjs").write_text("// shell\n", encoding="utf-8")

            with mock.patch.object(
                environment_check_service,
                "settings",
                mock.Mock(napcat_dir=configured),
            ):
                ready, found, detail = environment_check_service._configured_napcat_environment(
                    [alternate, configured]
                )

        self.assertFalse(ready)
        self.assertEqual(found, alternate)
        self.assertIn(str(configured), detail)
        self.assertIn(str(alternate), detail)
        self.assertIn("需要修复", detail)

    def test_configured_complete_shell_is_environment_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configured = Path(temporary)
            (configured / "NapCatWinBootMain.exe").write_bytes(b"shell")
            (configured / "NapCatWinBootHook.dll").write_bytes(b"hook")
            (configured / "napcat.mjs").write_text("// shell\n", encoding="utf-8")

            with mock.patch.object(
                environment_check_service,
                "settings",
                mock.Mock(napcat_dir=configured),
            ):
                ready, found, detail = environment_check_service._configured_napcat_environment(
                    [configured]
                )

        self.assertTrue(ready)
        self.assertEqual(found, configured)
        self.assertIn("已检测到", detail)

    def test_incomplete_napcat_bootmain_is_not_reported_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "NapCatWinBootMain.exe").write_bytes(b"legacy")
            (root / "NapCatWinBootHook.dll").write_bytes(b"hook")

            found = environment_check_service._find_ready_napcat_root([root])

        self.assertIsNone(found)

    def test_complete_napcat_shell_is_reported_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "NapCatWinBootMain.exe").write_bytes(b"shell")
            (root / "NapCatWinBootHook.dll").write_bytes(b"hook")
            (root / "napcat.mjs").write_text("// shell\n", encoding="utf-8")

            found = environment_check_service._find_ready_napcat_root([root])

        self.assertEqual(found, root)

    def test_legacy_api_layout_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "api.py").write_text("# legacy api\n", encoding="utf-8")
            config = root / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
            config.parent.mkdir(parents=True)
            config.write_text("custom: {}\n", encoding="utf-8")
            (root / ".voice-env" / "Scripts").mkdir(parents=True)
            (root / ".voice-env" / "Scripts" / "python.exe").write_bytes(b"python")
            pretrained = root / "GPT_SoVITS" / "pretrained_models"
            for relative in (
                "chinese-hubert-base/pytorch_model.bin",
                "chinese-roberta-wwm-ext-large/pytorch_model.bin",
                "gsv-v2final-pretrained/model.ckpt",
                "gsv-v2final-pretrained/model.pth",
            ):
                target = pretrained / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"weights")

            layout = environment_check_service._voice_layout(root)

        self.assertIsNotNone(layout)
        self.assertEqual(layout["layout"], "兼容旧版 api.py")
        self.assertTrue(layout["runnable"])

    def test_voice_entry_and_config_alone_are_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "api_v2.py").write_text("# incomplete\n", encoding="utf-8")
            config = root / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
            config.parent.mkdir(parents=True)
            config.write_text("custom: {}\n", encoding="utf-8")

            layout = environment_check_service._voice_layout(root)

        self.assertIsNotNone(layout)
        self.assertFalse(layout["runnable"])
        self.assertIn("Python 运行环境或启动器", layout["missing"])
        self.assertIn("基础模型权重", layout["missing"])

    def test_nested_v2_layout_uses_wrapper_python_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wrapper = Path(temporary)
            engine = wrapper / "GPT-SoVITS"
            engine.mkdir()
            (engine / "api_v2.py").write_text("# v2 api\n", encoding="utf-8")
            python = wrapper / ".voice-env" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"test")
            pretrained = engine / "GPT_SoVITS" / "pretrained_models"
            for relative in (
                "chinese-hubert-base/pytorch_model.bin",
                "chinese-roberta-wwm-ext-large/pytorch_model.bin",
                "gsv-v2final-pretrained/model.ckpt",
                "gsv-v2final-pretrained/model.pth",
            ):
                target = pretrained / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"weights")
            config = engine / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text("custom: {}\n", encoding="utf-8")

            layout = environment_check_service._voice_layout(wrapper)

        self.assertIsNotNone(layout)
        self.assertEqual(Path(layout["root"]), engine)
        self.assertEqual(layout["layout"], "新版 api_v2.py")
        self.assertTrue(layout["runnable"])

    def test_running_local_service_is_verified_by_openapi_signature(self) -> None:
        payload = {
            "info": {"title": "GPT-SoVITS API", "version": "2.0"},
            "paths": {"/tts": {"get": {}}},
        }
        response = mock.Mock(status=200)
        response.read.return_value = json.dumps(payload).encode("utf-8")
        connection = mock.Mock()
        connection.getresponse.return_value = response
        environment_check_service._probe_voice_service.cache_clear()

        with mock.patch.object(
            environment_check_service.http.client,
            "HTTPConnection",
            return_value=connection,
        ):
            ready, detail = environment_check_service._probe_voice_service("http://127.0.0.1:9880")

        self.assertTrue(ready)
        self.assertIn("2.0", detail)
        connection.request.assert_called_once_with("GET", "/openapi.json")

    def test_remote_voice_service_is_not_probed(self) -> None:
        environment_check_service._probe_voice_service.cache_clear()
        with mock.patch.object(environment_check_service.http.client, "HTTPConnection") as connection:
            ready, detail = environment_check_service._probe_voice_service("https://example.com:9880")
        self.assertFalse(ready)
        self.assertEqual(detail, "")
        connection.assert_not_called()


class WhisperRuntimeDetectionTests(unittest.TestCase):
    @staticmethod
    def _create_runtime(root: Path, *, include_python: bool = True, include_model: bool = True) -> None:
        if include_python:
            python = root / ".voice-env" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            site_packages = root / ".voice-env" / "Lib" / "site-packages"
            for package in ("faster_whisper", "ctranslate2"):
                init = site_packages / package / "__init__.py"
                init.parent.mkdir(parents=True, exist_ok=True)
                init.write_text("", encoding="utf-8")
        if include_model:
            snapshot = (
                root
                / "cache"
                / "faster-whisper"
                / "models--Systran--faster-whisper-base"
                / "snapshots"
                / "test"
            )
            snapshot.mkdir(parents=True)
            for name in environment_check_service.WHISPER_REQUIRED_FILES:
                (snapshot / name).write_bytes(b"model")

    def tearDown(self) -> None:
        environment_check_service.whisper_runtime_candidates.cache_clear()

    def test_complete_external_runtime_is_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configured = Path(temporary) / "portable-data" / "音色训练"
            external = Path(temporary) / "existing" / "音色训练"
            self._create_runtime(external)
            with (
                mock.patch.object(
                    environment_check_service,
                    "settings",
                    mock.Mock(voice_training_dir=configured),
                ),
                mock.patch.object(environment_check_service, "_find_voice_roots", return_value=[]),
                mock.patch.object(environment_check_service, "_find_dirs_named", return_value=[external]),
            ):
                environment_check_service.whisper_runtime_candidates.cache_clear()
                runtime = environment_check_service.find_whisper_runtime()

        self.assertIsNotNone(runtime)
        self.assertEqual(Path(runtime["root"]), external)
        self.assertTrue(runtime["ready"])

    def test_runtime_beyond_previous_three_level_scan_is_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scan_root = Path(temporary)
            external = scan_root / "one" / "two" / "three" / "four" / "voice-training"
            configured = scan_root / "portable" / "音色训练"
            self._create_runtime(external)
            with (
                mock.patch.object(
                    environment_check_service,
                    "settings",
                    mock.Mock(voice_training_dir=configured),
                ),
                mock.patch.object(environment_check_service, "_find_voice_roots", return_value=[]),
                mock.patch.object(environment_check_service, "_nearby_scan_roots", return_value=[]),
                mock.patch.object(environment_check_service, "_scan_roots", return_value=[scan_root]),
            ):
                environment_check_service.whisper_runtime_candidates.cache_clear()
                runtime = environment_check_service.find_whisper_runtime()

        self.assertIsNotNone(runtime)
        self.assertEqual(Path(runtime["root"]), external)
        self.assertTrue(runtime["ready"])

    def test_cache_directory_name_can_reveal_neutrally_named_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scan_root = Path(temporary)
            external = scan_root / "tools" / "audio-runtime"
            configured = scan_root / "portable" / "音色训练"
            self._create_runtime(external)
            with (
                mock.patch.object(
                    environment_check_service,
                    "settings",
                    mock.Mock(voice_training_dir=configured),
                ),
                mock.patch.object(environment_check_service, "_find_voice_roots", return_value=[]),
                mock.patch.object(environment_check_service, "_nearby_scan_roots", return_value=[]),
                mock.patch.object(environment_check_service, "_scan_roots", return_value=[scan_root]),
            ):
                environment_check_service.whisper_runtime_candidates.cache_clear()
                runtime = environment_check_service.find_whisper_runtime()

        self.assertIsNotNone(runtime)
        self.assertEqual(Path(runtime["root"]), external)
        self.assertTrue(runtime["ready"])

    def test_model_without_python_is_not_reported_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "音色训练"
            self._create_runtime(root, include_python=False)
            layout = environment_check_service._whisper_runtime_layout(root)

        self.assertTrue(layout["model_ready"])
        self.assertFalse(layout["python_ready"])
        self.assertFalse(layout["ready"])

    def test_system_audio_uses_detected_whisper_root(self) -> None:
        detected_root = Path(r"D:\existing\音色训练")
        with mock.patch.object(
            environment_check_service,
            "find_whisper_runtime",
            return_value={"root": detected_root, "ready": True},
        ):
            python, worker, cache = system_audio_service._runtime_paths("whisper")

        self.assertEqual(python, detected_root / ".voice-env" / "Scripts" / "python.exe")
        self.assertEqual(cache, detected_root / "cache" / "faster-whisper")
        self.assertEqual(worker, system_audio_service.settings.agent_control_scripts_dir / "system_audio_worker.py")

    def test_non_whisper_engine_keeps_configured_root(self) -> None:
        configured_root = Path(r"D:\portable\音色训练")
        with (
            mock.patch.object(
                system_audio_service,
                "settings",
                mock.Mock(
                    voice_training_dir=configured_root,
                    agent_control_scripts_dir=Path(r"D:\portable\scripts"),
                ),
            ),
            mock.patch.object(environment_check_service, "find_whisper_runtime") as find_runtime,
        ):
            python, _, cache = system_audio_service._runtime_paths("sensevoice")

        find_runtime.assert_not_called()
        self.assertEqual(python, configured_root / ".voice-env" / "Scripts" / "python.exe")
        self.assertEqual(cache, configured_root / "cache" / "faster-whisper")


class OllamaModelDetectionTests(unittest.TestCase):
    def test_manifest_requires_every_referenced_blob_with_exact_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models = Path(temporary)
            manifest = models / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5vl" / "3b"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "config": {"digest": "sha256:config", "size": 3},
                        "layers": [{"digest": "sha256:model", "size": 5}],
                    }
                ),
                encoding="utf-8",
            )
            blobs = models / "blobs"
            blobs.mkdir()
            (blobs / "sha256-config").write_bytes(b"abc")

            self.assertFalse(environment_check_service._ollama_manifest_complete(manifest, models))
            (blobs / "sha256-model").write_bytes(b"12345")
            self.assertTrue(environment_check_service._ollama_manifest_complete(manifest, models))
            (blobs / "sha256-model").write_bytes(b"shorter")
            self.assertFalse(environment_check_service._ollama_manifest_complete(manifest, models))


if __name__ == "__main__":
    unittest.main()
