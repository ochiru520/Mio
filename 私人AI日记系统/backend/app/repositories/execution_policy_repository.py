"""Persistence and atomic work units for execution policy records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def lock_for_update(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def ensure_policy_table(self):
        self.connection.execute("""CREATE TABLE IF NOT EXISTS execution_policy(
            id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,
            settings_json TEXT NOT NULL,updated_at TEXT NOT NULL)""")

    def save_policy(self, parameters):
        self.connection.execute('INSERT OR REPLACE INTO execution_policy VALUES(1,?,?,?)', parameters)

    def stored_policy_for_migration(self):
        return self.connection.execute('SELECT * FROM execution_policy WHERE id=1').fetchone()

    def migrate_policy(self, parameters):
        self.connection.execute('UPDATE execution_policy SET revision=?,settings_json=?,updated_at=? WHERE id=1', parameters)

    def stored_policy(self):
        return self.connection.execute('SELECT * FROM execution_policy WHERE id=1').fetchone()

    def policy_revision(self):
        return self.connection.execute('SELECT revision FROM execution_policy WHERE id=1').fetchone()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
