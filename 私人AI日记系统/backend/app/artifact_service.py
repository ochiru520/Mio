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
            CREATE TABLE IF NOT EXISTS artifact_reviews(
                artifact_id TEXT PRIMARY KEY,quality_status TEXT NOT NULL DEFAULT 'not_reviewed',
                quality_note TEXT NOT NULL DEFAULT '',reviewed_at TEXT NOT NULL DEFAULT '',
                integrity_status TEXT NOT NULL DEFAULT 'unknown',verified_at TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS artifact_metadata(
                artifact_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS artifact_favorites(artifact_id TEXT PRIMARY KEY,favorite INTEGER NOT NULL DEFAULT 0);
        """)


def _public(row) -> dict:
    item = dict(row)
    if item.get("metadata_json"):
        import json
        try:
            item["metadata"] = json.loads(item["metadata_json"])
        except (TypeError, ValueError):
            item["metadata"] = {}
        item.pop("metadata_json", None)
    else:
        item.setdefault("metadata", {})
    item["url"] = f"/api/artifacts/{item['id']}/download"
    item["preview_url"] = f"/api/artifacts/{item['id']}/preview"
    item["verification"] = "content_hash"
    item.setdefault("quality_status", "not_reviewed")
    item.setdefault("quality_note", "")
    item.setdefault("reviewed_at", "")
    item.setdefault("integrity_status", "unknown")
    item.setdefault("verified_at", "")
    if not item['task_id'] and item['job_id']:
        from .creation_provenance import owner
        provenance = owner(item['job_id'])
        item['task_id'] = provenance['task_id']
        item['provenance_status'] = provenance['provenance_status']
    else:
        item['provenance_status'] = 'recorded' if item['task_id'] else 'unknown'
    return item


def _rows_with_reviews(conn, rows):
    has_reviews = conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifact_reviews'").fetchone()
    has_metadata = conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifact_metadata'").fetchone()
    result = []
    for row in rows:
        item = dict(row)
        if has_reviews:
            review = conn.execute('SELECT * FROM artifact_reviews WHERE artifact_id=?', (item['id'],)).fetchone()
            if review:
                item.update({key: review[key] for key in review.keys() if key != 'artifact_id'})
        if has_metadata:
            metadata = conn.execute('SELECT metadata_json FROM artifact_metadata WHERE artifact_id=?', (item['id'],)).fetchone()
            if metadata:
                item['metadata_json'] = metadata['metadata_json']
        result.append(item)
    return result


@maintenance_service.mutation_scope()
def import_file(source: Path, *, source_root: Path, producer: str, expected_sha256: str,
                task_id: str = "", job_id: str = "", conversation_id: str = "", name: str = "", mime_type: str = "", metadata: dict | None = None) -> dict:
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
                if metadata:
                    import json
                    conn.execute("INSERT INTO artifact_metadata(artifact_id,metadata_json) VALUES(?,?) ON CONFLICT(artifact_id) DO UPDATE SET metadata_json=excluded.metadata_json", (existing['id'], json.dumps(metadata, ensure_ascii=False, sort_keys=True)))
                conn.execute("INSERT INTO artifact_reviews(artifact_id,integrity_status,verified_at) VALUES(?,'verified',?) ON CONFLICT(artifact_id) DO UPDATE SET integrity_status='verified',verified_at=excluded.verified_at", (existing['id'], db.now_iso()))
                return _public(_rows_with_reviews(conn, [existing])[0])
            version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM artifacts WHERE producer=? AND task_id=? AND job_id=? AND name=?',
                                   (producer, task_id, job_id, filename)).fetchone()[0]
            identifier = 'artifact_' + uuid.uuid4().hex
            conn.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (identifier, actual, filename, mime_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                          size, producer, task_id, job_id, conversation_id, str(source), version, db.now_iso()))
            conn.execute("INSERT INTO artifact_reviews(artifact_id,integrity_status,verified_at) VALUES(?,'verified',?)", (identifier, db.now_iso()))
            if metadata:
                import json
                conn.execute("INSERT INTO artifact_metadata(artifact_id,metadata_json) VALUES(?,?)", (identifier, json.dumps(metadata, ensure_ascii=False, sort_keys=True)))
            return _public(_rows_with_reviews(conn, [conn.execute('SELECT * FROM artifacts WHERE id=?', (identifier,)).fetchone()])[0])
    finally:
        temporary.unlink(missing_ok=True)


def get(artifact_id: str) -> dict | None:
    if not db.settings.db_path.is_file():
        return None
    with closing(db.get_conn()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone() is None:
            return None
        row = conn.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
        row = _rows_with_reviews(conn, [row])[0] if row else None
    return _public(row) if row else None


def list_artifacts(*, task_id: str = "", job_id: str = "", project_id: str = "", limit: int = 50, offset: int = 0,
                   q: str = '', kind: str = '', favorite: bool = False, conversation_id: str = '', artifact_id: str = '') -> list[dict]:
    if not db.settings.db_path.is_file():
        return []
    with closing(db.get_conn()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone() is None:
            return []
        where, args = "(?='' OR a.job_id=?)", [job_id, job_id]
        if artifact_id:
            where += ' AND a.id=?'
            args.append(artifact_id)
        if conversation_id:
            ownership = ['a.conversation_id=?']
            args.append(conversation_id)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'agent_tasks' in tables:
                ownership.append("(a.conversation_id='' AND EXISTS(SELECT 1 FROM agent_tasks t WHERE t.id=a.task_id AND t.conversation_id=?))")
                args.append(conversation_id)
            if 'creation_jobs' in tables:
                ownership.append("(a.conversation_id='' AND EXISTS(SELECT 1 FROM creation_jobs j WHERE j.id=a.job_id AND j.conversation_id=?))")
                args.append(conversation_id)
            where += ' AND (' + ' OR '.join(ownership) + ')'
        if q:
            where += " AND instr(lower(a.name),lower(?))>0"; args.append(q)
        if kind in {'image', 'video', 'audio'}:
            where += ' AND a.mime_type LIKE ?'; args.append(kind + '/%')
        elif kind == 'document':
            where += " AND a.mime_type NOT LIKE 'image/%' AND a.mime_type NOT LIKE 'video/%' AND a.mime_type NOT LIKE 'audio/%'"
        has_favorites = conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifact_favorites'").fetchone()
        if favorite:
            if not has_favorites: return []
            where += ' AND EXISTS(SELECT 1 FROM artifact_favorites f WHERE f.artifact_id=a.id AND f.favorite=1)'
        if task_id:
            from . import creation_provenance, agent_task_service
            task = agent_task_service.get(task_id)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            jobs = conn.execute('SELECT id FROM creation_jobs WHERE conversation_id=?', (task['conversation_id'],)).fetchall() if task and 'creation_jobs' in tables else []
            owned_jobs = [row['id'] for row in jobs if creation_provenance.owner(row['id'])['task_id'] == task_id]
            where += ' AND (a.task_id=?'
            args.append(task_id)
            if owned_jobs:
                where += " OR (a.task_id='' AND a.job_id IN (" + ','.join('?' for _ in owned_jobs) + '))'
                args.extend(owned_jobs)
            where += ')'
        if project_id:
            from .workspace_service import require_project
            require_project(project_id)
            where += """ AND (EXISTS(SELECT 1 FROM workspace_conversations c WHERE c.project_id=? AND c.conversation_id=a.conversation_id)
                OR (a.conversation_id='' AND EXISTS(SELECT 1 FROM agent_tasks t JOIN workspace_conversations c ON c.conversation_id=t.conversation_id WHERE t.id=a.task_id AND c.project_id=?))
                OR (a.conversation_id='' AND EXISTS(SELECT 1 FROM creation_jobs j JOIN workspace_conversations c ON c.conversation_id=j.conversation_id WHERE j.id=a.job_id AND c.project_id=?)))"""
            args.extend([project_id] * 3)
        rows = conn.execute(f"SELECT a.* FROM artifacts a WHERE {where} ORDER BY a.created_at DESC,a.id LIMIT ? OFFSET ?",
                            (*args, max(1,min(limit,101)), max(0,offset))).fetchall()
        rows = _rows_with_reviews(conn, rows)
        result = [_public(row) for row in rows]
        for item in result:
            saved = conn.execute('SELECT favorite FROM artifact_favorites WHERE artifact_id=?', (item['id'],)).fetchone() if has_favorites else None
            item['favorite'] = bool(saved and saved[0])
    return result


def set_favorite(artifact_id, favorite):
    initialize()
    if get(artifact_id) is None: raise ValueError('找不到成果。')
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR REPLACE INTO artifact_favorites VALUES(?,?)', (artifact_id, int(favorite)))
    return {'id': artifact_id, 'favorite': bool(favorite)}


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


def preview(artifact_id: str) -> dict:
    """Preview verified archived bytes, including files with no on-disk suffix."""
    from .artifact_preview_service import OFFICE, office_preview
    path, item = file(artifact_id)
    suffix = Path(item['name']).suffix.lower()
    mime = content_mime(item)
    if suffix in OFFICE:
        return {**office_preview(path, item['name']), 'url': item['url']}
    from .agent_file_service import TEXT
    if mime.startswith(('text/', 'application/json')) or suffix in TEXT | {'.svg'}:
        if path.stat().st_size > 2 * 1024 * 1024:
            return {'kind': 'download', 'url': item['url'], 'name': item['name'], 'supported': False,
                    'notice': '文本文件超过 2MB，请下载查看完整内容。'}
        content = path.read_text(encoding='utf-8-sig', errors='replace')
        return {'kind': 'text', 'content': content[:60000], 'name': item['name'], 'truncated': len(content) > 60000}
    if mime.startswith(('image/', 'video/', 'audio/')) or mime == 'application/pdf':
        return {'kind': mime.split('/')[0] if mime != 'application/pdf' else 'pdf',
                'url': '/api/artifacts/' + artifact_id + '/content', 'name': item['name']}
    return {'kind': 'download', 'url': item['url'], 'name': item['name'], 'supported': False,
            'notice': '暂不支持此格式的内容预览，请下载后打开。'}


def content_mime(item: dict) -> str:
    """Use a recorded MIME when specific; recover old generic MIME from the filename."""
    mime = item['mime_type']
    if mime == 'application/octet-stream':
        return mimetypes.guess_type(item['name'])[0] or mime
    return mime


@maintenance_service.mutation_scope()
def verify(artifact_id: str) -> dict:
    if get(artifact_id) is None:
        raise ValueError('找不到成果。')
    initialize()
    try:
        file(artifact_id)
        status = 'verified'
    except (ValueError, OSError):
        status = 'failed'
    with closing(db.get_conn()) as conn, conn:
        conn.execute("INSERT INTO artifact_reviews(artifact_id,integrity_status,verified_at) VALUES(?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET integrity_status=excluded.integrity_status,verified_at=excluded.verified_at", (artifact_id, status, db.now_iso()))
    return get(artifact_id)


@maintenance_service.mutation_scope()
def review(artifact_id: str, status: str, note: str = '') -> dict:
    if status not in {'not_reviewed', 'user_approved', 'user_rejected'}:
        raise ValueError('无效质量评价。')
    if get(artifact_id) is None:
        raise ValueError('找不到成果。')
    initialize()
    # A user quality review is independent of the stored file's integrity.
    with closing(db.get_conn()) as conn, conn:
        conn.execute("""INSERT INTO artifact_reviews(artifact_id,quality_status,quality_note,reviewed_at)
            VALUES(?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET quality_status=excluded.quality_status,
            quality_note=excluded.quality_note,reviewed_at=excluded.reviewed_at""",
            (artifact_id, status, str(note)[:2000], db.now_iso()))
    return get(artifact_id)
