from __future__ import annotations

import json
import os
import asyncio
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("MIO_DISABLE_DOTENV", "1")
os.environ.setdefault("MIO_RUNTIME_ROOT", str(Path(tempfile.mkdtemp(prefix="mio-napcat-test-"))))

from app.config import settings  # noqa: E402
from app.napcat_service import (  # noqa: E402
    _napcat_api_succeeded,
    _filesystem_status,
    _napcat_launchers,
    _process_status,
    configure_napcat,
    run_napcat_control,
)
from app.routes.agent import QQSetupRequest, control_qq, setup_qq_channel  # noqa: E402


class NapCatSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mio-napcat-setup-")
        self.root = Path(self.temp.name)
        self.config_dir = self.root / "napcat" / "config"
        self.config_dir.mkdir(parents=True)
        (self.root / "launcher.bat").write_text("@echo off\n", encoding="utf-8")
        (self.config_dir / "webui.json").write_text(
            json.dumps({"host": "127.0.0.1", "port": 6099, "token": ""}),
            encoding="utf-8",
        )
        (self.config_dir / "napcat.json").write_text(
            json.dumps({"fileLog": False}), encoding="utf-8"
        )
        (self.config_dir / "onebot11.json").write_text(
            json.dumps(
                {
                    "network": {
                        "httpServers": [],
                        "websocketClients": [
                            {"name": "other", "enable": True, "url": "ws://127.0.0.1:9999/ws"},
                            {"name": "legacy", "enable": True, "url": "ws://127.0.0.1:8000/onebot/ws"},
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        self.originals = {
            "napcat_dir": settings.napcat_dir,
            "napcat_account": settings.napcat_account,
            "qq_onebot_token": settings.qq_onebot_token,
            "qq_channel_config_path": settings.qq_channel_config_path,
            "project_root": settings.project_root,
        }
        object.__setattr__(settings, "napcat_dir", self.root)
        object.__setattr__(settings, "napcat_account", "")
        object.__setattr__(settings, "qq_onebot_token", "")
        object.__setattr__(settings, "qq_channel_config_path", self.root / "QQ通道设置.json")
        object.__setattr__(settings, "project_root", self.root / "installed-app")

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            object.__setattr__(settings, name, value)
        self.temp.cleanup()

    def test_configure_writes_account_specific_onebot_without_duplicate_mio_client(self) -> None:
        with patch("app.config.save_runtime_settings", return_value={}):
            result = configure_napcat("12345678")

        self.assertTrue(result["configured"])
        payload = json.loads(
            (self.config_dir / "onebot11_12345678.json").read_text(encoding="utf-8")
        )
        clients = payload["network"]["websocketClients"]
        self.assertEqual([item["name"] for item in clients], ["other", "mio-agent"])
        mio = clients[-1]
        self.assertEqual(mio["url"], "ws://127.0.0.1:8000/onebot/ws")
        self.assertTrue(mio["token"])
        self.assertNotEqual(mio["token"], "12345678")

        webui = json.loads((self.config_dir / "webui.json").read_text(encoding="utf-8"))
        self.assertEqual(webui["autoLoginAccount"], "12345678")
        self.assertTrue(webui["token"])
        self.assertTrue((self.config_dir / "napcat_12345678.json").is_file())

        persisted = json.loads(settings.qq_channel_config_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["account"], "12345678")
        self.assertEqual(persisted["onebot_token"], mio["token"])

    def test_configure_rejects_non_numeric_account(self) -> None:
        with self.assertRaisesRegex(ValueError, "QQ 号"):
            configure_napcat("abc")

    def test_control_passes_runtime_root_without_requiring_source_backend(self) -> None:
        object.__setattr__(settings, "napcat_account", "12345678")
        completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with (
            patch("app.napcat_service._control_script", return_value=self.root / "launcher.bat"),
            patch("app.napcat_service.subprocess.run", return_value=completed) as run,
        ):
            result = run_napcat_control("start")

        self.assertTrue(result["ok"])
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["MIO_RUNTIME_ROOT"], str(settings.project_root.resolve()))
        self.assertEqual(environment["MIO_APP_PORT"], str(settings.app_port))
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        )

    def test_control_force_qr_flag_is_passed_to_managed_launcher(self) -> None:
        object.__setattr__(settings, "napcat_account", "12345678")
        completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with (
            patch("app.napcat_service._control_script", return_value=self.root / "launcher.bat"),
            patch("app.napcat_service.subprocess.run", return_value=completed) as run,
        ):
            result = run_napcat_control("restart", force_qr_login=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["force_qr_login"])
        self.assertEqual(run.call_args.kwargs["env"]["MIO_NAPCAT_FORCE_QR"], "1")

    def test_napcat_http_200_application_error_is_not_success(self) -> None:
        self.assertTrue(_napcat_api_succeeded({"code": 0, "message": "success"}))
        self.assertFalse(_napcat_api_succeeded({"code": -1, "message": "QQ Is Logined"}))

    def test_login_action_restarts_in_forced_qr_mode(self) -> None:
        with (
            patch(
                "app.routes.agent.run_napcat_control",
                return_value={"ok": True, "output": "started", "returncode": 0},
            ) as control,
            patch("app.routes.agent.refresh_napcat_qrcode", new=AsyncMock(return_value=True)),
            patch("app.routes.agent._qq_status", new=AsyncMock(return_value={"logged_in": False})),
            patch("app.routes.agent.asyncio.sleep", new=AsyncMock()),
        ):
            result = asyncio.run(control_qq("login"))

        self.assertTrue(result["forced_qr_login"])
        control.assert_called_once_with("restart", force_qr_login=True)

    def test_setup_changed_account_skips_old_quick_login(self) -> None:
        payload = QQSetupRequest(account="87654321", target_user_id="")
        with (
            patch(
                "app.routes.agent._qq_status",
                new=AsyncMock(
                    side_effect=[
                        {"napcat_executable_exists": True, "connected_account": "12345678"},
                        {"logged_in": False, "configured_account": "87654321"},
                    ]
                ),
            ),
            patch(
                "app.routes.agent.configure_napcat",
                return_value={"configured": True, "account_changed": True},
            ),
            patch(
                "app.routes.agent.run_napcat_control",
                return_value={"ok": True, "output": "started", "returncode": 0},
            ) as control,
        ):
            result = asyncio.run(setup_qq_channel(payload))

        self.assertTrue(result["force_qr_login"])
        control.assert_called_once_with("restart", force_qr_login=True)

    def test_launcher_prefers_bootmain_and_supports_nested_bootmain(self) -> None:
        (self.root / "NapCatWinBootMain.exe").write_bytes(b"exe")
        (self.root / "NapCatWinBootHook.dll").write_bytes(b"hook")
        (self.root / "napcat.mjs").write_text("// shell\n", encoding="utf-8")
        self.assertEqual(_napcat_launchers()[0], self.root / "NapCatWinBootMain.exe")

        (self.root / "launcher.bat").unlink()
        (self.root / "NapCatWinBootMain.exe").unlink()
        nested = self.root / "OneKey" / "bootmain" / "NapCatWinBootMain.exe"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"exe")
        (nested.parent / "NapCatWinBootHook.dll").write_bytes(b"hook")
        (nested.parent / "napcat.mjs").write_text("// shell\n", encoding="utf-8")
        self.assertEqual(_napcat_launchers()[0], nested)

    def test_incomplete_bootmain_is_reported_for_repair_instead_of_ready(self) -> None:
        (self.root / "launcher.bat").unlink()
        (self.root / "NapCatWinBootMain.exe").write_bytes(b"legacy")
        (self.root / "NapCatWinBootHook.dll").write_bytes(b"hook")

        self.assertEqual(_napcat_launchers(), [])
        status = _filesystem_status()
        self.assertFalse(status["napcat_executable_exists"])
        self.assertTrue(status["napcat_repair_required"])

    def test_qq_child_of_managed_bootmain_is_counted_outside_napcat_directory(self) -> None:
        bootmain = self.root / "bootmain" / "NapCatWinBootMain.exe"
        processes = [
            (100, 1, "NapCatWinBootMain.exe", str(bootmain)),
            (101, 100, "QQ.exe", r"C:\Program Files\Tencent\QQNT\QQ.exe"),
        ]
        with patch("app.napcat_service._running_processes", return_value=processes):
            status = _process_status()

        self.assertTrue(status["napcat_process_running"])
        self.assertTrue(status["qq_process_running"])
        self.assertFalse(status["ordinary_qq_process_running"])

    def test_plain_installed_qq_is_reported_separately_from_managed_qq(self) -> None:
        processes = [
            (101, 1, "QQ.exe", r"C:\Program Files\Tencent\QQNT\QQ.exe"),
        ]
        with patch("app.napcat_service._running_processes", return_value=processes):
            status = _process_status()

        self.assertFalse(status["napcat_process_running"])
        self.assertFalse(status["qq_process_running"])
        self.assertTrue(status["ordinary_qq_process_running"])

    def test_control_script_returns_without_synchronous_readiness_loop(self) -> None:
        script = (
            Path(__file__).resolve().parents[3]
            / "澪Agent应用"
            / "scripts"
            / "napcat-control.ps1"
        ).read_text(encoding="utf-8-sig")

        self.assertNotIn("AddSeconds(45)", script)
        self.assertIn("-WorkingDirectory $LauncherDirectory", script)
        self.assertIn("readiness is reported by the app status endpoint", script)
        self.assertIn("NapCat shell is stale; restarting managed processes", script)
        self.assertIn("Get-InstalledQQPath", script)
        self.assertIn('"-q"', script)
        self.assertIn("请先从系统托盘彻底退出 QQ", script)
        self.assertIn("MIO_NAPCAT_FORCE_QR", script)
        self.assertIn("Get-NetTCPConnection", script)
        self.assertIn("Clear-NapCatAutoLogin", script)
        self.assertIn("缺少 napcat.mjs", script)
        self.assertIn("不要重装官方 QQ", script)

    @unittest.skipUnless(os.name == "nt", "只在 Windows 验证真实 PowerShell 启动工作目录")
    def test_control_script_launches_nested_wrapper_from_its_own_directory_and_returns_quickly(self) -> None:
        (self.root / "launcher.bat").unlink()
        nested = self.root / "nested" / "shell"
        nested.mkdir(parents=True)
        marker = nested / "working-directory.txt"
        (nested / "launcher.bat").write_text(
            f'@echo off\r\n<nul set /p =%CD% > "{marker}"\r\n',
            encoding="utf-8",
        )
        script = (
            Path(__file__).resolve().parents[3]
            / "澪Agent应用"
            / "scripts"
            / "napcat-control.ps1"
        )
        environment = os.environ.copy()
        environment.update(
            {
                "MIO_NAPCAT_DIR": str(self.root),
                "MIO_NAPCAT_ACCOUNT": "12345678",
                "MIO_NAPCAT_WEBUI_URL": "http://127.0.0.1:65534",
                "MIO_APP_PORT": "65533",
            }
        )

        started_at = time.monotonic()
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Action",
                "start",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        elapsed = time.monotonic() - started_at
        for _ in range(30):
            if marker.exists():
                break
            time.sleep(0.1)

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertLess(elapsed, 5.0)
        self.assertTrue(marker.is_file(), completed.stdout + completed.stderr)
        self.assertEqual(
            Path(marker.read_text(encoding="utf-8").strip()).resolve(),
            nested.resolve(),
        )

    @unittest.skipUnless(os.name == "nt", "只在 Windows 验证旧版 BootMain 启动门禁")
    def test_control_script_rejects_incomplete_legacy_bootmain(self) -> None:
        (self.root / "launcher.bat").unlink()
        (self.root / "NapCatWinBootMain.exe").write_bytes(b"legacy")
        (self.root / "NapCatWinBootHook.dll").write_bytes(b"hook")
        script = (
            Path(__file__).resolve().parents[3]
            / "澪Agent应用"
            / "scripts"
            / "napcat-control.ps1"
        )
        environment = os.environ.copy()
        environment.update(
            {
                "MIO_NAPCAT_DIR": str(self.root),
                "MIO_NAPCAT_ACCOUNT": "12345678",
                "MIO_NAPCAT_WEBUI_URL": "http://127.0.0.1:65534",
                "MIO_APP_PORT": "65533",
            }
        )

        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Action",
                "start",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("缺少 napcat.mjs", completed.stdout + completed.stderr)
        self.assertIn("不要重装官方 QQ", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
