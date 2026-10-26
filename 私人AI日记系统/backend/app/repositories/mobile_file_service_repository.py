"""SQLite persistence and transaction scopes for mobile file."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class MobileFileRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def attachment_authorized_find_mobile_task_attachments(self, parameters):
        """attachment authorized persistence: find mobile_task_attachments."""
        return self._conn.execute('SELECT a.* FROM mobile_task_attachments a JOIN mobile_task_bindings b ON a.task_id=b.task_id AND a.self_id=b.self_id AND a.user_id=b.user_id WHERE a.task_id=? AND a.path=?', parameters).fetchone()


    def store_document_insert_mobile_task_attachments(self, parameters):
        """store document persistence: insert mobile_task_attachments."""
        cursor = self._conn.execute('INSERT INTO mobile_task_attachments VALUES(?,?,?,?,?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def attachment_authorized_find_sqlite_master(self):
        """attachment authorized persistence: find sqlite_master."""
        return self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='mobile_task_attachments'").fetchone()


    def store_document_find_mobile_task_attachments(self, parameters):
        """store document persistence: find mobile_task_attachments."""
        return self._conn.execute('SELECT id FROM mobile_task_attachments WHERE self_id=? AND user_id=? AND file_id=?', parameters).fetchone()


    def receive_insert_mobile_task_attachments(self, parameters):
        """receive persistence: insert mobile_task_attachments."""
        cursor = self._conn.execute('INSERT INTO mobile_task_attachments VALUES(?,?,?,?,?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def store_document_find_mobile_task_attachments_2(self, parameters):
        """store document persistence: find mobile_task_attachments."""
        return self._conn.execute("SELECT COUNT(*) FROM mobile_task_attachments WHERE self_id=? AND user_id=? AND task_id=''", parameters).fetchone()


    def receive_find_mobile_task_attachments(self, parameters):
        """receive persistence: find mobile_task_attachments."""
        return self._conn.execute('SELECT name FROM mobile_task_attachments WHERE self_id=? AND user_id=? AND file_id=?', parameters).fetchone()


    def receive_find_mobile_task_attachments_2(self, parameters):
        """receive persistence: find mobile_task_attachments."""
        return self._conn.execute("SELECT COUNT(*) FROM mobile_task_attachments WHERE self_id=? AND user_id=? AND task_id=''", parameters).fetchone()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield MobileFileRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield MobileFileRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, MobileFileRepository) else MobileFileRepository(connection)
