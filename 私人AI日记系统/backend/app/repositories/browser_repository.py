"""Persistence and atomic work units for browser records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def ensure_browser_tables(self):
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS workspace_browser_config(project_id TEXT PRIMARY KEY,hosts_json TEXT NOT NULL,allow_public INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS workspace_browser_pending(
          id TEXT PRIMARY KEY,task_id TEXT NOT NULL,page_id TEXT NOT NULL,url TEXT NOT NULL,
          fingerprint TEXT NOT NULL,action_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,result_json TEXT NOT NULL DEFAULT '{}');
        """)

    def recover_previews(self):
        self.connection.execute("UPDATE workspace_browser_pending SET status=CASE WHEN status='running' THEN 'unknown' ELSE 'expired' END WHERE status IN ('pending','running')")

    def save_project_config(self, parameters):
        self.connection.execute('INSERT OR REPLACE INTO workspace_browser_config VALUES(?,?,?)', parameters)

    def reject_preview(self, parameters):
        self.connection.execute("UPDATE workspace_browser_pending SET status='rejected' WHERE id=?", parameters)

    def finish_preview(self, parameters):
        self.connection.execute('UPDATE workspace_browser_pending SET status=?,result_json=? WHERE id=?', parameters)

    def expire_project_page_previews(self, parameters):
        self.connection.execute("UPDATE workspace_browser_pending SET status='expired' WHERE page_id=? AND status='pending'", parameters)

    def project_config(self, parameters):
        return self.connection.execute('SELECT * FROM workspace_browser_config WHERE project_id=?', parameters).fetchone()

    def insert_preview(self, parameters):
        self.connection.execute('INSERT INTO workspace_browser_pending VALUES(?,?,?,?,?,?,?, ?,?)', parameters)

    def pending_previews(self, parameters):
        return self.connection.execute("SELECT * FROM workspace_browser_pending WHERE status='pending' AND (?='' OR task_id=?) ORDER BY created_at", parameters).fetchall()

    def pending_preview(self, parameters):
        return self.connection.execute("SELECT * FROM workspace_browser_pending WHERE id=? AND status='pending'", parameters).fetchone()

    def expire_preview(self, parameters):
        self.connection.execute("UPDATE workspace_browser_pending SET status='expired' WHERE id=?", parameters)

    def claim_preview(self, parameters):
        return self.connection.execute("UPDATE workspace_browser_pending SET status='running' WHERE id=? AND status='pending'", parameters).rowcount

    def expire_page_previews(self, parameters):
        self.connection.execute("UPDATE workspace_browser_pending SET status='expired' WHERE page_id=? AND status='pending'", parameters)


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
