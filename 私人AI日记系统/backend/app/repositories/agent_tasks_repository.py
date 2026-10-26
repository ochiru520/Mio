"""Persistence for agent tasks. Schema is applied by db.init_db."""
from __future__ import annotations
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
import json
from .. import db
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_tasks (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    original_goal TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    model_id TEXT NOT NULL,
    reasoning_level TEXT NOT NULL,
    allowed_tools_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    waiting_jobs_json TEXT NOT NULL DEFAULT '[]',
    spent_yuan REAL NOT NULL DEFAULT 0,
    budget_yuan REAL NOT NULL DEFAULT 2,
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_task_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_conversation ON agent_tasks(conversation_id, updated_at);
CREATE TABLE IF NOT EXISTS agent_task_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    settings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_task_notifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, title TEXT NOT NULL, actionable INTEGER NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0, resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, resolved_at TEXT NOT NULL DEFAULT '', presented_at TEXT NOT NULL DEFAULT '');
"""


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with closing(db.get_conn()) as conn, conn:
        yield conn


def get(task_id: str):
    with closing(db.get_conn()) as conn:
        return conn.execute('SELECT * FROM agent_tasks WHERE id=?', (task_id,)).fetchone()


def for_run(run_id: str):
    with closing(db.get_conn()) as conn:
        return conn.execute('SELECT t.* FROM agent_tasks t JOIN agent_task_runs r ON r.task_id=t.id WHERE r.run_id=?', (run_id,)).fetchone()


def listing(conversation_id: str, limit: int, states: tuple[str, ...], offset: int, oldest: bool):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM agent_tasks WHERE (?='' OR conversation_id=?)" + (' AND status IN (' + ','.join(('?' for _ in states)) + ')' if states else '') + ' ORDER BY updated_at ' + ('ASC' if oldest else 'DESC') + ',id LIMIT ? OFFSET ?', (conversation_id, conversation_id, *states, max(1, min(limit, 200)), max(0, offset))).fetchall()


def settings(conn: sqlite3.Connection):
    row = conn.execute('SELECT settings_json FROM agent_task_settings WHERE id=1').fetchone()
    return json.loads(row[0]) if row else {}


def set_settings(conn: sqlite3.Connection, result) -> None:
    conn.execute('INSERT INTO agent_task_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET settings_json=excluded.settings_json', (_json(result),))


def create(values) -> None:
    with transaction() as conn:
        conn.execute('INSERT INTO agent_tasks(id,conversation_id,source,status,original_goal,snapshot_json,model_id,reasoning_level,allowed_tools_json,context_json,budget_yuan,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', values)


def start(task_id: str, snapshot, model_id, reasoning_level, allowed_tools, context, stamp: str) -> None:
    with transaction() as conn:
        conn.execute("UPDATE agent_tasks SET status='running',snapshot_json=?,model_id=?,reasoning_level=?,allowed_tools_json=?,context_json=?,revision=revision+1,updated_at=? WHERE id=?", (_json(snapshot), model_id, reasoning_level, _json(allowed_tools), _json(context), stamp, task_id))


def link_run(run_id: str, task_id: str, request_id: str) -> None:
    with transaction() as conn:
        conn.execute('INSERT INTO agent_task_runs VALUES(?,?,?)', (run_id, task_id, request_id))


def update(task_id: str, fields, cost: float) -> None:
    if set(fields) - {'updated_at', 'status', 'snapshot_json', 'waiting_jobs_json'}:
        raise ValueError('Unsupported task columns')
    with transaction() as conn:
        conn.execute('UPDATE agent_tasks SET spent_yuan=spent_yuan+?,' + ','.join((key + '=?' for key in fields)) + ' WHERE id=?', (max(0, cost), *fields.values(), task_id))


def observations(task_id: str):
    with closing(db.get_conn()) as conn:
        return conn.execute("""SELECT s.* FROM agent_run_steps s JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.step_kind='tool_call' ORDER BY s.id DESC LIMIT 100""", (task_id,)).fetchall()


def completed_discovery_results(task_id: str) -> list[str]:
    with closing(db.get_conn()) as conn:
        rows = conn.execute('''SELECT s.result_json FROM agent_run_steps s
            JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.step_kind='tool_call'
            AND s.tool_name='agent_discover_tools' AND s.status IN ('completed','executed')
            ORDER BY s.id''', (task_id,)).fetchall()
    return [row['result_json'] for row in rows]


def cancel_pending(task_id: str) -> None:
    with transaction() as conn:
        run_ids = 'SELECT run_id FROM agent_task_runs WHERE task_id=?'
        conn.execute(f"UPDATE companion_actions SET status='cancelled' WHERE agent_run_id IN ({run_ids}) AND status IN ('needs_confirmation','running')", (task_id,))
        conn.execute(f"UPDATE agent_run_steps SET status='cancelled' WHERE run_id IN ({run_ids}) AND status IN ('needs_confirmation','running')", (task_id,))
        conn.execute(f"UPDATE tool_execution_receipts SET status='cancelled' WHERE agent_run_id IN ({run_ids}) AND status IN ('needs_confirmation','running')", (task_id,))


def active_batches(task_id: str):
    with closing(db.get_conn()) as conn:
        return [row[0] for row in conn.execute("SELECT id FROM creative_batches WHERE task_id=? AND status NOT IN ('completed','cancelled')", (task_id,))]


def unresolved_write(task_id: str) -> bool:
    with closing(db.get_conn()) as conn:
        return bool(conn.execute("""SELECT 1 FROM agent_run_steps s JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.step_kind='tool_call' AND s.permission!='read_only'
            AND s.status IN ('running','unknown') LIMIT 1""", (task_id,)).fetchone())


def unresolved_browser_write(task_id: str) -> bool:
    with closing(db.get_conn()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_browser_pending'").fetchone():
            return False
        return bool(conn.execute("SELECT 1 FROM workspace_browser_pending WHERE task_id=? AND status IN ('running','unknown') LIMIT 1", (task_id,)).fetchone())


def resume(task_id: str, budget: float) -> None:
    with transaction() as conn:
        conn.execute("UPDATE agent_tasks SET budget_yuan=?,status='ready',revision=revision+1,updated_at=? WHERE id=?", (budget, db.now_iso(), task_id))


def recover_interrupted() -> None:
    with transaction() as conn:
        conn.execute("""UPDATE agent_tasks SET status=CASE WHEN EXISTS(
            SELECT 1 FROM agent_run_steps s JOIN agent_task_runs r ON s.run_id=r.run_id
            WHERE r.task_id=agent_tasks.id AND s.step_kind='tool_call' AND s.status IN ('running','unknown')
                AND s.permission!='read_only') THEN 'paused' ELSE 'ready' END,
            updated_at=? WHERE status IN ('running','responding')""", (db.now_iso(),))
        # Claiming a step is not evidence that dispatch started. Only running
        # writes have an unknown effect and block automatic replay.
        conn.execute("UPDATE agent_run_steps SET status='unknown',error='执行中断，副作用结果未知；核对回执后再决定，禁止盲目重放。' WHERE step_kind='tool_call' AND status='running' AND permission!='read_only'")
        conn.execute("UPDATE agent_run_steps SET status='not_started',error='执行中断；该步骤尚未开始，可重新规划。' WHERE step_kind='tool_call' AND status IN ('pending','claimed')")


def ensure_notifications(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS agent_task_notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        dedupe_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, title TEXT NOT NULL, actionable INTEGER NOT NULL,
        dismissed INTEGER NOT NULL DEFAULT 0, resolved INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, resolved_at TEXT NOT NULL DEFAULT '')''')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(agent_task_notifications)')}
    if 'presented_at' not in columns:
        conn.execute("ALTER TABLE agent_task_notifications ADD COLUMN presented_at TEXT NOT NULL DEFAULT ''")


def publish_notification(task_id, conversation_id, state, title, actionable):
    with transaction() as conn:
        conn.execute("UPDATE agent_task_notifications SET resolved=1,resolved_at=? WHERE task_id=? AND actionable=1 AND kind!=? AND resolved=0", (db.now_iso(), task_id, state))
        if title:
            run = conn.execute('SELECT r.run_id FROM agent_task_runs r JOIN agent_runs a ON r.run_id=a.run_id WHERE r.task_id=? ORDER BY a.created_at DESC,a.rowid DESC LIMIT 1', (task_id,)).fetchone()
            dedupe = f'{task_id}:{run[0] if run else "initial"}:{state}'
            conn.execute('INSERT OR IGNORE INTO agent_task_notifications(task_id,conversation_id,dedupe_key,kind,title,actionable,created_at) VALUES(?,?,?,?,?,?,?)', (task_id, conversation_id, dedupe, state, title, int(actionable), db.now_iso()))


def notifications(task_id='', conversation_id=''):
    with transaction() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM agent_task_notifications WHERE (?='' OR task_id=?) AND (?='' OR conversation_id=?) ORDER BY CASE WHEN actionable=1 AND resolved=0 THEN 0 ELSE 1 END,id DESC LIMIT 100", (task_id, task_id, conversation_id, conversation_id))]


def present_notification(task_id, notification_id):
    with transaction() as conn:
        return conn.execute("UPDATE agent_task_notifications SET presented_at=CASE WHEN presented_at='' THEN ? ELSE presented_at END WHERE task_id=? AND id=?", (db.now_iso(), task_id, notification_id)).rowcount


def dismiss_notification(task_id, notification_id):
    with transaction() as conn:
        return conn.execute('UPDATE agent_task_notifications SET dismissed=1 WHERE task_id=? AND id=?', (task_id, notification_id)).rowcount


def background_message(receipt_id):
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT id,request_id FROM messages WHERE delivery_key=?', ('task-result:' + receipt_id,)).fetchone()
        return dict(row) if row else None


def latest_run_status(task_id: str):
    with closing(db.get_conn()) as conn:
        return conn.execute('SELECT a.status FROM agent_runs a JOIN agent_task_runs r ON a.run_id=r.run_id WHERE r.task_id=? ORDER BY a.created_at DESC LIMIT 1', (task_id,)).fetchone()


def stoppable_ids(conversation_id: str):
    with closing(db.get_conn()) as conn:
        return [row[0] for row in conn.execute("SELECT id FROM agent_tasks WHERE (?='' OR conversation_id=?) AND status!='cancelled'", (conversation_id, conversation_id))]


def ready_candidates():
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM agent_tasks WHERE status IN ('ready','waiting_jobs','waiting_confirmation') ORDER BY updated_at,id").fetchall()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def attach_retry(conn: sqlite3.Connection, original_id: str, retry_id: str) -> None:
    """Persist attachment in the same transaction that creates the retry."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_tasks'").fetchone():
        return
    rows = conn.execute("SELECT * FROM agent_tasks WHERE status NOT IN ('completed','cancelled')").fetchall()
    for row in rows:
        jobs = json.loads(row['waiting_jobs_json'])
        if original_id not in jobs:
            continue
        jobs = [retry_id if job == original_id else job for job in jobs]
        snapshot = json.loads(row['snapshot_json'])
        status = row['status']
        if status == 'waiting_user' and snapshot.get('blocker') == '关联生成结果待核对，已停止自动推进。':
            status = 'waiting_jobs'
            snapshot.update(blocker='', next_step='等待人工确认重试及生成结果。')
        conn.execute('UPDATE agent_tasks SET waiting_jobs_json=?,status=?,snapshot_json=?,revision=revision+1,updated_at=? WHERE id=?', (_json(jobs), status, _json(snapshot), db.now_iso(), row['id']))
