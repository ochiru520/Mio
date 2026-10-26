"""SQLite integrity probe for environment diagnostics."""
from contextlib import closing
import sqlite3


def quick_check(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])
