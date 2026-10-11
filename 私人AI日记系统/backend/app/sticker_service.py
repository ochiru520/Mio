"""User-owned sticker catalog. Files stay local and are never uploaded."""
from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path

from . import db, agent_file_service as files

IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}


def initialize() -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS stickers(
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            context TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
            favorite INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL,
            size INTEGER NOT NULL, modified REAL NOT NULL, created_at TEXT NOT NULL)''')


def _public(row) -> dict:
    item = dict(row)
    item['favorite'] = bool(item.get('favorite'))
    item['tags'] = [x for x in str(item.get('tags') or '').split(',') if x]
    item['url'] = f"/api/workspace/stickers/{item['id']}/file"
    return item


def listing(context: str = '', tag: str = '', limit: int = 200) -> dict:
    initialize()
    context, tag = context.strip().lower(), tag.strip().lower()
    with closing(db.get_conn()) as conn:
        rows = conn.execute('SELECT * FROM stickers ORDER BY favorite DESC,name COLLATE NOCASE,id LIMIT ?',
                            (max(1, min(int(limit), 500)),)).fetchall()
    items = [_public(row) for row in rows]
    if context:
        items = [x for x in items if not x['context'] or context in x['context'].lower()]
    if tag:
        items = [x for x in items if tag in {value.lower() for value in x['tags']}]
    return {'stickers': items}


def scan(path: str, *, context: str = '', tags: list[str] | None = None) -> dict:
    directory = files.resolve_read(path)
    if not directory.is_dir():
        raise ValueError('贴纸扫描需要已授权目录。')
    initialize(); found = 0
    normalized_tags = ','.join(dict.fromkeys(str(x).strip()[:40] for x in (tags or []) if str(x).strip()))[:500]
    with closing(db.get_conn()) as conn, conn:
        for item in directory.rglob('*'):
            if not item.is_file() or item.suffix.lower() not in IMAGE_TYPES or item.name.startswith('.'):
                continue
            try:
                source = files.resolve_read(str(item)); data = source.read_bytes(); stat = source.stat()
            except (ValueError, OSError):
                continue
            digest = hashlib.sha256(data).hexdigest()
            identifier = 'sticker_' + digest[:24]
            conn.execute('''INSERT INTO stickers(id,path,name,context,tags,favorite,sha256,size,modified,created_at)
                VALUES(?,?,?,?,?,0,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                id=excluded.id,name=excluded.name,sha256=excluded.sha256,size=excluded.size,modified=excluded.modified''',
                (identifier, str(source), source.name, context[:80], normalized_tags, digest, stat.st_size, stat.st_mtime, db.now_iso()))
            found += 1
    return {'scanned': found, **listing()}


def file(sticker_id: str) -> tuple[Path, str]:
    initialize()
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM stickers WHERE id=?', (sticker_id,)).fetchone()
    if row is None:
        raise ValueError('贴纸不存在。')
    source = files.resolve_read(row['path'])
    if not source.is_file() or source.stat().st_size != int(row['size']) or hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
        raise ValueError('贴纸文件已变化，请重新扫描目录。')
    return source, source.suffix.lower()


def set_favorite(sticker_id: str, favorite: bool) -> dict:
    initialize()
    with closing(db.get_conn()) as conn, conn:
        if conn.execute('SELECT 1 FROM stickers WHERE id=?', (sticker_id,)).fetchone() is None:
            raise ValueError('贴纸不存在。')
        conn.execute('UPDATE stickers SET favorite=? WHERE id=?', (int(bool(favorite)), sticker_id))
        return _public(conn.execute('SELECT * FROM stickers WHERE id=?', (sticker_id,)).fetchone())
