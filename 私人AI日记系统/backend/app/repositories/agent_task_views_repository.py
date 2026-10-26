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

    def last_response(self, parameters):
        return self._conn.execute("SELECT m.content FROM messages m\n            JOIN agent_task_runs r ON (r.request_id=m.request_id OR r.run_id=m.request_id)\n            WHERE r.task_id=? AND m.conversation_id=? AND m.role='assistant'\n            ORDER BY m.id DESC LIMIT 1", parameters)

    def task_run(self, parameters):
        return self._conn.execute("SELECT run_id FROM agent_task_runs WHERE task_id=? AND (?='' OR run_id=?) ORDER BY rowid DESC LIMIT 1", parameters)

    def reply_citations(self, parameters):
        return self._conn.execute("SELECT m.id,m.created_at,m.attachments_json FROM messages m\n            JOIN agent_task_runs r ON (r.request_id=m.request_id OR r.run_id=m.request_id) AND r.task_id=?\n            WHERE m.role='assistant' ORDER BY m.id DESC LIMIT 50", parameters)
