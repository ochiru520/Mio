"""Storage for persistent default execution capabilities."""
from contextlib import closing, contextmanager

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self, settings_json, updated_at):
        self.connection.execute("""CREATE TABLE IF NOT EXISTS execution_capability_defaults(
            id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
            settings_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        self.connection.execute('INSERT OR IGNORE INTO execution_capability_defaults VALUES(1,1,?,?)', (settings_json, updated_at))

    def lock_for_update(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def stored_defaults(self):
        return self.connection.execute('SELECT * FROM execution_capability_defaults WHERE id=1').fetchone()

    def save_defaults(self, revision, settings_json, updated_at):
        self.connection.execute('UPDATE execution_capability_defaults SET revision=?,settings_json=?,updated_at=? WHERE id=1', (revision, settings_json, updated_at))


@contextmanager
def unit_of_work():
    with closing(db.get_conn()) as connection, connection:
        yield Store(connection)


def stored_defaults(connection):
    return Store(connection).stored_defaults()
