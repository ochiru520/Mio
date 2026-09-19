"""Local release preparation/signing. Does not push, publish or reveal private keys.

Examples:
  python desktop/publish_update.py init-key
  python desktop/publish_update.py prepare --channel stable --version 0.3.0
  python desktop/publish_update.py sign --installer release/Mio-0.3.0-Windows-x64-Setup.exe \
      --manifest release/Mio/构建清单.json --notes release-notes.txt --output release/latest.json

Run init-key once on the release workstation. The encrypted private key remains
outside the workspace; only its verification key is included in installers.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from updates.manifest import PRODUCT_ID, canonical_json, version_tuple, UpdateChannel, verify_manifest
from updates.transport import atomic_json

DESKTOP = Path(__file__).resolve().parent
WORKSPACE = DESKTOP.parent.parent


def private_path() -> Path:
    return Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'MioPublisher' / 'release-signing-key.dpapi'


def _secret_helpers():
    backend = WORKSPACE / '私人AI日记系统' / 'backend'
    sys.path.insert(0, str(backend))
    from app.secret_store import protect_secret, unprotect_secret
    return protect_secret, unprotect_secret


def load_private_key() -> Ed25519PrivateKey:
    supplied = os.getenv('MIO_UPDATE_PRIVATE_KEY', '')
    if supplied:
        raw = base64.b64decode(supplied, validate=True)
    else:
        _, unprotect = _secret_helpers()
        raw = base64.b64decode(unprotect(private_path().read_text('utf-8')), validate=True)
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_config(key: Ed25519PrivateKey) -> dict:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_id = 'mio-release-' + hashlib.sha256(raw).hexdigest()[:16]
    return {key_id: base64.b64encode(raw).decode('ascii')}


def init_key() -> None:
    path = private_path()
    if not path.exists():
        if path.resolve().is_relative_to(WORKSPACE.resolve()):
            raise ValueError('发布私钥必须保存在源码工作区之外。')
        protect, _ = _secret_helpers()
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            stream.write(protect(base64.b64encode(raw).decode('ascii')))
    key = load_private_key()
    destination = DESKTOP / 'update_channel.json'
    config = json.loads(destination.read_text('utf-8')) if destination.exists() else {
        'schema_version': 1, 'product_id': PRODUCT_ID, 'version': '0.11.0',
        'channel': 'development', 'feed_url': '', 'enabled': False, 'public_keys': {}}
    keys = public_config(key)
    existing = config.get('public_keys') or {}
    if existing and existing != keys:
        raise ValueError('当前公钥与本机发布密钥不同；轮换密钥必须单独设计，不覆盖已有信任。')
    config['public_keys'] = keys
    atomic_json(destination, config)
    print('Signing key protected outside workspace. Public verification key pinned.')
    print('Protected key location:', path)


def prepare(channel: str, version: str | None) -> None:
    template = json.loads((DESKTOP / 'update_channel.json').read_text('utf-8'))
    package_version = json.loads((DESKTOP.parent / 'package.json').read_text('utf-8'))['version']
    requested = version or package_version
    version_tuple(requested)
    # Version stamping belongs to the source-release build. Never silently emit
    # a manifest claiming a different version than the actual binary.
    import re
    file_version = re.search(r"FileVersion',\s*u'([^']+)'", (DESKTOP / 'version_info.txt').read_text('utf-8'))[1]
    installer_version = re.search(r'#define MyAppVersion "([^"]+)"', (DESKTOP / 'installer.iss').read_text('utf-8-sig'))[1]
    if not (version_tuple(requested) == version_tuple(package_version) == version_tuple(file_version) == version_tuple(installer_version)):
        raise ValueError('package.json、version_info.txt、installer.iss 与发布版本不一致；先统一版本再构建。')
    if channel == 'stable' and not template.get('public_keys'):
        raise ValueError('稳定版必须先配置发布公钥。')
    template.update(channel=channel, version=requested, enabled=channel == 'stable',
                    feed_url='https://github.com/ochiru520/Mio/releases/latest/download/latest.json' if channel == 'stable' else '')
    atomic_json(DESKTOP / 'generated' / 'update_channel.json', template)
    print('Prepared embedded update channel:', channel, requested)


def sign(installer: Path, manifest: Path, notes: Path, output: Path) -> None:
    channel = UpdateChannel.load(DESKTOP / 'generated' / 'update_channel.json')
    if channel.channel != 'stable' or not channel.enabled:
        raise ValueError('只有已按稳定版构建的安装器可生成正式更新清单。')
    build = json.loads(manifest.read_text('utf-8'))
    if version_tuple(build['app_version']) != version_tuple(channel.version):
        raise ValueError('构建清单版本与更新通道不一致。')
    if not installer.is_file() or installer.suffix.lower() != '.exe':
        raise ValueError('请提供已构建并验收的 Windows 安装器。')
    # Prove that every recorded artifact still matches this build before signing.
    for item in build.get('artifacts', []):
        path = (manifest.parent / item['path']).resolve()
        if not path.is_relative_to(manifest.parent.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest().lower() != item['sha256'].lower():
            raise ValueError('制品与构建身份不符，不签名。')
    key = load_private_key()
    keys = public_config(key)
    if keys != dict(channel.public_keys):
        raise ValueError('签名私钥与安装版公钥不同，不签名。')
    now = datetime.now(timezone.utc)
    payload = {'schema_version': 1, 'product_id': PRODUCT_ID, 'channel': channel.channel,
               'platform': 'windows-x64', 'version': channel.version, 'build_id': build['build_id'],
               'minimum_updater_version': '1.0.0', 'published_at': now.isoformat(),
               'expires_at': (now + timedelta(days=365)).isoformat(),
               'notes': notes.read_text('utf-8'),
               'package': {'url': f'https://github.com/ochiru520/Mio/releases/download/v{channel.version}/{installer.name}',
                           'size': installer.stat().st_size, 'sha256': hashlib.sha256(installer.read_bytes()).hexdigest()}}
    envelope = {'payload': payload, 'key_id': next(iter(keys)),
                'signature': base64.b64encode(key.sign(canonical_json(payload))).decode('ascii')}
    raw = canonical_json(envelope)
    verify_manifest(raw, channel)
    atomic_json(output, envelope)
    print('Signed update manifest:', output)
    print('Upload the installer and latest.json to the matching draft Release; publish only after download verification.')


def main() -> int:
    parser = argparse.ArgumentParser(description='Mio 更新签名与构建配置，不自动公开发布')
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('init-key')
    p = subs.add_parser('prepare'); p.add_argument('--channel', choices=['stable', 'development'], default=os.getenv('MIO_UPDATE_CHANNEL', 'development')); p.add_argument('--version')
    p = subs.add_parser('sign'); p.add_argument('--installer', required=True, type=Path); p.add_argument('--manifest', required=True, type=Path); p.add_argument('--notes', required=True, type=Path); p.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'init-key': init_key()
    elif args.command == 'prepare': prepare(args.channel, args.version)
    else: sign(args.installer, args.manifest, args.notes, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
