"""Small local life feed built from user text and existing Mio records."""
from __future__ import annotations

import uuid
from contextlib import closing

from . import db, workspace_service as workspace, artifact_service


def initialize() -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS moments(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, content TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'text', source_type TEXT NOT NULL DEFAULT 'user',
            source_id TEXT NOT NULL DEFAULT '', source_label TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_moments_project ON moments(project_id,archived,created_at);
            CREATE TABLE IF NOT EXISTS moment_comments(
            id TEXT PRIMARY KEY, moment_id TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL);''')


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
    where = 'project_id=?'; args: list[object] = [project_id]
    if not include_archived: where += ' AND archived=0'
    if q.strip(): where += ' AND content LIKE ?'; args.append('%' + q.strip()[:200] + '%')
    with closing(db.get_conn()) as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM moments WHERE {where}', args).fetchone()[0]
        rows = conn.execute(f'SELECT * FROM moments WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (*args, max(1,min(limit,200)), max(0,offset))).fetchall()
        items = []
        for row in rows:
            comments = conn.execute('SELECT * FROM moment_comments WHERE moment_id=? ORDER BY created_at,id', (row['id'],)).fetchall()
            items.append(_public(row, comments))
    return {'moments': items, 'total': total, 'offset': max(0,offset), 'has_more': max(0,offset) + len(items) < total}


def create(project_id: str, content: str, *, source_type: str = 'user', source_id: str = '', media_type: str = 'text') -> dict:
    initialize(); workspace.require_project(project_id, active=True)
    content = content.strip()
    if not content or len(content) > 20000: raise ValueError('动态内容不能为空且不能超过 20000 字符。')
    source_label = _check_source(source_type, source_id, project_id)
    identifier = 'moment_' + uuid.uuid4().hex[:24]; now = db.now_iso()
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT INTO moments VALUES(?,?,?,?,?,?,?,?,?,?)', (identifier, project_id, content, media_type[:40], source_type, source_id, source_label, 0, now, now))
        return _public(conn.execute('SELECT * FROM moments WHERE id=?', (identifier,)).fetchone())


def comment(moment_id: str, content: str) -> dict:
    initialize(); content = content.strip()
    if not content or len(content) > 4000: raise ValueError('评论不能为空且不能超过 4000 字符。')
    identifier = 'comment_' + uuid.uuid4().hex[:24]
    with closing(db.get_conn()) as conn, conn:
        if conn.execute('SELECT 1 FROM moments WHERE id=?', (moment_id,)).fetchone() is None: raise ValueError('动态不存在。')
        conn.execute('INSERT INTO moment_comments VALUES(?,?,?,?)', (identifier, moment_id, content, db.now_iso()))
        return dict(conn.execute('SELECT * FROM moment_comments WHERE id=?', (identifier,)).fetchone())


def archive(moment_id: str, archived: bool = True) -> dict:
    initialize()
    with closing(db.get_conn()) as conn, conn:
        if conn.execute('SELECT 1 FROM moments WHERE id=?', (moment_id,)).fetchone() is None: raise ValueError('动态不存在。')
        conn.execute('UPDATE moments SET archived=?,updated_at=? WHERE id=?', (int(bool(archived)), db.now_iso(), moment_id))
        row = conn.execute('SELECT * FROM moments WHERE id=?', (moment_id,)).fetchone()
        comments = conn.execute('SELECT * FROM moment_comments WHERE moment_id=? ORDER BY created_at,id', (moment_id,)).fetchall()
        return _public(row, comments)
