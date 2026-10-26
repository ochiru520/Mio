"""Database integrity reads shared by isolated preview workers."""
from contextlib import closing

from .. import db


def quick_check() -> str:
    with closing(db.get_conn()) as conn:
        return conn.execute('PRAGMA quick_check').fetchone()[0]
