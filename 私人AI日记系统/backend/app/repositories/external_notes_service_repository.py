"""SQLite persistence and transaction scopes for external notes."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class ExternalNotesRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def initialize_initialize_workspace_external_roots(self):
        """initialize persistence: initialize workspace_external_roots."""
        cursor = self._conn.executescript("\n            CREATE TABLE IF NOT EXISTS workspace_external_roots(\n              id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,label TEXT NOT NULL DEFAULT '',\n              created_at TEXT NOT NULL,updated_at TEXT NOT NULL\n            );\n            CREATE TABLE IF NOT EXISTS workspace_external_notes(\n              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,root_id TEXT NOT NULL,\n              relative_path TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1,\n              content_sha256 TEXT NOT NULL DEFAULT '',file_mtime_ns INTEGER NOT NULL DEFAULT 0,\n              file_size INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,\n              UNIQUE(project_id,root_id,relative_path)\n            );\n            CREATE INDEX IF NOT EXISTS idx_external_notes_project\n              ON workspace_external_notes(project_id,updated_at DESC);\n            ")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def authorize_insert_workspace_external_roots(self, parameters):
        """authorize persistence: insert workspace_external_roots."""
        cursor = self._conn.execute('INSERT INTO workspace_external_roots(id,path,label,created_at,updated_at)\n               VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET label=excluded.label,updated_at=excluded.updated_at', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def refresh_update_workspace_external_notes(self, parameters):
        """refresh persistence: update workspace_external_notes."""
        cursor = self._conn.execute('UPDATE workspace_external_notes SET title=?,revision=revision+1,content_sha256=?,file_mtime_ns=?,file_size=?,updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def write_update_workspace_external_notes(self, parameters):
        """write persistence: update workspace_external_notes."""
        cursor = self._conn.execute('UPDATE workspace_external_notes SET title=?,revision=revision+1,content_sha256=?,file_mtime_ns=?,file_size=?,updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def link_update_workspace_external_notes(self, parameters):
        """link persistence: update workspace_external_notes."""
        cursor = self._conn.execute('UPDATE workspace_external_notes SET title=?,content_sha256=?,file_mtime_ns=?,file_size=?,updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def link_insert_workspace_external_notes(self, parameters):
        """link persistence: insert workspace_external_notes."""
        cursor = self._conn.execute('INSERT INTO workspace_external_notes VALUES(?,?,?,?,?,?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def root_row_find_workspace_external_roots(self, parameters):
        """root row persistence: find workspace_external_roots."""
        return self._conn.execute('SELECT * FROM workspace_external_roots WHERE id=?', parameters).fetchone()


    def note_row_find_workspace_external_notes(self, parameters):
        """note row persistence: find workspace_external_notes."""
        return self._conn.execute('SELECT * FROM workspace_external_notes WHERE project_id=? AND id=?', parameters).fetchone()


    def roots_find_workspace_external_roots(self):
        """roots persistence: find workspace_external_roots."""
        return self._conn.execute('SELECT * FROM workspace_external_roots ORDER BY path').fetchall()


    def authorize_find_workspace_external_roots(self, parameters):
        """authorize persistence: find workspace_external_roots."""
        return self._conn.execute('SELECT * FROM workspace_external_roots WHERE path=?', parameters).fetchone()


    def list_notes_find_workspace_external_notes(self, parameters):
        """list notes persistence: find workspace_external_notes."""
        return self._conn.execute('SELECT * FROM workspace_external_notes WHERE project_id=? ORDER BY updated_at DESC,id', parameters).fetchall()


    def link_find_workspace_external_notes(self, parameters):
        """link persistence: find workspace_external_notes."""
        return self._conn.execute('SELECT * FROM workspace_external_notes WHERE project_id=? AND root_id=? AND relative_path=?', parameters).fetchone()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield ExternalNotesRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield ExternalNotesRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, ExternalNotesRepository) else ExternalNotesRepository(connection)
