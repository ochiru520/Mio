"""Domain persistence; connections and shared transaction handles stay here."""
from __future__ import annotations
from contextlib import closing, contextmanager
from .. import db

@contextmanager
def transaction():
    with closing(db.get_conn()) as conn, conn:
        yield conn

def create_owners(conn):
    return conn.execute('''CREATE TABLE IF NOT EXISTS creation_job_owners(
            job_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,run_id TEXT NOT NULL DEFAULT '',
            source_message_id INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''')

def create_task_index(conn):
    return conn.execute('CREATE INDEX IF NOT EXISTS creation_owners_task ON creation_job_owners(task_id)')

def existing_owner(conn, parameters):
    return conn.execute('SELECT task_id FROM creation_job_owners WHERE job_id=?', parameters)

def insert_owner(conn, parameters):
    return conn.execute('INSERT OR IGNORE INTO creation_job_owners VALUES(?,?,?,?,?)', parameters)

def table_names(conn):
    return conn.execute("SELECT name FROM sqlite_master WHERE type='table'")

def exact_owner(conn, parameters):
    return conn.execute('SELECT * FROM creation_job_owners WHERE job_id=?', parameters)

def job_parent(conn, parameters):
    return conn.execute('SELECT conversation_id,parent_job_id FROM creation_jobs WHERE id=?', parameters)

def tool_evidence(conn, parameters):
    return conn.execute('''SELECT DISTINCT t.id FROM agent_run_steps s
                JOIN agent_task_runs r ON r.run_id=s.run_id JOIN agent_tasks t ON t.id=r.task_id
                WHERE t.conversation_id=? AND s.step_kind='tool_call' AND json_valid(s.result_json)
                AND (json_extract(s.result_json,'$.job.id')=? OR json_extract(s.result_json,'$.job_id')=?)''', parameters)

def batch_evidence(conn, parameters):
    return conn.execute('''SELECT DISTINCT b.task_id
                FROM creative_batch_items i JOIN creative_batches b ON b.id=i.batch_id
                WHERE i.job_id=? AND b.conversation_id=?''', parameters)
