from __future__ import annotations

import io
import hashlib
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import backup_service, db, maintenance_service, migration_service
from app.config import settings
from app.main import create_app


class BackupMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.originals = {
            "data_dir": settings.data_dir,
            "db_path": settings.db_path,
            "diary_dir": settings.diary_dir,
            "mio_profile_path": settings.mio_profile_path,
            "runtime_config_path": settings.runtime_config_path,
            "backup_enabled": settings.backup_enabled,
        }
        object.__setattr__(settings, "data_dir", root / "数据")
        object.__setattr__(settings, "db_path", root / "数据" / "personal_ai.db")
        object.__setattr__(settings, "diary_dir", root / "数据" / "日记")
        object.__setattr__(settings, "mio_profile_path", root / "数据" / "澪属性.json")
        object.__setattr__(settings, "runtime_config_path", root / "数据" / "运行设置.json")
        object.__setattr__(settings, "backup_enabled", True)
        settings.ensure_directories()
        db.init_db()

    def tearDown(self) -> None:
        for key, value in self.originals.items():
            object.__setattr__(settings, key, value)
        self.temp_dir.cleanup()

    def test_migration_ledger_records_baseline_once(self) -> None:
        first = migration_service.run_migrations()
        second = migration_service.run_migrations()
        status = migration_service.migration_status()

        self.assertEqual([item["version"] for item in first], [1])
        self.assertEqual(second, [])
        self.assertTrue(status["up_to_date"])
        self.assertEqual(status["current_version"], 1)
        self.assertTrue(list((settings.data_dir / "备份").glob("*.zip")))

    def test_complete_backup_restores_database_and_files(self) -> None:
        db.create_agent_conversation("desktop_before", "恢复前")
        settings.mio_profile_path.write_text('{"identity":{"name":"澪"}}', encoding="utf-8")
        diary = settings.diary_dir / "2026-08-12.md"
        diary.write_text("原始日记", encoding="utf-8")
        archive = backup_service.create_complete_backup()

        db.create_agent_conversation("desktop_after", "备份后")
        diary.write_text("被修改", encoding="utf-8")
        extra = settings.data_dir / "稍后创建.txt"
        extra.write_text("不应保留", encoding="utf-8")

        result = backup_service.restore_backup(archive.name)

        self.assertTrue(result["restart_required"])
        self.assertEqual(diary.read_text(encoding="utf-8"), "原始日记")
        self.assertFalse(extra.exists())
        with db.get_conn() as conn:
            names = {
                row["id"]
                for row in conn.execute("SELECT id FROM agent_conversations").fetchall()
            }
        self.assertIn("desktop_before", names)
        self.assertNotIn("desktop_after", names)
        self.assertTrue((settings.data_dir / "备份" / result["rollback_backup"]).is_file())

    def test_complete_backup_uses_current_version_and_self_verifies(self) -> None:
        profile = settings.data_dir / "profile.json"
        profile.write_text('{"name":"澪"}', encoding="utf-8")
        with patch.object(backup_service, "inspect_backup", wraps=backup_service.inspect_backup) as inspect:
            archive_path = backup_service.create_complete_backup()

        inspect.assert_called_once()
        info = backup_service.inspect_backup(archive_path)
        self.assertEqual(info["app_version"], backup_service.CURRENT_APP_VERSION)

    def test_complete_backup_rejects_limits_before_publishing_archive(self) -> None:
        with patch.object(backup_service, "MAX_ARCHIVE_FILES", 1):
            with self.assertRaisesRegex(ValueError, "文件数量超过"):
                backup_service.create_complete_backup()
        self.assertFalse(list((settings.data_dir / "备份").glob("*.zip")))

    def test_complete_backup_archives_staged_file_not_mutated_source(self) -> None:
        profile = settings.data_dir / "profile.json"
        profile.write_text("before", encoding="utf-8")
        real_copy = backup_service.shutil.copy2

        def mutate_after_copy(source, target):
            result = real_copy(source, target)
            if Path(source) == profile:
                profile.write_text("after", encoding="utf-8")
            return result

        with patch.object(backup_service.shutil, "copy2", side_effect=mutate_after_copy):
            archive_path = backup_service.create_complete_backup()

        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.read("profile.json"), b"before")
        self.assertEqual(profile.read_text(encoding="utf-8"), "after")

    def test_backup_and_restore_checkpoint_wal_before_database_io(self) -> None:
        db.create_agent_conversation("wal_before", "检查点前")
        with patch.object(
            backup_service,
            "_checkpoint_database",
            wraps=backup_service._checkpoint_database,
        ) as checkpoint:
            archive = backup_service.create_complete_backup()
            db.create_agent_conversation("wal_after", "检查点后")
            backup_service.restore_backup(archive.name)

        self.assertGreaterEqual(checkpoint.call_count, 4)
        self.assertIsNotNone(db.get_agent_conversation("wal_before"))
        self.assertIsNone(db.get_agent_conversation("wal_after"))

    def test_backup_rejects_path_traversal(self) -> None:
        malicious = settings.data_dir / "备份" / "恶意.zip"
        malicious.parent.mkdir(parents=True, exist_ok=True)
        payload = b"bad"
        manifest = {
            "format": backup_service.BACKUP_FORMAT,
            "format_version": backup_service.BACKUP_FORMAT_VERSION,
            "files": [
                {
                    "path": "../outside.txt",
                    "size": len(payload),
                    "sha256": "0" * 64,
                }
            ],
        }
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("../outside.txt", payload)
        with self.assertRaises(ValueError):
            backup_service.inspect_backup(malicious)

    def _write_backup(self, name: str, files: list[tuple[str, bytes]]) -> Path:
        target = settings.data_dir / "备份" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": backup_service.BACKUP_FORMAT,
            "format_version": backup_service.BACKUP_FORMAT_VERSION,
            "files": [
                {
                    "path": path,
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for path, payload in files
            ],
        }
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for path, payload in files:
                archive.writestr(path, payload)
        return target

    def test_backup_rejects_oversized_manifest(self) -> None:
        archive_path = self._write_backup("清单过大.zip", [("personal_ai.db", b"db")])
        with patch.object(backup_service, "MAX_MANIFEST_BYTES", 16):
            with self.assertRaisesRegex(ValueError, "备份清单超过"):
                backup_service.inspect_backup(archive_path)

    def test_backup_rejects_too_many_zip_entries(self) -> None:
        archive_path = self._write_backup("文件过多.zip", [("personal_ai.db", b"db")])
        with patch.object(backup_service, "MAX_ARCHIVE_FILES", 1):
            with self.assertRaisesRegex(ValueError, "文件数量超过"):
                backup_service.inspect_backup(archive_path)

    def test_backup_rejects_duplicate_zip_entries(self) -> None:
        archive_path = self._write_backup("重复条目.zip", [("personal_ai.db", b"db")])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive_path, "a") as archive:
                archive.writestr("personal_ai.db", b"duplicate")
        with self.assertRaisesRegex(ValueError, "重复 ZIP 条目"):
            backup_service.inspect_backup(archive_path)

    def test_backup_rejects_case_insensitive_path_collision(self) -> None:
        archive_path = self._write_backup(
            "大小写碰撞.zip",
            [("personal_ai.db", b"db"), ("Notes.txt", b"one"), ("notes.txt", b"two")],
        )
        with self.assertRaisesRegex(ValueError, "重复 ZIP 条目"):
            backup_service.inspect_backup(archive_path)

    def test_backup_rejects_windows_device_and_stream_paths(self) -> None:
        for index, unsafe_path in enumerate(("NUL.txt", "notes.txt:payload")):
            with self.subTest(path=unsafe_path):
                archive_path = self._write_backup(
                    f"危险路径-{index}.zip",
                    [("personal_ai.db", b"db"), (unsafe_path, b"payload")],
                )
                with self.assertRaisesRegex(ValueError, "不安全或缺失的路径"):
                    backup_service.inspect_backup(archive_path)

    def test_backup_rejects_excessive_extracted_size(self) -> None:
        archive_path = self._write_backup("解压过大.zip", [("personal_ai.db", b"database")])
        with patch.object(backup_service, "MAX_EXTRACTED_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "解压总量超过"):
                backup_service.inspect_backup(archive_path)

    def test_backup_inspection_hashes_entries_without_archive_read(self) -> None:
        archive_path = self._write_backup("流式校验.zip", [("personal_ai.db", b"database")])
        with patch.object(zipfile.ZipFile, "read", side_effect=AssertionError("must stream")):
            result = backup_service.inspect_backup(archive_path)
        self.assertTrue(result["valid"])

    def test_restore_failure_reports_successful_automatic_rollback(self) -> None:
        archive = backup_service.create_complete_backup()
        real_replace = backup_service._replace_from_staging
        calls = 0

        def fail_restore_once(staging, manifest):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("restore copy failed")
            return real_replace(staging, manifest)

        with patch.object(backup_service, "_replace_from_staging", side_effect=fail_restore_once):
            with self.assertRaisesRegex(ValueError, "已自动回滚"):
                backup_service.restore_backup(archive.name)
        self.assertEqual(calls, 2)

    def test_restore_reports_when_restore_and_rollback_both_fail(self) -> None:
        archive = backup_service.create_complete_backup()
        with patch.object(
            backup_service,
            "_replace_from_staging",
            side_effect=[OSError("restore failed"), OSError("rollback failed")],
        ):
            with self.assertRaisesRegex(ValueError, "自动回滚也失败") as raised:
                backup_service.restore_backup(archive.name)

        self.assertIn("restore failed", str(raised.exception))
        self.assertIn("rollback failed", str(raised.exception))

    def test_backup_routes_create_list_download_and_report_migrations(self) -> None:
        migration_service.run_migrations()
        app = create_app()
        with TestClient(app) as client:
            created = client.post("/api/backups")
            self.assertEqual(created.status_code, 200)
            name = created.json()["backup"]["name"]
            listed = client.get("/api/backups")
            self.assertEqual(listed.status_code, 200)
            self.assertIn(name, [item["name"] for item in listed.json()["backups"]])
            downloaded = client.get(f"/api/backups/{name}/download")
            self.assertEqual(downloaded.status_code, 200)
            self.assertTrue(downloaded.content.startswith(b"PK"))
            imported = client.post(
                "/api/backups/import?filename=%E6%9D%A5%E8%87%AA%E6%97%A7%E7%94%B5%E8%84%91.zip",
                content=downloaded.content,
                headers={"Content-Type": "application/zip"},
            )
            self.assertEqual(imported.status_code, 200)
            self.assertTrue(imported.json()["backup"]["valid"])
            status = client.get("/api/migrations/status")
            self.assertEqual(status.status_code, 200)
            self.assertTrue(status.json()["up_to_date"])

    def test_backup_import_rejects_declared_oversize_before_reading_body(self) -> None:
        app = create_app()
        with (
            patch.object(backup_service, "MAX_IMPORT_BYTES", 4),
            patch("app.main.initialize_runtime"),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/backups/import?filename=oversized.zip",
                    content=b"small",
                    headers={"Content-Type": "application/zip", "Content-Length": "5"},
                )
        self.assertEqual(response.status_code, 413)
        self.assertFalse(list((settings.data_dir / "备份").glob(".mio-import-*")))

    def _create_maintenance_test_app(self):
        app = create_app()

        @app.post("/api/test-write")
        async def test_write():
            return {"written": True}

        return app

    def test_restore_success_keeps_application_read_only_until_restart(self) -> None:
        archive = backup_service.create_complete_backup()
        app = self._create_maintenance_test_app()
        restored = {
            "restored": True,
            "backup": archive.name,
            "rollback_backup": "safety.zip",
            "restart_required": True,
        }
        with (
            patch.dict("app.main.BACKGROUND_TASK_FACTORIES", {}, clear=True),
            patch("app.main.initialize_runtime"),
            patch("app.main.onebot.disconnect_all_connections"),
            patch.object(backup_service, "restore_backup", return_value=restored),
            TestClient(app) as client,
        ):
            response = client.post(f"/api/backups/{archive.name}/restore")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["maintenance"]["status"], "restart_required")
            self.assertTrue(response.json()["maintenance"]["blocked"])

            blocked_write = client.post("/api/test-write")
            self.assertEqual(blocked_write.status_code, 503)
            self.assertEqual(blocked_write.headers["retry-after"], "5")

            second_restore = client.post(f"/api/backups/{archive.name}/restore")
            self.assertEqual(second_restore.status_code, 503)
            health = client.get("/health").json()["maintenance"]
            self.assertEqual(health["status"], "restart_required")
            self.assertTrue(health["blocked"])

    def test_restore_failure_with_rollback_resumes_writes(self) -> None:
        archive = backup_service.create_complete_backup()
        app = self._create_maintenance_test_app()
        with (
            patch.dict("app.main.BACKGROUND_TASK_FACTORIES", {}, clear=True),
            patch("app.main.initialize_runtime"),
            patch("app.main.onebot.disconnect_all_connections"),
            patch.object(
                backup_service,
                "restore_backup",
                side_effect=ValueError("恢复失败，已自动回滚"),
            ),
            TestClient(app) as client,
        ):
            response = client.post(f"/api/backups/{archive.name}/restore")
            self.assertEqual(response.status_code, 400)
            maintenance = client.get("/health").json()["maintenance"]
            self.assertEqual(maintenance["status"], "rollback_complete")
            self.assertFalse(maintenance["blocked"])
            self.assertEqual(client.post("/api/test-write").status_code, 200)

    def test_restore_and_rollback_failure_keeps_write_barrier(self) -> None:
        archive = backup_service.create_complete_backup()
        app = self._create_maintenance_test_app()
        with (
            patch.dict("app.main.BACKGROUND_TASK_FACTORIES", {}, clear=True),
            patch("app.main.initialize_runtime"),
            patch("app.main.onebot.disconnect_all_connections"),
            patch.object(
                backup_service,
                "restore_backup",
                side_effect=backup_service.RestoreStateUncertainError("restore and rollback failed"),
            ),
            TestClient(app) as client,
        ):
            response = client.post(f"/api/backups/{archive.name}/restore")
            self.assertEqual(response.status_code, 500)
            maintenance = client.get("/health").json()["maintenance"]
            self.assertEqual(maintenance["status"], "state_uncertain")
            self.assertTrue(maintenance["blocked"])
            self.assertEqual(client.post("/api/test-write").status_code, 503)


if __name__ == "__main__":
    unittest.main()
