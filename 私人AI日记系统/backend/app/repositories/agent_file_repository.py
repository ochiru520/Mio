"""Persistence and atomic work units for agent file records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def ensure_roots_table(self):
        self.connection.execute('CREATE TABLE IF NOT EXISTS agent_file_roots(path TEXT PRIMARY KEY)')

    def clear_roots(self):
        self.connection.execute('DELETE FROM agent_file_roots')

    def insert_roots(self, parameters):
        self.connection.executemany('INSERT OR IGNORE INTO agent_file_roots VALUES(?)', parameters)

    def list_roots(self):
        return self.connection.execute('SELECT path FROM agent_file_roots ORDER BY path').fetchall()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
