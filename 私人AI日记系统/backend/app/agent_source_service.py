"""Inspectable retrieval evidence, scoped to real tool steps and source versions."""
from __future__ import annotations

import hashlib
import json
from contextlib import closing

from . import db, knowledge_service as knowledge


def listing(task_id: str, *, after_id: int = 0, limit: int = 50) -> dict:
    limit = max(1, min(int(limit), 200))
    after_id = max(0, int(after_id))
    with closing(db.get_conn()) as conn:
        rows = conn.execute("""
            SELECT s.id,s.run_id,s.result_json FROM agent_run_steps s
            JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.id>? AND s.step_kind='tool_call'
              AND s.tool_name='knowledge_search' AND s.status='completed'
            ORDER BY s.id LIMIT ?
        """, (task_id, after_id, limit + 1)).fetchall()
    sources, seen, checked = [], set(), {}
    for row in rows[:limit]:
        try:
            payload = json.loads(row['result_json'] or '{}')
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        for blocked, items in ((False, payload.get('results', [])), (True, payload.get('blocked_sources', []))):
            if not isinstance(items, list):
                continue
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                source = raw.get('source', raw)
                if not isinstance(source, dict) or not source.get('document_id'):
                    continue
                document_id = str(source['document_id'])
                digest = str(source.get('sha256') or '')
                key = (document_id, digest, source.get('chunk_id', raw.get('id', '')), blocked)
                if key in seen:
                    continue
                seen.add(key)
                if document_id not in checked:
                    try:
                        path, document = knowledge.source_file(document_id)
                        checked[document_id] = (document, hashlib.sha256(path.read_bytes()).hexdigest())
                    except (ValueError, OSError):
                        checked[document_id] = None
                current = checked[document_id]
                status, reason, url, can_reindex = 'unavailable', '原文件不存在、不可读或读取授权已撤销。', '', False
                if current:
                    document, current_hash = current
                    try:
                        can_reindex = not knowledge.workspace.require_project(document['project_id'])['archived']
                    except ValueError:
                        can_reindex = False
                    if blocked or raw.get('stale'):
                        status, reason = 'blocked', '这次检索已阻止旧片段进入模型；更新索引后需重新检索。'
                    elif not digest:
                        status, reason = 'unknown', '旧检索记录缺少来源版本，无法验证。'
                    elif digest != current_hash:
                        status, reason = 'stale', '检索后原文件发生变化，此版本不能作为当前依据。'
                    else:
                        status, reason = 'current', ''
                        page = int(source.get('page', 0) or 0)
                        url = f'/api/workspace/knowledge/{document_id}/source' + (f'#page={page}' if page else '')
                sources.append({
                    'key': ':'.join(map(str, key)), 'document_id': document_id,
                    'name': str(source.get('name') or raw.get('name') or '资料'),
                    'sha256': digest, 'page': source.get('page', 0), 'line': source.get('line', 0),
                    'chunk_id': source.get('chunk_id', raw.get('id')), 'status': status,
                    'reason': reason, 'url': url, 'can_reindex': can_reindex,
                    'run_id': row['run_id'], 'step_id': row['id'],
                })
    selected = rows[:limit]
    return {'sources': sources, 'next_id': selected[-1]['id'] if selected else after_id,
            'has_more': len(rows) > limit,
            'notice': '展示任务实际检索的来源，不代表最终回复每句话均已核验。'}
