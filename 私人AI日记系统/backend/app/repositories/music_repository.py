"""Domain persistence; connections and transaction boundaries stay here."""
from __future__ import annotations

from contextlib import closing, contextmanager
from .. import db

def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS music_tracks(
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            size INTEGER NOT NULL, modified REAL NOT NULL, created_at TEXT NOT NULL)""")

def list_tracks(limit):
    with closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM music_tracks ORDER BY name COLLATE NOCASE,id LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
    return rows

def get(track_id):
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM music_tracks WHERE id=?', (track_id,)).fetchone()
    return row

def upsert_scanned(records):
    found = 0
    with closing(db.get_conn()) as conn, conn:
        for record in records:
            conn.execute('INSERT INTO music_tracks(id,path,name,size,modified,created_at) VALUES(?,?,?,?,?,?)\n            ON CONFLICT(path) DO UPDATE SET id=excluded.id,name=excluded.name,size=excluded.size,modified=excluded.modified', record)
            found += 1
    return found
