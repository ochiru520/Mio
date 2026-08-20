from __future__ import annotations

import tempfile
import json
import os
import threading
import types
import unittest
import urllib.error
import logging
import sys
from logging.handlers import RotatingFileHandler
from unittest.mock import patch
from pathlib import Path

from desktop import launcher


class WebViewRecoveryTests(unittest.TestCase):
    def test_state_json_retries_when_windows_temporarily_locks_destination(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "state.json"
            real_replace = os.replace
            attempts = 0

            def locked_then_replace(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    error = PermissionError(13, "destination is temporarily locked", str(destination))
                    error.winerror = 5
                    raise error
                real_replace(source, destination)

            with (
                patch.object(launcher.os, "replace", side_effect=locked_then_replace),
                patch.object(launcher.time, "sleep") as sleep,
            ):
                launcher._write_state_json(path, {"ok": True})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(attempts, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_instance_channels_keep_default_compatibility_and_isolate_custom_ports(self):
        self.assertEqual(
            launcher._instance_channel_name("MioAgentDesktop", 8000),
            "Local\\MioAgentDesktop-7C53C273",
        )
        self.assertEqual(
            launcher._instance_channel_name("MioAgentDesktop", 8027),
            "Local\\MioAgentDesktop-7C53C273-8027",
        )
        self.assertNotEqual(
            launcher._instance_channel_name("MioAgentDesktop", 8010),
            launcher._instance_channel_name("MioAgentDesktop", 8027),
        )

    def test_installer_legacy_chinese_encoding_is_migrated_to_utf8(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / launcher.INSTALL_DATA_DIR_FILENAME
            selected_data_dir = r"D:\澪Agent_旧误报版本_20260804\澪Agent数据"
            config_path.write_bytes(selected_data_dir.encode("gb18030"))

            self.assertEqual(
                launcher._read_installed_data_dir_config(config_path),
                selected_data_dir,
            )
            self.assertEqual(config_path.read_text(encoding="utf-8"), selected_data_dir)

    def test_invalid_installer_data_directory_config_does_not_crash(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / launcher.INSTALL_DATA_DIR_FILENAME
            config_path.write_bytes(b"\xff\xff\xff")

            self.assertEqual(launcher._read_installed_data_dir_config(config_path), "")

    def test_frozen_portable_build_defaults_to_data_beside_executable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = Path(temporary_directory) / "Mio.exe"
            executable.touch()
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
                patch.object(launcher, "_installed_state_dir", return_value=None),
                patch.dict(os.environ, {"MIO_DESKTOP_STATE_DIR": ""}, clear=False),
            ):
                self.assertEqual(launcher._desktop_state_dir(), executable.parent / "Data")

    def test_bundled_default_voice_seeds_only_empty_runtime(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime_root = root / "Data" / "运行数据"
            source = root / "bundle" / "default_voice" / "mio_v2_00.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"RIFF-default-reference")
            with patch.object(launcher, "_bundled_default_voice_path", return_value=source):
                self.assertTrue(launcher._prepare_bundled_default_voice(runtime_root))
                self.assertFalse(launcher._prepare_bundled_default_voice(runtime_root))
            config_path = runtime_root / "数据" / "桌宠" / "设置.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            profile = config["voice_profiles"]["mio"]
            reference = Path(profile["gpt_sovits_ref_audio"])
            self.assertEqual(reference.read_bytes(), source.read_bytes())
            self.assertEqual(profile["gpt_sovits_prompt_language"], "ja")
            self.assertEqual(
                profile["gpt_sovits_prompt_text"],
                "つまらないものですが、いや、ありがとうございます。",
            )

    def test_bundled_default_voice_repairs_existing_mio_profile_without_resetting_language(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime_root = root / "Data" / "运行数据"
            config_path = runtime_root / "数据" / "桌宠" / "设置.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({
                "default_voice_profile_id": "mio",
                "voice_profiles": {
                    "mio": {
                        "name": "旧默认音色",
                        "gpt_sovits_ref_audio": "",
                        "gpt_sovits_text_language": "ja",
                    },
                },
            }, ensure_ascii=False), encoding="utf-8")
            source = root / "bundle" / "default_voice" / "mio_v2_00.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"RIFF-default-reference")
            voice_root = root / "Data" / "音色训练"
            gpt = voice_root / "GPT-SoVITS" / "GPT_weights_v2" / "mio_v1-e15.ckpt"
            sovits = voice_root / "GPT-SoVITS" / "SoVITS_weights_v2" / "mio_v1_e8_s200.pth"
            gpt.parent.mkdir(parents=True)
            sovits.parent.mkdir(parents=True)
            gpt.write_bytes(b"gpt")
            sovits.write_bytes(b"sovits")

            with (
                patch.object(launcher, "_bundled_default_voice_path", return_value=source),
                patch.object(launcher, "_voice_runtime_root", return_value=voice_root),
            ):
                self.assertTrue(launcher._prepare_bundled_default_voice(runtime_root))
                self.assertFalse(launcher._prepare_bundled_default_voice(runtime_root))

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            profile = saved["voice_profiles"]["mio"]
            self.assertEqual(profile["name"], "Mio 默认音色")
            self.assertEqual(profile["gpt_sovits_text_language"], "ja")
            self.assertEqual(Path(profile["gpt_sovits_gpt_weights"]), gpt.resolve())
            self.assertEqual(Path(profile["gpt_sovits_sovits_weights"]), sovits.resolve())
            self.assertTrue(Path(profile["gpt_sovits_ref_audio"]).is_file())

    def test_installed_data_directory_file_controls_state_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            install_dir = Path(temporary_directory) / "澪Agent"
            install_dir.mkdir()
            executable = install_dir / "澪.exe"
            executable.touch()
            selected_data_dir = Path(temporary_directory) / "全新澪数据"
            (install_dir / launcher.INSTALL_DATA_DIR_FILENAME).write_text(
                str(selected_data_dir),
                encoding="utf-8",
            )
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
                patch.dict("os.environ", {}, clear=True),
            ):
                self.assertEqual(launcher._installed_state_dir(), selected_data_dir.resolve())
                self.assertEqual(launcher._desktop_state_dir(), selected_data_dir.resolve())

    def test_environment_data_directory_overrides_installer_selection(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            configured = Path(temporary_directory) / "环境变量数据"
            with patch.dict(
                "os.environ",
                {"MIO_DESKTOP_STATE_DIR": str(configured)},
                clear=True,
            ):
                self.assertEqual(launcher._desktop_state_dir(), configured)

    def test_frozen_build_forces_data_voice_root_over_stale_environment(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = Path(temporary_directory) / "Mio" / "Mio.exe"
            runtime_root = Path(temporary_directory) / "Data" / "运行数据"
            state_root = Path(temporary_directory) / "Data"
            voice_root = state_root / "音色训练"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
                patch.object(launcher, "STATE_DIR", state_root),
                patch.dict(os.environ, {"MIO_VOICE_TRAINING_DIR": r"D:\stale-source-voice"}),
            ):
                launcher._configure_runtime_environment(runtime_root)
                self.assertEqual(Path(os.environ["MIO_VOICE_TRAINING_DIR"]), voice_root)
                self.assertNotIn("MIO_BUNDLED_VOICE_DIR", os.environ)

    def test_main_window_keeps_a_compact_resizable_minimum(self):
        self.assertEqual(launcher.MAIN_WINDOW_MIN_SIZE, (480, 500))

    def test_main_window_resize_uses_native_edge_hit_testing(self):
        self.assertTrue(callable(launcher._install_resize_hit_test))
        self.assertEqual(launcher._RESIZE_SUBCLASS_CALLBACKS, {})

    def test_resize_hit_code_maps_all_window_edges(self):
        def fill_rect(_handle, pointer):
            pointer._obj.left = 100
            pointer._obj.top = 200
            pointer._obj.right = 900
            pointer._obj.bottom = 800
            return True

        with patch.object(launcher.ctypes.windll.user32, "GetWindowRect", side_effect=fill_rect):
            self.assertEqual(launcher._resize_hit_code(1, (202 << 16) | 102), 13)
            self.assertEqual(launcher._resize_hit_code(1, (500 << 16) | 898), 11)
            self.assertEqual(launcher._resize_hit_code(1, (798 << 16) | 500), 15)

    def test_desktop_preferences_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "桌面偏好.json"
            with patch.object(launcher, "DESKTOP_PREFERENCES_PATH", path):
                self.assertTrue(launcher._read_desktop_preferences()["close_to_background"])
                saved = launcher._write_desktop_preferences({
                    "close_to_background": False,
                    "background_notifications": False,
                })
                self.assertFalse(saved["close_to_background"])
                self.assertFalse(launcher._read_desktop_preferences()["background_notifications"])

    def test_desktop_bridge_exposes_startup_and_background_preferences(self):
        bridge = launcher.DesktopBridge()
        with (
            patch.object(launcher, "_read_desktop_preferences", return_value={
                "close_to_background": True,
                "background_notifications": False,
            }),
            patch.object(launcher, "_windows_startup_enabled", return_value=True),
        ):
            self.assertEqual(bridge.get_desktop_preferences(), {
                "ok": True,
                "close_to_background": True,
                "background_notifications": False,
                "windows_startup": True,
            })

    def test_desktop_bridge_saves_startup_and_background_preferences(self):
        bridge = launcher.DesktopBridge()
        with (
            patch.object(launcher, "_write_desktop_preferences", return_value={
                "close_to_background": False,
                "background_notifications": True,
            }) as write_preferences,
            patch.object(launcher, "_windows_startup_enabled", return_value=False),
            patch.object(launcher, "_set_windows_startup", return_value=True) as set_startup,
        ):
            result = bridge.set_desktop_preferences({
                "close_to_background": False,
                "background_notifications": True,
                "windows_startup": True,
            })
        write_preferences.assert_called_once()
        set_startup.assert_called_once_with(True)
        self.assertTrue(result["windows_startup"])

    def test_desktop_log_uses_bounded_rotation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = logging.getLogger()
            existing = list(root.handlers)
            for handler in existing:
                root.removeHandler(handler)
            try:
                handler = launcher._configure_logging(Path(temporary_directory) / "desktop.log")
                self.assertIsInstance(handler, RotatingFileHandler)
                self.assertEqual(handler.maxBytes, 2 * 1024 * 1024)
                self.assertEqual(handler.backupCount, 3)
            finally:
                for handler in list(root.handlers):
                    handler.close()
                    root.removeHandler(handler)
                for handler in existing:
                    root.addHandler(handler)

    def test_process_failures_choose_the_correct_recovery(self):
        self.assertEqual(launcher._webview_failure_action("RenderProcessExited"), "reload")
        self.assertEqual(launcher._webview_failure_action("RenderProcessUnresponsive"), "reload")
        self.assertEqual(launcher._webview_failure_action("GpuProcessExited"), "restart")
        self.assertEqual(launcher._webview_failure_action("BrowserProcessExited"), "restart")
        self.assertEqual(launcher._webview_failure_action("UtilityProcessExited"), "log")

    def test_backend_failure_does_not_trigger_webview_cache_repair(self):
        self.assertFalse(launcher._failure_requires_webview_cache_repair("BackendUnavailable"))
        self.assertTrue(launcher._failure_requires_webview_cache_repair("BrowserProcessExited"))

    def test_webview2_preflight_reports_missing_runtime(self):
        with patch.object(launcher, "_webview2_runtime_version", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "WebView2 Runtime"):
                launcher._require_webview2_runtime()

    def test_webview2_preflight_returns_detected_version(self):
        with patch.object(launcher, "_webview2_runtime_version", return_value="151.0"):
            self.assertEqual(launcher._require_webview2_runtime(), "151.0")

    def test_backend_health_requires_the_expected_runtime_root(self):
        runtime_root = Path(r"D:\projects\mio")
        with patch.object(
            launcher,
            "_health",
            return_value={"ok": True, "project_root": str(runtime_root)},
        ):
            self.assertTrue(launcher._backend_health_matches_runtime(runtime_root))
            self.assertFalse(
                launcher._backend_health_matches_runtime(Path(r"D:\projects\another-mio"))
            )

    def test_backend_health_requires_the_expected_build_id(self):
        runtime_root = Path(r"D:\projects\mio")
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / launcher.BUILD_MANIFEST_FILENAME
            manifest.write_text(
                json.dumps({"schema_version": 1, "build_id": "expected-build"}),
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"MIO_BUILD_MANIFEST": str(manifest)}, clear=False),
                patch.object(
                    launcher,
                    "_health",
                    return_value={
                        "ok": True,
                        "project_root": str(runtime_root),
                        "build_id": "different-build",
                    },
                ),
            ):
                self.assertFalse(launcher._backend_health_matches_runtime(runtime_root))

        self.assertIn("构建身份不匹配", launcher._LAST_BACKEND_HEALTH_ERROR)

    def test_backend_watchdog_recovers_only_the_backend_after_true_outage(self):
        stop_event = unittest.mock.Mock()
        stop_event.wait.side_effect = [False, False, False, True]
        backend_thread = unittest.mock.Mock()
        backend_thread.is_alive.return_value = False
        backend_runtime = unittest.mock.Mock()
        backend_runtime.current_thread.return_value = backend_thread
        backend_runtime.recover.return_value = True
        with (
            patch.object(launcher, "_backend_health_matches_runtime", return_value=False),
            patch.object(launcher, "_port_is_open", return_value=False),
            patch.object(launcher, "_record_backend_recovery_state"),
        ):
            launcher._watch_backend_health(
                stop_event,
                Path(r"D:\projects\mio"),
                interval_seconds=0.01,
                failure_threshold=3,
                backend_runtime=backend_runtime,
            )
        backend_runtime.recover.assert_called_once()
        self.assertIn("后端线程已退出", backend_runtime.recover.call_args.args[0])
        self.assertIn("8000端口未监听", backend_runtime.recover.call_args.args[0])

    def test_backend_watchdog_does_not_restart_when_thread_and_port_are_alive(self):
        stop_event = unittest.mock.Mock()
        stop_event.wait.side_effect = [False, False, True]
        backend_thread = unittest.mock.Mock()
        backend_thread.is_alive.return_value = True
        backend_runtime = unittest.mock.Mock()
        backend_runtime.current_thread.return_value = backend_thread
        diagnostics = {"event_loop": {"current_lag_ms": 5000}}
        with (
            patch.object(launcher, "_backend_health_matches_runtime", return_value=False),
            patch.object(launcher, "_port_is_open", return_value=True),
            patch.object(launcher, "_record_backend_recovery_state") as record,
        ):
            launcher._watch_backend_health(
                stop_event,
                Path(r"D:\projects\mio"),
                interval_seconds=0.01,
                failure_threshold=2,
                backend_runtime=backend_runtime,
                diagnostics_provider=lambda: diagnostics,
            )

        backend_runtime.recover.assert_not_called()
        self.assertEqual(record.call_args.args[0], "degraded_no_restart")
        recorded_diagnostics = record.call_args.kwargs["diagnostics"]
        self.assertEqual(recorded_diagnostics["event_loop"], diagnostics["event_loop"])
        self.assertFalse(recorded_diagnostics["subservice_recovery"]["attempted"])

    def test_backend_watchdog_recovers_only_failed_subservice_without_restarting_app(self):
        stop_event = unittest.mock.Mock()
        stop_event.wait.side_effect = [False, False, True]
        backend_thread = unittest.mock.Mock()
        backend_thread.is_alive.return_value = True
        backend_runtime = unittest.mock.Mock()
        backend_runtime.current_thread.return_value = backend_thread
        diagnostics = {"subservices": {"degraded_services": ["tts"]}}
        recover_subservice = unittest.mock.Mock(
            return_value={"attempted": True, "recovered": ["tts"], "failed": [], "fused": []}
        )
        with (
            patch.object(launcher, "_backend_health_matches_runtime", return_value=False),
            patch.object(launcher, "_port_is_open", return_value=True),
            patch.object(launcher, "_record_backend_recovery_state") as record,
        ):
            launcher._watch_backend_health(
                stop_event,
                Path(r"D:\projects\mio"),
                interval_seconds=0.01,
                failure_threshold=2,
                backend_runtime=backend_runtime,
                diagnostics_provider=lambda: diagnostics,
                subservice_recovery_provider=recover_subservice,
            )

        recover_subservice.assert_called_once_with(diagnostics)
        backend_runtime.recover.assert_not_called()
        self.assertEqual(record.call_args.args[0], "subservice_recovered")
        self.assertEqual(
            record.call_args.kwargs["diagnostics"]["subservice_recovery"]["recovered"],
            ["tts"],
        )

    def test_backend_health_keeps_the_original_network_error(self):
        network_error = urllib.error.URLError(OSError(64, "指定的网络名不再可用"))
        with patch.object(launcher.urllib.request, "urlopen", side_effect=network_error):
            self.assertIsNone(launcher._health())

        self.assertIn("64", launcher._LAST_BACKEND_HEALTH_ERROR)
        self.assertIn("指定的网络名不再可用", launcher._LAST_BACKEND_HEALTH_ERROR)

    def test_local_backend_diagnostics_includes_passive_subservices(self):
        app_module = types.ModuleType("app")
        app_module.__path__ = []
        diagnostics_module = types.ModuleType("app.runtime_diagnostics")
        diagnostics_module.snapshot = lambda: {"event_loop": {}}
        subservice_module = types.ModuleType("app.subservice_health")
        subservice_module.snapshot = lambda: {
            "passive": True,
            "services": {"tts": {"state": "ready"}},
        }
        with patch.dict(
            sys.modules,
            {
                "app": app_module,
                "app.runtime_diagnostics": diagnostics_module,
                "app.subservice_health": subservice_module,
            },
        ):
            result = launcher._local_backend_diagnostics()

        self.assertTrue(result["subservices"]["passive"])
        self.assertEqual(result["subservices"]["services"]["tts"]["state"], "ready")

    def test_backend_watchdog_resets_failures_after_recovery(self):
        stop_event = unittest.mock.Mock()
        stop_event.wait.side_effect = [False, False, False, True]
        callback = unittest.mock.Mock()
        with patch.object(
            launcher,
            "_backend_health_matches_runtime",
            side_effect=[False, True, False],
        ):
            launcher._watch_backend_health(
                stop_event,
                Path(r"D:\projects\mio"),
                interval_seconds=0.01,
                failure_threshold=2,
            )
        callback.assert_not_called()

    def test_full_recovery_circuit_opens_after_two_attempts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "recovery.json"

            self.assertTrue(launcher._claim_full_recovery("BrowserProcessExited", history_path=history, now=100))
            self.assertTrue(launcher._claim_full_recovery("BrowserProcessExited", history_path=history, now=101))
            self.assertFalse(launcher._claim_full_recovery("BrowserProcessExited", history_path=history, now=102))
            payload = json.loads(history.read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "fused")
        self.assertEqual(len(payload["attempts"]), 2)

    def test_backend_runtime_restarts_without_touching_webview(self):
        runtime = launcher.BackendRuntime(Path(r"D:\projects\mio"))
        old_server = unittest.mock.Mock()
        old_thread = unittest.mock.Mock()
        old_thread.is_alive.side_effect = [False]
        runtime.server = old_server
        runtime.thread = old_thread
        new_server = unittest.mock.Mock()
        new_thread = unittest.mock.Mock()
        new_thread.is_alive.return_value = True
        with (
            patch.object(launcher, "_start_backend", return_value=(new_server, new_thread)),
            patch.object(launcher, "_port_is_open", return_value=True),
        ):
            self.assertTrue(runtime.recover("test outage"))

        old_server.should_exit = True
        old_thread.join.assert_called_once()
        self.assertIs(runtime.server, new_server)
        self.assertIs(runtime.thread, new_thread)

    def test_frontend_exposes_concrete_dom_readiness_signal(self):
        app_source = Path(launcher.__file__).resolve().parents[1] / "src" / "App.vue"
        source = app_source.read_text(encoding="utf-8")
        self.assertIn("dataset.mioReady", source)
        self.assertGreaterEqual(source.count("markAppReady()"), 3)

    def test_only_render_caches_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory) / "WebView数据"
            cookies = data_dir / "EBWebView" / "Default" / "Cookies"
            cookies.parent.mkdir(parents=True)
            cookies.write_text("keep", encoding="utf-8")
            for cache_path in launcher._webview_cache_paths(data_dir):
                cache_path.mkdir(parents=True)
                (cache_path / "cache.bin").write_bytes(b"cache")

            cleared = launcher._clear_webview_render_caches(data_dir)

            self.assertEqual(len(cleared), len(launcher._webview_cache_paths(data_dir)))
            self.assertTrue(cookies.exists())
            self.assertTrue(all(not path.exists() for path in launcher._webview_cache_paths(data_dir)))

    def test_cache_paths_stay_inside_webview_data_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory) / "WebView数据"

            for path in launcher._webview_cache_paths(data_dir):
                self.assertTrue(path.resolve().is_relative_to(data_dir.resolve()))

    def test_screen_preview_uses_a_separate_process_command(self):
        command = launcher._screen_preview_command()

        self.assertIn(launcher.SCREEN_PREVIEW_ARGUMENT, command)
        self.assertTrue(any(item.startswith(launcher.SCREEN_PREVIEW_PARENT_ARGUMENT) for item in command))

    def test_pet_chat_uses_a_separate_process_command(self):
        command = launcher._pet_chat_window_command()

        self.assertIn(launcher.PET_CHAT_WINDOW_ARGUMENT, command)
        self.assertTrue(any(item.startswith(launcher.PET_CHAT_PARENT_ARGUMENT) for item in command))

    def test_pet_chat_state_notification_posts_json_to_backend(self):
        response = unittest.mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        with patch.object(launcher.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertTrue(launcher._notify_pet_chat_window_state(False))

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/api/companion/chat-window/state")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b'{"open": false}')

    def test_window_topology_notification_includes_runtime_identity(self):
        response = unittest.mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        window = unittest.mock.Mock(x=12, y=34, width=1280, height=820)
        with patch.object(launcher.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertTrue(launcher._notify_window_topology(
                "agent-main",
                "shown",
                window=window,
                visible=True,
                focused=True,
            ))

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/api/companion/window-topology/events")
        self.assertEqual(payload["source"], "desktop-launcher")
        self.assertEqual(payload["runtime"], "pywebview")
        self.assertEqual(payload["window_id"], "agent-main")
        self.assertEqual(payload["action"], "shown")
        self.assertEqual(payload["bounds"], {"x": 12, "y": 34, "width": 1280, "height": 820})

    def test_created_window_topology_does_not_read_uninitialized_native_bounds(self):
        class UninitializedWindow:
            native = None

            @property
            def x(self):
                raise AssertionError("uninitialized pywebview bounds were read")

        response = unittest.mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        with patch.object(launcher.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertTrue(launcher._notify_window_topology(
                "agent-main",
                "created",
                window=UninitializedWindow(),
            ))

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["action"], "created")
        self.assertEqual(payload["bounds"], {})

    def test_desktop_bridge_closes_child_windows(self):
        bridge = launcher.DesktopBridge()
        preview = unittest.mock.Mock()
        preview.poll.return_value = None
        pet_chat = unittest.mock.Mock(pid=222)
        pet_chat.poll.return_value = None
        bridge._preview_process = preview
        bridge._pet_chat_process = pet_chat

        with (
            patch.object(launcher, "_notify_pet_chat_window_state") as notify_state,
            patch.object(launcher, "_notify_window_topology") as notify_topology,
        ):
            bridge.close_child_windows()

        preview.terminate.assert_called_once_with()
        preview.wait.assert_called_once_with(timeout=3)
        pet_chat.terminate.assert_called_once_with()
        pet_chat.wait.assert_called_once_with(timeout=3)
        notify_state.assert_called_once_with(False)
        notify_topology.assert_called_once_with("pet-chat-input", "closed", pid=222)
        self.assertIsNone(bridge._preview_process)
        self.assertIsNone(bridge._pet_chat_process)

    def test_desktop_bridge_opens_pet_chat_on_demand(self):
        bridge = launcher.DesktopBridge()
        process = unittest.mock.Mock()
        process.poll.return_value = None
        with patch("desktop.launcher.subprocess.Popen", return_value=process) as popen:
            result = bridge.open_pet_chat_window()

        self.assertEqual(result, {"ok": True, "already_open": False})
        popen.assert_called_once()
        self.assertIs(bridge._pet_chat_process, process)

    def test_desktop_bridge_focuses_existing_pet_chat_process(self):
        bridge = launcher.DesktopBridge()
        process = unittest.mock.Mock(pid=123)
        process.poll.return_value = None
        bridge._pet_chat_process = process

        with patch("desktop.launcher._focus_process_window") as focus:
            result = bridge.open_pet_chat_window()

        self.assertEqual(result, {"ok": True, "already_open": True})
        focus.assert_called_once_with(123)

    def test_desktop_bridge_hides_pet_chat_child_process(self):
        bridge = launcher.DesktopBridge()
        process = unittest.mock.Mock(pid=321)
        process.poll.return_value = None
        bridge._pet_chat_process = process

        with (
            patch.object(launcher, "_notify_pet_chat_window_state") as notify,
            patch.object(launcher, "_notify_window_topology") as notify_topology,
        ):
            result = bridge.hide_pet_chat_window()

        self.assertEqual(result, {"ok": True, "already_closed": False})
        notify.assert_called_once_with(False)
        notify_topology.assert_called_once_with("pet-chat-input", "closed", pid=321)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)
        self.assertIsNone(bridge._pet_chat_process)

    def test_desktop_bridge_toggles_pet_chat_child_process(self):
        bridge = launcher.DesktopBridge()
        process = unittest.mock.Mock(pid=456)
        process.poll.return_value = None
        with (
            patch("desktop.launcher.subprocess.Popen", return_value=process),
            patch.object(launcher, "_notify_pet_chat_window_state"),
            patch.object(launcher, "_notify_window_topology"),
        ):
            opened = bridge.toggle_pet_chat_window()
            closed = bridge.toggle_pet_chat_window()

        self.assertTrue(opened["visible"])
        self.assertFalse(closed["visible"])
        process.terminate.assert_called_once_with()

    def test_pet_chat_bridge_closes_its_window(self):
        bridge = launcher.PetChatWindowBridge()
        window = unittest.mock.Mock()
        bridge.attach_window(window)
        with patch("desktop.launcher.threading.Timer") as timer:
            result = bridge.hide_pet_chat_window()

        self.assertEqual(result, {"ok": True, "already_closed": False})
        window.evaluate_js.assert_called_once()
        timer.assert_called_once_with(0.05, window.destroy)
        timer.return_value.start.assert_called_once_with()

    def test_pet_chat_bridge_starts_native_window_drag(self):
        bridge = launcher.PetChatWindowBridge()
        native = unittest.mock.Mock()
        native.Handle.ToInt64.return_value = 456
        bridge.attach_window(unittest.mock.Mock(native=native))
        with (
            patch.object(launcher.ctypes.windll.user32, "ReleaseCapture") as release,
            patch.object(launcher.ctypes.windll.user32, "SendMessageW") as send,
        ):
            result = bridge.window_drag()

        self.assertEqual(result, {"ok": True})
        release.assert_called_once_with()
        send.assert_called_once_with(456, 0x00A1, 2, 0)

    def test_pet_chat_window_is_topmost_and_accepts_mouse_input(self):
        native = unittest.mock.Mock()
        native.Handle.ToInt64.return_value = 789
        window = unittest.mock.Mock(native=native)
        with (
            patch.object(launcher.ctypes.windll.user32, "GetWindowLongPtrW", return_value=0x80020),
            patch.object(launcher.ctypes.windll.user32, "SetWindowLongPtrW") as set_style,
            patch.object(launcher.ctypes.windll.user32, "SetWindowPos") as set_position,
        ):
            self.assertTrue(launcher._make_pet_chat_window_interactive(window))

        set_style.assert_called_once_with(789, -20, 0x80000)
        set_position.assert_called_once_with(
            789,
            -1,
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0010 | 0x0020,
        )

    def test_pet_chat_position_is_centered_above_anchor(self):
        with patch.object(launcher.ctypes.windll.user32, "GetSystemMetrics", side_effect={76: 0, 77: 0, 78: 1920, 79: 1080}.get):
            self.assertEqual(launcher._pet_chat_window_position((1000, 500)), (740, 404))

    def test_pet_chat_dom_diagnostic_covers_hit_targets_and_cursor_styles(self):
        script = launcher._pet_chat_dom_diagnostic_script()

        self.assertIn(".standalone-pet-chat-drag", script)
        self.assertIn(".standalone-pet-chat-composer textarea", script)
        self.assertIn("document.elementFromPoint", script)
        self.assertIn("base.overlap.area === 0", script)
        self.assertIn("base.textarea.hit_count === inputSamples.length", script)

    def test_pet_chat_dom_diagnostic_logs_ready_result(self):
        window = unittest.mock.Mock()
        window.evaluate_js.return_value = json.dumps({
            "ready": True,
            "passed": True,
            "textarea": {"hit_count": 20, "cursor": "text"},
        })
        with patch.object(launcher.logging, "info") as log:
            result = launcher._collect_pet_chat_dom_diagnostics(
                window,
                attempts=1,
                interval_seconds=0,
            )

        self.assertTrue(result["passed"])
        log.assert_called_once()
        self.assertEqual(log.call_args.args[0], "Pet chat DOM diagnostics: %s")

    def test_main_window_resize_uses_native_non_client_command(self):
        bridge = launcher.DesktopBridge()
        native = unittest.mock.Mock()
        native.Handle.ToInt64.return_value = 123
        window = unittest.mock.Mock(native=native)
        bridge.attach_window(window, unittest.mock.Mock())
        with (
            patch.object(launcher.ctypes.windll.user32, "ReleaseCapture") as release,
            patch.object(launcher.ctypes.windll.user32, "SendMessageW") as send,
        ):
            result = bridge.window_resize("bottom-right")
        self.assertTrue(result["ok"])
        release.assert_called_once_with()
        send.assert_called_once_with(123, 0x00A1, 17, 0)

    def test_main_window_native_style_allows_resizing(self):
        native = unittest.mock.Mock()
        native.Handle.ToInt64.return_value = 321
        window = unittest.mock.Mock(native=native)
        with (
            patch.object(launcher.ctypes.windll.user32, "GetWindowLongPtrW", return_value=0x16010000),
            patch.object(launcher.ctypes.windll.user32, "SetWindowLongPtrW") as set_style,
            patch.object(launcher.ctypes.windll.user32, "SetWindowPos") as set_position,
        ):
            self.assertTrue(launcher._make_main_window_resizable(window))

        set_style.assert_called_once_with(321, -16, 0x16070000)
        set_position.assert_called_once_with(
            321,
            0,
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020,
        )

    def test_desktop_bridge_imports_selected_live2d_directory(self):
        bridge = launcher.DesktopBridge()
        window = unittest.mock.Mock()
        window.create_file_dialog.return_value = [r"D:\models\mio"]
        bridge.attach_window(window, unittest.mock.Mock())
        model = {"id": "mio-custom", "name": "测试澪"}
        fake_webview = unittest.mock.Mock(FOLDER_DIALOG="folder")
        backend_root = Path(__file__).resolve().parents[2] / "私人AI日记系统" / "backend"

        with (
            patch.object(sys, "path", [str(backend_root), *sys.path]),
            patch.dict("sys.modules", {"webview": fake_webview}),
            patch("app.companion_service.import_live2d_model_directory", return_value=model) as importer,
            patch("app.companion_service.save_config") as save_config,
            patch("app.companion_service.pet_running", return_value=True),
            patch("app.companion_service.restart_pet") as restart_pet,
        ):
            result = bridge.import_live2d_model()

        self.assertEqual(result, {"ok": True, "model": model})
        window.create_file_dialog.assert_called_once_with(
            "folder",
            directory=str(Path.home()),
            allow_multiple=False,
        )
        importer.assert_called_once_with(r"D:\models\mio")
        save_config.assert_called_once_with({"pet_renderer": "live2d", "live2d_model_id": "mio-custom"})
        restart_pet.assert_called_once_with()

    def test_desktop_bridge_live2d_import_can_be_cancelled(self):
        bridge = launcher.DesktopBridge()
        window = unittest.mock.Mock()
        window.create_file_dialog.return_value = None
        bridge.attach_window(window, unittest.mock.Mock())
        fake_webview = unittest.mock.Mock(FOLDER_DIALOG="folder")
        backend_root = Path(__file__).resolve().parents[2] / "私人AI日记系统" / "backend"

        with (
            patch.object(sys, "path", [str(backend_root), *sys.path]),
            patch.dict("sys.modules", {"webview": fake_webview}),
        ):
            result = bridge.import_live2d_model()

        self.assertEqual(result, {"ok": False, "canceled": True})

    def test_desktop_bridge_starts_isolated_voice_package_import(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory) / "state"
            selected_path = Path(temporary_directory) / "mio.zip"
            selected_path.write_bytes(b"zip-data")
            bridge = launcher.DesktopBridge()
            window = unittest.mock.Mock()
            window.create_file_dialog.return_value = [str(selected_path)]
            bridge.attach_window(window, unittest.mock.Mock())
            fake_webview = unittest.mock.Mock(OPEN_DIALOG="open")
            process = unittest.mock.Mock(pid=4321)
            process.poll.return_value = None

            with (
                patch.object(launcher, "STATE_DIR", state_dir),
                patch.dict("sys.modules", {"webview": fake_webview}),
                patch.object(launcher.subprocess, "Popen", return_value=process) as popen,
            ):
                result = bridge.import_voice_package()

            self.assertTrue(result["ok"])
            self.assertTrue(result["started"])
            self.assertEqual(result["filename"], "mio.zip")
            self.assertEqual(result["total_bytes"], len(b"zip-data"))
            self.assertTrue(result["job_id"] in bridge._voice_import_jobs)
            popen.assert_called_once()
            command = popen.call_args.args[0]
            self.assertIn(launcher.VOICE_PACKAGE_IMPORT_WORKER_ARGUMENT, command)
            window.create_file_dialog.assert_called_once_with(
                "open",
                directory=str(Path.home()),
                allow_multiple=False,
                file_types=("ZIP 音色包 (*.zip)",),
            )

    def test_desktop_bridge_voice_package_import_can_be_cancelled(self):
        bridge = launcher.DesktopBridge()
        window = unittest.mock.Mock()
        window.create_file_dialog.return_value = None
        bridge.attach_window(window, unittest.mock.Mock())
        fake_webview = unittest.mock.Mock(OPEN_DIALOG="open")
        backend_root = Path(__file__).resolve().parents[2] / "私人AI日记系统" / "backend"

        with (
            patch.object(sys, "path", [str(backend_root), *sys.path]),
            patch.dict("sys.modules", {"webview": fake_webview}),
        ):
            result = bridge.import_voice_package()

        self.assertEqual(result, {"ok": False, "canceled": True})

    def test_desktop_bridge_voice_package_import_reports_validation_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory) / "state"
            selected_path = Path(temporary_directory) / "broken.zip"
            selected_path.write_bytes(b"broken")
            bridge = launcher.DesktopBridge()
            window = unittest.mock.Mock()
            window.create_file_dialog.return_value = [str(selected_path)]
            bridge.attach_window(window, unittest.mock.Mock())
            fake_webview = unittest.mock.Mock(OPEN_DIALOG="open")
            process = unittest.mock.Mock(pid=4321)
            process.poll.return_value = 1

            with (
                patch.object(launcher, "STATE_DIR", state_dir),
                patch.dict("sys.modules", {"webview": fake_webview}),
                patch.object(launcher.subprocess, "Popen", return_value=process),
            ):
                started = bridge.import_voice_package()
                job = bridge._voice_import_jobs[started["job_id"]]
                launcher._write_state_json(
                    job["status_path"],
                    {"state": "completed", "ok": False, "error": "音色包损坏"},
                )
                result = bridge.voice_package_import_status(started["job_id"])

            self.assertEqual(result, {"state": "completed", "ok": False, "error": "音色包损坏"})
            self.assertNotIn(started["job_id"], bridge._voice_import_jobs)

    def test_isolated_voice_package_worker_reports_progress_and_result(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory) / "state"
            job_dir = state_dir / "音色包导入任务"
            job_dir.mkdir(parents=True)
            source_path = Path(temporary_directory) / "voice.zip"
            source_path.write_bytes(b"zip-data")
            job_path = job_dir / "job.job.json"
            status_path = job_dir / "job.status.json"
            launcher._write_state_json(
                job_path,
                {"source_path": str(source_path), "status_path": str(status_path)},
            )
            job_path.write_text(job_path.read_text(encoding="utf-8"), encoding="utf-8-sig")
            backend_root = Path(__file__).resolve().parents[2] / "私人AI日记系统" / "backend"

            def import_package(path, *, progress):
                self.assertEqual(path, source_path)
                progress({"phase": "extracting", "message": "正在复制音色模型", "percent": 55})
                return {"id": "voice-1", "name": "测试音色"}

            with (
                patch.object(launcher, "STATE_DIR", state_dir),
                patch.object(sys, "path", [str(backend_root), *sys.path]),
                patch("app.companion_service.import_voice_package_file", side_effect=import_package),
            ):
                return_code = launcher._run_voice_package_import_worker(job_path)

            self.assertEqual(return_code, 0)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertTrue(status["ok"])
            self.assertEqual(status["percent"], 100)
            self.assertEqual(status["imported"]["id"], "voice-1")

    def test_isolated_voice_package_worker_ignores_progress_file_lock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory) / "state"
            job_dir = state_dir / "音色包导入任务"
            job_dir.mkdir(parents=True)
            source_path = Path(temporary_directory) / "voice.zip"
            source_path.write_bytes(b"zip-data")
            job_path = job_dir / "job.job.json"
            status_path = job_dir / "job.status.json"
            launcher._write_state_json(
                job_path,
                {"source_path": str(source_path), "status_path": str(status_path)},
            )
            backend_root = Path(__file__).resolve().parents[2] / "私人AI日记系统" / "backend"
            real_write = launcher._write_state_json
            running_write_attempts = 0

            def fail_running_state(path, payload, **kwargs):
                nonlocal running_write_attempts
                if payload.get("state") == "running":
                    running_write_attempts += 1
                    error = PermissionError(13, "status is being read", str(path))
                    error.winerror = 5
                    raise error
                return real_write(path, payload, **kwargs)

            def import_package(path, *, progress):
                self.assertEqual(path, source_path)
                for percent in range(1, 100):
                    progress({"phase": "extracting", "message": "正在复制音色模型", "percent": percent})
                return {"id": "voice-1", "name": "测试音色"}

            with (
                patch.object(launcher, "STATE_DIR", state_dir),
                patch.object(sys, "path", [str(backend_root), *sys.path]),
                patch.object(launcher, "_write_state_json", side_effect=fail_running_state),
                patch("app.companion_service.import_voice_package_file", side_effect=import_package),
            ):
                return_code = launcher._run_voice_package_import_worker(job_path)

            self.assertEqual(return_code, 0)
            self.assertLess(running_write_attempts, 10)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertTrue(status["ok"])
            self.assertEqual(status["state"], "completed")

    def test_webview_runtime_forces_software_rendering_without_losing_existing_flags(self):
        with patch.dict(
            "os.environ",
            {"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--lang=zh-CN"},
            clear=False,
        ):
            configured = launcher._configure_webview_runtime()

        self.assertIn("--lang=zh-CN", configured)
        self.assertNotIn("--disable-gpu", configured)
        self.assertNotIn("--disable-gpu-compositing", configured)


if __name__ == "__main__":
    unittest.main()
