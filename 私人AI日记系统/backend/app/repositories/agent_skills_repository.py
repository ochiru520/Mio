"""Persistence for agent skills. Schema is applied by db.init_db."""
from __future__ import annotations
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from .. import db
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_skill_versions (
    skill_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(skill_id, revision)
);
CREATE TABLE IF NOT EXISTS agent_skill_drafts (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    activated_skill_id TEXT NOT NULL DEFAULT ''
);
"""


def seed_defaults(conn: sqlite3.Connection, defaults) -> None:
    import hashlib
    for (identifier, name, description, content) in defaults:
        stamp = db.now_iso()
        cursor = conn.execute('INSERT OR IGNORE INTO agent_skills VALUES(?,?,?,?,?,?,?,?)', ('skill_' + identifier, name, description, 1, 1, '', '', stamp))
        if cursor.rowcount:
            conn.execute('INSERT INTO agent_skill_versions VALUES(?,?,?,?,?)', ('skill_' + identifier, 1, content, hashlib.sha256(content.encode()).hexdigest(), stamp))


def listing():
    with closing(db.get_conn()) as conn:
        return conn.execute("""SELECT s.*,v.content_hash FROM agent_skills s JOIN agent_skill_versions v
            ON v.skill_id=s.id AND v.revision=s.revision ORDER BY s.name,s.id""").fetchall()


def get(skill_id: str):
    with closing(db.get_conn()) as conn:
        return conn.execute('SELECT * FROM agent_skills WHERE id=?', (skill_id,)).fetchone()


def version(skill_id: str, revision: int):
    with closing(db.get_conn()) as conn:
        return conn.execute('SELECT * FROM agent_skill_versions WHERE skill_id=? AND revision=?', (skill_id, revision)).fetchone()


def save(identifier: str, name: str, description: str, content: str, expected_revision: int, enabled: bool, source_path: str, source_hash: str) -> None:
    import hashlib
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT * FROM agent_skills WHERE id=?', (identifier,)).fetchone()
        if (current['revision'] if current else 0) != expected_revision:
            raise ValueError('技能已被其他编辑更新，请先刷新再保存；你的草稿未覆盖新版本。')
        revision = expected_revision + 1
        stamp = db.now_iso()
        conn.execute("""INSERT INTO agent_skills VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,description=excluded.description,enabled=excluded.enabled,revision=excluded.revision,
            source_path=excluded.source_path,source_hash=excluded.source_hash,updated_at=excluded.updated_at""", (identifier, name, description, bool(enabled), revision, source_path, source_hash, stamp))
        conn.execute('INSERT INTO agent_skill_versions VALUES(?,?,?,?,?)', (identifier, revision, content, hashlib.sha256(content.encode()).hexdigest(), stamp))


def set_enabled(skill_id: str, enabled: bool, expected_revision: int) -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        stamp = db.now_iso()
        cursor = conn.execute('UPDATE agent_skills SET enabled=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?', (bool(enabled), stamp, skill_id, expected_revision))
        if not cursor.rowcount:
            raise ValueError('技能版本已变化，请刷新后再设置。')
        conn.execute("""INSERT INTO agent_skill_versions SELECT skill_id,revision+1,content,content_hash,?
            FROM agent_skill_versions WHERE skill_id=? AND revision=?""", (stamp, skill_id, expected_revision))


def save_draft(identifier: str, skill_id: str, expected_revision: int, name: str, description: str, content: str) -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT INTO agent_skill_drafts VALUES(?,?,?,?,?,?,?,?,?)', (identifier, skill_id, expected_revision, name, description, content, 'draft', db.now_iso(), ''))


def draft(draft_id: str):
    with closing(db.get_conn()) as conn:
        return conn.execute('SELECT * FROM agent_skill_drafts WHERE id=?', (draft_id,)).fetchone()


def activate_draft(draft_id: str) -> str:
    import hashlib
    import uuid
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM agent_skill_drafts WHERE id=?', (draft_id,)).fetchone()
        if not row:
            raise ValueError('技能草稿不存在。')
        if row['status'] == 'activated':
            return row['activated_skill_id']
        identifier = row['skill_id'] or 'skill_' + uuid.uuid4().hex[:24]
        current = conn.execute('SELECT * FROM agent_skills WHERE id=?', (identifier,)).fetchone()
        if (current['revision'] if current else 0) != row['expected_revision']:
            raise ValueError('技能已被其他编辑更新，请重新保存草稿；原版本未覆盖。')
        revision = row['expected_revision'] + 1
        stamp = db.now_iso()
        conn.execute("""INSERT INTO agent_skills VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,description=excluded.description,enabled=excluded.enabled,revision=excluded.revision,
            source_path=excluded.source_path,source_hash=excluded.source_hash,updated_at=excluded.updated_at""", (identifier, row['name'], row['description'], 1, revision, '', '', stamp))
        conn.execute('INSERT INTO agent_skill_versions VALUES(?,?,?,?,?)', (identifier, revision, row['content'], hashlib.sha256(row['content'].encode()).hexdigest(), stamp))
        conn.execute("UPDATE agent_skill_drafts SET status='activated',activated_skill_id=? WHERE id=?", (identifier, draft_id))
    return identifier
