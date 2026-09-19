"""Bounded HTTPS downloads with source-bound resume and mandatory final verification."""
from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urljoin

import httpx

from .manifest import MAX_MANIFEST_BYTES, Release, UpdateError, validate_url, verify_file


class UpdateCancelled(UpdateError):
    pass


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_plain_path(path: Path) -> Path:
    """Reject links/junctions within updater-controlled paths (including Windows)."""
    absolute = path.absolute()
    for item in [absolute, *absolute.parents]:
        if item.exists():
            info = item.lstat()
            if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise UpdateError('更新缓存或安装位置不能经过联接或符号链接。')
    return absolute


class UpdateTransport:
    def __init__(self, *, client_factory: Callable = httpx.Client):
        self._client_factory = client_factory

    @contextmanager
    def _stream(self, url: str, headers: dict[str, str] | None = None) -> Iterator[httpx.Response]:
        with self._client_factory(timeout=httpx.Timeout(25, connect=8), follow_redirects=False,
                                  headers={'User-Agent': 'MioUpdater/1.0', 'Accept-Encoding': 'identity'}) as client:
            current = validate_url(url)
            for index in range(6):
                with client.stream('GET', current, headers=headers or {}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if index == 5 or not response.headers.get('location'):
                            raise UpdateError('更新下载重定向异常。')
                        current = validate_url(urljoin(current, response.headers['location']))
                        continue
                    if response.status_code == 404:
                        raise UpdateError('发布端尚未提供对应的更新清单或安装包；这不表示已是最新版。')
                    response.raise_for_status()
                    yield response
                    return
        raise UpdateError('更新服务器没有返回内容。')

    def manifest(self, url: str, cancelled: Callable[[], bool]) -> bytes:
        if cancelled():
            raise UpdateCancelled('已取消检查更新。')
        with self._stream(url) as response:
            data = bytearray()
            for chunk in response.iter_bytes(16384):
                if cancelled():
                    raise UpdateCancelled('隐私暂停或用户取消，已停止检查更新。')
                data.extend(chunk)
                if len(data) > MAX_MANIFEST_BYTES:
                    raise UpdateError('更新清单超过大小限制。')
            return bytes(data)

    def download(self, release: Release, destination: Path,
                 progress: Callable[[int, int], None], cancelled: Callable[[], bool]) -> Path:
        destination = ensure_plain_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = ensure_plain_path(destination.with_suffix('.part'))
        metadata = ensure_plain_path(destination.with_suffix('.part.json'))
        identity = {'url': release.url, 'sha256': release.sha256, 'size': release.size}
        previous = {}
        try:
            previous = json.loads(metadata.read_text('utf-8'))
        except (OSError, ValueError):
            pass
        if previous != identity or (partial.exists() and partial.stat().st_size > release.size):
            partial.unlink(missing_ok=True)
        if destination.exists():
            try:
                verify_file(destination, release)
                progress(release.size, release.size)
                return destination
            except UpdateError:
                destination.unlink()
        atomic_json(metadata, identity)
        offset = partial.stat().st_size if partial.exists() else 0
        if offset < release.size:
            if cancelled():
                raise UpdateCancelled('下载已暂停，可以重新点击继续。')
            headers = {'Range': f'bytes={offset}-'} if offset else {}
            with self._stream(release.url, headers) as response:
                if response.status_code == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
                    if (not match or int(match[1]) != offset or int(match[3]) != release.size
                            or int(match[2]) < offset or int(match[2]) >= release.size):
                        raise UpdateError('断点下载范围与已签名安装包不一致。')
                elif response.status_code == 200:
                    offset = 0  # This server ignored Range. Never append a full body to a partial file.
                else:
                    raise UpdateError('更新服务器返回了不支持的下载状态。')
                size_header = response.headers.get('content-length')
                if size_header and int(size_header) > release.size - offset:
                    raise UpdateError('下载体积超过已签名清单。')
                with partial.open('ab' if offset else 'wb') as stream:
                    written = offset
                    for chunk in response.iter_bytes(65536):
                        if cancelled():
                            raise UpdateCancelled('隐私暂停或用户取消，已保留下载断点。')
                        if written + len(chunk) > release.size:
                            raise UpdateError('更新安装包超过允许大小。')
                        stream.write(chunk)
                        written += len(chunk)
                        progress(written, release.size)
                    stream.flush()
                    os.fsync(stream.fileno())
        if cancelled():
            raise UpdateCancelled('已取消下载，未安装。')
        try:
            verify_file(partial, release)
        except UpdateError:
            # A complete corrupt file must not be reused on the next retry.
            if partial.exists() and partial.stat().st_size >= release.size:
                partial.unlink()
                metadata.unlink(missing_ok=True)
            raise
        os.replace(partial, destination)
        metadata.unlink(missing_ok=True)
        return destination
