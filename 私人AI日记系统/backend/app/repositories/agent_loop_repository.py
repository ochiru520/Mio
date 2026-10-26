"""Persistence and atomic work units for agent loop records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def latest_creation_id(self, parameters):
        return self.connection.execute('SELECT id FROM creation_jobs WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1', parameters).fetchone()

    def recent_user_messages(self, parameters):
        return self.connection.execute("""
            SELECT id, content, created_at
            FROM messages
            WHERE conversation_id = ? AND role = 'user' AND id < ?
            ORDER BY id DESC
            LIMIT 16
            """, parameters).fetchall()

    def recent_creation_jobs(self, parameters):
        return self.connection.execute('SELECT id,media_type,workflow_id,status FROM creation_jobs WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 5', parameters).fetchall()

    def creation_after_message(self, parameters):
        return self.connection.execute("""
                SELECT id FROM creation_jobs
                WHERE conversation_id = ? AND created_at >= ?
                  AND status NOT IN ('failed', 'cancelled', 'timed_out')
                LIMIT 1
                """, parameters).fetchone()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
