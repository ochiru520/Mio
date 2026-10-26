"""SQLite persistence and transaction scopes for knowledge."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class KnowledgeRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def initialize_retrieval(self):
        self._conn.executescript('''CREATE TABLE IF NOT EXISTS knowledge_retrieval_settings(
          id INTEGER PRIMARY KEY CHECK(id=1),config_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS knowledge_vectors(
          chunk_id INTEGER NOT NULL,signature TEXT NOT NULL,content_hash TEXT NOT NULL,
          vector_json TEXT NOT NULL,PRIMARY KEY(chunk_id,signature));''')

    def retrieval_options(self):
        return self._conn.execute('SELECT config_json FROM knowledge_retrieval_settings WHERE id=1').fetchone()

    def save_retrieval_options(self, value):
        self._conn.execute('INSERT OR REPLACE INTO knowledge_retrieval_settings VALUES(1,?)', (value,))

    def semantic_chunks(self, project_id, limit=1000):
        return self._conn.execute('SELECT c.*,d.name,d.path,d.sha256,d.indexed_at FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id WHERE d.project_id=? AND d.status=? ORDER BY c.id DESC LIMIT ?', (project_id, 'ready', limit)).fetchall()

    def vector(self, chunk_id, signature, digest):
        return self._conn.execute('SELECT vector_json FROM knowledge_vectors WHERE chunk_id=? AND signature=? AND content_hash=?', (chunk_id, signature, digest)).fetchone()

    def save_vectors(self, values):
        self._conn.executemany('INSERT OR REPLACE INTO knowledge_vectors VALUES(?,?,?,?)', values)

    def initialize_initialize_knowledge_documents(self):
        """initialize persistence: initialize knowledge_documents."""
        cursor = self._conn.executescript("\n        CREATE TABLE IF NOT EXISTS knowledge_documents(\n          id TEXT PRIMARY KEY,project_id TEXT NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL,\n          sha256 TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',indexed_at TEXT NOT NULL,\n          UNIQUE(project_id,path));\n        CREATE TABLE IF NOT EXISTS knowledge_chunks(\n          id INTEGER PRIMARY KEY AUTOINCREMENT,document_id TEXT NOT NULL,ordinal INTEGER NOT NULL,\n          page INTEGER NOT NULL,line INTEGER NOT NULL,content TEXT NOT NULL);\n        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(tokens,content='',tokenize='unicode61');\n        CREATE TABLE IF NOT EXISTS knowledge_ocr_state(\n          project_id TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,\n          status TEXT NOT NULL,ocr_used INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',\n          updated_at TEXT NOT NULL,PRIMARY KEY(project_id,path));\n        ")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def record_ocr_failure_insert_knowledge_documents(self, parameters):
        """record ocr failure persistence: insert knowledge_documents."""
        cursor = self._conn.execute("INSERT INTO knowledge_documents VALUES(?,?,?,?,?,'failed',?,?)\n          ON CONFLICT(project_id,path) DO UPDATE SET status='failed',error=excluded.error", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def record_ocr_failure_insert_knowledge_ocr_state(self, parameters):
        """record ocr failure persistence: insert knowledge_ocr_state."""
        cursor = self._conn.execute('INSERT OR REPLACE INTO knowledge_ocr_state VALUES(?,?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def index_document_insert_knowledge_documents(self, parameters):
        """index document persistence: insert knowledge_documents."""
        cursor = self._conn.execute("INSERT INTO knowledge_documents VALUES(?,?,?,?,?,'ready','',?) ON CONFLICT(project_id,path)\n          DO UPDATE SET name=excluded.name,sha256=excluded.sha256,status='ready',error='',indexed_at=excluded.indexed_at", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def index_document_insert_knowledge_ocr_state(self, parameters):
        """index document persistence: insert knowledge_ocr_state."""
        cursor = self._conn.execute('INSERT OR REPLACE INTO knowledge_ocr_state VALUES(?,?,?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def index_document_delete_knowledge_chunks(self, parameters):
        """index document persistence: delete knowledge_chunks."""
        cursor = self._conn.execute('DELETE FROM knowledge_chunks WHERE document_id=?', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def list_documents_find_knowledge_documents(self, parameters):
        """list documents persistence: find knowledge_documents."""
        return self._conn.execute("SELECT d.*,COALESCE(o.status,'not_needed') AS ocr_status,\n          COALESCE(o.ocr_used,0) AS ocr_used,COALESCE(o.error,'') AS ocr_error\n          FROM knowledge_documents d LEFT JOIN knowledge_ocr_state o ON o.project_id=d.project_id AND o.path=d.path\n          WHERE d.project_id=? ORDER BY d.indexed_at DESC", parameters).fetchall()


    def index_document_find_knowledge_documents(self, parameters):
        """index document persistence: find knowledge_documents."""
        return self._conn.execute('SELECT * FROM knowledge_documents WHERE project_id=? AND path=?', parameters).fetchone()


    def index_document_find_knowledge_documents_2(self, parameters):
        """index document persistence: find knowledge_documents."""
        return self._conn.execute('SELECT * FROM knowledge_documents WHERE project_id=? AND path=?', parameters).fetchone()


    def index_document_insert_knowledge_fts(self, parameters):
        """index document persistence: insert knowledge_fts."""
        cursor = self._conn.execute("INSERT INTO knowledge_fts(knowledge_fts,rowid,tokens) VALUES('delete',?,?)", parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def index_document_insert_knowledge_chunks(self, parameters):
        """index document persistence: insert knowledge_chunks."""
        cursor = self._conn.execute('INSERT INTO knowledge_chunks(document_id,ordinal,page,line,content) VALUES(?,?,?,?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def index_document_insert_knowledge_fts_2(self, parameters):
        """index document persistence: insert knowledge_fts."""
        cursor = self._conn.execute('INSERT INTO knowledge_fts(rowid,tokens) VALUES(?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def search_find_knowledge_fts(self, parameters):
        """search persistence: find knowledge_fts."""
        return self._conn.execute("SELECT c.*,d.name,d.path,d.sha256,d.indexed_at,\n          COALESCE(o.status,'not_needed') AS ocr_status,COALESCE(o.ocr_used,0) AS ocr_used,COALESCE(o.error,'') AS ocr_error,\n          bm25(knowledge_fts) AS score\n          FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.rowid\n          JOIN knowledge_documents d ON d.id=c.document_id\n          LEFT JOIN knowledge_ocr_state o ON o.project_id=d.project_id AND o.path=d.path AND o.sha256=d.sha256\n          WHERE knowledge_fts MATCH ? AND d.project_id=? ORDER BY score LIMIT ?", parameters).fetchall()


    def source_file_find_knowledge_documents(self, parameters):
        """source file persistence: find knowledge_documents."""
        return self._conn.execute('SELECT * FROM knowledge_documents WHERE id=?', parameters).fetchone()


    def index_document_find_knowledge_chunks(self, parameters):
        """index document persistence: find knowledge_chunks."""
        return self._conn.execute('SELECT COUNT(*),COUNT(DISTINCT page) FROM knowledge_chunks WHERE document_id=?', parameters).fetchone()


    def index_document_find_knowledge_ocr_state(self, parameters):
        """index document persistence: find knowledge_ocr_state."""
        return self._conn.execute('SELECT * FROM knowledge_ocr_state WHERE project_id=? AND path=?', parameters).fetchone()


    def index_document_find_knowledge_chunks_2(self, parameters):
        """index document persistence: find knowledge_chunks."""
        return self._conn.execute('SELECT id,content FROM knowledge_chunks WHERE document_id=?', parameters).fetchall()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield KnowledgeRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield KnowledgeRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, KnowledgeRepository) else KnowledgeRepository(connection)
