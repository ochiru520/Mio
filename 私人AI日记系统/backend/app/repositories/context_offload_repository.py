"""Durable input omissions. Originals are retained locally, never deleted."""
from contextlib import closing
import hashlib
import json
from .. import db


def _initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS context_input_offloads (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_hash TEXT NOT NULL,
        kind TEXT NOT NULL, original_json TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(scope,source_hash,kind))''')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _scope_allowed(conn, scope):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='deleted_conversations'").fetchone():
        if conn.execute('SELECT 1 FROM deleted_conversations WHERE id=?', (scope,)).fetchone():
            return False
    if str(scope).startswith('task_'):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_tasks'").fetchone():
            return False
        task = conn.execute('SELECT conversation_id FROM agent_tasks WHERE id=?', (scope,)).fetchone()
        if task is None:
            return False
        if conn.execute('SELECT 1 FROM deleted_conversations WHERE id=?', (task[0],)).fetchone():
            return False
    return True


def find(scope, value, kind):
    if not db.settings.db_path.is_file():
        return None
    with closing(db.get_conn()) as conn, conn:
        _initialize(conn)
        if not _scope_allowed(conn, scope):
            return None
        row = conn.execute('SELECT id FROM context_input_offloads WHERE scope=? AND source_hash=? AND kind=?',
                           (scope, digest(value), kind)).fetchone()
        return row[0] if row else None


def save(scope, value, kind):
    if not db.settings.db_path.is_file():
        return None
    identifier = 'input_' + hashlib.sha256((scope + ':' + kind + ':' + digest(value)).encode()).hexdigest()[:32]
    with closing(db.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        _initialize(conn)
        if not _scope_allowed(conn, scope):
            return None
        conn.execute('INSERT OR IGNORE INTO context_input_offloads VALUES(?,?,?,?,?,?)',
                     (identifier, scope, digest(value), kind, json.dumps(value, ensure_ascii=False), db.now_iso()))
    return identifier


def read(identifier, scope):
    with closing(db.get_conn()) as conn, conn:
        _initialize(conn)
        if not _scope_allowed(conn, scope):
            raise ValueError('输入引用所属会话或任务已删除。')
        row = conn.execute('SELECT kind,original_json,created_at FROM context_input_offloads WHERE id=? AND scope=?',
                           (identifier, scope)).fetchone()
        if not row:
            raise ValueError('输入引用不存在或不属于当前会话。')
        return {'id': identifier, 'kind': row[0], 'original': json.loads(row[1]), 'created_at': row[2]}


def delete_scope(scope):
    """Called by the conversation/task deletion owner, under its authorization."""
    with closing(db.get_conn()) as conn, conn:
        _initialize(conn)
        conn.execute('DELETE FROM context_input_offloads WHERE scope=?', (scope,))


def list_scope(scope):
    """Public metadata only. Originals require a separate scoped read."""
    if not db.settings.db_path.is_file():
        return []
    with closing(db.get_conn()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='context_input_offloads'").fetchone():
            return []
        if not _scope_allowed(conn, scope):
            return []
        return [dict(row) for row in conn.execute('SELECT id,kind,created_at FROM context_input_offloads WHERE scope=? ORDER BY created_at,id', (scope,))]
