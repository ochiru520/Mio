"""User-owned sticker catalog. Files stay local and are never uploaded."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .repositories import sticker_repository as repository

from . import db, agent_file_service as files

IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}


def initialize() -> None:
    repository.initialize()


def _public(row) -> dict:
    item = dict(row)
    item['favorite'] = bool(item.get('favorite'))
    item['tags'] = [x for x in str(item.get('tags') or '').split(',') if x]
    item['url'] = f"/api/workspace/stickers/{item['id']}/file"
    return item


def listing(context: str = '', tag: str = '', limit: int = 200) -> dict:
    initialize()
    context, tag = context.strip().lower(), tag.strip().lower()
    rows = repository.list_stickers(limit)
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
    def records():
        for item in directory.rglob('*'):
            if not item.is_file() or item.suffix.lower() not in IMAGE_TYPES or item.name.startswith('.'):
                continue
            try:
                source = files.resolve_read(str(item)); data = source.read_bytes(); stat = source.stat()
            except (ValueError, OSError):
                continue
            digest = hashlib.sha256(data).hexdigest()
            identifier = 'sticker_' + digest[:24]
            yield (identifier, str(source), source.name, context[:80], normalized_tags, digest, stat.st_size, stat.st_mtime, db.now_iso())

    found = repository.upsert_scanned(records())
    return {'scanned': found, **listing()}


def file(sticker_id: str) -> tuple[Path, str]:
    initialize()
    row = repository.get(sticker_id)
    if row is None:
        raise ValueError('贴纸不存在。')
    source = files.resolve_read(row['path'])
    if not source.is_file() or source.stat().st_size != int(row['size']) or hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
        raise ValueError('贴纸文件已变化，请重新扫描目录。')
    return source, source.suffix.lower()


def set_favorite(sticker_id: str, favorite: bool) -> dict:
    initialize()
    row = repository.set_favorite(sticker_id, int(bool(favorite)))
    if row is None:
        raise ValueError('贴纸不存在。')
    return _public(row)
