"""SQLite persistence and transaction scopes for diagnostic export."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class DiagnosticExportRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def snapshot_find_creation_jobs(self):
        """snapshot persistence: find creation_jobs."""
        return self._conn.execute('SELECT status,COUNT(*) FROM creation_jobs GROUP BY status').fetchall()


    def behavior_baseline_find_sqlite_master(self, parameters):
        """behavior baseline persistence: find sqlite_master."""
        return self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", parameters).fetchone()


    def behavior_baseline_f_schema(self, table):
        """behavior baseline persistence: f schema."""
        if table not in {'messages', 'agent_tasks', 'agent_task_runs', 'agent_run_steps',
                         'knowledge_documents', 'knowledge_chunks', 'knowledge_ocr_state',
                         'learning_sessions', 'learning_items'}:
            raise ValueError('Table is not allowed in diagnostics')
        return self._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield DiagnosticExportRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield DiagnosticExportRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, DiagnosticExportRepository) else DiagnosticExportRepository(connection)
