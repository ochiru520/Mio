"""SQLite online snapshots, WAL checkpoints and restore connections."""
from contextlib import closing
from pathlib import Path
import sqlite3

from .. import db


def checkpoint() -> None:
    with closing(db.get_conn()) as connection:
        result = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if result and int(result[0] or 0) != 0:
            connection.execute('PRAGMA wal_checkpoint(PASSIVE)')


def snapshot(target: Path) -> None:
    with closing(db.get_conn()) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)


def restore(source: Path) -> None:
    with closing(sqlite3.connect(source)) as source_connection, closing(db.get_conn()) as destination:
        source_connection.backup(destination)
