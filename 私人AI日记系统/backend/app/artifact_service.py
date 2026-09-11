"""Content-addressed outputs with producer identity, versions and verified downloads."""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
import re
import uuid
from contextlib import closing

from . import db, maintenance_service


def root() -> Path:
    directory = db.settings.db_path.resolve().parent / "成果"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != directory:
        raise ValueError("成果目录不能是联接或符号链接。")
    return directory


def initialize() -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, name TEXT NOT NULL, mime_type TEXT NOT NULL,
                size INTEGER NOT NULL, producer TEXT NOT NULL, task_id TEXT NOT NULL,
                job_id TEXT NOT NULL, conversation_id TEXT NOT NULL, source_path TEXT NOT NULL,
                version INTEGER NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(producer,task_id,job_id,name,sha256)
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id,created_at);
        """)


def _public(row) -> dict:
    item = dict(row)
    item["url"] = f"/api/artifacts/{item['id']}/download"
    item["verification"] = "content_hash"
    item["quality_status"] = "not_reviewed"
    return item


@maintenance_service.mutation_scope()
def import_file(source: Path, *, source_root: Path, producer: str, expected_sha256: str,
                task_id: str = "", job_id: str = "", conversation_id: str = "", name: str = "", mime_type: str = "") -> dict:
    source = source.resolve(strict=True)
    if not source.is_file() or not source.is_relative_to(source_root.resolve(strict=True)):
        raise ValueError("来源文件超出生产工具的授权目录。")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("成果必须提供可核验的内容哈希。")
    filename = name or source.name
    if Path(filename).name != filename or ':' in filename or filename.startswith('.'):
        raise ValueError("无效成果文件名。")
    initialize()
    temporary = root() / f"{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open('rb') as original, temporary.open('xb') as target:
            for chunk in iter(lambda: original.read(1024*1024), b''):
                digest.update(chunk); size += len(chunk); target.write(chunk)
            target.flush(); os.fsync(target.fileno())
        actual = digest.hexdigest()
        if actual != expected_sha256.lower():
            raise ValueError("来源文件已经改变，成果未入库。")
        blob = root() / actual
        # Replacement is safe: every writer has verified exactly these content bytes.
        temporary.replace(blob)
        with closing(db.get_conn()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT * FROM artifacts WHERE producer=? AND task_id=? AND job_id=? AND name=? AND sha256=?',
                                    (producer, task_id, job_id, filename, actual)).fetchone()
            if existing:
                return _public(existing)
            version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM artifacts WHERE producer=? AND task_id=? AND job_id=? AND name=?',
                                   (producer, task_id, job_id, filename)).fetchone()[0]
            identifier = 'artifact_' + uuid.uuid4().hex
            conn.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (identifier, actual, filename, mime_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                          size, producer, task_id, job_id, conversation_id, str(source), version, db.now_iso()))
            return _public(conn.execute('SELECT * FROM artifacts WHERE id=?', (identifier,)).fetchone())
    finally:
        temporary.unlink(missing_ok=True)


def get(artifact_id: str) -> dict | None:
    if not db.settings.db_path.is_file():
        return None
    with closing(db.get_conn()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone() is None:
            return None
        row = conn.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
    return _public(row) if row else None


def list_artifacts(*, task_id: str = "", job_id: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
    if not db.settings.db_path.is_file():
        return []
    with closing(db.get_conn()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone() is None:
            return []
        rows = conn.execute("SELECT * FROM artifacts WHERE (?='' OR task_id=?) AND (?='' OR job_id=?) ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                            (task_id, task_id, job_id, job_id, max(1,min(limit,101)), max(0,offset))).fetchall()
    return [_public(row) for row in rows]


def file(artifact_id: str) -> tuple[Path, dict]:
    item = get(artifact_id)
    if item is None or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
        raise ValueError("找不到成果。")
    path = (root() / item['sha256']).resolve(strict=True)
    if not path.is_relative_to(root()) or not path.is_file():
        raise ValueError("成果文件路径无效。")
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):digest.update(chunk)
    if digest.hexdigest() != item['sha256']:
        raise ValueError("成果文件校验失败。")
    return path, item
