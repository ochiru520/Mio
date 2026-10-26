"""Exact generation ownership; legacy evidence never falls back to conversation order."""

from .repositories import creation_provenance_repository as repository

from . import db


def initialize():
    with repository.transaction() as conn:
        repository.create_owners(conn)
        repository.create_task_index(conn)


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
    old = repository.existing_owner(conn, (job_id,)).fetchone()
    if old and old['task_id'] != task_id:
        raise ValueError('这项生成已属于另一个任务，不能重新归属。')
    repository.insert_owner(conn, (job_id, task_id, run_id, int(source_message_id or 0), db.now_iso()))


def validate_reuse(job, conversation_id, task_id):
    if job['conversation_id'] != conversation_id:
        raise ValueError('生成请求标识已用于另一个会话。')
    if task_id and owner(job['id'])['task_id'] != task_id:
        raise ValueError('生成请求标识的来源任务不一致，不能重新归属。')


def owner(job_id):
    with repository.transaction() as conn:
        tables = {row[0] for row in repository.table_names(conn)}
        exact = repository.exact_owner(conn, (job_id,)).fetchone() if 'creation_job_owners' in tables else None
        if exact:
            return {**dict(exact), 'provenance_status': 'recorded'}
        job = repository.job_parent(conn, (job_id,)).fetchone() if 'creation_jobs' in tables else None
        if not job:
            return {'task_id': '', 'provenance_status': 'unknown'}
        # Actual persisted tool results and batch membership are the only legacy evidence.
        candidates = set()
        if {'agent_run_steps', 'agent_task_runs', 'agent_tasks'} <= tables:
            rows = repository.tool_evidence(conn, (job['conversation_id'], job_id, job_id)).fetchall()
            candidates.update(row[0] for row in rows)
        if {'creative_batch_items', 'creative_batches'} <= tables:
            candidates.update(row[0] for row in repository.batch_evidence(conn, (job_id, job['conversation_id'])))
        if len(candidates) == 1:
            return {'task_id': candidates.pop(), 'provenance_status': 'legacy_evidence'}
        return {'task_id': '', 'provenance_status': 'conflicting' if candidates else 'unknown'}
