"""Paginated views of existing task and browser states, without a second queue."""
from __future__ import annotations

import json
import hashlib
from contextlib import closing

from . import db, workspace_service as workspace, browser_service as browser


_QUERY = """
SELECT 'task:' || t.id AS id, 'task' AS kind, t.id AS task_id,
       t.conversation_id, COALESCE(c.project_id, '') AS project_id,
       COALESCE(p.name, '') AS project_name, t.original_goal AS title,
       t.status, t.updated_at, t.snapshot_json AS details_json,
       '' AS url, '' AS confirmation_id,
       CASE t.status WHEN 'waiting_confirmation' THEN 0 WHEN 'waiting_user' THEN 0
         WHEN 'waiting_jobs' THEN 2 ELSE 1 END AS priority
FROM agent_tasks t
LEFT JOIN workspace_conversations c ON c.conversation_id=t.conversation_id
LEFT JOIN workspace_projects p ON p.id=c.project_id
WHERE t.status IN ('waiting_confirmation','waiting_user','waiting_jobs','failed','budget_exhausted')
UNION ALL
SELECT 'browser:' || b.id, 'browser', t.id, t.conversation_id,
       COALESCE(c.project_id, ''), COALESCE(p.name, ''), t.original_goal,
       'waiting_confirmation', b.created_at, b.action_json, b.url, b.id, 0
FROM workspace_browser_pending b
JOIN agent_tasks t ON t.id=b.task_id
LEFT JOIN workspace_conversations c ON c.conversation_id=t.conversation_id
LEFT JOIN workspace_projects p ON p.id=c.project_id
WHERE b.status='pending' AND t.status NOT IN ('completed','cancelled','paused')
"""


def listing(project_id: str = '', *, limit: int = 50, offset: int = 0) -> dict:
    workspace.initialize()
    browser.initialize()
    if project_id:
        workspace.require_project(project_id)
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    query = f"SELECT * FROM ({_QUERY}) WHERE (?='' OR project_id=?)"
    with closing(db.get_conn()) as conn:
        # Count and page share one snapshot when another window resolves an item.
        conn.execute('BEGIN')
        total = conn.execute(f'SELECT COUNT(*) FROM ({query})', (project_id, project_id)).fetchone()[0]
        rows = conn.execute(query + ' ORDER BY priority,updated_at DESC,id LIMIT ? OFFSET ?',
                            (project_id, project_id, limit + 1, offset)).fetchall()
    items = []
    for row in rows[:limit]:
        item = dict(row)
        try:
            details = json.loads(item.pop('details_json'))
        except (ValueError, TypeError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        item['action'] = details if item['kind'] == 'browser' else None
        item['reason'] = details.get('purpose', '') if item['kind'] == 'browser' else details.get('blocker', '')
        item['requires_action'] = item['status'] != 'waiting_jobs'
        item['event_id'] = hashlib.sha256(f"{item['kind']}:{item['id']}:{item['updated_at']}".encode()).hexdigest()[:24]
        items.append(item)
    return {'items': items, 'total': total, 'has_more': len(rows) > limit,
            'offset': offset, 'next_offset': offset + len(items),
            'next_cursor': items[-1]['event_id'] if items else ''}
