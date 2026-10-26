"""Domain persistence; connections and transaction boundaries stay here."""
from __future__ import annotations

from contextlib import closing, contextmanager
from .. import db

def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS media_assemblies(
          id TEXT PRIMARY KEY,task_id TEXT NOT NULL,spec_json TEXT NOT NULL,status TEXT NOT NULL,
          artifact_id TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')

def get(assembly_id):
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM media_assemblies WHERE id=?',(assembly_id,)).fetchone()
    return row

def create(assembly_id, task_id, spec_json, created_at, updated_at):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO media_assemblies VALUES(?,?,?,\'queued\',\'\',\'\',?,?)',(assembly_id,task_id,spec_json,created_at,updated_at))

def mark_running(assembly_id, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE media_assemblies SET status='running',updated_at=? WHERE id=?",(now,assembly_id))

def mark_completed(assembly_id, artifact_id, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE media_assemblies SET status='completed',artifact_id=?,updated_at=? WHERE id=?",(artifact_id,now,assembly_id))

def requeue(assembly_id, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE media_assemblies SET status='queued',updated_at=? WHERE id=?",(now,assembly_id))

def mark_failed(assembly_id, error, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE media_assemblies SET status='failed',error=?,updated_at=? WHERE id=?",(error,now,assembly_id))

def active_for_task(task_id):
    with closing(db.get_conn()) as conn:
        ids=[row[0] for row in conn.execute("SELECT id FROM media_assemblies WHERE task_id=? AND status IN ('queued','running')",(task_id,))]
    return ids

def cancel_queued(task_id):
    with closing(db.get_conn()) as conn,conn:
        conn.execute("UPDATE media_assemblies SET status='cancelled' WHERE task_id=? AND status='queued'",(task_id,))

def active_assemblies():
    with closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT id,task_id FROM media_assemblies WHERE status IN ('queued','running')").fetchall()
    return rows
