"""Local, user-authorized music catalog with lazy playback."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .repositories import music_repository as repository

from . import db, agent_file_service as files

AUDIO = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.webm'}


def initialize() -> None:
    repository.initialize()


def _public(row) -> dict:
    item = dict(row)
    item['url'] = f"/api/workspace/music/{item['id']}/file"
    return item


def listing(limit: int = 200) -> dict:
    initialize()
    rows = repository.list_tracks(limit)
    return {'tracks': [_public(row) for row in rows]}


def scan(path: str) -> dict:
    directory = files.resolve_read(path)
    if not directory.is_dir():
        raise ValueError('音乐扫描需要已授权目录。')
    initialize(); found = 0
    def records():
        for item in directory.rglob('*'):
            if not item.is_file() or item.suffix.lower() not in AUDIO or item.name.startswith('.'):
                continue
            try:
                source = files.resolve_read(str(item)); stat = source.stat()
            except (ValueError, OSError):
                continue
            identifier = 'music_' + hashlib.sha256(f'{source}|{stat.st_size}|{stat.st_mtime_ns}'.encode()).hexdigest()[:24]
            yield (identifier, str(source), source.name, stat.st_size, stat.st_mtime, db.now_iso())

    found = repository.upsert_scanned(records())
    return {'scanned': found, **listing()}


def file(track_id: str) -> tuple[Path, str]:
    initialize()
    row = repository.get(track_id)
    if row is None:
        raise ValueError('音乐不存在。')
    source = files.resolve_read(row['path'])
    if not source.is_file() or source.stat().st_size != int(row['size']):
        raise ValueError('音乐文件已变化，请重新扫描目录。')
    return source, source.suffix.lower()
