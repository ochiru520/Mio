"""Persisted route projections; query and transaction ownership only."""
from contextlib import closing, contextmanager
from .. import db


@contextmanager
def read():
    with closing(db.get_conn()) as conn:
        yield RouteUnit(conn)


@contextmanager
def transaction():
    with closing(db.get_conn()) as conn, conn:
        yield RouteUnit(conn)


class RouteUnit:
    def __init__(self, conn):
        self._conn = conn

    def changes(self, parameters):
        return self._conn.execute('SELECT id,task_id,status,created_at,changes_json FROM code_changes WHERE project_id=? AND conversation_id=? ORDER BY created_at DESC LIMIT 50', parameters)

    def jobs(self, parameters):
        return self._conn.execute('SELECT id,task_id,command,status,exit_code,duration,created_at FROM terminal_jobs WHERE project_id=? AND conversation_id=? ORDER BY created_at DESC LIMIT 50', parameters)

    def observations(self, parameters):
        return self._conn.execute('SELECT id,window_json,created_at FROM desktop_observations WHERE project_id=? AND conversation_id=? ORDER BY created_at DESC LIMIT 10', parameters)
