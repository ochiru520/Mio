"""Domain persistence; connections and transaction boundaries stay here."""
from __future__ import annotations

from contextlib import closing, contextmanager
from .. import db

def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS creative_batches(
          id TEXT PRIMARY KEY,task_id TEXT NOT NULL,conversation_id TEXT NOT NULL,name TEXT NOT NULL,
          status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS creative_batch_items(
          batch_id TEXT NOT NULL,ordinal INTEGER NOT NULL,spec_json TEXT NOT NULL,
          job_id TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending',error TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(batch_id,ordinal));
        ''')

def get(batch_id):
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM creative_batches WHERE id=?', (batch_id,)).fetchone()
        items = conn.execute('SELECT * FROM creative_batch_items WHERE batch_id=? ORDER BY ordinal', (batch_id,)).fetchall() if row else []
        return row, items

def create(batch_id, task_id, conversation_id, name, now, records):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO creative_batches VALUES(?,?,?,?,?,?,?)', (batch_id, task_id, conversation_id, name, 'queued', now, now))
        for index, spec_json in records:
            conn.execute('INSERT OR IGNORE INTO creative_batch_items(batch_id,ordinal,spec_json) VALUES(?,?,?)', (batch_id, index, spec_json))

def reset_item(spec_json, batch_id, index):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE creative_batch_items SET spec_json=?,job_id='',status='pending',error='' WHERE batch_id=? AND ordinal=?", (spec_json, batch_id, index))

def update_status(status, now, batch_id):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE creative_batches SET status=?,updated_at=? WHERE id=?', (status, now, batch_id))

def active_ids():
    with closing(db.get_conn()) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM creative_batches WHERE status IN ('queued','running') ORDER BY created_at,id")]
    return ids

def update_item_result(state, error, batch_id, ordinal):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE creative_batch_items SET status=?,error=? WHERE batch_id=? AND ordinal=?', (state, error, batch_id, ordinal))

def creation_busy():
    with closing(db.get_conn()) as conn:
        busy = conn.execute("SELECT 1 FROM creation_jobs WHERE status IN ('created','submitted','queued','running','cancel_requested') LIMIT 1").fetchone()
    return busy

def attach_job(job_id, status, batch_id, ordinal, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE creative_batch_items SET job_id=?,status=? WHERE batch_id=? AND ordinal=?', (job_id, status, batch_id, ordinal))
        conn.execute("UPDATE creative_batches SET status='running',updated_at=? WHERE id=?", (now, batch_id))

def fail_item(error, batch_id, ordinal):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE creative_batch_items SET status='failed',error=? WHERE batch_id=? AND ordinal=?", (error, batch_id, ordinal))
