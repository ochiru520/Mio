"""Persistence and atomic work units for terminal records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def ensure_jobs_table(self):
        self.connection.executescript("""CREATE TABLE IF NOT EXISTS terminal_jobs(
          id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
          command TEXT NOT NULL,shell TEXT NOT NULL,cwd TEXT NOT NULL,workspace_revision INTEGER NOT NULL,
          status TEXT NOT NULL,pid INTEGER NOT NULL DEFAULT 0,exit_code INTEGER,duration REAL NOT NULL DEFAULT 0,
          stdout_path TEXT NOT NULL,stderr_path TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',
          evidence_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,finished_at TEXT NOT NULL DEFAULT '');""")
        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(terminal_jobs)')}
        for name, declaration in [('delivered_to', "TEXT NOT NULL DEFAULT ''"), ('interactive', 'INTEGER NOT NULL DEFAULT 0'), ('waiting_input', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                self.connection.execute(f'ALTER TABLE terminal_jobs ADD COLUMN {name} {declaration}')

    def deliver(self, job_id, consumer):
        return self.connection.execute("UPDATE terminal_jobs SET delivered_to=? WHERE id=? AND status!='running' AND delivered_to=''", (consumer, job_id)).rowcount

    def recover_running_jobs(self):
        self.connection.execute("UPDATE terminal_jobs SET status='unknown',error='上次运行中断，结果未知；请核对输出后决定，不自动重放命令。' WHERE status='running'")

    def insert_job(self, parameters):
        self.connection.execute("""INSERT INTO terminal_jobs(id,project_id,task_id,conversation_id,command,shell,cwd,workspace_revision,
              status,stdout_path,stderr_path,evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", parameters)

    def job_for_context(self, parameters):
        return self.connection.execute('SELECT * FROM terminal_jobs WHERE id=?', parameters).fetchone()

    def task_jobs(self, parameters):
        return self.connection.execute('SELECT id,status,command,exit_code,error,workspace_revision,evidence_json,delivered_to,interactive,waiting_input FROM terminal_jobs WHERE task_id=? ORDER BY created_at', parameters).fetchall()

    def job_for_watch(self, parameters):
        return self.connection.execute('SELECT * FROM terminal_jobs WHERE id=?', parameters).fetchone()

    def job_status(self, parameters):
        return self.connection.execute('SELECT status FROM terminal_jobs WHERE id=?', parameters).fetchone()

    def update_job(self, job_id, values):
        self.connection.execute("UPDATE terminal_jobs SET " + ",".join(key + "=?" for key in values) + " WHERE id=?", (*values.values(), job_id))


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
