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

    def create_preferences(self):
        return self._conn.execute("CREATE TABLE IF NOT EXISTS agent_surface_preferences(id INTEGER PRIMARY KEY CHECK(id=1), preferences_json TEXT NOT NULL DEFAULT '{}')")

    def preferences(self):
        return self._conn.execute('SELECT preferences_json FROM agent_surface_preferences WHERE id=1')

    def save_preferences(self, parameters):
        return self._conn.execute('INSERT OR REPLACE INTO agent_surface_preferences VALUES(1,?)', parameters)
