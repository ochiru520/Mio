"""SQLite persistence and transaction scopes for conversation."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class ConversationRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def public_messages_find_sqlite_master(self):
        """public messages persistence: find sqlite_master."""
        return self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_task_runs'").fetchone()


    def public_messages_find_agent_task_runs(self, run_ids, parameters):
        """public messages persistence: find agent_task_runs."""
        return self._conn.execute('SELECT run_id,task_id FROM agent_task_runs WHERE run_id IN (' + ','.join(('?' for _ in run_ids)) + ')', parameters).fetchall()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield ConversationRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield ConversationRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, ConversationRepository) else ConversationRepository(connection)
