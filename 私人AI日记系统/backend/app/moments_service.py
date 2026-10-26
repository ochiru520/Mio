"""Small local life feed built from user text and existing Mio records."""
from __future__ import annotations

import uuid

from .repositories import moments_repository as repository

from . import db, workspace_service as workspace, artifact_service


def initialize() -> None:
    repository.initialize()


def _public(row, comments=()):
    item = dict(row); item['archived'] = bool(item.get('archived')); item['comments'] = [dict(x) for x in comments]
    item['source'] = {'type': item['source_type'], 'id': item['source_id'], 'label': item['source_label']} if item['source_type'] != 'user' else None
    return item


def _check_source(source_type: str, source_id: str, project_id: str) -> str:
    if source_type == 'user': return ''
    if source_type == 'artifact':
        items = artifact_service.list_artifacts(project_id=project_id, artifact_id=source_id, limit=1) if source_id else []
        if not items:
            raise ValueError('引用成果不存在或不属于当前项目。')
        return items[0]['name']
    if source_type == 'diary':
        item = db.get_diary(source_id)
        if not item: raise ValueError('引用日记不存在。')
        return f'{source_id} 日记'
    raise ValueError('动态来源类型无效。')


def listing(project_id: str, *, include_archived: bool = False, q: str = '', limit: int = 50, offset: int = 0) -> dict:
    initialize(); workspace.require_project(project_id)
    total, records = repository.listing(project_id, include_archived, q, limit, offset)
    items = [_public(row, comments) for row, comments in records]
    return {'moments': items, 'total': total, 'offset': max(0,offset), 'has_more': max(0,offset) + len(items) < total}


def create(project_id: str, content: str, *, source_type: str = 'user', source_id: str = '', media_type: str = 'text') -> dict:
    initialize(); workspace.require_project(project_id, active=True)
    content = content.strip()
    if not content or len(content) > 20000: raise ValueError('动态内容不能为空且不能超过 20000 字符。')
    source_label = _check_source(source_type, source_id, project_id)
    identifier = 'moment_' + uuid.uuid4().hex[:24]; now = db.now_iso()
    row = repository.create(identifier, project_id, content, media_type[:40], source_type, source_id, source_label, now)
    return _public(row)


def comment(moment_id: str, content: str) -> dict:
    initialize(); content = content.strip()
    if not content or len(content) > 4000: raise ValueError('评论不能为空且不能超过 4000 字符。')
    identifier = 'comment_' + uuid.uuid4().hex[:24]
    row = repository.comment(identifier, moment_id, content, db.now_iso())
    if row is None:
        raise ValueError('动态不存在。')
    return dict(row)


def archive(moment_id: str, archived: bool = True) -> dict:
    initialize()
    record = repository.archive(moment_id, int(bool(archived)), db.now_iso())
    if record is None:
        raise ValueError('动态不存在。')
    return _public(*record)
