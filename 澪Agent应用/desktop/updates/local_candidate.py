"""Explicit locally authorized builds; never a fallback for signed feed manifests.

Integrity pins protect a selected snapshot against accidental changes. They are
not a signature and not a security boundary against the same Windows account.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

from .manifest import PRODUCT_ID, UpdateError, read_json_bytes
from .transport import ensure_plain_path

LOCAL_KIND = 'mio.local-development-candidate.v1'
LOCAL_MANIFEST = 'local-candidate.json'
ALLOWED_NAMES = {'Mio.exe', 'MioUpdater.exe', '_internal', '构建清单.json'}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> list[dict]:
    root = ensure_plain_path(root).resolve(strict=True)
    records = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            ensure_plain_path(Path(folder) / name)
        for name in sorted(files):
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if PurePosixPath(relative).parts[0] not in ALLOWED_NAMES:
                raise UpdateError('候选包含非程序文件：' + relative)
            records.append({'path': relative, 'size': path.stat().st_size, 'sha256': sha256(path)})
    return sorted(records, key=lambda item: item['path'])


def verify_candidate(root: Path, manifest_path: Path, expected_hash: str, *, base_build_id: str = '') -> dict:
    root, manifest_path = ensure_plain_path(root), ensure_plain_path(manifest_path)
    if len(expected_hash) != 64 or sha256(manifest_path) != expected_hash.lower():
        raise UpdateError('本地候选清单校验失败。')
    manifest = read_json_bytes(manifest_path.read_bytes(), limit=16 * 1024**2)
    if (manifest.get('kind') != LOCAL_KIND or manifest.get('product_id') != PRODUCT_ID
            or not manifest.get('build_id') or not manifest.get('source_job_id')
            or not manifest.get('base_build_id') or manifest.get('build_id') == manifest.get('base_build_id')):
        raise UpdateError('本地候选身份无效。')
    if base_build_id and manifest['base_build_id'] != base_build_id:
        raise UpdateError('正式版本已改变，请重新生成候选。')
    if not manifest.get('checks') or not all(item.get('ok') is True for item in manifest['checks']):
        raise UpdateError('候选缺少成功的实际验证记录。')
    if manifest.get('preview', {}).get('verified') is not True:
        raise UpdateError('候选尚未通过隔离预览启动核对。')
    actual = inventory(root)
    if actual != manifest.get('files') or not actual:
        raise UpdateError('候选文件与清单不一致。')
    paths = {item['path'] for item in actual}
    if not {'Mio.exe', '构建清单.json', '_internal/build_identity.json', '_internal/agent_frontend/index.html',
            '_internal/MioUpdater.exe'}.issubset(paths):
        raise UpdateError('候选缺少完整前后端程序或独立更新器。')
    build = read_json_bytes((root / '构建清单.json').read_bytes(), limit=16 * 1024**2)
    identity = read_json_bytes((root / '_internal/build_identity.json').read_bytes(), limit=2 * 1024**2)
    if (build.get('build_id') != manifest['build_id'] or build.get('sources') != manifest.get('sources')
            or identity.get('build_id') != manifest['build_id'] or identity.get('sources') != build.get('sources')):
        raise UpdateError('候选源码与内嵌构建身份不一致。')
    for item in build.get('artifacts', []):
        matched = next((record for record in actual if record['path'] == item.get('path')), None)
        if not matched or matched['sha256'] != str(item.get('sha256', '')).lower() or matched['size'] != item.get('size'):
            raise UpdateError('构建制品校验失败。')
    return manifest
