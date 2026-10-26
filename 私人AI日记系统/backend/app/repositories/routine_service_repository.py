"""SQLite persistence and transaction scopes for routine."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class RoutineRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def initialize_initialize_workspace_routines(self):
        """initialize persistence: initialize workspace_routines."""
        cursor = self._conn.executescript("\n        CREATE TABLE IF NOT EXISTS workspace_routines(\n          id TEXT PRIMARY KEY,name TEXT NOT NULL,project_id TEXT NOT NULL,kind TEXT NOT NULL,\n          config_json TEXT NOT NULL,goal TEXT NOT NULL,template_id TEXT NOT NULL DEFAULT '',\n          enabled INTEGER NOT NULL DEFAULT 0,cursor_json TEXT NOT NULL DEFAULT '{}',\n          next_run_at TEXT NOT NULL DEFAULT '',last_error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS workspace_routine_events(\n          routine_id TEXT NOT NULL,event_key TEXT NOT NULL,task_id TEXT NOT NULL,created_at TEXT NOT NULL,\n          PRIMARY KEY(routine_id,event_key));\n        ")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def save_insert_workspace_routines(self, parameters):
        """save persistence: insert workspace_routines."""
        cursor = self._conn.execute("INSERT INTO workspace_routines VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,?)\n          ON CONFLICT(id) DO UPDATE SET name=excluded.name,project_id=excluded.project_id,kind=excluded.kind,\n          config_json=excluded.config_json,goal=excluded.goal,template_id=excluded.template_id,enabled=excluded.enabled,\n          cursor_json=excluded.cursor_json,next_run_at=excluded.next_run_at,last_error='',updated_at=excluded.updated_at", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def set_enabled_update_workspace_routines(self, parameters):
        """set enabled persistence: update workspace_routines."""
        cursor = self._conn.execute('UPDATE workspace_routines SET enabled=?,next_run_at=?,updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def fire_insert_workspace_routine_events(self, parameters):
        """fire persistence: insert workspace_routine_events."""
        cursor = self._conn.execute('INSERT OR IGNORE INTO workspace_routine_events VALUES(?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def listing_find_workspace_routines(self):
        """listing persistence: find workspace_routines."""
        return self._conn.execute('SELECT * FROM workspace_routines ORDER BY updated_at DESC').fetchall()


    def set_enabled_find_workspace_routines(self, parameters):
        """set enabled persistence: find workspace_routines."""
        return self._conn.execute('SELECT * FROM workspace_routines WHERE id=?', parameters).fetchone()


    def fire_find_workspace_routine_events(self, parameters):
        """fire persistence: find workspace_routine_events."""
        return self._conn.execute('SELECT 1 FROM workspace_routine_events WHERE routine_id=? AND event_key=?', parameters).fetchone()


    def tick_update_workspace_routines(self, parameters):
        """tick persistence: update workspace_routines."""
        cursor = self._conn.execute("UPDATE workspace_routines SET last_error='' WHERE id=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def tick_update_workspace_routines_2(self, parameters):
        """tick persistence: update workspace_routines."""
        cursor = self._conn.execute('UPDATE workspace_routines SET last_error=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def tick_update_workspace_routines_3(self, parameters):
        """tick persistence: update workspace_routines."""
        cursor = self._conn.execute('UPDATE workspace_routines SET cursor_json=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def tick_update_workspace_routines_4(self, parameters):
        """tick persistence: update workspace_routines."""
        cursor = self._conn.execute("UPDATE workspace_routines SET enabled=0,next_run_at='' WHERE id=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def tick_update_workspace_routines_5(self, parameters):
        """tick persistence: update workspace_routines."""
        cursor = self._conn.execute('UPDATE workspace_routines SET next_run_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def tick_find_agent_tasks(self, parameters):
        """tick persistence: find agent_tasks."""
        return self._conn.execute("SELECT t.id,t.updated_at FROM agent_tasks t JOIN workspace_conversations c ON c.conversation_id=t.conversation_id WHERE c.project_id=? AND t.status='completed' AND t.updated_at>? AND t.source!='routine'", parameters).fetchall()


    def tick_find_creation_jobs(self, parameters):
        """tick persistence: find creation_jobs."""
        return self._conn.execute("SELECT j.id,j.updated_at FROM creation_jobs j JOIN workspace_conversations c ON c.conversation_id=j.conversation_id WHERE c.project_id=? AND j.status='completed' AND j.updated_at>? AND NOT EXISTS(SELECT 1 FROM agent_tasks t WHERE t.conversation_id=j.conversation_id AND t.source='routine')", parameters).fetchall()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield RoutineRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield RoutineRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, RoutineRepository) else RoutineRepository(connection)
