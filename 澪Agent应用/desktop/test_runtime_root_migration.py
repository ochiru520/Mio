from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from desktop import runtime_root_migration as migration


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RuntimeRootMigrationTests(unittest.TestCase):
    def _create_legacy_root(self, root: Path) -> Path:
        legacy = root / "旧项目"
        data = legacy / "数据"
        data.mkdir(parents=True)
        with closing(sqlite3.connect(data / "personal_ai.db")) as connection:
            connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT NOT NULL)")
            connection.execute("INSERT INTO messages(content) VALUES ('保留这条消息')")
            connection.commit()
        (data / "日记").mkdir()
        (data / "日记" / "2026-08-15.md").write_text("今天的日记", encoding="utf-8")
        (data / "模型供应商.json").write_text('{"providers": []}', encoding="utf-8")
        (legacy / "backend").mkdir()
        (legacy / "backend" / ".env").write_text("OPENAI_API_KEY=test-only\n", encoding="utf-8")
        (legacy / "澪_日记网站").mkdir()
        (legacy / "澪_日记网站" / "自定义.css").write_text("body {}", encoding="utf-8")
        (root / "澪运行时说明书.md").write_text("运行说明", encoding="utf-8")
        return legacy

    def test_choose_migrates_and_preserves_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = self._create_legacy_root(root)
            state = root / "桌面状态"
            config = state / "运行配置.json"
            source_diary = legacy / "数据" / "日记" / "2026-08-15.md"
            source_database = legacy / "数据" / "personal_ai.db"
            source_hashes = (_sha256(source_diary), _sha256(source_database))

            selected = migration.choose_runtime_root(state, config, [legacy])

            self.assertEqual(selected, (state / "运行数据").resolve())
            self.assertEqual(source_hashes, (_sha256(source_diary), _sha256(source_database)))
            self.assertEqual(
                (selected / "数据" / "日记" / "2026-08-15.md").read_text(encoding="utf-8"),
                "今天的日记",
            )
            self.assertTrue((selected / "backend" / ".env").is_file())
            self.assertTrue((selected / "澪_日记网站" / "自定义.css").is_file())
            self.assertTrue((selected / "澪运行时说明书.md").is_file())
            with closing(sqlite3.connect(selected / "数据" / "personal_ai.db")) as connection:
                self.assertEqual(connection.execute("SELECT content FROM messages").fetchone()[0], "保留这条消息")
            manifest = migration.verify_migration_manifest(selected)
            self.assertTrue(manifest["source_preserved"])
            saved = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(Path(saved["data_root"]), selected)
            self.assertEqual(Path(saved["previous_data_root"]), legacy.resolve())

    def test_existing_target_wins_without_overwriting_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = self._create_legacy_root(root)
            state = root / "桌面状态"
            target_database = state / "运行数据" / "数据" / "personal_ai.db"
            target_database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(target_database)) as connection:
                connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
                connection.execute("INSERT INTO marker(value) VALUES ('target')")
                connection.commit()

            selected = migration.choose_runtime_root(state, state / "运行配置.json", [legacy])

            self.assertEqual(selected, (state / "运行数据").resolve())
            with closing(sqlite3.connect(target_database)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM marker").fetchone()[0], "target")

    def test_copy_failure_keeps_previous_config_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = self._create_legacy_root(root)
            state = root / "桌面状态"
            config = state / "运行配置.json"
            config.parent.mkdir(parents=True)
            original_config = json.dumps({"data_root": str(legacy.resolve())}, ensure_ascii=False)
            config.write_text(original_config, encoding="utf-8")
            source_diary = legacy / "数据" / "日记" / "2026-08-15.md"

            with patch.object(migration.shutil, "copy2", side_effect=OSError("copy failed")):
                with self.assertRaisesRegex(migration.RuntimeMigrationError, "copy failed"):
                    migration.choose_runtime_root(state, config, [legacy])

            self.assertEqual(config.read_text(encoding="utf-8"), original_config)
            self.assertEqual(source_diary.read_text(encoding="utf-8"), "今天的日记")
            self.assertFalse((state / "运行数据").exists())
            self.assertTrue(list(state.glob("运行数据.迁移中-*")))

    def test_manifest_detects_tampered_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = self._create_legacy_root(root)
            target = root / "桌面状态" / "运行数据"
            migration.migrate_runtime_root(legacy, target)
            (target / "数据" / "模型供应商.json").write_text("tampered", encoding="utf-8")

            with self.assertRaisesRegex(migration.RuntimeMigrationError, "哈希不一致|大小不一致"):
                migration.verify_migration_manifest(target)

    def test_existing_migrated_target_allows_live_data_to_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = self._create_legacy_root(root)
            state = root / "桌面状态"
            config = state / "运行配置.json"
            target = migration.choose_runtime_root(state, config, [legacy])

            with closing(sqlite3.connect(target / "数据" / "personal_ai.db")) as connection:
                connection.execute("INSERT INTO messages(content) VALUES ('迁移后的新消息')")
                connection.commit()
            (target / "数据" / "模型供应商.json").write_text(
                '{"providers": [{"id": "after-migration"}]}',
                encoding="utf-8",
            )

            selected = migration.choose_runtime_root(state, config, [legacy])

            self.assertEqual(selected, target)
            with closing(sqlite3.connect(target / "数据" / "personal_ai.db")) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            self.assertIn("after-migration", (target / "数据" / "模型供应商.json").read_text(encoding="utf-8"))

    def test_existing_target_rejects_manifest_for_another_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = self._create_legacy_root(root)
            state = root / "桌面状态"
            config = state / "运行配置.json"
            target = migration.choose_runtime_root(state, config, [legacy])
            manifest_path = target / migration.MIGRATION_MANIFEST_FILENAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["target_root"] = str(root / "其他运行根")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(migration.RuntimeMigrationError, "目标目录与当前运行根不一致"):
                migration.choose_runtime_root(state, config, [legacy])


if __name__ == "__main__":
    unittest.main()
