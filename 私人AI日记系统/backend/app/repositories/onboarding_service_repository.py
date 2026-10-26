"""SQLite persistence and transaction scopes for onboarding."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class OnboardingRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def existing_user_evidence_find_memories(self):
        """existing user evidence persistence: find memories."""
        return self._conn.execute("SELECT COUNT(*) FROM memories WHERE type != 'manual'").fetchone()


    def existing_user_evidence_f_schema(self, table):
        """existing user evidence persistence: f schema."""
        if table not in {'messages', 'diaries', 'structured_memories'}:
            raise ValueError('Unknown evidence table')
        return self._conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield OnboardingRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield OnboardingRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, OnboardingRepository) else OnboardingRepository(connection)
