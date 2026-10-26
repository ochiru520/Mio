"""SQLite persistence and transaction scopes for wechat history."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class WechatHistoryRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def archive_outbox_insert_messages(self, parameters):
        """archive outbox persistence: insert messages."""
        cursor = self._conn.execute("INSERT OR IGNORE INTO messages\n        (role,content,source,conversation_id,created_at,request_id,delivery_key,attachments_json)\n        VALUES('assistant',?,'wechat',?,?,?,?,?)", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def archive_document_insert_messages(self, parameters):
        """archive document persistence: insert messages."""
        cursor = self._conn.execute("INSERT OR IGNORE INTO messages\n        (role,content,source,conversation_id,created_at,request_id,delivery_key,attachments_json)\n        VALUES('user',?,'wechat',?,?,?,?,?)", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def add_delivery_states_find_messages(self, entries, parameters):
        """add delivery states persistence: find messages."""
        return self._conn.execute("SELECT m.id,o.status,o.error FROM messages m\n            JOIN mobile_task_outbox o ON m.delivery_key = 'wechat-outbox:' || o.id\n            WHERE m.id IN (" + ','.join(('?' for _ in entries)) + ')', parameters).fetchall()


    def conversations_find_messages(self):
        """conversations persistence: find messages."""
        return self._conn.execute("SELECT DISTINCT conversation_id FROM messages WHERE conversation_id LIKE 'wechat_private_%'").fetchall()


    def archive_outbox_find_artifacts(self, parameters):
        """archive outbox persistence: find artifacts."""
        return self._conn.execute('SELECT * FROM artifacts WHERE id=?', parameters).fetchone()


    def archive_outbox_find_sqlite_master(self):
        """archive outbox persistence: find sqlite_master."""
        return self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone()


    def backfill_find_sqlite_master(self, parameters):
        """backfill persistence: find sqlite_master."""
        return self._conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', parameters).fetchone()


    def add_delivery_states_find_sqlite_master(self):
        """add delivery states persistence: find sqlite_master."""
        return self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='mobile_task_outbox'").fetchone()


    def backfill_f_schema(self, table):
        """backfill persistence: f schema."""
        if table not in {'mobile_task_outbox', 'mobile_task_attachments'}:
            raise ValueError('Unknown WeChat backfill source')
        return self._conn.execute(f"SELECT * FROM {table} WHERE self_id LIKE 'wechat:%' ORDER BY created_at").fetchall()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield WechatHistoryRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield WechatHistoryRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, WechatHistoryRepository) else WechatHistoryRepository(connection)
