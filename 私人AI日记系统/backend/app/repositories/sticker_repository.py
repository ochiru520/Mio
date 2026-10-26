"""Domain persistence; connections and transaction boundaries stay here."""
from __future__ import annotations

from contextlib import closing, contextmanager
from .. import db

def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS stickers(
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            context TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
            favorite INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL,
            size INTEGER NOT NULL, modified REAL NOT NULL, created_at TEXT NOT NULL)''')

def list_stickers(limit):
    with closing(db.get_conn()) as conn:
        rows = conn.execute('SELECT * FROM stickers ORDER BY favorite DESC,name COLLATE NOCASE,id LIMIT ?',
                            (max(1, min(int(limit), 500)),)).fetchall()
    return rows

def get(sticker_id):
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM stickers WHERE id=?', (sticker_id,)).fetchone()
    return row

def upsert_scanned(records):
    found = 0
    with closing(db.get_conn()) as conn, conn:
        for record in records:
            conn.execute('INSERT INTO stickers(id,path,name,context,tags,favorite,sha256,size,modified,created_at)\n            VALUES(?,?,?,?,?,0,?,?,?,?) ON CONFLICT(path) DO UPDATE SET\n            id=excluded.id,name=excluded.name,sha256=excluded.sha256,size=excluded.size,modified=excluded.modified', record)
            found += 1
    return found

def set_favorite(sticker_id, favorite):
    with closing(db.get_conn()) as conn, conn:
        if conn.execute('SELECT 1 FROM stickers WHERE id=?', (sticker_id,)).fetchone() is None:
            return None
        conn.execute('UPDATE stickers SET favorite=? WHERE id=?', (favorite, sticker_id))
        return conn.execute('SELECT * FROM stickers WHERE id=?', (sticker_id,)).fetchone()
