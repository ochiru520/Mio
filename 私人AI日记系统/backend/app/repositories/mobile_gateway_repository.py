"""SQLite persistence and transaction scopes for mobile gateway."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class MobileGatewayRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def initialize_initialize_mobile_portal_settings(self):
        """initialize persistence: initialize mobile_portal_settings."""
        cursor = self._conn.executescript('CREATE TABLE IF NOT EXISTS mobile_portal_settings(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,port INTEGER NOT NULL);\n          INSERT OR IGNORE INTO mobile_portal_settings VALUES(1,0,8443);\n          CREATE TABLE IF NOT EXISTS mobile_portal_devices(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,expires_at REAL NOT NULL,created_at TEXT NOT NULL,last_seen TEXT NOT NULL);')
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def configure_update_mobile_portal_settings(self, parameters):
        """configure persistence: update mobile_portal_settings."""
        cursor = self._conn.execute('UPDATE mobile_portal_settings SET enabled=?,port=? WHERE id=1', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def revoke_delete_mobile_portal_devices(self, parameters):
        """revoke persistence: delete mobile_portal_devices."""
        cursor = self._conn.execute('DELETE FROM mobile_portal_devices WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def pair_device_insert_mobile_portal_devices(self, parameters):
        """pair device persistence: insert mobile_portal_devices."""
        cursor = self._conn.execute('INSERT INTO mobile_portal_devices VALUES(?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def logout_delete_mobile_portal_devices(self, parameters):
        """logout persistence: delete mobile_portal_devices."""
        cursor = self._conn.execute('DELETE FROM mobile_portal_devices WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def status_find_mobile_portal_devices(self, parameters):
        """status persistence: find mobile_portal_devices."""
        return self._conn.execute('SELECT id,name,created_at,last_seen,expires_at FROM mobile_portal_devices WHERE expires_at>? ORDER BY created_at DESC', parameters).fetchall()


    def session_find_mobile_portal_devices(self, parameters):
        """session persistence: find mobile_portal_devices."""
        return self._conn.execute('SELECT * FROM mobile_portal_devices WHERE token_hash=? AND expires_at>?', parameters).fetchone()


    def protect_update_mobile_portal_devices(self, parameters):
        """protect persistence: update mobile_portal_devices."""
        cursor = self._conn.execute('UPDATE mobile_portal_devices SET last_seen=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def configuration_find_mobile_portal_settings(self):
        """configuration persistence: find mobile_portal_settings."""
        return self._conn.execute('SELECT enabled,port FROM mobile_portal_settings WHERE id=1').fetchone()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield MobileGatewayRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield MobileGatewayRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, MobileGatewayRepository) else MobileGatewayRepository(connection)
