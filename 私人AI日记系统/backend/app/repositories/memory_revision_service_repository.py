"""SQLite persistence and transaction scopes for memory revision."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class MemoryRevisionRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def initialize_initialize_memory_user_controls(self):
        """initialize persistence: initialize memory_user_controls."""
        cursor = self._conn.executescript('\n            CREATE TABLE IF NOT EXISTS memory_user_controls (\n                category TEXT NOT NULL, memory_key TEXT NOT NULL,\n                memory_id INTEGER NOT NULL, action TEXT NOT NULL, updated_at TEXT NOT NULL,\n                PRIMARY KEY(category, memory_key)\n            );\n            CREATE TABLE IF NOT EXISTS memory_revision_events (\n                id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL,\n                previous_id INTEGER NOT NULL, action TEXT NOT NULL,\n                created_at TEXT NOT NULL\n            );\n        ')
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def revise_begin_schema(self):
        """revise persistence: begin schema."""
        cursor = self._conn.execute('BEGIN IMMEDIATE')
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def revise_insert_memory_user_controls(self, parameters):
        """revise persistence: insert memory_user_controls."""
        cursor = self._conn.execute('INSERT INTO memory_user_controls VALUES(?,?,?,?,?) ON CONFLICT(category,memory_key) DO UPDATE SET memory_id=excluded.memory_id,action=excluded.action,updated_at=excluded.updated_at', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def revise_insert_memory_revision_events(self, parameters):
        """revise persistence: insert memory_revision_events."""
        cursor = self._conn.execute('INSERT INTO memory_revision_events(memory_id,previous_id,action,created_at) VALUES(?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def revise_update_structured_memories(self, parameters):
        """revise persistence: update structured_memories."""
        cursor = self._conn.execute("UPDATE structured_memories SET status='superseded',superseded_by=?,updated_at=? WHERE category=? AND memory_key=? AND status='active' AND id<>?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def revise_update_structured_memories_2(self, parameters):
        """revise persistence: update structured_memories."""
        cursor = self._conn.execute("UPDATE structured_memories SET status='active',superseded_by=0,confidence=1,last_confirmed_at=?,updated_at=? WHERE id=?", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def revise_update_structured_memories_3(self, parameters):
        """revise persistence: update structured_memories."""
        cursor = self._conn.execute('UPDATE structured_memories SET status=?,updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def control_find_memory_user_controls(self, parameters):
        """control persistence: find memory_user_controls."""
        return self._conn.execute('SELECT * FROM memory_user_controls WHERE category=? AND memory_key=?', parameters).fetchone()


    def revise_find_structured_memories(self, parameters):
        """revise persistence: find structured_memories."""
        return self._conn.execute('SELECT * FROM structured_memories WHERE id=?', parameters).fetchone()


    def insert_corrected_memory(self, data):
        """Copy the stored schema fields so legacy/additive columns survive edits."""
        # The source is the row read by this transaction, never an SQL fragment.
        allowed = {row['name'] for row in self._conn.execute('PRAGMA table_info(structured_memories)')}
        if not set(data).issubset(allowed):
            raise ValueError('Unknown structured memory fields')
        columns = ','.join('"' + key.replace('"', '""') + '"' for key in data)
        return self._conn.execute(
            f"INSERT INTO structured_memories ({columns}) VALUES ({','.join('?' for _ in data)})",
            tuple(data.values()),
        ).lastrowid


    def evidence_find_memory_revision_events(self, parameters):
        """evidence persistence: find memory_revision_events."""
        return self._conn.execute('SELECT * FROM memory_revision_events WHERE memory_id=? OR previous_id=? ORDER BY id DESC', parameters).fetchall()


    def rules_find_memory_user_controls(self):
        """rules persistence: find memory_user_controls."""
        return self._conn.execute('SELECT c.*,m.content,m.status,COALESCE(s.id,0) AS source_message_id FROM memory_user_controls c JOIN structured_memories m ON m.id=c.memory_id LEFT JOIN messages s ON s.id=m.source_message_id AND s.conversation_id=m.source_conversation_id ORDER BY c.updated_at DESC').fetchall()


    def rules_find_structured_memories(self, parameters):
        """rules persistence: find structured_memories."""
        return self._conn.execute('SELECT content FROM structured_memories WHERE category=? AND memory_key=? AND id<>?', parameters).fetchall()


    def upsert_memory_fts(self, memory_id, memory_key, category, content):
        db._upsert_structured_memory_fts(self._conn, memory_id, memory_key, category, content)


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield MemoryRevisionRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield MemoryRevisionRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, MemoryRevisionRepository) else MemoryRevisionRepository(connection)
