"""Persistence and atomic work units for code workspace records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def lock_for_update(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def ensure_code_tables(self):
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS code_workspaces(
          project_id TEXT PRIMARY KEY,root TEXT NOT NULL,read_enabled INTEGER NOT NULL DEFAULT 0,
          write_enabled INTEGER NOT NULL DEFAULT 0,execute_enabled INTEGER NOT NULL DEFAULT 0,
          revision INTEGER NOT NULL DEFAULT 1,verification_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS code_changes(
          id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
          workspace_revision INTEGER NOT NULL,changes_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
        """)

    def save_binding(self, parameters):
        self.connection.execute('INSERT OR REPLACE INTO code_workspaces VALUES(?,?,?,?,?,?,?,?)', parameters)

    def bind_project_directory(self, parameters):
        self.connection.execute("""INSERT INTO workspace_project_directories(project_id,path,created_by_mio)
            VALUES(?,?,COALESCE((SELECT created_by_mio FROM workspace_project_directories
                                 WHERE project_id=? AND path=?),0))
            ON CONFLICT(project_id) DO UPDATE SET path=excluded.path,created_by_mio=excluded.created_by_mio""", parameters)

    def insert_change(self, parameters):
        self.connection.execute('INSERT INTO code_changes VALUES(?,?,?,?,?,?,?,?)', parameters)

    def mark_change_applied(self, parameters):
        self.connection.execute("UPDATE code_changes SET status='applied' WHERE id=?", parameters)

    def mark_change_restored(self, parameters):
        self.connection.execute("UPDATE code_changes SET status='restored' WHERE id=?", parameters)

    def project_binding(self, parameters):
        return self.connection.execute('SELECT * FROM code_workspaces WHERE project_id=?', parameters).fetchone()

    def binding_for_update(self, parameters):
        return self.connection.execute('SELECT root,revision FROM code_workspaces WHERE project_id=?', parameters).fetchone()

    def change_by_id(self, parameters):
        return self.connection.execute('SELECT * FROM code_changes WHERE id=?', parameters).fetchone()

    def mark_change_failed(self, parameters):
        self.connection.execute("UPDATE code_changes SET status='failed' WHERE id=?", parameters)


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
