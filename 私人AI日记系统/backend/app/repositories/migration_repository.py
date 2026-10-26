"""Version ledger and transactional migration execution."""
from contextlib import closing, contextmanager

from .. import db


class MigrationRepository:
    def __init__(self, connection):
        self._connection = connection

    def initialize(self):
        self._connection.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)''')
        self._connection.commit()

    def applied(self):
        return self._connection.execute(
            'SELECT version, name, applied_at FROM schema_migrations ORDER BY version'
        ).fetchall()

    def apply(self, migration, timestamp_provider):
        with self._connection:
            migration.apply(self._connection)
            applied_at = timestamp_provider()
            self._connection.execute(
                'INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)',
                (migration.version, migration.name, applied_at),
            )
        return applied_at


@contextmanager
def ledger():
    with closing(db.get_conn()) as connection:
        store = MigrationRepository(connection)
        store.initialize()
        yield store
