from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import db
from .config import settings


logger = logging.getLogger(__name__)


class RestoreStateUncertainError(ValueError):
    pass

BACKUP_FORMAT = "mio-complete-backup"
BACKUP_FORMAT_VERSION = 1
CURRENT_APP_VERSION = "0.5.9"
MAX_IMPORT_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
MANIFEST_NAME = "manifest.json"
DATABASE_ARCHIVE_PATH = "personal_ai.db"
_BACKUP_LOCK = threading.RLock()
_VOLATILE_NAMES = {
    "游戏预览.jpg",
    "窗口预览.jpg",
    "屏幕预览.jpg",
    "preview.jpg",
}


def _backup_dir() -> Path:
    directory = settings.data_dir / "备份"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _snapshot_database(target: Path) -> None:
    _checkpoint_database()
    source = db.get_conn()
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def _checkpoint_database() -> None:
    """Flush SQLite WAL pages before a backup or restore touches the DB files."""
    connection = db.get_conn()
    try:
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # A busy checkpoint is not fatal when sqlite's online backup can still
        # obtain a consistent snapshot, but make a passive retry explicit.
        if result and int(result[0] or 0) != 0:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_persistent_file(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(settings.data_dir.resolve())
    except (OSError, ValueError):
        return False
    if not relative.parts or relative.parts[0] == "备份":
        return False
    if path.name in _VOLATILE_NAMES or path.suffix.lower() in {".tmp", ".lock"}:
        return False
    if path.name.startswith("personal_ai.db-"):
        return False
    return path.is_file()


def _persistent_files() -> list[Path]:
    if not settings.data_dir.exists():
        return []
    return sorted(
        (path for path in settings.data_dir.rglob("*") if _is_persistent_file(path)),
        key=lambda item: item.as_posix().casefold(),
    )


def _safe_backup_name(kind: str) -> str:
    prefix = "自动备份" if kind == "auto" else "完整备份"
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.zip"


def create_complete_backup(*, kind: str = "manual", reason: str = "用户手动创建") -> Path:
    """Create a verified snapshot of all persistent data without plaintext .env files."""
    with _BACKUP_LOCK:
        directory = _backup_dir()
        target = directory / _safe_backup_name(kind)
        temporary_target = target.with_suffix(".zip.tmp")
        with tempfile.TemporaryDirectory(prefix="mio-backup-") as temp_dir:
            staging = Path(temp_dir)
            db_snapshot = staging / DATABASE_ARCHIVE_PATH
            _snapshot_database(db_snapshot)

            entries: list[dict[str, object]] = []
            files: list[tuple[Path, str]] = [(db_snapshot, DATABASE_ARCHIVE_PATH)]
            persistent_sources = [
                source
                for source in _persistent_files()
                if source.resolve() != settings.db_path.resolve()
            ]
            if len(persistent_sources) + 2 > MAX_ARCHIVE_FILES:
                raise ValueError(f"备份文件数量超过上限 {MAX_ARCHIVE_FILES}。")
            staged_total = db_snapshot.stat().st_size
            if staged_total > MAX_EXTRACTED_BYTES:
                raise ValueError("备份数据总量超过允许的解压上限。")
            staged_files_root = staging / "files"
            for source in persistent_sources:
                if source.resolve() == settings.db_path.resolve():
                    continue
                archive_name = source.resolve().relative_to(settings.data_dir.resolve()).as_posix()
                source_size = source.stat().st_size
                if staged_total + source_size > MAX_EXTRACTED_BYTES:
                    raise ValueError("备份数据总量超过允许的解压上限。")
                staged_source = staged_files_root / Path(*PurePosixPath(archive_name).parts)
                staged_source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, staged_source)
                staged_total += staged_source.stat().st_size
                if staged_total > MAX_EXTRACTED_BYTES:
                    raise ValueError("备份数据总量超过允许的解压上限。")
                files.append((staged_source, archive_name))

            for source, archive_name in files:
                entries.append(
                    {
                        "path": archive_name,
                        "size": source.stat().st_size,
                        "sha256": _sha256(source),
                    }
                )

            manifest = {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": db.now_iso(),
                "kind": kind,
                "reason": reason,
                "app_version": CURRENT_APP_VERSION,
                "files": entries,
            }
            manifest_payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(manifest_payload) > MAX_MANIFEST_BYTES:
                raise ValueError("备份清单超过允许的大小上限。")
            try:
                with zipfile.ZipFile(temporary_target, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(MANIFEST_NAME, manifest_payload)
                    for source, archive_name in files:
                        archive.write(source, archive_name)
                if temporary_target.stat().st_size > MAX_IMPORT_BYTES:
                    raise ValueError("完整备份压缩后超过 2 GB，无法由当前版本重新导入。")
                inspect_backup(temporary_target)
                os.replace(temporary_target, target)
            except Exception:
                temporary_target.unlink(missing_ok=True)
                target.unlink(missing_ok=True)
                raise
        return target


def _archive_info_map(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise ValueError(f"备份文件数量超过上限 {MAX_ARCHIVE_FILES}。")
    result: dict[str, zipfile.ZipInfo] = {}
    normalized_names: set[str] = set()
    for info in infos:
        normalized = info.filename.casefold()
        if info.filename in result or normalized in normalized_names:
            raise ValueError(f"备份包含重复 ZIP 条目：{info.filename or '(空)'}")
        result[info.filename] = info
        normalized_names.add(normalized)
    return result


def _read_manifest(
    archive: zipfile.ZipFile,
    infos: dict[str, zipfile.ZipInfo],
) -> dict[str, object]:
    try:
        info = infos[MANIFEST_NAME]
        if info.is_dir() or info.file_size > MAX_MANIFEST_BYTES:
            raise ValueError(f"备份清单超过 {MAX_MANIFEST_BYTES // 1024 // 1024} MB 上限。")
        with archive.open(info) as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError(f"备份清单超过 {MAX_MANIFEST_BYTES // 1024 // 1024} MB 上限。")
    except KeyError as exc:
        raise ValueError("备份缺少有效的 manifest.json。") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError("备份缺少有效的 manifest.json。") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("备份缺少有效的 manifest.json。") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
        raise ValueError("这不是 Mio 的完整备份文件。")
    if int(manifest.get("format_version") or 0) != BACKUP_FORMAT_VERSION:
        raise ValueError("备份格式版本不受当前应用支持。")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("备份清单为空。")
    return manifest


def _validated_entries(
    manifest: dict[str, object],
    infos: dict[str, zipfile.ZipInfo],
) -> list[tuple[dict[str, object], zipfile.ZipInfo]]:
    entries: list[tuple[dict[str, object], zipfile.ZipInfo]] = []
    seen: set[str] = set()
    total_size = 0
    for raw in manifest["files"]:
        if not isinstance(raw, dict):
            raise ValueError("备份清单包含无效记录。")
        name = str(raw.get("path") or "")
        pure = PurePosixPath(name)
        info = infos.get(name)
        if (
            not name
            or pure.is_absolute()
            or not pure.parts
            or ".." in pure.parts
            or "\\" in name
            or any(_unsafe_windows_archive_part(part) for part in pure.parts)
            or pure.parts[0] == "备份"
            or name.casefold() in seen
            or info is None
            or info.is_dir()
        ):
            raise ValueError(f"备份包含不安全或缺失的路径：{name or '(空)'}")
        if name != DATABASE_ARCHIVE_PATH and name.lower().endswith((".env", "/.env")):
            raise ValueError("完整备份不能包含 .env 文件。")
        try:
            expected_size = int(raw.get("size"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"备份文件大小记录无效：{name}") from exc
        expected_hash = str(raw.get("sha256") or "").lower()
        if expected_size < 0 or info.file_size != expected_size:
            raise ValueError(f"备份文件大小校验失败：{name}")
        if len(expected_hash) != 64 or any(character not in "0123456789abcdef" for character in expected_hash):
            raise ValueError(f"备份文件哈希记录无效：{name}")
        total_size += expected_size
        if total_size > MAX_EXTRACTED_BYTES:
            raise ValueError(
                f"备份解压总量超过 {MAX_EXTRACTED_BYTES // 1024 // 1024 // 1024} GB 上限。"
            )
        seen.add(name.casefold())
        entries.append((raw, info))
    if DATABASE_ARCHIVE_PATH.casefold() not in seen:
        raise ValueError("备份中缺少数据库快照。")
    expected_zip_names = {str(entry[0]["path"]) for entry in entries}
    unexpected = set(infos) - expected_zip_names - {MANIFEST_NAME}
    if unexpected:
        name = sorted(unexpected)[0]
        raise ValueError(f"备份包含清单外文件：{name or '(空)'}")
    return entries


def _unsafe_windows_archive_part(part: str) -> bool:
    if not part or ":" in part or part.endswith((" ", ".")):
        return True
    device = part.split(".", 1)[0].casefold()
    return device in {"con", "prn", "aux", "nul"} or (
        len(device) == 4
        and device[:3] in {"com", "lpt"}
        and device[3] in "123456789"
    )


def _stream_entry_hash(source, output=None, *, max_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("备份条目的实际解压大小超过清单声明。")
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    return size, digest.hexdigest()


def inspect_backup(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path, "r") as archive:
        infos = _archive_info_map(archive)
        manifest = _read_manifest(archive, infos)
        entries = _validated_entries(manifest, infos)
        for entry, info in entries:
            name = str(entry["path"])
            with archive.open(info) as source:
                size, digest = _stream_entry_hash(source, max_bytes=int(entry["size"]))
            if size != int(entry["size"]):
                raise ValueError(f"备份文件大小校验失败：{name}")
            if digest != str(entry["sha256"]).lower():
                raise ValueError(f"备份文件校验失败：{name}")
    return {
        **manifest,
        "name": path.name,
        "size": path.stat().st_size,
        "valid": True,
    }


def list_backups() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(_backup_dir().glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            info = inspect_backup(path)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            info = {
                "name": path.name,
                "size": path.stat().st_size,
                "valid": False,
                "error": str(exc),
                "created_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                "kind": "legacy_or_invalid",
            }
        records.append(info)
    return records


def _resolve_backup(name: str) -> Path:
    if Path(name).name != name or not name.lower().endswith(".zip"):
        raise ValueError("备份名称无效。")
    path = (_backup_dir() / name).resolve()
    if path.parent != _backup_dir().resolve() or not path.is_file():
        raise FileNotFoundError(name)
    return path


def backup_path(name: str) -> Path:
    return _resolve_backup(name)


def create_import_staging_path() -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=".mio-import-",
        suffix=".zip.tmp",
        dir=str(_backup_dir()),
    )
    os.close(descriptor)
    return Path(raw_path)


def import_backup_file(filename: str, source: Path) -> dict[str, object]:
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ValueError("无法读取待导入的完整备份。") from exc
    if size <= 0 or size > MAX_IMPORT_BYTES:
        raise ValueError("完整备份必须大于 0 字节且不超过 2 GB。")
    clean_stem = Path(filename).stem.strip()[:80] or "导入备份"
    safe_stem = "".join(character for character in clean_stem if character.isalnum() or character in "-_（）")
    target = _backup_dir() / f"导入-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{safe_stem or '备份'}.zip"
    try:
        os.replace(source, target)
        return inspect_backup(target)
    except Exception:
        source.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def import_backup(filename: str, content: bytes) -> dict[str, object]:
    if not content or len(content) > MAX_IMPORT_BYTES:
        raise ValueError("完整备份必须大于 0 字节且不超过 2 GB。")
    staging = create_import_staging_path()
    try:
        staging.write_bytes(content)
        return import_backup_file(filename, staging)
    except Exception:
        staging.unlink(missing_ok=True)
        raise


def _extract_verified(path: Path, destination: Path) -> dict[str, object]:
    with zipfile.ZipFile(path, "r") as archive:
        infos = _archive_info_map(archive)
        manifest = _read_manifest(archive, infos)
        entries = _validated_entries(manifest, infos)
        for entry, info in entries:
            name = str(entry["path"])
            target = (destination / Path(*PurePosixPath(name).parts)).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError(f"备份路径越界：{name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                size, digest = _stream_entry_hash(source, output, max_bytes=int(entry["size"]))
            if size != int(entry["size"]) or digest != str(entry["sha256"]).lower():
                raise ValueError(f"备份文件校验失败：{name}")
    return manifest


def _replace_from_staging(staging: Path, manifest: dict[str, object]) -> None:
    _checkpoint_database()
    data_root = settings.data_dir.resolve()
    expected = {str(raw["path"]) for raw in manifest["files"]}
    for current in _persistent_files():
        archive_name = (
            DATABASE_ARCHIVE_PATH
            if current.resolve() == settings.db_path.resolve()
            else current.resolve().relative_to(data_root).as_posix()
        )
        if archive_name not in expected:
            current.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        settings.db_path.with_name(settings.db_path.name + suffix).unlink(missing_ok=True)
    for raw in manifest["files"]:
        name = str(raw["path"])
        source = (staging / Path(*PurePosixPath(name).parts)).resolve()
        target = settings.db_path.resolve() if name == DATABASE_ARCHIVE_PATH else (data_root / Path(*PurePosixPath(name).parts)).resolve()
        if not target.is_relative_to(data_root):
            raise ValueError(f"恢复路径越界：{name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == DATABASE_ARCHIVE_PATH:
            source_conn = sqlite3.connect(source)
            target_conn = db.get_conn()
            try:
                source_conn.backup(target_conn)
            finally:
                target_conn.close()
                source_conn.close()
            _checkpoint_database()
            continue
        temporary = target.with_name(f".{target.name}.restore.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def restore_backup(name: str) -> dict[str, object]:
    """Restore a verified backup after creating a rollback snapshot."""
    with _BACKUP_LOCK:
        source = _resolve_backup(name)
        inspect_backup(source)
        rollback = create_complete_backup(kind="safety", reason=f"恢复 {name} 前自动创建")
        try:
            with tempfile.TemporaryDirectory(prefix="mio-restore-") as temp_dir:
                staging = Path(temp_dir)
                manifest = _extract_verified(source, staging)
                _replace_from_staging(staging, manifest)
        except Exception as restore_error:
            try:
                with tempfile.TemporaryDirectory(prefix="mio-rollback-") as temp_dir:
                    staging = Path(temp_dir)
                    rollback_manifest = _extract_verified(rollback, staging)
                    _replace_from_staging(staging, rollback_manifest)
            except Exception as rollback_error:
                logger.exception("备份恢复失败后，自动回滚也失败")
                raise RestoreStateUncertainError(
                    "恢复失败，自动回滚也失败；"
                    f"恢复错误：{restore_error}；回滚错误：{rollback_error}"
                ) from restore_error
            raise ValueError(f"恢复失败，已自动回滚：{restore_error}") from restore_error
        return {
            "restored": True,
            "backup": name,
            "rollback_backup": rollback.name,
            "restart_required": True,
        }


def _cleanup_old_backups(directory: Path, keep_count: int) -> None:
    backups = sorted(
        (path for path in directory.glob("自动备份-*.zip") if path.is_file()),
        key=lambda item: item.stat().st_mtime,
    )
    for old in backups[: max(0, len(backups) - keep_count)]:
        old.unlink(missing_ok=True)


def run_backup_once() -> Path | None:
    if not settings.backup_enabled:
        return None
    directory = _backup_dir()
    date_prefix = f"自动备份-{datetime.now().strftime('%Y%m%d')}"
    if any(directory.glob(f"{date_prefix}-*.zip")):
        return None
    target = create_complete_backup(kind="auto", reason="每日自动备份")
    _cleanup_old_backups(directory, settings.backup_keep_count)
    return target


async def backup_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(run_backup_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自动备份检查失败")
        await asyncio.sleep(max(300, settings.backup_check_seconds))
