"""Persistence for Agent summaries, compression requests and evidence indexes."""
from collections.abc import Iterator
from contextlib import closing
import sqlite3

from .. import db


def initialize() -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS agent_context_compression_requests (
            conversation_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, requested_at TEXT NOT NULL)""")
        conn.execute('''CREATE TABLE IF NOT EXISTS agent_context_cache (
            task_id TEXT PRIMARY KEY REFERENCES agent_tasks(id) ON DELETE CASCADE, summary TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL DEFAULT '', through_step INTEGER NOT NULL DEFAULT 0,
            compression_count INTEGER NOT NULL DEFAULT 0, failed_hash TEXT NOT NULL DEFAULT '',
            usage_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
            last_compressed_at TEXT NOT NULL DEFAULT '', evidence_hash TEXT NOT NULL DEFAULT '')''')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(agent_context_cache)')}
        if 'last_compressed_at' not in columns:
            conn.execute("ALTER TABLE agent_context_cache ADD COLUMN last_compressed_at TEXT NOT NULL DEFAULT ''")
        if 'evidence_hash' not in columns:
            conn.execute("ALTER TABLE agent_context_cache ADD COLUMN evidence_hash TEXT NOT NULL DEFAULT ''")


def queue_compression(conversation_id: str, request_id: str, requested_at: str) -> str:
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        pending = conn.execute('SELECT request_id FROM agent_context_compression_requests WHERE conversation_id=?',
                               (conversation_id,)).fetchone()
        if pending:
            return pending['request_id']
        conn.execute('INSERT INTO agent_context_compression_requests VALUES(?,?,?)',
                     (conversation_id, request_id, requested_at))
    return request_id


def pending_compression(conversation_id: str) -> str | None:
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT request_id FROM agent_context_compression_requests WHERE conversation_id=?',
                           (conversation_id,)).fetchone()
    return row['request_id'] if row else None


def clear_compression(conversation_id: str, request_id: str) -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.execute('DELETE FROM agent_context_compression_requests WHERE conversation_id=? AND request_id=?',
                     (conversation_id, request_id))


def get_cache(task_id: str) -> dict | None:
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM agent_context_cache WHERE task_id=?', (task_id,)).fetchone()
    return dict(row) if row else None


def latest_usage(conversation_id: str) -> str | None:
    with closing(db.get_conn()) as conn:
        row = conn.execute('''SELECT c.usage_json FROM agent_context_cache c
            JOIN agent_tasks t ON t.id=c.task_id WHERE t.conversation_id=?
            ORDER BY c.updated_at DESC LIMIT 1''', (conversation_id,)).fetchone()
    return row[0] if row else None


def evidence_through(task_id: str, cutoff: int) -> Iterator[sqlite3.Row]:
    with closing(db.get_conn()) as conn:
        yield from conn.execute('''SELECT s.id,s.tool_name,s.status,s.error,s.result_json FROM agent_run_steps s
            JOIN agent_task_runs r ON r.run_id=s.run_id WHERE r.task_id=?
            AND s.step_kind='tool_call' AND s.id<=? ORDER BY s.id''', (task_id, cutoff))


def incremental_evidence(task_id: str, after_step: int, cutoff: int) -> list[sqlite3.Row]:
    with closing(db.get_conn()) as conn:
        return conn.execute('''SELECT s.id,s.tool_name,s.status,s.error,s.result_json,s.action_id
            FROM agent_run_steps s JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.step_kind='tool_call' AND s.id>? AND s.id<=?
            ORDER BY s.id LIMIT 60''', (task_id, after_step, cutoff)).fetchall()


def action_ledger(task_id: str, write_names: set[str]) -> tuple[list[sqlite3.Row], int]:
    placeholders = ','.join('?' for _ in write_names) or 'NULL'
    params = (task_id, *sorted(write_names))
    with closing(db.get_conn()) as conn:
        rows = conn.execute('''SELECT s.id,s.tool_name,s.status,s.action_id,s.arguments_json
            FROM agent_run_steps s JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.step_kind='tool_call' AND (s.action_id>0 OR s.tool_name IN (
            ''' + placeholders + ')) ORDER BY s.id DESC LIMIT 30', params).fetchall()
        count = conn.execute('''SELECT COUNT(*) FROM agent_run_steps s JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.step_kind='tool_call' AND (s.action_id>0 OR s.tool_name IN (
            ''' + placeholders + '))', params).fetchone()[0]
    return rows, count


def save_cache(task_id: str, cache: dict, usage_json: str, updated_at: str, *, source_revision=None) -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        task = conn.execute('SELECT status FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
        if task is None or task['status'] in {'paused', 'cancelled'}:
            return
        if source_revision is not None:
            revision = conn.execute('SELECT COALESCE(MAX(revision),0) FROM conversation_rewinds WHERE conversation_id=?',
                                   (source_revision['conversation_id'],)).fetchone()[0]
            if revision != source_revision['revision']:
                return
        conn.execute('''INSERT INTO agent_context_cache (
            task_id,summary,source_hash,through_step,compression_count,failed_hash,
            usage_json,updated_at,last_compressed_at,evidence_hash) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET summary=excluded.summary,source_hash=excluded.source_hash,
            through_step=excluded.through_step,compression_count=excluded.compression_count,
            failed_hash=excluded.failed_hash,usage_json=excluded.usage_json,updated_at=excluded.updated_at,
            last_compressed_at=excluded.last_compressed_at,evidence_hash=excluded.evidence_hash''',
            (task_id, cache['summary'], cache['source_hash'], cache['through_step'],
             cache['compression_count'], cache['failed_hash'], usage_json, updated_at,
             cache['last_compressed_at'], cache['evidence_hash']))
