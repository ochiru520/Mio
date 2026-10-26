"""Domain persistence; connections and shared transaction handles stay here."""
from __future__ import annotations
from contextlib import closing, contextmanager
from .. import db

@contextmanager
def transaction():
    with closing(db.get_conn()) as conn, conn:
        yield conn

def create_notes(conn):
    return conn.execute('CREATE TABLE IF NOT EXISTS workflow_research_notes (workflow_key TEXT PRIMARY KEY, revision TEXT NOT NULL, note_json TEXT NOT NULL, updated_at TEXT NOT NULL)')

def create_evidence(conn):
    return conn.execute('CREATE TABLE IF NOT EXISTS workflow_reference_evidence (url TEXT PRIMARY KEY, sha256 TEXT NOT NULL, read_at TEXT NOT NULL)')

def record_evidence(conn, parameters):
    return conn.execute('INSERT INTO workflow_reference_evidence VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET sha256=excluded.sha256,read_at=excluded.read_at', parameters)

def get_note(conn, parameters):
    return conn.execute('SELECT * FROM workflow_research_notes WHERE workflow_key=?', parameters)

def reference_matches(conn, parameters):
    return conn.execute('SELECT 1 FROM workflow_reference_evidence WHERE url=? AND sha256=?', parameters)

def get_reference(conn, parameters):
    return conn.execute('SELECT * FROM workflow_reference_evidence WHERE url=?', parameters)

def save_note(conn, parameters):
    return conn.execute('INSERT INTO workflow_research_notes VALUES(?,?,?,?) ON CONFLICT(workflow_key) DO UPDATE SET revision=excluded.revision,note_json=excluded.note_json,updated_at=excluded.updated_at', parameters)
