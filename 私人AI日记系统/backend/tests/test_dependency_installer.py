"""环境与模型中心（依赖安装器）测试。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("MIO_DISABLE_DOTENV", "1")
os.environ.setdefault("MIO_RUNTIME_ROOT", str(Path(tempfile.mkdtemp(prefix="mio-deps-test-"))))

from app import dependency_installer  # noqa: E402
from app import genie_tts_service  # noqa: E402
from app.config import settings  # noqa: E402


class DependencyListTests(unittest.TestCase):
    def test_list_contains_all_expected_ids(self) -> None:
        entries = dependency_installer.list_dependencies()
        ids = {item["id"] for item in entries}
        self.assertSetEqual(
            ids,
            {
                "cloud_model",
                "cloud_tts",
                "genie_runtime",
                "gpt_sovits",
                "napcat",
                "ollama_vision",
                "whisper",
                "screen_capture",
            },
        )

    def test_every_entry_has_guidance_copy(self) -> None:
        for item in dependency_installer.list_dependencies():
            self.assertTrue(str(item.get("what") or "").strip(), f"{item['id']} 缺 what 文案")
            self.assertTrue(str(item.get("how") or "").strip(), f"{item['id']} 缺 how 文案")
            self.assertIn(item["status"], {"ready", "configured", "unconfigured", "missing"})

    def test_screen_capture_is_builtin_ready(self) -> None:
        entries = {item["id"]: item for item in dependency_installer.list_dependencies()}
        self.assertEqual(entries["screen_capture"]["status"], "ready")
        self.assertEqual(entries["screen_capture"]["kind"], "builtin")

    def test_local_voice_installer_exposes_complete_native_voice_flow(self) -> None:
        entries = {item["id"]: item for item in dependency_installer.list_dependencies()}
        local_voice = entries["gpt_sovits"]
        self.assertIn("Mio", local_voice["label"])
        self.assertIn("预热", local_voice["how"])
        self.assertIn("试听", local_voice["how"])

    def test_genie_runtime_is_a_separate_dependency(self) -> None:
        entries = {item["id"]: item for item in dependency_installer.list_dependencies()}
        engine = entries["genie_runtime"]
        self.assertEqual(engine["script"], "install-genie-runtime.ps1")
        self.assertIn("先安装", engine["how"])

    def test_script_dependencies_expose_install_paths(self) -> None:
        entries = {item["id"]: item for item in dependency_installer.list_dependencies()}
        self.assertEqual(entries["gpt_sovits"]["install_path"], str(settings.voice_training_dir.resolve()))
        self.assertEqual(entries["genie_runtime"]["install_path"], str(settings.voice_training_dir.resolve()))
        self.assertEqual(
            entries["whisper"]["install_path"],
            str((settings.voice_training_dir / "cache" / "faster-whisper").resolve()),
        )
        self.assertEqual(entries["ollama_vision"]["install_path"], str(settings.local_vision_dir.resolve()))
        self.assertEqual(entries["napcat"]["install_path"], str(settings.napcat_dir.resolve()))

    def test_ready_dependency_does_not_show_stale_install_error(self) -> None:
        status_path = settings.data_dir / "dependency-install" / "whisper.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps({"done": True, "error": "旧的下载失败"}, ensure_ascii=False),
            encoding="utf-8",
        )
        environment = {
            "optional": [
                {"id": "system_audio", "status": "available", "detail": "模型完整"},
            ]
        }
        with mock.patch(
            "app.dependency_installer.environment_check_service.environment_status",
            return_value=environment,
        ):
            entries = {item["id"]: item for item in dependency_installer.list_dependencies()}

        self.assertEqual(entries["whisper"]["status"], "ready")
        self.assertNotIn("last_error", entries["whisper"])

    def test_ready_dependency_ignores_stale_live_installer_progress(self) -> None:
        environment = {
            "optional": [
                {"id": "system_audio", "status": "available", "detail": "模型完整"},
            ]
        }
        with (
            mock.patch(
                "app.dependency_installer.environment_check_service.environment_status",
                return_value=environment,
            ),
            mock.patch("app.dependency_installer._install_running", return_value=True),
        ):
            entries = {item["id"]: item for item in dependency_installer.list_dependencies()}

        self.assertEqual(entries["whisper"]["status"], "ready")
        self.assertFalse(entries["whisper"]["installing"])
        self.assertNotIn("progress", entries["whisper"])

    def test_napcat_degraded_configured_path_is_not_reported_ready(self) -> None:
        environment = {
            "optional": [
                {
                    "id": "qq",
                    "status": "degraded",
                    "detail": "当前目录不完整，其他目录只作提示",
                },
            ]
        }
        with mock.patch(
            "app.dependency_installer.environment_check_service.environment_status",
            return_value=environment,
        ):
            entries = {item["id"]: item for item in dependency_installer.list_dependencies()}

        self.assertEqual(entries["napcat"]["status"], "unconfigured")
        self.assertIn("当前目录不完整", entries["napcat"]["detail"])

    def test_install_rejects_unknown_dependency(self) -> None:
        with self.assertRaises(ValueError):
            dependency_installer.install_dependency("not-a-dependency")

    def test_install_rejects_configure_kind(self) -> None:
        with self.assertRaises(ValueError):
            dependency_installer.install_dependency("cloud_model")

    def test_install_status_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            dependency_installer.install_status("not-a-dependency")


class DependencyInstallLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scripts_dir = Path(tempfile.mkdtemp(prefix="mio-deps-scripts-"))
        (self.scripts_dir / "deps").mkdir(parents=True)
        (self.scripts_dir / "deps" / "install-ollama-vision.ps1").write_text(
            "# test script\n", encoding="utf-8"
        )
        self.patch_scripts = mock.patch.object(
            dependency_installer,
            "_scripts_dir",
            return_value=self.scripts_dir / "deps",
        )
        self.patch_scripts.start()

    def tearDown(self) -> None:
        self.patch_scripts.stop()

    @mock.patch("app.dependency_installer.subprocess.Popen")
    def test_install_spawns_powershell_with_status_env(self, popen_mock: mock.Mock) -> None:
        popen_mock.return_value.pid = 12345
        result = dependency_installer.install_dependency("ollama_vision")
        self.assertTrue(result["installing"])
        self.assertEqual(result["console_pid"], 12345)
        args, kwargs = popen_mock.call_args
        command = args[0]
        self.assertIn("powershell", command[0])
        self.assertTrue(any("install-ollama-vision.ps1" in str(part) for part in command))
        env = kwargs.get("env") or {}
        self.assertTrue(env.get("MIO_STATUS_FILE", "").endswith("ollama_vision.json"))
        self.assertIn("MIO_WORKSPACE_ROOT", env)
        self.assertIn("MIO_PROGRAM_DIR", env)
        self.assertIn("MIO_LOCAL_VISION_DIR", env)

    def test_install_writes_initial_status_file(self) -> None:
        with mock.patch("app.dependency_installer.subprocess.Popen") as popen_mock:
            popen_mock.return_value.pid = 12345
            dependency_installer.install_dependency("ollama_vision")
        status_path = settings.data_dir / "dependency-install" / "ollama_vision.json"
        self.assertTrue(status_path.is_file())
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["id"], "ollama_vision")
        self.assertFalse(payload["done"])
        self.assertEqual(payload["console_pid"], 12345)

    def test_install_rejects_pid_recorded_by_another_backend_instance(self) -> None:
        status_path = settings.data_dir / "dependency-install" / "ollama_vision.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps({"console_pid": 24680, "done": False}), encoding="utf-8")
        dependency_installer._running_installs.pop("ollama_vision", None)
        with (
            mock.patch("app.dependency_installer._pid_alive", return_value=True),
            mock.patch("app.dependency_installer.subprocess.Popen") as popen_mock,
        ):
            with self.assertRaisesRegex(ValueError, "正在安装"):
                dependency_installer.install_dependency("ollama_vision")
        popen_mock.assert_not_called()

    def test_install_status_reads_progress_file(self) -> None:
        status_path = settings.data_dir / "dependency-install" / "whisper.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "stage": "model",
                    "percent": 60,
                    "message": "正在下载模型",
                    "file_name": "model.bin",
                    "downloaded_bytes": 50,
                    "total_bytes": 100,
                    "download_percent": 50,
                    "done": False,
                    "error": "",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        status = dependency_installer.install_status("whisper")
        self.assertEqual(status["stage"], "model")
        self.assertEqual(status["percent"], 60)
        self.assertEqual(status["file_name"], "model.bin")
        self.assertEqual(status["downloaded_bytes"], 50)
        self.assertEqual(status["total_bytes"], 100)
        self.assertEqual(status["download_percent"], 50)
        self.assertEqual(status["install_path"], str((settings.voice_training_dir / "cache" / "faster-whisper").resolve()))
        self.assertFalse(status["installing"])

    def test_finished_voice_install_is_finalized_once(self) -> None:
        status_path = settings.data_dir / "dependency-install" / "gpt_sovits.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps({"done": True, "error": ""}), encoding="utf-8")
        verification = {"registered_profile_id": "mio", "preview_played": True}
        with mock.patch.object(
            dependency_installer,
            "_finalize_local_voice_install",
            return_value=verification,
        ) as finalize:
            first = dependency_installer._ensure_install_finalized("gpt_sovits")
            second = dependency_installer._ensure_install_finalized("gpt_sovits")
        self.assertTrue(first["finalized"])
        self.assertTrue(first["verified"])
        self.assertEqual(first["verification"], verification)
        self.assertEqual(second["verification"], verification)
        finalize.assert_called_once_with()

    def test_voice_finalize_registers_warms_and_plays_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            voice_root = Path(temporary_directory)
            for target in (
                voice_root / "models" / "genie" / "mio-v1" / "mio-genie-v2.json",
                voice_root / "materials" / "prepared" / "wav32k_v2" / "mio_v2_00.wav",
                voice_root / "emotion-references.json",
            ):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"ready")
            saved: dict[str, object] = {}
            fake_settings = SimpleNamespace(voice_training_dir=voice_root)
            with (
                mock.patch.object(dependency_installer, "settings", fake_settings),
                mock.patch.object(genie_tts_service, "runtime_status", return_value={"ready": True, "missing": []}),
                mock.patch.object(genie_tts_service, "stop_worker"),
                mock.patch.object(genie_tts_service, "start_worker"),
                mock.patch.object(dependency_installer.companion_service, "load_config", return_value={"voice_profiles": {}}),
                mock.patch.object(dependency_installer.companion_service, "save_config", side_effect=lambda value: saved.update(value)),
                mock.patch.object(dependency_installer.companion_service, "warm_voice_runtime", return_value={"warmup_state": "ready", "warmup_seconds": 1.2}),
                mock.patch.object(dependency_installer.companion_service, "speak_text", return_value=True) as speak,
            ):
                result = dependency_installer._finalize_local_voice_install()
        self.assertEqual(saved["default_voice_profile_id"], "mio")
        self.assertEqual(saved["local_voice_runtime"], "genie")
        self.assertTrue(result["preview_played"])
        speak.assert_called_once()


class GenieNativeModelTests(unittest.TestCase):
    def test_mio_onnx_is_used_without_original_training_weights(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            mio_model = root / "models" / "genie" / "mio-v1"
            mio_model.mkdir(parents=True)
            for name in genie_tts_service.REQUIRED_CHARACTER_FILES:
                (mio_model / name).write_bytes(b"model")
            paths = {
                "mio_model": mio_model,
                "default_model": root / "models" / "genie" / "default-v2",
            }
            with mock.patch.object(genie_tts_service, "_paths", return_value=paths):
                selected = genie_tts_service._resolve_character_model({})
        self.assertEqual(selected, mio_model)


if __name__ == "__main__":
    unittest.main()
