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

    def conversation_exists(self, parameters):
        return self._conn.execute('SELECT 1 FROM agent_conversations WHERE id=?', parameters)

    def mark_deleted(self, parameters):
        return self._conn.execute('INSERT OR IGNORE INTO deleted_conversations VALUES(?)', parameters)

    def pending_jobs(self, parameters):
        return self._conn.execute("SELECT id FROM creation_jobs WHERE conversation_id=? AND status NOT IN ('completed','failed','cancelled','timed_out')", parameters)

    def task_ids(self, conversation_id):
        return [row[0] for row in self._conn.execute('SELECT id FROM agent_tasks WHERE conversation_id=?', (conversation_id,))]

    def unmark_deleted(self, parameters):
        return self._conn.execute('DELETE FROM deleted_conversations WHERE id=?', parameters)
