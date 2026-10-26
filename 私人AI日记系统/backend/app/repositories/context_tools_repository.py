"""Persistence operations for context tools; callers retain model and permission decisions."""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def ensure_request_table(self):
        self.connection.execute("""CREATE TABLE IF NOT EXISTS context_tool_requests(
            request_key TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}')""")

    def claim_request(self, parameters):
        return self.connection.execute("INSERT OR IGNORE INTO context_tool_requests(request_key,conversation_id,status) VALUES(?,?,'running')", parameters).rowcount

    def stored_request(self, parameters):
        return self.connection.execute('SELECT status,result_json FROM context_tool_requests WHERE request_key=?', parameters).fetchone()

    def cancel_request(self, parameters):
        self.connection.execute("UPDATE context_tool_requests SET status='cancelled' WHERE request_key=?", parameters)

    def finish_request(self, parameters):
        self.connection.execute('UPDATE context_tool_requests SET status=?,result_json=? WHERE request_key=?', parameters)


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
