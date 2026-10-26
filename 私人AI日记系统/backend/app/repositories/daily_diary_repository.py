"""Domain persistence; connections and transaction boundaries stay here."""
from __future__ import annotations

from contextlib import closing, contextmanager
from .. import db

@contextmanager
def source_dates(cutoff):
    with closing(db.get_conn()) as conn:
        existing = {row[0] for row in conn.execute("SELECT date FROM diaries")}
        dates = {row[0] for row in conn.execute("SELECT DISTINCT date FROM diary_materials WHERE date < ?", (cutoff,))}
        timestamps = conn.execute("SELECT created_at FROM messages WHERE conversation_id NOT LIKE 'qq_group_%'")
        yield existing, dates, timestamps

def attempts_for_configuration(key):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_diary_attempts (
            date TEXT PRIMARY KEY, configuration_key TEXT NOT NULL,
            failures INTEGER NOT NULL, next_retry_at TEXT NOT NULL, error TEXT NOT NULL
        )""")
        rows = conn.execute("SELECT * FROM daily_diary_attempts WHERE configuration_key=?", (key,)).fetchall()
    return rows

def save_attempt(date, configuration_key, failures, retry_at, error):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("INSERT INTO daily_diary_attempts VALUES(?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET configuration_key=excluded.configuration_key,failures=excluded.failures,next_retry_at=excluded.next_retry_at,error=excluded.error",
                     (date, configuration_key, failures, retry_at, error))

def clear_attempt(date):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("DELETE FROM daily_diary_attempts WHERE date=?", (date,))
