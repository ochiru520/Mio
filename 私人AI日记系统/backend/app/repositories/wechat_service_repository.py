"""SQLite persistence and transaction scopes for wechat."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class WechatRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def initialize_initialize_wechat_state(self):
        """initialize persistence: initialize wechat_state."""
        cursor = self._conn.executescript("\n            CREATE TABLE IF NOT EXISTS wechat_state (key TEXT PRIMARY KEY,value TEXT NOT NULL);\n            CREATE TABLE IF NOT EXISTS wechat_inbox (\n                event_key TEXT PRIMARY KEY, bot_id TEXT NOT NULL, payload TEXT NOT NULL,\n                status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL\n            );\n        ")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def save_config_insert_wechat_state(self, parameters):
        """save config persistence: insert wechat_state."""
        cursor = self._conn.execute("INSERT OR REPLACE INTO wechat_state VALUES('session',?)", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def forget_delete_wechat_state(self):
        """forget persistence: delete wechat_state."""
        cursor = self._conn.execute('DELETE FROM wechat_state')
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def forget_delete_wechat_inbox(self):
        """forget persistence: delete wechat_inbox."""
        cursor = self._conn.execute('DELETE FROM wechat_inbox')
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def consume_once_update_wechat_inbox(self, parameters):
        """consume once persistence: update wechat_inbox."""
        cursor = self._conn.execute("UPDATE wechat_inbox SET status='handled',payload='' WHERE event_key=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def consume_once_update_wechat_inbox_2(self, parameters):
        """consume once persistence: update wechat_inbox."""
        cursor = self._conn.execute("UPDATE wechat_inbox SET status='ignored',payload='' WHERE event_key=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def consume_once_update_wechat_inbox_3(self, parameters):
        """consume once persistence: update wechat_inbox."""
        cursor = self._conn.execute("UPDATE wechat_inbox SET status='handled',payload='' WHERE event_key=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def config_find_wechat_state(self):
        """config persistence: find wechat_state."""
        return self._conn.execute("SELECT value FROM wechat_state WHERE key='session'").fetchone()


    def poll_once_find_wechat_state(self, parameters):
        """poll once persistence: find wechat_state."""
        return self._conn.execute('SELECT value FROM wechat_state WHERE key=?', parameters).fetchone()


    def poll_once_insert_wechat_inbox(self, parameters):
        """poll once persistence: insert wechat_inbox."""
        cursor = self._conn.execute('INSERT OR IGNORE INTO wechat_inbox(event_key,bot_id,payload,created_at) VALUES(?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def poll_once_insert_wechat_state(self, parameters):
        """poll once persistence: insert wechat_state."""
        cursor = self._conn.execute('INSERT OR REPLACE INTO wechat_state VALUES(?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def deliver_find_wechat_state(self, parameters):
        """deliver persistence: find wechat_state."""
        return self._conn.execute('SELECT value FROM wechat_state WHERE key=?', parameters).fetchone()


    def consume_once_find_wechat_inbox(self, parameters):
        """consume once persistence: find wechat_inbox."""
        return self._conn.execute("SELECT * FROM wechat_inbox WHERE bot_id=? AND status='pending' ORDER BY created_at,rowid LIMIT 100", parameters).fetchall()


    def poll_once_insert_wechat_state_2(self, parameters):
        """poll once persistence: insert wechat_state."""
        cursor = self._conn.execute('INSERT OR REPLACE INTO wechat_state VALUES(?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def consume_once_find_mobile_task_attachments(self, parameters):
        """consume once persistence: find mobile_task_attachments."""
        return self._conn.execute('SELECT * FROM mobile_task_attachments WHERE id=?', parameters).fetchone()


    def history(self):
        """Share this transaction with transcript persistence without owning effects."""
        from .wechat_history_repository import bind
        return bind(self._conn)


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield WechatRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield WechatRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, WechatRepository) else WechatRepository(connection)
