"""Domain persistence; connections and transaction boundaries stay here."""
from __future__ import annotations

from contextlib import closing, contextmanager
from .. import db

def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS moments(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, content TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'text', source_type TEXT NOT NULL DEFAULT 'user',
            source_id TEXT NOT NULL DEFAULT '', source_label TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_moments_project ON moments(project_id,archived,created_at);
            CREATE TABLE IF NOT EXISTS moment_comments(
            id TEXT PRIMARY KEY, moment_id TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL);''')

def listing(project_id, include_archived, q, limit, offset):
    where = 'project_id=?'; args: list[object] = [project_id]
    if not include_archived: where += ' AND archived=0'
    if q.strip(): where += ' AND content LIKE ?'; args.append('%' + q.strip()[:200] + '%')
    with closing(db.get_conn()) as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM moments WHERE {where}', args).fetchone()[0]
        rows = conn.execute(f'SELECT * FROM moments WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (*args, max(1,min(limit,200)), max(0,offset))).fetchall()
        return total, [(row, conn.execute('SELECT * FROM moment_comments WHERE moment_id=? ORDER BY created_at,id', (row['id'],)).fetchall()) for row in rows]

def create(identifier, project_id, content, media_type, source_type, source_id, source_label, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT INTO moments VALUES(?,?,?,?,?,?,?,?,?,?)', (identifier, project_id, content, media_type, source_type, source_id, source_label, 0, now, now))
        return conn.execute('SELECT * FROM moments WHERE id=?', (identifier,)).fetchone()

def comment(identifier, moment_id, content, now):
    with closing(db.get_conn()) as conn, conn:
        if conn.execute('SELECT 1 FROM moments WHERE id=?', (moment_id,)).fetchone() is None:
            return None
        conn.execute('INSERT INTO moment_comments VALUES(?,?,?,?)', (identifier, moment_id, content, now))
        return conn.execute('SELECT * FROM moment_comments WHERE id=?', (identifier,)).fetchone()

def archive(moment_id, archived, now):
    with closing(db.get_conn()) as conn, conn:
        if conn.execute('SELECT 1 FROM moments WHERE id=?', (moment_id,)).fetchone() is None:
            return None
        conn.execute('UPDATE moments SET archived=?,updated_at=? WHERE id=?', (archived, now, moment_id))
        row = conn.execute('SELECT * FROM moments WHERE id=?', (moment_id,)).fetchone()
        comments = conn.execute('SELECT * FROM moment_comments WHERE moment_id=? ORDER BY created_at,id', (moment_id,)).fetchall()
        return row, comments
