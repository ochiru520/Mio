"""User-configured event triggers enqueue goals; the model chooses the steps."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import closing
from datetime import datetime, timedelta

from . import db, agent_task_service as tasks, workspace_service as workspace, agent_file_service as files


def initialize():
    workspace.initialize()
    with closing(db.get_conn()) as conn, conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS workspace_routines(
          id TEXT PRIMARY KEY,name TEXT NOT NULL,project_id TEXT NOT NULL,kind TEXT NOT NULL,
          config_json TEXT NOT NULL,goal TEXT NOT NULL,template_id TEXT NOT NULL DEFAULT '',
          enabled INTEGER NOT NULL DEFAULT 0,cursor_json TEXT NOT NULL DEFAULT '{}',
          next_run_at TEXT NOT NULL DEFAULT '',last_error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_routine_events(
          routine_id TEXT NOT NULL,event_key TEXT NOT NULL,task_id TEXT NOT NULL,created_at TEXT NOT NULL,
          PRIMARY KEY(routine_id,event_key));
        ''')


def listing():
    initialize()
    with closing(db.get_conn()) as conn:
        rows = [dict(row) for row in conn.execute('SELECT * FROM workspace_routines ORDER BY updated_at DESC')]
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
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''INSERT INTO workspace_routines VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,?)
          ON CONFLICT(id) DO UPDATE SET name=excluded.name,project_id=excluded.project_id,kind=excluded.kind,
          config_json=excluded.config_json,goal=excluded.goal,template_id=excluded.template_id,enabled=excluded.enabled,
          cursor_json=excluded.cursor_json,next_run_at=excluded.next_run_at,last_error='',updated_at=excluded.updated_at''',
          (routine_id,name,project_id,kind,workspace.encode(config),goal,template_id,int(enabled),workspace.encode(cursor),next_run,'',db.now_iso(),db.now_iso()))
    return next(row for row in listing() if row['id'] == routine_id)


def set_enabled(routine_id, enabled):
    initialize()
    with closing(db.get_conn()) as conn, conn:
        row = conn.execute('SELECT * FROM workspace_routines WHERE id=?', (routine_id,)).fetchone()
        if not row: raise ValueError('找不到自动任务。')
        workspace.require_project(row['project_id'], active=True)
        due = next_time(json.loads(row['config_json']), datetime.fromisoformat(db.now_iso())).isoformat(timespec='seconds') if enabled and row['kind'] == 'schedule' else row['next_run_at']
        conn.execute('UPDATE workspace_routines SET enabled=?,next_run_at=?,updated_at=? WHERE id=?', (int(enabled),due,db.now_iso(),routine_id))
    return {'id': routine_id, 'enabled': enabled}


def fire(row, event_key, event):
    with closing(db.get_conn()) as conn:
        if conn.execute('SELECT 1 FROM workspace_routine_events WHERE routine_id=? AND event_key=?', (row['id'],event_key)).fetchone():
            return
    task = workspace.enqueue_goal(row['goal'], row['project_id'], row['template_id'], variables={'event': event},
        request_key=f"routine:{row['id']}:{event_key}", source='routine')
    tasks.update(task['id'], snapshot={'trigger_event': event, 'routine_id': row['id']})
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO workspace_routine_events VALUES(?,?,?,?)', (row['id'],event_key,task['id'],db.now_iso()))


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
                    with closing(db.get_conn()) as conn, conn:
                        if row['config'].get('mode') == 'once':
                            conn.execute("UPDATE workspace_routines SET enabled=0,next_run_at='' WHERE id=?", (row['id'],))
                        else:
                            conn.execute('UPDATE workspace_routines SET next_run_at=? WHERE id=?', (next_time(row['config'],now).isoformat(timespec='seconds'),row['id']))
            elif row['kind'] == 'files':
                snapshot = file_snapshot(row['config'])
                changes=[{'path':path,'version':version,'change':'modified' if path in row['cursor'] else 'created'} for path,version in snapshot.items() if row['cursor'].get(path)!=version]
                if changes:
                    event_key=hashlib.sha256(workspace.encode(changes).encode()).hexdigest()
                    fire(row,event_key,{'changes':changes,'count':len(changes),'root':row['config']['path']})
                with closing(db.get_conn()) as conn, conn:
                    conn.execute('UPDATE workspace_routines SET cursor_json=? WHERE id=?', (workspace.encode(snapshot),row['id']))
            else:
                with closing(db.get_conn()) as conn:
                    if row['kind'] == 'task_completed':
                        events = [dict(r) for r in conn.execute("SELECT t.id,t.updated_at FROM agent_tasks t JOIN workspace_conversations c ON c.conversation_id=t.conversation_id WHERE c.project_id=? AND t.status='completed' AND t.updated_at>? AND t.source!='routine'", (row['project_id'],row['cursor']['since']))]
                    else:
                        events = [dict(r) for r in conn.execute("SELECT j.id,j.updated_at FROM creation_jobs j JOIN workspace_conversations c ON c.conversation_id=j.conversation_id WHERE c.project_id=? AND j.status='completed' AND j.updated_at>? AND NOT EXISTS(SELECT 1 FROM agent_tasks t WHERE t.conversation_id=j.conversation_id AND t.source='routine')", (row['project_id'],row['cursor']['since']))]
                for event in events:
                    fire(row,event['id'],event)
            with closing(db.get_conn()) as conn, conn:
                conn.execute("UPDATE workspace_routines SET last_error='' WHERE id=?", (row['id'],))
        except Exception as exc:
            with closing(db.get_conn()) as conn, conn:
                conn.execute('UPDATE workspace_routines SET last_error=? WHERE id=?', (str(exc)[:1000],row['id']))


async def service_loop():
    while True:
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:raise
        except Exception:
            import logging
            logging.getLogger(__name__).exception('自动任务检查失败')
        await asyncio.sleep(15)
