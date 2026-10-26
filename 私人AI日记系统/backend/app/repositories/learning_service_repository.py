"""SQLite persistence and transaction scopes for learning."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class LearningRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def initialize_drafts(self):
        self._conn.execute('''CREATE TABLE IF NOT EXISTS learning_drafts(
          session_id TEXT PRIMARY KEY,answers_json TEXT NOT NULL DEFAULT '{}',
          flagged_json TEXT NOT NULL DEFAULT '[]', active_item_id TEXT NOT NULL DEFAULT '',
          revision INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL)''')

    def draft(self, session_id):
        return self._conn.execute('SELECT * FROM learning_drafts WHERE session_id=?', (session_id,)).fetchone()

    def save_draft(self, session_id, answers, flagged, active, expected, now):
        import json
        self._conn.execute('BEGIN IMMEDIATE')
        previous = self.draft(session_id)
        revision = previous['revision'] if previous else 0
        if expected is not None and expected != revision:
            raise ValueError('草稿已在其他窗口更新，请刷新后重试。')
        self._conn.execute('INSERT OR REPLACE INTO learning_drafts VALUES(?,?,?,?,?,?)',
            (session_id, json.dumps(answers, ensure_ascii=False), json.dumps(flagged), active, revision + 1, now))

    def clear_answered_draft(self, session_id, item_id):
        import json
        row = self.draft(session_id)
        if row:
            answers = json.loads(row['answers_json'])
            answers.pop(item_id, None)
            self._conn.execute('UPDATE learning_drafts SET answers_json=?,revision=revision+1 WHERE session_id=?', (json.dumps(answers, ensure_ascii=False), session_id))

    def initialize_initialize_learning_sessions(self):
        """initialize persistence: initialize learning_sessions."""
        cursor = self._conn.executescript("\n        CREATE TABLE IF NOT EXISTS learning_sessions(\n          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,\n          goal TEXT NOT NULL DEFAULT '', source_document_id TEXT NOT NULL DEFAULT '',\n          status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS learning_items(\n          id TEXT PRIMARY KEY, session_id TEXT NOT NULL, prompt TEXT NOT NULL,\n          answer_key TEXT NOT NULL DEFAULT '', source_ref TEXT NOT NULL DEFAULT '',\n          created_at TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS learning_attempts(\n          id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, answer TEXT NOT NULL,\n          score REAL NOT NULL, feedback TEXT NOT NULL, created_at TEXT NOT NULL);\n        CREATE INDEX IF NOT EXISTS idx_learning_sessions_project ON learning_sessions(project_id,updated_at DESC);\n        CREATE INDEX IF NOT EXISTS idx_learning_items_session ON learning_items(session_id,created_at);\n        CREATE INDEX IF NOT EXISTS idx_learning_attempts_item ON learning_attempts(item_id,created_at DESC);\n        ")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def create_session_insert_learning_sessions(self, parameters):
        """create session persistence: insert learning_sessions."""
        cursor = self._conn.execute('INSERT INTO learning_sessions VALUES(?,?,?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def add_item_insert_learning_items(self, parameters):
        """add item persistence: insert learning_items."""
        cursor = self._conn.execute('INSERT INTO learning_items\n            (id,session_id,prompt,answer_key,source_ref,created_at,review_at,review_streak)\n            VALUES(?,?,?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def add_item_update_learning_sessions(self, parameters):
        """add item persistence: update learning_sessions."""
        cursor = self._conn.execute('UPDATE learning_sessions SET updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def answer_insert_learning_attempts(self, parameters):
        """answer persistence: insert learning_attempts."""
        cursor = self._conn.execute('INSERT INTO learning_attempts(item_id,answer,score,feedback,created_at) VALUES(?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def answer_update_learning_items(self, parameters):
        """answer persistence: update learning_items."""
        cursor = self._conn.execute('UPDATE learning_items SET review_at=?, review_streak=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def answer_update_learning_sessions(self, parameters):
        """answer persistence: update learning_sessions."""
        cursor = self._conn.execute('UPDATE learning_sessions SET updated_at=? WHERE id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def initialize_alter_learning_items(self):
        """initialize persistence: alter learning_items."""
        cursor = self._conn.execute("ALTER TABLE learning_items ADD COLUMN review_at TEXT NOT NULL DEFAULT ''")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def initialize_alter_learning_items_2(self):
        """initialize persistence: alter learning_items."""
        cursor = self._conn.execute('ALTER TABLE learning_items ADD COLUMN review_streak INTEGER NOT NULL DEFAULT 0')
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def initialize_inspect_schema(self):
        """initialize persistence: inspect schema."""
        return self._conn.execute('PRAGMA table_info(learning_items)').fetchall()


    def session_find_learning_sessions(self, parameters):
        """session persistence: find learning_sessions."""
        return self._conn.execute("SELECT * FROM learning_sessions WHERE id=? AND (?='' OR project_id=?)", parameters).fetchone()


    def public_find_learning_items(self, parameters):
        """public persistence: find learning_items."""
        return self._conn.execute('SELECT * FROM learning_items WHERE session_id=? ORDER BY created_at,id', parameters).fetchall()


    def public_find_learning_attempts(self, parameters):
        """public persistence: find learning_attempts."""
        return self._conn.execute('SELECT a.* FROM learning_attempts a JOIN learning_items i ON i.id=a.item_id\n            WHERE i.session_id=? ORDER BY a.created_at DESC,a.id DESC', parameters).fetchall()


    def sessions_find_learning_sessions(self, parameters):
        """sessions persistence: find learning_sessions."""
        return self._conn.execute('SELECT id FROM learning_sessions WHERE project_id=? ORDER BY updated_at DESC,id', parameters).fetchall()


    def answer_find_learning_items(self, parameters):
        """answer persistence: find learning_items."""
        return self._conn.execute('SELECT i.*,s.project_id FROM learning_items i JOIN learning_sessions s ON s.id=i.session_id\n            WHERE i.id=? AND s.project_id=?', parameters).fetchone()


    def review_n_learning_items(self, parameters):
        """review persistence: n learning_items."""
        return self._conn.execute("\n            SELECT i.*, s.title AS session_title, s.project_id,\n                   a.answer AS latest_answer, a.score AS latest_score,\n                   a.feedback AS latest_feedback, a.created_at AS answered_at\n            FROM learning_items i\n            JOIN learning_sessions s ON s.id=i.session_id\n            LEFT JOIN learning_attempts a ON a.id=(\n              SELECT a2.id FROM learning_attempts a2 WHERE a2.item_id=i.id ORDER BY a2.created_at DESC,a2.id DESC LIMIT 1)\n            WHERE s.project_id=? AND (i.review_at='' OR i.review_at<=? OR a.score<0.8)\n            ORDER BY CASE WHEN a.score IS NULL THEN 0 ELSE a.score END ASC,\n                     CASE WHEN i.review_at='' THEN '' ELSE i.review_at END ASC, i.created_at ASC\n            LIMIT ?\n        ", parameters).fetchall()


    def create_session_find_knowledge_documents(self, parameters):
        """create session persistence: find knowledge_documents."""
        return self._conn.execute('SELECT 1 FROM knowledge_documents WHERE id=? AND project_id=?', parameters).fetchone()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield LearningRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield LearningRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, LearningRepository) else LearningRepository(connection)
