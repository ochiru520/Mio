"""Read snapshot of existing task and browser pending states."""
from contextlib import closing

from .. import db


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


def page(project_id, limit, offset):
    query = f"SELECT * FROM ({_QUERY}) WHERE (?='' OR project_id=?)"
    with closing(db.get_conn()) as connection:
        connection.execute('BEGIN')
        total = connection.execute(f'SELECT COUNT(*) FROM ({query})', (project_id, project_id)).fetchone()[0]
        rows = connection.execute(query + ' ORDER BY priority,updated_at DESC,id LIMIT ? OFFSET ?',
                                  (project_id, project_id, limit + 1, offset)).fetchall()
    return total, rows
