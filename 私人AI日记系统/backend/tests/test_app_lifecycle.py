from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.local_security import SecureAttachmentFiles
from app.main import BACKGROUND_TASK_FACTORIES, app_lifespan, create_app
from app import maintenance_service, runtime_diagnostics
from app import companion_service


class AppLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_voice_warmup_waits_for_interactive_ui_signal(self) -> None:
        companion_service.reset_frontend_ready()
        with (
            patch.dict(os.environ, {"MIO_DESKTOP_APP": "1"}),
            patch.object(companion_service, "load_config", return_value={
                "voice_startup_enabled": True,
                "voice_enabled": True,
                "voice_engine": "gpt_sovits",
            }),
            patch.object(companion_service, "start_voice_service", return_value={"ready": True}) as start_voice,
        ):
            task = asyncio.create_task(companion_service.start_voice_on_app_startup())
            await asyncio.sleep(0)
            start_voice.assert_not_called()

            self.assertTrue(companion_service.signal_frontend_ready())
            self.assertTrue(await asyncio.wait_for(task, timeout=1))
            start_voice.assert_called_once_with()

    async def test_lifespan_starts_cancels_and_cleans_up_background_tasks(self) -> None:
        started = {name: asyncio.Event() for name in BACKGROUND_TASK_FACTORIES}

        def make_loop(name: str):
            async def loop() -> None:
                started[name].set()
                await asyncio.Event().wait()

            return loop

        replacements = {name: make_loop(name) for name in BACKGROUND_TASK_FACTORIES}
        app = FastAPI()

        with (
            patch.dict("app.main.BACKGROUND_TASK_FACTORIES", replacements, clear=True),
            patch("app.main.initialize_runtime") as initialize_runtime,
            patch("app.main.companion_service.shutdown") as shutdown,
        ):
            async with app_lifespan(app):
                await asyncio.gather(*(event.wait() for event in started.values()))
                tasks = [getattr(app.state, name) for name in replacements]
                self.assertTrue(all(not task.done() for task in tasks))
                diagnostics = runtime_diagnostics.snapshot()
                self.assertEqual(set(diagnostics["background_tasks"]), set(replacements))

            self.assertTrue(all(task.cancelled() for task in tasks))
            self.assertEqual(runtime_diagnostics.snapshot()["background_tasks"], {})
            initialize_runtime.assert_called_once_with()
            shutdown.assert_called_once_with()

    async def test_create_app_does_not_initialize_persistent_runtime_on_import(self) -> None:
        with patch("app.main.initialize_runtime") as initialize_runtime:
            created = create_app()

        self.assertIsInstance(created, FastAPI)
        initialize_runtime.assert_not_called()

    async def test_lifespan_continues_cleanup_when_tasks_and_qq_shutdown_fail(self) -> None:
        async def failing_loop() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError("task cleanup failed")

        async def waiting_loop() -> None:
            await asyncio.Event().wait()

        app = FastAPI()
        replacements = {"bad_task": failing_loop, "waiting_task": waiting_loop}
        with (
            patch.dict("app.main.BACKGROUND_TASK_FACTORIES", replacements, clear=True),
            patch("app.main.initialize_runtime"),
            patch(
                "app.main.onebot.disconnect_all_connections",
                side_effect=RuntimeError("qq cleanup failed"),
            ) as disconnect_qq,
            patch("app.main.companion_service.shutdown") as shutdown,
            self.assertLogs("app.main", level="ERROR") as captured,
        ):
            async with app_lifespan(app):
                await asyncio.sleep(0)
                bad_task = app.state.bad_task
                waiting_task = app.state.waiting_task

        self.assertTrue(bad_task.done())
        self.assertTrue(waiting_task.cancelled())
        self.assertIsNone(app.state.bad_task)
        self.assertIsNone(app.state.waiting_task)
        disconnect_qq.assert_awaited_once_with(reason="Mio 后端正在关闭")
        shutdown.assert_called_once_with()
        self.assertTrue(any("bad_task" in line for line in captured.output))
        self.assertTrue(any("断开 QQ" in line for line in captured.output))

    async def test_maintenance_stops_business_tasks_and_resumes_only_after_rollback(self) -> None:
        started = {"runtime_diagnostics_task": asyncio.Event(), "writer_task": asyncio.Event()}

        def make_loop(name: str):
            async def loop() -> None:
                started[name].set()
                await asyncio.Event().wait()

            return loop

        replacements = {name: make_loop(name) for name in started}
        app = FastAPI()
        with (
            patch.dict("app.main.BACKGROUND_TASK_FACTORIES", replacements, clear=True),
            patch("app.main.initialize_runtime"),
            patch("app.main.onebot.disconnect_all_connections") as disconnect_qq,
            patch("app.main.companion_service.shutdown"),
        ):
            async with app_lifespan(app):
                await asyncio.gather(*(event.wait() for event in started.values()))
                diagnostics_task = app.state.runtime_diagnostics_task
                original_writer = app.state.writer_task

                await app.state.enter_maintenance("restore test")

                self.assertFalse(diagnostics_task.done())
                self.assertTrue(original_writer.cancelled())
                self.assertIsNone(app.state.writer_task)
                self.assertEqual(
                    set(runtime_diagnostics.snapshot()["background_tasks"]),
                    {"runtime_diagnostics_task"},
                )

                result = await app.state.finish_maintenance("rollback_complete", resume=True)
                await asyncio.sleep(0)

                self.assertEqual(result["status"], "rollback_complete")
                self.assertFalse(result["blocked"])
                self.assertIsNot(app.state.writer_task, original_writer)
                self.assertFalse(app.state.writer_task.done())

        self.assertGreaterEqual(disconnect_qq.await_count, 2)
        self.assertFalse(maintenance_service.status()["blocked"])

    def test_local_control_rejects_cross_site_and_invalid_hosts(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/health", headers={"Origin": "https://example.test"}).status_code, 403)
            self.assertEqual(client.get("/health", headers={"Host": "127.0.0.1.evil.test"}).status_code, 403)
            response = client.get(
                    "/health",
                    headers={
                        "Host": "127.0.0.1:8000",
                        "Origin": "http://127.0.0.1:8000",
                        "Sec-Fetch-Site": "same-origin",
                    },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            for field in ("exe_path", "build_id", "runtime_root", "state_root", "database_path"):
                self.assertTrue(payload[field])
            self.assertEqual(payload["runtime_identity"]["database_path"], payload["database_path"])
            self.assertIn("event_loop", payload["runtime_diagnostics"])
            self.assertIn("requests", payload["runtime_diagnostics"])
            self.assertTrue(payload["subservices"]["passive"])
            self.assertIn("tts", payload["subservices"]["services"])
            self.assertEqual(payload["maintenance"]["status"], "available")
            self.assertFalse(payload["maintenance"]["blocked"])
            self.assertEqual(response.headers["x-frame-options"], "DENY")
            self.assertEqual(response.headers["content-security-policy"], "frame-ancestors 'none'")
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")
            self.assertEqual(
                client.get("/health", headers={"Origin": "http://127.0.0.1:1420"}).status_code,
                403,
            )

    def test_local_control_rejects_cross_site_fetch_even_with_local_origin(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(create_app()) as client:
            response = client.get(
                "/health",
                headers={
                    "Origin": "http://127.0.0.1:8000",
                    "Sec-Fetch-Site": "cross-site",
                },
            )

        self.assertEqual(response.status_code, 403)

    def test_local_control_rejects_cross_site_websocket(self) -> None:
        with TestClient(create_app()) as client:
            with self.assertRaises(WebSocketDisconnect) as rejected:
                with client.websocket_connect(
                    "/api/companion/ws",
                    headers={"Origin": "https://example.test"},
                ):
                    pass

        self.assertEqual(rejected.exception.code, 1008)

    def test_archived_attachments_force_active_content_to_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "unsafe.html").write_text("<script>top.location='/api/privacy/resume'</script>", encoding="utf-8")
            (root / "safe.png").write_bytes(b"not-a-real-image")
            attachment_app = FastAPI()
            attachment_app.mount("/files", SecureAttachmentFiles(directory=root), name="files")
            with TestClient(attachment_app) as client:
                unsafe = client.get("/files/unsafe.html")
                safe = client.get("/files/safe.png")

        self.assertEqual(unsafe.status_code, 200)
        self.assertEqual(unsafe.headers["content-disposition"], "attachment")
        self.assertEqual(unsafe.headers["x-content-type-options"], "nosniff")
        self.assertEqual(unsafe.headers["content-security-policy"], "sandbox; default-src 'none'")
        self.assertEqual(unsafe.headers["cache-control"], "private, no-store")
        self.assertNotIn("content-disposition", safe.headers)

    async def test_isolated_runtime_keeps_source_frontend_assets(self) -> None:
        backend = Path(__file__).resolve().parents[1]
        expected = backend.parents[1] / "澪Agent应用" / "dist"
        with tempfile.TemporaryDirectory() as temporary_directory:
            env = dict(os.environ)
            env["MIO_RUNTIME_ROOT"] = temporary_directory
            env["PYTHONPATH"] = str(backend)
            env["PYTHONIOENCODING"] = "utf-8"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from app.config import settings; print(settings.agent_frontend_dir)",
                ],
                cwd=backend,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        self.assertEqual(Path(completed.stdout.strip()).resolve(), expected.resolve())


if __name__ == "__main__":
    unittest.main()
