"""Persistence operations for model runtime; callers retain model and permission decisions."""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def ensure_runtime_tables(self):
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS runtime_operations (
                id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, request_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL, task_id TEXT NOT NULL, mode TEXT NOT NULL,
                purpose TEXT NOT NULL, automatic INTEGER NOT NULL, status TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_operations_request ON runtime_operations(request_id);
            CREATE INDEX IF NOT EXISTS idx_runtime_operations_task ON runtime_operations(task_id,started_at);
            CREATE TABLE IF NOT EXISTS runtime_model_calls (
                id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, request_id TEXT NOT NULL,
                task_id TEXT NOT NULL, conversation_id TEXT NOT NULL, mode TEXT NOT NULL,
                selected_model_id TEXT NOT NULL, actual_model_id TEXT NOT NULL DEFAULT '',
                provider_id TEXT NOT NULL DEFAULT '', reasoning_level TEXT NOT NULL,
                status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
                prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
                cost_yuan REAL, cost_source TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '',
                http_status INTEGER NOT NULL DEFAULT 0, duration_ms REAL
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_model_calls_mode ON runtime_model_calls(mode,started_at);
            CREATE INDEX IF NOT EXISTS idx_runtime_model_calls_request ON runtime_model_calls(request_id);
            CREATE TABLE IF NOT EXISTS runtime_model_policies (
                mode TEXT PRIMARY KEY, policy_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1
            );
        """)

    def model_call_columns(self):
        return self.connection.execute('PRAGMA table_info(runtime_model_calls)').fetchall()

    def add_call_duration(self):
        self.connection.execute('ALTER TABLE runtime_model_calls ADD COLUMN duration_ms REAL')

    def interrupt_operations(self, parameters):
        self.connection.execute("UPDATE runtime_operations SET status='interrupted',finished_at=? WHERE status='running'", parameters)

    def interrupt_model_calls(self, parameters):
        self.connection.execute("UPDATE runtime_model_calls SET status='unknown',finished_at=?,error_code='interrupted' WHERE status='running'", parameters)

    def policies_available(self):
        return self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_model_policies'").fetchone()

    def stored_policy(self, parameters):
        return self.connection.execute('SELECT * FROM runtime_model_policies WHERE mode=?', parameters).fetchone()

    def lock_for_update(self):
        self.connection.execute('BEGIN IMMEDIATE')

    def policy_revision(self, parameters):
        return self.connection.execute('SELECT revision FROM runtime_model_policies WHERE mode=?', parameters).fetchone()

    def save_policy(self, parameters):
        self.connection.execute('INSERT INTO runtime_model_policies VALUES(?,?,?) ON CONFLICT(mode) DO UPDATE SET policy_json=excluded.policy_json,revision=excluded.revision', parameters)

    def calls_available(self):
        return self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_model_calls'").fetchone()

    def insert_model_call(self, parameters):
        self.connection.execute('INSERT INTO runtime_model_calls(id,operation_id,request_id,task_id,conversation_id,mode,selected_model_id,reasoning_level,status,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)', parameters)

    def fail_model_call(self, parameters):
        self.connection.execute('UPDATE runtime_model_calls SET status=?,finished_at=?,error_code=?,http_status=?,duration_ms=? WHERE id=?', parameters)

    def complete_model_call(self, parameters):
        self.connection.execute("UPDATE runtime_model_calls SET status='completed',finished_at=?,actual_model_id=?,provider_id=?,prompt_tokens=?,completion_tokens=?,cost_yuan=?,cost_source=?,http_status=?,duration_ms=? WHERE id=?", parameters)

    def operations_available(self):
        return self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_operations'").fetchone()

    def bind_task(self, parameters):
        self.connection.execute('UPDATE runtime_operations SET task_id=? WHERE id=?', parameters)

    def operation_records_available(self):
        return self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_operations'").fetchone()

    def insert_operation(self, parameters):
        self.connection.execute('INSERT OR IGNORE INTO runtime_operations(id,parent_id,request_id,conversation_id,task_id,mode,purpose,automatic,status,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)', parameters)

    def finish_operation(self, parameters):
        self.connection.execute('UPDATE runtime_operations SET status=?,finished_at=?,error_code=? WHERE id=?', parameters)

    def tasks_available(self):
        return self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='agent_tasks'").fetchone()

    def task_conversation(self, parameters):
        return self.connection.execute('SELECT conversation_id FROM agent_tasks WHERE id=?', parameters).fetchone()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
