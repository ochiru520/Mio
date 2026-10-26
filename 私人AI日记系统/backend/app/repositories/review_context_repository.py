"""Domain persistence; connections and transaction boundaries stay here."""
from __future__ import annotations

from contextlib import closing, contextmanager
from .. import db

def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS review_fact_cache(
            cache_key TEXT PRIMARY KEY,source_hash TEXT NOT NULL,model_fingerprint TEXT NOT NULL,
            summary TEXT NOT NULL,created_at TEXT NOT NULL,last_used_at TEXT NOT NULL)''')

def get_and_touch(key, now):
    with closing(db.get_conn()) as conn, conn:
        row = conn.execute('SELECT summary FROM review_fact_cache WHERE cache_key=?', (key,)).fetchone()
        if row:
            conn.execute('UPDATE review_fact_cache SET last_used_at=? WHERE cache_key=?', (now, key))
        return row

def save(key, source_hash, model_fingerprint, facts, now):
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO review_fact_cache VALUES(?,?,?,?,?,?)', (key,source_hash,model_fingerprint,facts,now,now))
        conn.execute('DELETE FROM review_fact_cache WHERE cache_key IN (SELECT cache_key FROM review_fact_cache ORDER BY last_used_at DESC,cache_key LIMIT -1 OFFSET 2000)')
