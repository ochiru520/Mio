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

    def has_pending_job(self):
        return self._conn.execute("SELECT 1 FROM creation_jobs WHERE status IN ('created','needs_confirmation','submitted','queued','running','cancel_requested') LIMIT 1")
