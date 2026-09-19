"""Signed release manifests. No model decisions, remote commands or shared tokens."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRODUCT_ID = 'io.github.mio-agent.desktop'
UPDATER_VERSION = '1.0.0'
MAX_MANIFEST_BYTES = 131072
MAX_PACKAGE_BYTES = 2 * 1024**3 - 1
ALLOWED_DOWNLOAD_HOSTS = frozenset({'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'})
VERSION_RE = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$')


class UpdateError(ValueError):
    """A user-visible updater error, never interpreted as 'already latest'."""


def version_tuple(value: str) -> tuple[int, int, int, int]:
    match = VERSION_RE.fullmatch(str(value))
    if not match:
        raise UpdateError('版本号格式无效，必须使用数字版本，例如 0.3.0。')
    return tuple(int(part or 0) for part in match.groups())


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise UpdateError('更新清单包含重复字段。')
        result[key] = value
    return result


def read_json_bytes(raw: bytes, *, limit: int = MAX_MANIFEST_BYTES) -> dict[str, Any]:
    if not raw or len(raw) > limit:
        raise UpdateError('更新清单为空或超过大小限制。')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(UpdateError('无效 JSON 数值。')))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError('更新清单不是有效 JSON。') from exc
    if not isinstance(value, dict):
        raise UpdateError('更新清单必须是对象。')
    return value


def validate_url(url: str) -> str:
    try:
        parsed = urlsplit(str(url))
        if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or parsed.fragment or '\\' in str(url) or any(ord(c) < 32 for c in str(url))):
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise UpdateError('更新地址必须是受信任的 GitHub HTTPS 下载地址。') from exc
    return str(url)


@dataclass(frozen=True)
class UpdateChannel:
    channel: str
    version: str
    feed_url: str
    public_keys: Mapping[str, str]
    enabled: bool = True

    @classmethod
    def load(cls, path: Path) -> 'UpdateChannel':
        data = read_json_bytes(path.read_bytes())
        if data.get('product_id') != PRODUCT_ID or data.get('schema_version') != 1:
            raise UpdateError('本机构建的更新配置无效。')
        channel = str(data.get('channel', 'development'))
        if channel not in {'stable', 'beta', 'development'}:
            raise UpdateError('未知更新通道。')
        version_tuple(str(data.get('version', '')))
        url = str(data.get('feed_url', ''))
        if url:
            validate_url(url)
            if not url.startswith('https://github.com/ochiru520/Mio/releases/'):
                raise UpdateError('更新清单不属于本产品发布仓库。')
        keys = data.get('public_keys')
        if not isinstance(keys, dict):
            raise UpdateError('本地更新公钥配置无效。')
        return cls(channel, str(data['version']), url, keys, bool(data.get('enabled', True)))


@dataclass(frozen=True)
class Release:
    version: str
    build_id: str
    url: str
    size: int
    sha256: str
    notes: str
    payload: Mapping[str, Any]

    def public(self) -> dict[str, Any]:
        return {'version': self.version, 'build_id': self.build_id, 'size': self.size,
                'notes': self.notes, 'published_at': self.payload['published_at']}


def _timestamp(value: Any) -> datetime:
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise UpdateError('更新清单时间格式无效。') from exc


def verify_manifest(raw: bytes, channel: UpdateChannel, *, now: datetime | None = None,
                    highest_seen: str = '', allow_current: bool = False) -> Release:
    envelope = read_json_bytes(raw)
    payload = envelope.get('payload')
    if not isinstance(payload, dict):
        raise UpdateError('更新清单缺少签名内容。')
    key = channel.public_keys.get(str(envelope.get('key_id', '')))
    if not key:
        raise UpdateError('更新清单使用了未授权的发布密钥。')
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))
        public.verify(base64.b64decode(envelope.get('signature', ''), validate=True), canonical_json(payload))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise UpdateError('更新签名验证失败，已阻止安装。') from exc
    if payload.get('schema_version') != 1 or payload.get('product_id') != PRODUCT_ID:
        raise UpdateError('更新清单不属于 Mio 或格式版本不受支持。')
    if payload.get('channel') != channel.channel or payload.get('platform') != 'windows-x64':
        raise UpdateError('更新的通道或系统架构与当前安装不一致。')
    if version_tuple(str(payload.get('minimum_updater_version', ''))) > version_tuple(UPDATER_VERSION):
        raise UpdateError('本次更新需要更新的升级助手，请手动升级一次。')
    version = str(payload.get('version', ''))
    parsed = version_tuple(version)
    if parsed < version_tuple(channel.version):
        raise UpdateError('发布端返回了较旧版本，已阻止降级。')
    if highest_seen and parsed < version_tuple(highest_seen):
        raise UpdateError('发布端返回了以前的清单，已阻止回退。')
    current = now or datetime.now(timezone.utc)
    published = _timestamp(payload.get('published_at'))
    expires = _timestamp(payload.get('expires_at'))
    if published > current + timedelta(minutes=10) or expires <= current or not published < expires:
        raise UpdateError('更新清单已过期或发布时间异常，请稍后重试或手动检查发布页。')
    if expires - published > timedelta(days=370):
        raise UpdateError('更新清单有效期过长。')
    package = payload.get('package')
    if not isinstance(package, dict):
        raise UpdateError('更新清单缺少安装包。')
    size = package.get('size')
    digest = str(package.get('sha256', '')).lower()
    url = validate_url(str(package.get('url', '')))
    expected = f'https://github.com/ochiru520/Mio/releases/download/v{version}/'
    if not url.startswith(expected) or not urlsplit(url).path.lower().endswith('.exe'):
        raise UpdateError('安装包必须是对应版本发布下的 EXE。')
    if type(size) is not int or not 0 < size <= MAX_PACKAGE_BYTES or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise UpdateError('安装包大小或校验值无效。')
    build = str(payload.get('build_id', ''))
    if not build.startswith('mio-') or len(build) > 200:
        raise UpdateError('更新清单缺少可验证的 Build ID。')
    notes = payload.get('notes', '')
    if not isinstance(notes, str) or len(notes) > 20000:
        raise UpdateError('版本说明格式无效。')
    return Release(version, build, url, size, digest, notes, payload)


def verify_file(path: Path, release: Release) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size != release.size:
        raise UpdateError('安装包不完整，不能安装。')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != release.sha256:
        raise UpdateError('安装包校验失败，已阻止安装。')
