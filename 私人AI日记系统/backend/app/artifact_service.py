"""Content-addressed outputs with producer identity, versions and verified downloads."""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
import re
import uuid

from .repositories import artifact_repository as repository

from . import db, maintenance_service


def root() -> Path:
    directory = db.settings.db_path.resolve().parent / "成果"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != directory:
        raise ValueError("成果目录不能是联接或符号链接。")
    return directory


def initialize() -> None:
    repository.initialize()


def _public(row) -> dict:
    item = dict(row)
    if item.get("metadata_json"):
        import json
        try:
            item["metadata"] = json.loads(item["metadata_json"])
        except (TypeError, ValueError):
            item["metadata"] = {}
        item.pop("metadata_json", None)
    else:
        item.setdefault("metadata", {})
    item["url"] = f"/api/artifacts/{item['id']}/download"
    item["preview_url"] = f"/api/artifacts/{item['id']}/preview"
    item["verification"] = "content_hash"
    item.setdefault("quality_status", "not_reviewed")
    item.setdefault("quality_note", "")
    item.setdefault("reviewed_at", "")
    item.setdefault("integrity_status", "unknown")
    item.setdefault("verified_at", "")
    if not item['task_id'] and item['job_id']:
        from .creation_provenance import owner
        provenance = owner(item['job_id'])
        item['task_id'] = provenance['task_id']
        item['provenance_status'] = provenance['provenance_status']
    else:
        item['provenance_status'] = 'recorded' if item['task_id'] else 'unknown'
    return item




@maintenance_service.mutation_scope()
def import_file(source: Path, *, source_root: Path, producer: str, expected_sha256: str,
                task_id: str = "", job_id: str = "", conversation_id: str = "", name: str = "", mime_type: str = "", metadata: dict | None = None) -> dict:
    source = source.resolve(strict=True)
    if not source.is_file() or not source.is_relative_to(source_root.resolve(strict=True)):
        raise ValueError("来源文件超出生产工具的授权目录。")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("成果必须提供可核验的内容哈希。")
    filename = name or source.name
    if Path(filename).name != filename or ':' in filename or filename.startswith('.'):
        raise ValueError("无效成果文件名。")
    initialize()
    temporary = root() / f"{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open('rb') as original, temporary.open('xb') as target:
            for chunk in iter(lambda: original.read(1024*1024), b''):
                digest.update(chunk); size += len(chunk); target.write(chunk)
            target.flush(); os.fsync(target.fileno())
        actual = digest.hexdigest()
        if actual != expected_sha256.lower():
            raise ValueError("来源文件已经改变，成果未入库。")
        blob = root() / actual
        # Replacement is safe: every writer has verified exactly these content bytes.
        temporary.replace(blob)
        import json
        return _public(repository.upsert_file(producer, task_id, job_id, filename, actual,
            mime_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream',
            size, conversation_id, str(source), json.dumps(metadata, ensure_ascii=False, sort_keys=True) if metadata else '', db.now_iso()))
    finally:
        temporary.unlink(missing_ok=True)


def get(artifact_id: str) -> dict | None:
    if not db.settings.db_path.is_file():
        return None
    row = repository.get(artifact_id)
    return _public(row) if row else None


def list_artifacts(*, task_id: str = "", job_id: str = "", project_id: str = "", limit: int = 50, offset: int = 0,
                   q: str = '', kind: str = '', favorite: bool = False, conversation_id: str = '', artifact_id: str = '') -> list[dict]:
    if not db.settings.db_path.is_file():
        return []
    owned_jobs = []
    if task_id:
        from . import creation_provenance, agent_task_service
        task = agent_task_service.get(task_id)
        jobs = repository.conversation_job_ids(task['conversation_id']) if task else []
        owned_jobs = [identifier for identifier in jobs if creation_provenance.owner(identifier)['task_id'] == task_id]
    if project_id:
        from .workspace_service import require_project
        require_project(project_id)
    rows = repository.listing(task_id=task_id, job_id=job_id, project_id=project_id, limit=limit, offset=offset,
        q=q, kind=kind, favorite=favorite, conversation_id=conversation_id, artifact_id=artifact_id, owned_jobs=owned_jobs)
    result = [_public(row) for row in rows]
    return result


def set_favorite(artifact_id, favorite):
    initialize()
    if get(artifact_id) is None: raise ValueError('找不到成果。')
    repository.set_favorite(artifact_id, int(favorite))
    return {'id': artifact_id, 'favorite': bool(favorite)}


def file(artifact_id: str) -> tuple[Path, dict]:
    item = get(artifact_id)
    if item is None or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
        raise ValueError("找不到成果。")
    path = (root() / item['sha256']).resolve(strict=True)
    if not path.is_relative_to(root()) or not path.is_file():
        raise ValueError("成果文件路径无效。")
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):digest.update(chunk)
    if digest.hexdigest() != item['sha256']:
        raise ValueError("成果文件校验失败。")
    return path, item


def preview(artifact_id: str) -> dict:
    """Preview verified archived bytes, including files with no on-disk suffix."""
    from .artifact_preview_service import OFFICE, office_preview
    path, item = file(artifact_id)
    suffix = Path(item['name']).suffix.lower()
    mime = content_mime(item)
    if suffix in OFFICE:
        return {**office_preview(path, item['name']), 'url': item['url']}
    from .agent_file_service import TEXT
    if mime.startswith(('text/', 'application/json')) or suffix in TEXT | {'.svg'}:
        if path.stat().st_size > 2 * 1024 * 1024:
            return {'kind': 'download', 'url': item['url'], 'name': item['name'], 'supported': False,
                    'notice': '文本文件超过 2MB，请下载查看完整内容。'}
        content = path.read_text(encoding='utf-8-sig', errors='replace')
        return {'kind': 'text', 'content': content[:60000], 'name': item['name'], 'truncated': len(content) > 60000}
    if mime.startswith(('image/', 'video/', 'audio/')) or mime == 'application/pdf':
        return {'kind': mime.split('/')[0] if mime != 'application/pdf' else 'pdf',
                'url': '/api/artifacts/' + artifact_id + '/content', 'name': item['name']}
    return {'kind': 'download', 'url': item['url'], 'name': item['name'], 'supported': False,
            'notice': '暂不支持此格式的内容预览，请下载后打开。'}


def content_mime(item: dict) -> str:
    """Use a recorded MIME when specific; recover old generic MIME from the filename."""
    mime = item['mime_type']
    if mime == 'application/octet-stream':
        return mimetypes.guess_type(item['name'])[0] or mime
    return mime


@maintenance_service.mutation_scope()
def verify(artifact_id: str) -> dict:
    if get(artifact_id) is None:
        raise ValueError('找不到成果。')
    initialize()
    try:
        file(artifact_id)
        status = 'verified'
    except (ValueError, OSError):
        status = 'failed'
    repository.record_integrity(artifact_id, status, db.now_iso())
    return get(artifact_id)


@maintenance_service.mutation_scope()
def review(artifact_id: str, status: str, note: str = '') -> dict:
    if status not in {'not_reviewed', 'user_approved', 'user_rejected'}:
        raise ValueError('无效质量评价。')
    if get(artifact_id) is None:
        raise ValueError('找不到成果。')
    initialize()
    # A user quality review is independent of the stored file's integrity.
    repository.record_quality(artifact_id, status, str(note)[:2000], db.now_iso())
    return get(artifact_id)
