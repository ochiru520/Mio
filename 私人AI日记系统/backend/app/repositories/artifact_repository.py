"""Domain persistence; connections and transaction boundaries stay here."""
from __future__ import annotations

from contextlib import closing, contextmanager
from .. import db

import uuid

def initialize():
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

def rows_with_reviews(conn, rows):
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


def upsert_file(producer, task_id, job_id, filename, actual, mime_type, size, conversation_id, source, metadata_json, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT * FROM artifacts WHERE producer=? AND task_id=? AND job_id=? AND name=? AND sha256=?',
                                (producer, task_id, job_id, filename, actual)).fetchone()
        if existing:
            if metadata_json:
                conn.execute("INSERT INTO artifact_metadata(artifact_id,metadata_json) VALUES(?,?) ON CONFLICT(artifact_id) DO UPDATE SET metadata_json=excluded.metadata_json", (existing['id'], metadata_json))
            conn.execute("INSERT INTO artifact_reviews(artifact_id,integrity_status,verified_at) VALUES(?,'verified',?) ON CONFLICT(artifact_id) DO UPDATE SET integrity_status='verified',verified_at=excluded.verified_at", (existing['id'], now))
            return rows_with_reviews(conn, [existing])[0]
        version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM artifacts WHERE producer=? AND task_id=? AND job_id=? AND name=?',
                               (producer, task_id, job_id, filename)).fetchone()[0]
        identifier = 'artifact_' + uuid.uuid4().hex
        conn.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (identifier, actual, filename, mime_type,
                      size, producer, task_id, job_id, conversation_id, str(source), version, now))
        conn.execute("INSERT INTO artifact_reviews(artifact_id,integrity_status,verified_at) VALUES(?,'verified',?)", (identifier, now))
        if metadata_json:
            import json
            conn.execute("INSERT INTO artifact_metadata(artifact_id,metadata_json) VALUES(?,?)", (identifier, metadata_json))
        return rows_with_reviews(conn, [conn.execute('SELECT * FROM artifacts WHERE id=?', (identifier,)).fetchone()])[0]

def get(artifact_id):
    with closing(db.get_conn()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone() is None:
            return None
        row = conn.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
        return rows_with_reviews(conn, [row])[0] if row else None

def conversation_job_ids(conversation_id):
    with closing(db.get_conn()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='creation_jobs'").fetchone() is None:
            return []
        return [row[0] for row in conn.execute('SELECT id FROM creation_jobs WHERE conversation_id=?', (conversation_id,))]

def listing(*, task_id="", job_id="", project_id="", limit=50, offset=0, q="", kind="", favorite=False, conversation_id="", artifact_id="", owned_jobs=()):
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
            where += ' AND (a.task_id=?'
            args.append(task_id)
            if owned_jobs:
                where += " OR (a.task_id='' AND a.job_id IN (" + ','.join('?' for _ in owned_jobs) + '))'
                args.extend(owned_jobs)
            where += ')'
        if project_id:
            where += """ AND (EXISTS(SELECT 1 FROM workspace_conversations c WHERE c.project_id=? AND c.conversation_id=a.conversation_id)
                OR (a.conversation_id='' AND EXISTS(SELECT 1 FROM agent_tasks t JOIN workspace_conversations c ON c.conversation_id=t.conversation_id WHERE t.id=a.task_id AND c.project_id=?))
                OR (a.conversation_id='' AND EXISTS(SELECT 1 FROM creation_jobs j JOIN workspace_conversations c ON c.conversation_id=j.conversation_id WHERE j.id=a.job_id AND c.project_id=?)))"""
            args.extend([project_id] * 3)
        rows = conn.execute(f"SELECT a.* FROM artifacts a WHERE {where} ORDER BY a.created_at DESC,a.id LIMIT ? OFFSET ?",
                            (*args, max(1,min(limit,101)), max(0,offset))).fetchall()
        rows = rows_with_reviews(conn, rows)
        result = rows
        for item in result:
            saved = conn.execute('SELECT favorite FROM artifact_favorites WHERE artifact_id=?', (item['id'],)).fetchone() if has_favorites else None
            item['favorite'] = bool(saved and saved[0])
    return result

def set_favorite(artifact_id, favorite):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR REPLACE INTO artifact_favorites VALUES(?,?)', (artifact_id, favorite))

def record_integrity(artifact_id, status, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("INSERT INTO artifact_reviews(artifact_id,integrity_status,verified_at) VALUES(?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET integrity_status=excluded.integrity_status,verified_at=excluded.verified_at", (artifact_id, status, now))

def record_quality(artifact_id, status, note, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("""INSERT INTO artifact_reviews(artifact_id,quality_status,quality_note,reviewed_at)
            VALUES(?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET quality_status=excluded.quality_status,
            quality_note=excluded.quality_note,reviewed_at=excluded.reviewed_at""",
            (artifact_id, status, note, now))
