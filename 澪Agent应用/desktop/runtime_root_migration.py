from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MIGRATION_SCHEMA_VERSION = 1
MIGRATION_MANIFEST_FILENAME = "运行根迁移清单.json"
FAILED_MIGRATION_FILENAME = "运行根迁移失败.json"
RUNTIME_DATA_DIRECTORY = "运行数据"
DATA_DIRECTORY = "数据"
DATABASE_RELATIVE_PATH = Path(DATA_DIRECTORY) / "personal_ai.db"
RUNTIME_DOCUMENT_NAMES = (
    "澪运行时说明书.md",
    "澪_私人AI人格设定与提示词.md",
    "个人说明书.txt",
    "个人天赋使用说明书.txt",
)


class RuntimeMigrationError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:
        return False


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def is_runtime_root(path: Path) -> bool:
    return (
        (path / DATABASE_RELATIVE_PATH).is_file()
        or (path / "backend" / ".env").is_file()
        or (path / ".env").is_file()
    )


def read_runtime_root_config(config_path: Path) -> Path | None:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        raw = str(payload.get("data_root") or "").strip()
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not raw:
        return None
    candidate = Path(os.path.expandvars(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _write_runtime_root_config(
    config_path: Path,
    runtime_root: Path,
    *,
    previous_root: Path | None,
    migration_manifest: Path | None,
) -> None:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.update(
        {
            "schema_version": 2,
            "data_root": str(runtime_root.resolve()),
            "previous_data_root": str(previous_root.resolve()) if previous_root else "",
            "migration_manifest": str(migration_manifest.resolve()) if migration_manifest else "",
            "updated_at_utc": _utc_now(),
        }
    )
    _write_json(config_path, payload)


def _is_reparse_point(path: Path) -> bool:
    details = path.lstat()
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse_attribute = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(attributes & reparse_attribute)


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return ()
    files: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        kept_directories: list[str] = []
        for name in directory_names:
            candidate = current / name
            if _is_reparse_point(candidate):
                raise RuntimeMigrationError(f"运行数据包含不允许迁移的链接目录：{candidate}")
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in file_names:
            candidate = current / name
            if _is_reparse_point(candidate):
                raise RuntimeMigrationError(f"运行数据包含不允许迁移的链接文件：{candidate}")
            files.append(candidate)
    return sorted(files, key=lambda item: item.as_posix().casefold())


def _copy_stable_file(source: Path, target: Path) -> dict[str, object]:
    before = source.stat()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeMigrationError(f"迁移期间源文件发生变化：{source}")
    source_hash = _sha256(source)
    target_hash = _sha256(target)
    if source_hash != target_hash:
        raise RuntimeMigrationError(f"迁移文件哈希不一致：{source}")
    return {"size": target.stat().st_size, "sha256": target_hash}


def _copy_database(source: Path, target: Path) -> dict[str, object]:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=15)) as source_db:
        with closing(sqlite3.connect(target, timeout=15)) as target_db:
            source_db.backup(target_db)
            result = str(target_db.execute("PRAGMA integrity_check").fetchone()[0])
    if result.lower() != "ok":
        raise RuntimeMigrationError(f"迁移数据库完整性检查失败：{result}")
    return {"size": target.stat().st_size, "sha256": _sha256(target)}


def _source_entries(source_root: Path) -> list[tuple[Path, Path]]:
    entries: dict[str, tuple[Path, Path]] = {}

    data_root = source_root / DATA_DIRECTORY
    for source in _iter_files(data_root):
        relative = source.relative_to(source_root)
        if relative == DATABASE_RELATIVE_PATH or source.name in {
            "personal_ai.db-wal",
            "personal_ai.db-shm",
        }:
            continue
        entries[relative.as_posix().casefold()] = (source, relative)

    for relative in (Path(".env"), Path("backend") / ".env"):
        source = source_root / relative
        if source.is_file():
            entries[relative.as_posix().casefold()] = (source, relative)

    site_root = source_root / "澪_日记网站"
    for source in _iter_files(site_root):
        relative = source.relative_to(source_root)
        entries[relative.as_posix().casefold()] = (source, relative)

    for name in RUNTIME_DOCUMENT_NAMES:
        source = source_root / name
        if not source.is_file():
            source = source_root.parent / name
        if source.is_file():
            relative = Path(name)
            entries[relative.as_posix().casefold()] = (source, relative)

    return [entries[key] for key in sorted(entries)]


def verify_migration_manifest(
    runtime_root: Path,
    *,
    verify_artifacts: bool = True,
    expected_target_root: Path | None = None,
) -> dict[str, Any]:
    manifest_path = runtime_root / MIGRATION_MANIFEST_FILENAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeMigrationError(f"无法读取运行根迁移清单：{exc}") from exc
    if int(payload.get("schema_version") or 0) != MIGRATION_SCHEMA_VERSION:
        raise RuntimeMigrationError("运行根迁移清单版本无效。")
    declared_target = Path(str(payload.get("target_root") or "")).expanduser()
    expected_target = expected_target_root or runtime_root
    if not declared_target.is_absolute() or not _same_path(declared_target, expected_target):
        raise RuntimeMigrationError("运行根迁移清单的目标目录与当前运行根不一致。")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise RuntimeMigrationError("运行根迁移清单缺少文件记录。")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise RuntimeMigrationError("运行根迁移清单包含无效记录。")
        relative = Path(str(artifact.get("path") or ""))
        target = (runtime_root / relative).resolve()
        if not str(relative) or not _is_within(target, runtime_root):
            raise RuntimeMigrationError(f"迁移文件缺失或路径越界：{relative}")
        try:
            declared_size = int(artifact.get("size"))
        except (TypeError, ValueError) as exc:
            raise RuntimeMigrationError("运行根迁移清单包含无效记录。") from exc
        declared_hash = str(artifact.get("sha256") or "").upper()
        if declared_size < 0 or len(declared_hash) != 64 or any(
            character not in "0123456789ABCDEF" for character in declared_hash
        ):
            raise RuntimeMigrationError("运行根迁移清单包含无效记录。")
        if not verify_artifacts:
            continue
        if not target.is_file():
            raise RuntimeMigrationError(f"迁移文件缺失或路径越界：{relative}")
        if target.stat().st_size != declared_size:
            raise RuntimeMigrationError(f"迁移文件大小不一致：{relative}")
        if _sha256(target) != declared_hash:
            raise RuntimeMigrationError(f"迁移文件哈希不一致：{relative}")
    return payload


def migrate_runtime_root(source_root: Path, target_root: Path) -> Path:
    source = source_root.expanduser().resolve()
    target = target_root.expanduser().resolve()
    if _same_path(source, target):
        return target
    if _is_within(target, source) or _is_within(source, target):
        raise RuntimeMigrationError("源运行根与目标运行根不能互相包含。")
    if not is_runtime_root(source):
        raise RuntimeMigrationError(f"源目录不是有效运行根：{source}")

    migration_id = uuid.uuid4().hex
    staging = target.with_name(f"{target.name}.迁移中-{migration_id}")
    preserved_target: Path | None = None
    artifacts: list[dict[str, object]] = []
    try:
        staging.mkdir(parents=True, exist_ok=False)
        source_database = source / DATABASE_RELATIVE_PATH
        if source_database.is_file():
            target_database = staging / DATABASE_RELATIVE_PATH
            database_result = _copy_database(source_database, target_database)
            artifacts.append(
                {"path": DATABASE_RELATIVE_PATH.as_posix(), **database_result}
            )

        for source_file, relative in _source_entries(source):
            result = _copy_stable_file(source_file, staging / relative)
            artifacts.append({"path": relative.as_posix(), **result})

        if not artifacts:
            raise RuntimeMigrationError("源运行根没有可迁移的持久文件。")
        artifacts.sort(key=lambda item: str(item["path"]).casefold())
        manifest = {
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "migration_id": migration_id,
            "created_at_utc": _utc_now(),
            "source_root": str(source),
            "target_root": str(target),
            "source_preserved": True,
            "artifacts": artifacts,
        }
        _write_json(staging / MIGRATION_MANIFEST_FILENAME, manifest)
        verify_migration_manifest(staging, expected_target_root=target)

        if target.exists():
            preserved_target = target.with_name(
                f"{target.name}.迁移前-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{migration_id[:8]}"
            )
            target.replace(preserved_target)
        staging.replace(target)
        verify_migration_manifest(target)
        return target
    except Exception as exc:
        if staging.exists():
            try:
                _write_json(
                    staging / FAILED_MIGRATION_FILENAME,
                    {
                        "migration_id": migration_id,
                        "failed_at_utc": _utc_now(),
                        "source_root": str(source),
                        "target_root": str(target),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            except OSError:
                pass
        if preserved_target is not None and preserved_target.exists() and not target.exists():
            preserved_target.replace(target)
        if isinstance(exc, RuntimeMigrationError):
            raise
        raise RuntimeMigrationError(f"运行根迁移失败：{exc}") from exc


def choose_runtime_root(
    state_dir: Path,
    config_path: Path,
    legacy_roots: Iterable[Path],
) -> Path:
    state = state_dir.expanduser().resolve()
    target = (state / RUNTIME_DATA_DIRECTORY).resolve()
    saved = read_runtime_root_config(config_path)

    if is_runtime_root(target):
        manifest_path = target / MIGRATION_MANIFEST_FILENAME
        if manifest_path.is_file():
            # The manifest proves that the initial copy was verified. Once the
            # target becomes the live runtime, its database and settings are
            # expected to change and must not be compared with migration-time hashes.
            verify_migration_manifest(target, verify_artifacts=False)
        previous = saved if saved is not None and not _same_path(saved, target) else None
        _write_runtime_root_config(
            config_path,
            target,
            previous_root=previous,
            migration_manifest=manifest_path if manifest_path.is_file() else None,
        )
        return target

    candidates = [saved, *legacy_roots]
    source = next(
        (
            candidate.expanduser().resolve()
            for candidate in candidates
            if candidate is not None
            and not _same_path(candidate, target)
            and is_runtime_root(candidate.expanduser().resolve())
        ),
        None,
    )
    manifest_path: Path | None = None
    if source is not None:
        migrate_runtime_root(source, target)
        manifest_path = target / MIGRATION_MANIFEST_FILENAME
    else:
        (target / DATA_DIRECTORY).mkdir(parents=True, exist_ok=True)

    _write_runtime_root_config(
        config_path,
        target,
        previous_root=source,
        migration_manifest=manifest_path,
    )
    return target
