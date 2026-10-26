"""SQLite persistence and transaction scopes for mobile task."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class MobileTaskRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def bind_handoff_insert_mobile_task_bindings(self, parameters):
        """bind handoff persistence: insert mobile_task_bindings."""
        cursor = self._conn.execute('INSERT OR IGNORE INTO mobile_task_bindings VALUES(?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def cancel_old_confirmations_update_mobile_task_confirmations(self, parameters):
        """cancel old confirmations persistence: update mobile_task_confirmations."""
        cursor = self._conn.execute('UPDATE mobile_task_confirmations SET used=1 WHERE task_id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def initialize_initialize_mobile_task_bindings(self):
        """initialize persistence: initialize mobile_task_bindings."""
        cursor = self._conn.executescript("\n            CREATE TABLE IF NOT EXISTS mobile_task_bindings (\n                task_id TEXT PRIMARY KEY, self_id TEXT NOT NULL, user_id TEXT NOT NULL,\n                created_at TEXT NOT NULL\n            );\n            CREATE INDEX IF NOT EXISTS mobile_task_owner ON mobile_task_bindings(self_id,user_id,created_at);\n            CREATE TABLE IF NOT EXISTS mobile_task_events (\n                event_key TEXT PRIMARY KEY, self_id TEXT NOT NULL, user_id TEXT NOT NULL,\n                status TEXT NOT NULL, created_at TEXT NOT NULL\n            );\n            CREATE TABLE IF NOT EXISTS mobile_task_outbox (\n                id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT UNIQUE NOT NULL,\n                task_id TEXT NOT NULL DEFAULT '', self_id TEXT NOT NULL, user_id TEXT NOT NULL,\n                payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',\n                error TEXT NOT NULL DEFAULT '', receipt_json TEXT NOT NULL DEFAULT '{}',\n                created_at TEXT NOT NULL, updated_at TEXT NOT NULL\n            );\n            CREATE INDEX IF NOT EXISTS mobile_task_pending ON mobile_task_outbox(status,id);\n            CREATE TABLE IF NOT EXISTS mobile_task_confirmations (\n                token TEXT PRIMARY KEY, task_id TEXT NOT NULL, action_id INTEGER NOT NULL,\n                revision INTEGER NOT NULL, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,\n                UNIQUE(task_id,action_id,revision)\n            );\n            CREATE TABLE IF NOT EXISTS mobile_task_attachments (\n                id TEXT PRIMARY KEY, self_id TEXT NOT NULL, user_id TEXT NOT NULL,\n                file_id TEXT NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,\n                size INTEGER NOT NULL, sha256 TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',\n                created_at TEXT NOT NULL, UNIQUE(self_id,user_id,file_id)\n            );\n        ")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def finish_event_update_mobile_task_events(self, parameters):
        """finish event persistence: update mobile_task_events."""
        cursor = self._conn.execute('UPDATE mobile_task_events SET status=? WHERE event_key=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def insert_insert_mobile_task_outbox(self, parameters):
        """insert persistence: insert mobile_task_outbox."""
        cursor = self._conn.execute('INSERT OR IGNORE INTO mobile_task_outbox(dedupe_key,task_id,self_id,user_id,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def bind_handoff_update_mobile_task_attachments(self, parameters):
        """bind handoff persistence: update mobile_task_attachments."""
        cursor = self._conn.execute("UPDATE mobile_task_attachments SET task_id=? WHERE id=? AND self_id=? AND user_id=? AND task_id=''", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def cancel_old_confirmations(self, task_id):
        """Invalidate the task's actions and codes in the caller's transaction."""
        for table, column in [('companion_actions', 'agent_run_id'), ('agent_run_steps', 'run_id'), ('tool_execution_receipts', 'agent_run_id')]:
            self._conn.execute(
                f"UPDATE {table} SET status='cancelled' WHERE {column} IN "
                "(SELECT run_id FROM agent_task_runs WHERE task_id=?) AND status='needs_confirmation'",
                (task_id,),
            )
        self.cancel_old_confirmations_update_mobile_task_confirmations((task_id,))


    def recover_delivery_update_mobile_task_outbox(self):
        """recover delivery persistence: update mobile_task_outbox."""
        cursor = self._conn.execute("UPDATE mobile_task_outbox SET status='unknown',error='程序重启，之前的发送结果待核对。' WHERE status='sending'")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def recover_delivery_update_mobile_task_events(self):
        """recover delivery persistence: update mobile_task_events."""
        cursor = self._conn.execute("UPDATE mobile_task_events SET status='interrupted' WHERE status='processing'")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def bind_handoff_find_mobile_task_bindings(self, parameters):
        """bind handoff persistence: find mobile_task_bindings."""
        return self._conn.execute('SELECT * FROM mobile_task_bindings WHERE task_id=?', parameters).fetchone()


    def amend_update_agent_tasks(self, parameters):
        """amend persistence: update agent_tasks."""
        cursor = self._conn.execute('UPDATE agent_tasks SET snapshot_json=?,context_json=?,revision=revision+1,updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def amend_update_mobile_task_outbox(self, parameters):
        """amend persistence: update mobile_task_outbox."""
        cursor = self._conn.execute("UPDATE mobile_task_outbox SET status='superseded' WHERE task_id=? AND status='pending' AND dedupe_key LIKE 'report:%'", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def action_prompts_insert_mobile_task_confirmations(self, parameters):
        """action prompts persistence: insert mobile_task_confirmations."""
        cursor = self._conn.execute('INSERT OR IGNORE INTO mobile_task_confirmations VALUES(?,?,?,?,?,0)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def flush_outbox_update_mobile_task_outbox(self, parameters):
        """flush outbox persistence: update mobile_task_outbox."""
        cursor = self._conn.execute('UPDATE mobile_task_outbox SET status=?,error=?,receipt_json=?,updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def claim_event_insert_mobile_task_events(self, parameters):
        """claim event persistence: insert mobile_task_events."""
        return self._conn.execute('INSERT OR IGNORE INTO mobile_task_events VALUES(?,?,?,?,?)', parameters).rowcount


    def owner_tasks_find_mobile_task_bindings(self, parameters):
        """owner tasks persistence: find mobile_task_bindings."""
        return self._conn.execute('SELECT task_id FROM mobile_task_bindings WHERE self_id=? AND user_id=? ORDER BY created_at DESC,task_id DESC LIMIT 100', parameters).fetchall()


    def input_context_find_mobile_task_attachments(self, parameters):
        """input context persistence: find mobile_task_attachments."""
        return self._conn.execute("SELECT id,name,path FROM mobile_task_attachments WHERE self_id=? AND user_id=? AND task_id='' ORDER BY created_at DESC LIMIT 8", parameters).fetchall()


    def amend_update_mobile_task_attachments(self, parameters):
        """amend persistence: update mobile_task_attachments."""
        cursor = self._conn.execute("UPDATE mobile_task_attachments SET task_id=? WHERE id=? AND self_id=? AND user_id=? AND task_id=''", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def status_text_find_mobile_task_outbox(self, parameters):
        """status text persistence: find mobile_task_outbox."""
        return self._conn.execute("SELECT status FROM mobile_task_outbox WHERE task_id=? AND status IN ('unknown','failed') ORDER BY id DESC LIMIT 1", parameters).fetchone()


    def action_prompts_update_mobile_task_confirmations(self, parameters):
        """action prompts persistence: update mobile_task_confirmations."""
        cursor = self._conn.execute('UPDATE mobile_task_confirmations SET token=?,expires_at=? WHERE token=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def confirm_find_mobile_task_confirmations(self, parameters):
        """confirm persistence: find mobile_task_confirmations."""
        return self._conn.execute('SELECT * FROM mobile_task_confirmations WHERE token=?', parameters).fetchone()


    def report_task_find_mobile_task_bindings(self, parameters):
        """report task persistence: find mobile_task_bindings."""
        return self._conn.execute('SELECT * FROM mobile_task_bindings WHERE task_id=?', parameters).fetchone()


    def report_task_find_agent_task_runs(self, parameters):
        """report task persistence: find agent_task_runs."""
        return self._conn.execute('SELECT request_id FROM agent_task_runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1', parameters).fetchone()


    def report_task_find_messages(self, parameters):
        """report task persistence: find messages."""
        return self._conn.execute("SELECT id,content,attachments_json FROM messages WHERE conversation_id=? AND role='assistant' AND request_id=? ORDER BY id DESC LIMIT 1", parameters).fetchone()


    def flush_outbox_find_mobile_task_outbox(self):
        """flush outbox persistence: find mobile_task_outbox."""
        return self._conn.execute("SELECT DISTINCT self_id,user_id FROM mobile_task_outbox WHERE status='pending'").fetchall()


    def claim_event_update_mobile_task_events(self, parameters):
        """claim event persistence: update mobile_task_events."""
        return self._conn.execute("UPDATE mobile_task_events SET status='processing' WHERE event_key=? AND status='interrupted'", parameters).rowcount


    def action_prompts_find_mobile_task_confirmations(self, parameters):
        """action prompts persistence: find mobile_task_confirmations."""
        return self._conn.execute('SELECT * FROM mobile_task_confirmations WHERE task_id=? AND action_id=? AND revision=?', parameters).fetchone()


    def confirm_update_mobile_task_confirmations(self, parameters):
        """confirm persistence: update mobile_task_confirmations."""
        return self._conn.execute('UPDATE mobile_task_confirmations SET used=1 WHERE token=? AND used=0', parameters).rowcount


    def flush_outbox_update_mobile_task_outbox_2(self, parameters):
        """flush outbox persistence: update mobile_task_outbox."""
        cursor = self._conn.execute("UPDATE mobile_task_outbox SET status='failed',error=? WHERE id=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def flush_outbox_update_mobile_task_outbox_3(self, parameters):
        """flush outbox persistence: update mobile_task_outbox."""
        return self._conn.execute("UPDATE mobile_task_outbox SET status='sending',updated_at=? WHERE id=? AND status='pending'", parameters).rowcount


    def flush_outbox_update_mobile_task_outbox_4(self, parameters):
        """flush outbox persistence: update mobile_task_outbox."""
        cursor = self._conn.execute("UPDATE mobile_task_outbox SET status='unknown',error='发送被中断，需核对回执。' WHERE id=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def insert_find_mobile_task_outbox(self, parameters):
        """insert persistence: find mobile_task_outbox."""
        return self._conn.execute('SELECT * FROM mobile_task_outbox WHERE dedupe_key=?', parameters).fetchone()


    def handle_command_update_mobile_task_attachments(self, parameters):
        """handle command persistence: update mobile_task_attachments."""
        cursor = self._conn.execute("UPDATE mobile_task_attachments SET task_id='discarded' WHERE self_id=? AND user_id=? AND task_id=''", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def action_prompts_find_mobile_task_confirmations_2(self, parameters):
        """action prompts persistence: find mobile_task_confirmations."""
        return self._conn.execute('SELECT * FROM mobile_task_confirmations WHERE task_id=? AND action_id=? AND revision=?', parameters).fetchone()


    def flush_outbox_find_mobile_task_outbox_2(self, parameters):
        """flush outbox persistence: find mobile_task_outbox."""
        return self._conn.execute("SELECT * FROM mobile_task_outbox WHERE status='pending' AND self_id=? AND user_id=? ORDER BY id LIMIT 20", parameters).fetchall()


    def flush_outbox_update_mobile_task_outbox_5(self, parameters):
        """flush outbox persistence: update mobile_task_outbox."""
        cursor = self._conn.execute("UPDATE mobile_task_outbox SET status='superseded' WHERE id=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def delivery_loop_find_mobile_task_bindings(self):
        """delivery loop persistence: find mobile_task_bindings."""
        return self._conn.execute('SELECT task_id FROM mobile_task_bindings').fetchall()


    def history(self):
        """Share this transaction with transcript persistence without owning effects."""
        from .wechat_history_repository import bind
        return bind(self._conn)


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield MobileTaskRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield MobileTaskRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, MobileTaskRepository) else MobileTaskRepository(connection)
