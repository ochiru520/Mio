"""Exact generation ownership; legacy evidence never falls back to conversation order."""
from contextlib import closing

from . import db


def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS creation_job_owners(
            job_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,run_id TEXT NOT NULL DEFAULT '',
            source_message_id INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS creation_owners_task ON creation_job_owners(task_id)')


def validate(task_id, conversation_id, run_id=''):
    if not task_id:
        return
    from . import agent_task_service as tasks
    task = tasks.get(task_id)
    if not task or task['conversation_id'] != conversation_id:
        raise ValueError('生成任务的来源任务不属于当前会话。')
    if run_id:
        owner = tasks.for_run(run_id)
        if not owner or owner['id'] != task_id:
            raise ValueError('生成轮次不属于来源任务。')


def bind(conn, job_id, task_id, run_id='', source_message_id=0):
    if not task_id:
        return
    old = conn.execute('SELECT task_id FROM creation_job_owners WHERE job_id=?', (job_id,)).fetchone()
    if old and old['task_id'] != task_id:
        raise ValueError('这项生成已属于另一个任务，不能重新归属。')
    conn.execute('INSERT OR IGNORE INTO creation_job_owners VALUES(?,?,?,?,?)',
                 (job_id, task_id, run_id, int(source_message_id or 0), db.now_iso()))


def validate_reuse(job, conversation_id, task_id):
    if job['conversation_id'] != conversation_id:
        raise ValueError('生成请求标识已用于另一个会话。')
    if task_id and owner(job['id'])['task_id'] != task_id:
        raise ValueError('生成请求标识的来源任务不一致，不能重新归属。')


def owner(job_id):
    with closing(db.get_conn()) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        exact = conn.execute('SELECT * FROM creation_job_owners WHERE job_id=?', (job_id,)).fetchone() if 'creation_job_owners' in tables else None
        if exact:
            return {**dict(exact), 'provenance_status': 'recorded'}
        job = conn.execute('SELECT conversation_id,parent_job_id FROM creation_jobs WHERE id=?', (job_id,)).fetchone() if 'creation_jobs' in tables else None
        if not job:
            return {'task_id': '', 'provenance_status': 'unknown'}
        # Actual persisted tool results and batch membership are the only legacy evidence.
        candidates = set()
        if {'agent_run_steps', 'agent_task_runs', 'agent_tasks'} <= tables:
            rows = conn.execute('''SELECT DISTINCT t.id FROM agent_run_steps s
                JOIN agent_task_runs r ON r.run_id=s.run_id JOIN agent_tasks t ON t.id=r.task_id
                WHERE t.conversation_id=? AND s.step_kind='tool_call' AND json_valid(s.result_json)
                AND (json_extract(s.result_json,'$.job.id')=? OR json_extract(s.result_json,'$.job_id')=?)''',
                (job['conversation_id'], job_id, job_id)).fetchall()
            candidates.update(row[0] for row in rows)
        if {'creative_batch_items', 'creative_batches'} <= tables:
            candidates.update(row[0] for row in conn.execute('''SELECT DISTINCT b.task_id
                FROM creative_batch_items i JOIN creative_batches b ON b.id=i.batch_id
                WHERE i.job_id=? AND b.conversation_id=?''', (job_id, job['conversation_id'])))
        if len(candidates) == 1:
            return {'task_id': candidates.pop(), 'provenance_status': 'legacy_evidence'}
        return {'task_id': '', 'provenance_status': 'conflicting' if candidates else 'unknown'}
