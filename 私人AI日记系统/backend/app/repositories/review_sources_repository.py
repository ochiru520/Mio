"""SQLite persistence and transaction scopes for review sources."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class ReviewSourcesRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def ensure_table_n_review_source_snapshots(self):
        """ensure table persistence: n review_source_snapshots."""
        return self._conn.execute('\n            CREATE TABLE IF NOT EXISTS review_source_snapshots (\n                review_kind TEXT NOT NULL,\n                review_key TEXT NOT NULL,\n                source_hash TEXT NOT NULL,\n                source_json TEXT NOT NULL,\n                generated_at TEXT NOT NULL,\n                PRIMARY KEY (review_kind, review_key)\n            )\n            ').fetchall()


    def save_snapshot_n_review_source_snapshots(self, parameters):
        """save snapshot persistence: n review_source_snapshots."""
        return self._conn.execute('\n            INSERT INTO review_source_snapshots(review_kind, review_key, source_hash, source_json, generated_at)\n            VALUES (?, ?, ?, ?, ?)\n            ON CONFLICT(review_kind, review_key) DO UPDATE SET\n                source_hash=excluded.source_hash, source_json=excluded.source_json, generated_at=excluded.generated_at\n            ', parameters).fetchall()


    def status_find_review_source_snapshots(self, parameters):
        """status persistence: find review_source_snapshots."""
        return self._conn.execute('SELECT source_hash, source_json, generated_at FROM review_source_snapshots WHERE review_kind=? AND review_key=?', parameters).fetchone()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield ReviewSourcesRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield ReviewSourcesRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, ReviewSourcesRepository) else ReviewSourcesRepository(connection)
