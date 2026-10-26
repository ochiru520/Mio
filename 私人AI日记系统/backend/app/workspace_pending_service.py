"""Paginated views of existing task and browser states, without a second queue."""
from __future__ import annotations

import json
import hashlib

from . import workspace_service as workspace, browser_service as browser
from .repositories import workspace_pending_repository as repository


def listing(project_id: str = '', *, limit: int = 50, offset: int = 0) -> dict:
    workspace.initialize()
    browser.initialize()
    if project_id:
        workspace.require_project(project_id)
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    total, rows = repository.page(project_id, limit, offset)
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
