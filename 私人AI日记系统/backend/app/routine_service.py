"""User-configured event triggers enqueue goals; the model chooses the steps."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta

from .repositories import routine_service_repository as repository
from . import db, agent_task_service as tasks, workspace_service as workspace, agent_file_service as files


def initialize():
    workspace.initialize()
    with repository.transaction() as conn:
        conn.initialize_initialize_workspace_routines()


def listing():
    initialize()
    with repository.read() as conn:
        rows = [dict(row) for row in conn.listing_find_workspace_routines()]
    for row in rows:
        row['config'] = json.loads(row.pop('config_json'))
        row['cursor'] = json.loads(row.pop('cursor_json'))
    return rows


def file_snapshot(config):
    root = files.resolve_read(config.get('path', ''))
    if not root.is_dir():
        raise ValueError('文件触发需要授权目录。')
    pattern = config.get('pattern') or '*.pdf'
    if '/' in pattern or '\\' in pattern or '..' in pattern:
        raise ValueError('请填写文件名匹配规则，如 *.pdf。')
    result = {}
    for path in root.rglob(pattern):
        if len(result) >= 5000:
            raise ValueError('监控文件过多，请选择更具体的目录。')
        try:
            authorized = files.resolve_read(str(path))
            if authorized.is_file():
                stat = authorized.stat()
                result[str(authorized)] = f'{stat.st_size}:{stat.st_mtime_ns}'
        except (ValueError, OSError):
            continue
    return result


def next_time(config, now, *, require_future=True):
    mode = config.get('mode', 'interval')
    if mode == 'once':
        candidate = datetime.fromisoformat(config.get('date', '') + 'T' + config.get('time', '20:00'))
        if now.tzinfo: candidate = candidate.replace(tzinfo=now.tzinfo)
        if require_future and candidate <= now: raise ValueError('指定时间须晚于当前时间。')
        return candidate
    if mode not in {'interval', 'daily', 'weekly'}: raise ValueError('周期配置无效。')
    if mode == 'weekly' and not 0 <= int(config.get('weekday', 6)) <= 6: raise ValueError('星期配置无效。')
    if mode == 'interval':
        return now + timedelta(minutes=max(1, int(config.get('minutes', 60))))
    hour, minute = map(int, config.get('time', '20:00').split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('时间格式应为 HH:MM。')
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for _ in range(8):
        if candidate > now and (mode == 'daily' or candidate.weekday() == int(config.get('weekday', 6))):
            return candidate
        candidate += timedelta(days=1)
    raise ValueError('周期配置无效。')


def save(name, project_id, kind, config, goal='', template_id='', enabled=False, routine_id=''):
    initialize()
    workspace.require_project(project_id, active=True)
    if not name.strip() or not (goal.strip() or template_id):
        raise ValueError('自动任务需要名称和目标或模板。')
    if kind not in {'schedule','files','task_completed','generation_completed'}:
        raise ValueError('不支持的触发条件。')
    previous = next((row for row in listing() if row['id'] == routine_id), None)
    cursor = previous['cursor'] if previous and previous['kind'] == kind and previous['config'] == config else {}
    now = datetime.fromisoformat(db.now_iso())
    next_run = next_time(config, now, require_future=enabled).isoformat(timespec='seconds') if kind == 'schedule' else ''
    if kind == 'files' and not cursor:
        cursor = file_snapshot(config)  # Existing files are the baseline, not new events.
    if kind in {'task_completed','generation_completed'} and not cursor:
        cursor = {'since': now.isoformat(timespec='seconds')}
    routine_id = routine_id or 'routine_' + uuid.uuid4().hex[:24]
    with repository.transaction() as conn:
        conn.save_insert_workspace_routines((routine_id,name,project_id,kind,workspace.encode(config),goal,template_id,int(enabled),workspace.encode(cursor),next_run,'',db.now_iso(),db.now_iso()))
    return next(row for row in listing() if row['id'] == routine_id)


def set_enabled(routine_id, enabled):
    initialize()
    with repository.transaction() as conn:
        row = conn.set_enabled_find_workspace_routines((routine_id,))
        if not row: raise ValueError('找不到自动任务。')
        workspace.require_project(row['project_id'], active=True)
        due = next_time(json.loads(row['config_json']), datetime.fromisoformat(db.now_iso())).isoformat(timespec='seconds') if enabled and row['kind'] == 'schedule' else row['next_run_at']
        conn.set_enabled_update_workspace_routines((int(enabled),due,db.now_iso(),routine_id))
    return {'id': routine_id, 'enabled': enabled}


def fire(row, event_key, event):
    with repository.read() as conn:
        if conn.fire_find_workspace_routine_events((row['id'],event_key)):
            return
    task = workspace.enqueue_goal(row['goal'], row['project_id'], row['template_id'], variables={'event': event},
        request_key=f"routine:{row['id']}:{event_key}", source='routine')
    tasks.update(task['id'], snapshot={'trigger_event': event, 'routine_id': row['id']})
    with repository.transaction() as conn:
        conn.fire_insert_workspace_routine_events((row['id'],event_key,task['id'],db.now_iso()))


def tick(now=None):
    from .maintenance_service import mutation_scope
    with mutation_scope():
        return _tick(now)


def _tick(now=None):
    initialize()
    if tasks.automatic_work_paused():
        return
    now = now or datetime.fromisoformat(db.now_iso())
    for row in listing():
        if not row['enabled']:
            continue
        try:
            workspace.require_project(row['project_id'], active=True)
            if row['kind'] == 'schedule':
                due = datetime.fromisoformat(row['next_run_at'])
                if now >= due:
                    fire(row, row['next_run_at'], {'scheduled_at':row['next_run_at']})
                    with repository.transaction() as conn:
                        if row['config'].get('mode') == 'once':
                            conn.tick_update_workspace_routines_4((row['id'],))
                        else:
                            conn.tick_update_workspace_routines_5((next_time(row['config'],now).isoformat(timespec='seconds'),row['id']))
            elif row['kind'] == 'files':
                snapshot = file_snapshot(row['config'])
                changes=[{'path':path,'version':version,'change':'modified' if path in row['cursor'] else 'created'} for path,version in snapshot.items() if row['cursor'].get(path)!=version]
                if changes:
                    event_key=hashlib.sha256(workspace.encode(changes).encode()).hexdigest()
                    fire(row,event_key,{'changes':changes,'count':len(changes),'root':row['config']['path']})
                with repository.transaction() as conn:
                    conn.tick_update_workspace_routines_3((workspace.encode(snapshot),row['id']))
            else:
                with repository.read() as conn:
                    if row['kind'] == 'task_completed':
                        events = [dict(r) for r in conn.tick_find_agent_tasks((row['project_id'],row['cursor']['since']))]
                    else:
                        events = [dict(r) for r in conn.tick_find_creation_jobs((row['project_id'],row['cursor']['since']))]
                for event in events:
                    fire(row,event['id'],event)
            with repository.transaction() as conn:
                conn.tick_update_workspace_routines((row['id'],))
        except Exception as exc:
            with repository.transaction() as conn:
                conn.tick_update_workspace_routines_2((str(exc)[:1000],row['id']))


async def service_loop():
    while True:
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:raise
        except Exception:
            import logging
            logging.getLogger(__name__).exception('自动任务检查失败')
        await asyncio.sleep(15)
