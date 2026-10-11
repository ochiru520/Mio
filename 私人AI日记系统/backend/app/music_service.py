"""Local, user-authorized music catalog with lazy playback."""
from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path

from . import db, agent_file_service as files

AUDIO = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.webm'}


def initialize() -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS music_tracks(
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            size INTEGER NOT NULL, modified REAL NOT NULL, created_at TEXT NOT NULL)""")


def _public(row) -> dict:
    item = dict(row)
    item['url'] = f"/api/workspace/music/{item['id']}/file"
    return item


def listing(limit: int = 200) -> dict:
    initialize()
    with closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM music_tracks ORDER BY name COLLATE NOCASE,id LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
    return {'tracks': [_public(row) for row in rows]}


def scan(path: str) -> dict:
    directory = files.resolve_read(path)
    if not directory.is_dir():
        raise ValueError('音乐扫描需要已授权目录。')
    initialize(); found = 0
    with closing(db.get_conn()) as conn, conn:
        for item in directory.rglob('*'):
            if not item.is_file() or item.suffix.lower() not in AUDIO or item.name.startswith('.'):
                continue
            try:
                source = files.resolve_read(str(item)); stat = source.stat()
            except (ValueError, OSError):
                continue
            identifier = 'music_' + hashlib.sha256(f'{source}|{stat.st_size}|{stat.st_mtime_ns}'.encode()).hexdigest()[:24]
            conn.execute("""INSERT INTO music_tracks(id,path,name,size,modified,created_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET id=excluded.id,name=excluded.name,size=excluded.size,modified=excluded.modified""",
                         (identifier, str(source), source.name, stat.st_size, stat.st_mtime, db.now_iso()))
            found += 1
    return {'scanned': found, **listing()}


def file(track_id: str) -> tuple[Path, str]:
    initialize()
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM music_tracks WHERE id=?', (track_id,)).fetchone()
    if row is None:
        raise ValueError('音乐不存在。')
    source = files.resolve_read(row['path'])
    if not source.is_file() or source.stat().st_size != int(row['size']):
        raise ValueError('音乐文件已变化，请重新扫描目录。')
    return source, source.suffix.lower()
