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

    def month_diaries(self, parameters):
        return self._conn.execute('\n            SELECT date, markdown_content\n            FROM diaries\n            WHERE date LIKE ?\n            ORDER BY date\n            ', parameters)
