"""Persistence and atomic work units for desktop control records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def lock_for_update(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def ensure_desktop_tables(self):
        self.connection.executescript("""CREATE TABLE IF NOT EXISTS desktop_permissions(
          project_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,windows_json TEXT NOT NULL DEFAULT '[]',
          executables_json TEXT NOT NULL DEFAULT '[]',revision INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS desktop_observations(
          id TEXT PRIMARY KEY,project_id TEXT NOT NULL,conversation_id TEXT NOT NULL,task_id TEXT NOT NULL,
          permission_revision INTEGER NOT NULL,window_json TEXT NOT NULL,path TEXT NOT NULL,
          sha256 TEXT NOT NULL,created_at TEXT NOT NULL);""")

    def save_permission(self, parameters):
        self.connection.execute('INSERT OR REPLACE INTO desktop_permissions VALUES(?,?,?,?,?,?)', parameters)

    def insert_observation(self, parameters):
        self.connection.execute('INSERT INTO desktop_observations VALUES(?,?,?,?,?,?,?,?,?)', parameters)

    def project_permission(self, parameters):
        return self.connection.execute('SELECT * FROM desktop_permissions WHERE project_id=?', parameters).fetchone()

    def permission_for_update(self, parameters):
        return self.connection.execute('SELECT revision FROM desktop_permissions WHERE project_id=?', parameters).fetchone()

    def observation_for_action(self, parameters):
        return self.connection.execute('SELECT * FROM desktop_observations WHERE id=?', parameters).fetchone()

    def observation_for_read(self, parameters):
        return self.connection.execute('SELECT * FROM desktop_observations WHERE id=?', parameters).fetchone()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
