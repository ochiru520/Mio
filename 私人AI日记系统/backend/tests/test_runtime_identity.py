from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.runtime_identity import build_runtime_identity
from scripts.run_clean_regression import build_clean_environment


class RuntimeIdentityTests(unittest.TestCase):
    def test_manifest_identifies_build_roots_and_verified_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            artifact = root / "artifact.bin"
            artifact.write_bytes(b"mio-build")
            manifest = root / "构建清单.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "build_id": "mio-test-build",
                        "app_version": "0.5.9.0",
                        "built_at_utc": "2026-08-15T00:00:00Z",
                        "sources": {
                            "backend": {"commit": "abc123", "dirty": False, "dirty_hash": ""},
                            "desktop": {"commit": "def456", "dirty": False, "dirty_hash": ""},
                        },
                        "artifacts": [
                            {
                                "name": "test",
                                "path": "artifact.bin",
                                "size": artifact.stat().st_size,
                                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest().upper(),
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            runtime_root = root / "runtime"
            state_root = root / "state"
            database = runtime_root / "数据" / "personal_ai.db"

            identity = build_runtime_identity(
                manifest_path=manifest,
                exe_path=root / "program" / "澪.exe",
                runtime_root=runtime_root,
                state_root=state_root,
                database_path=database,
                frozen=True,
            )

        self.assertEqual(identity["build_id"], "mio-test-build")
        self.assertEqual(identity["runtime_root"], str(runtime_root.resolve()))
        self.assertEqual(identity["state_root"], str(state_root.resolve()))
        self.assertEqual(identity["database_path"], str(database.resolve()))
        self.assertTrue(identity["artifact_verification"]["ok"])
        self.assertEqual(identity["source_revisions"]["backend"]["commit"], "abc123")
        self.assertEqual(identity["warnings"], [])

    def test_supported_program_data_layout_is_not_reported_as_a_boundary_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            program_root = root / "Mio"
            program_root.mkdir()
            manifest = program_root / "构建清单.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "build_id": "mio-program-data",
                        "app_version": "0.1.0.0",
                        "artifacts": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state_root = program_root / "Data"
            runtime_root = state_root / "运行数据"
            database = runtime_root / "数据" / "personal_ai.db"

            identity = build_runtime_identity(
                manifest_path=manifest,
                exe_path=program_root / "Mio.exe",
                runtime_root=runtime_root,
                state_root=state_root,
                database_path=database,
                frozen=True,
            )

        self.assertNotIn("业务数据库位于程序目录内，程序与数据边界发生冲突。", identity["warnings"])

    def test_arbitrary_database_inside_program_root_still_reports_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            program_root = root / "Mio"
            program_root.mkdir()
            manifest = program_root / "构建清单.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "build_id": "mio-test", "artifacts": []}),
                encoding="utf-8",
            )

            identity = build_runtime_identity(
                manifest_path=manifest,
                exe_path=program_root / "Mio.exe",
                runtime_root=program_root / "runtime",
                state_root=root / "state",
                database_path=program_root / "personal_ai.db",
                frozen=True,
            )

        self.assertIn("业务数据库位于程序目录内，程序与数据边界发生冲突。", identity["warnings"])

    def test_clean_environment_scrubs_models_proxy_and_private_runtime(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            previous = dict(os.environ)
            try:
                os.environ.update(
                    {
                        "OPENAI_API_KEY": "must-not-leak",
                        "OPENAI_MODEL": "private-model",
                        "HTTPS_PROXY": "http://private.proxy",
                        "QQ_ONEBOT_TOKEN": "private-token",
                    }
                )
                environment = build_clean_environment(root / "runtime", root / "backend")
            finally:
                os.environ.clear()
                os.environ.update(previous)

        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("OPENAI_MODEL", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertNotIn("QQ_ONEBOT_TOKEN", environment)
        self.assertEqual(environment["MIO_DISABLE_DOTENV"], "1")
        self.assertEqual(environment["MIO_RUNTIME_ROOT"], str(root / "runtime"))
