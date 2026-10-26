"""Durable serial generation queue, without rewriting ComfyUI workflows."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from .repositories import creative_batch_repository as repository

from . import db, agent_task_service as tasks, creation_service as creation
from .creation_models import CreationJobRequest
from .workspace_service import encode


def initialize():
    repository.initialize()


def get(batch_id):
    initialize()
    row, rows = repository.get(batch_id)
    if row is None:
        raise ValueError('找不到批量任务。')
    result = dict(row)
    result['items'] = []
    for row in rows:
        item = dict(row)
        item['spec'] = json.loads(item.pop('spec_json'))
        item['job'] = creation.get_job(item['job_id']) if item['job_id'] else None
        result['items'].append(item)
    return result


def create(name, items, context):
    initialize()
    if not items or len(items) > 100:
        raise ValueError('批量列表需要包含 1–100 个具体生成请求，可分批继续。')
    if not context.task_id or not tasks.get(context.task_id):
        raise ValueError('批量生成必须属于当前任务。')
    from .agent_creation_selection import bind_selected_workflow
    parsed = []
    for item in items:
        item = dict(item)
        tool = 'comfyui_generate_video' if item.get('media_type') == 'video' else 'comfyui_generate_image'
        if item.get('backend') == 'remote_api':
            tool = 'remote_generate_image'
        item = bind_selected_workflow(tool, item, context.creation_workflow_id)
        item['batch_size'] = 1  # Queue independently to stay within the current GPU budget.
        spec = CreationJobRequest.model_validate(item)
        creation._effective_spec(spec)  # Validate all requests before accepting the queue.
        parsed.append(spec.model_dump(exclude_none=True))
    digest = hashlib.sha256(encode([context.task_id, context.source_message_id, name, parsed]).encode()).hexdigest()[:24]
    batch_id = 'batch_' + digest
    now = db.now_iso()
    def records():
        for index, spec in enumerate(parsed):
            spec['idempotency_key'] = f'{batch_id}:{index}:0'
            yield index, encode(spec)
    repository.create(batch_id, context.task_id, context.conversation_id, name, now, records())
    return {'batch': get(batch_id), 'message': '已加入持久队列，顺序调用原工作流；需要费用确认的项目仍等待确认。'}


async def control(batch_id, action, index=None):
    batch = get(batch_id)
    if action not in {'pause', 'resume', 'cancel', 'retry'}:
        raise ValueError('不支持的队列操作。')
    if action == 'retry':
        if index is None or index < 0 or index >= len(batch['items']):
            raise ValueError('请选择需要重做的项目。')
        item = batch['items'][index]
        if item['job'] and item['job']['status'] not in creation.TERMINAL_STATUSES:
            raise ValueError('此项目仍在执行或等待确认。')
        if item['job'] and item['job']['status'] == 'unknown':
            raise ValueError('上次结果未知，请先核对回执。')
        spec = item['spec']
        spec['idempotency_key'] = f'{batch_id}:{index}:{uuid.uuid4().hex}'
        repository.reset_item(encode(spec), batch_id, index)
    if action == 'cancel':
        for item in batch['items']:
            if item['job'] and item['job']['status'] not in creation.TERMINAL_STATUSES:
                await creation.cancel_job(item['job_id'])
    repository.update_status({'pause':'paused','resume':'queued','retry':'queued','cancel':'cancelled'}[action], db.now_iso(), batch_id)
    parent=tasks.get(batch['task_id'])
    if action in {'resume','retry'} and parent and parent['status']=='completed':
        tasks.update(parent['id'],status='waiting_jobs',snapshot={'waiting_batches':list(dict.fromkeys([*parent['snapshot'].get('waiting_batches',[]),batch_id]))})
    return get(batch_id)


async def tick():
    initialize()
    if tasks.automatic_work_paused():
        return
    ids = repository.active_ids()
    for batch_id in ids:
        batch = get(batch_id)
        parent = tasks.get(batch['task_id'])
        if not parent or parent['status'] in {'cancelled','paused'} or db.conversation_deleted(batch['conversation_id']):
            continue
        pending = None
        for item in batch['items']:
            job = item['job']
            state = job['status'] if job else item['status']
            if job:
                repository.update_item_result(state, job.get('error') or '', batch_id, item['ordinal'])
            if state not in {'completed','failed','timed_out','cancelled','unknown'}:
                pending = item
                break
        if pending is None:
            statuses = [(item['job'] or {}).get('status', item['status']) for item in batch['items']]
            status = 'completed' if all(s == 'completed' for s in statuses) else 'needs_attention'
            repository.update_status(status, db.now_iso(), batch_id)
            continue
        if pending['job_id']:
            return  # Do not fill the GPU while a previously submitted item is unresolved.
        busy = repository.creation_busy()
        if busy:
            return
        try:
            job, _ = creation.create_job(CreationJobRequest.model_validate(pending['spec']), source='desktop', conversation_id=batch['conversation_id'], task_id=batch['task_id'])
            repository.attach_job(job['id'], job['status'], batch_id, pending['ordinal'], db.now_iso())
        except ValueError as exc:
            if '已有一项创作任务' in str(exc):
                return
            repository.fail_item(str(exc)[:1000], batch_id, pending['ordinal'])
        return


def ready(batch_ids):
    return all(get(batch_id)['status'] in {'completed','needs_attention','cancelled'} for batch_id in batch_ids)


async def service_loop():
    import logging
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception('批量队列推进失败')
        await asyncio.sleep(2)
