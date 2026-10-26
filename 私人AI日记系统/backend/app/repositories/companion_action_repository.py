"""Persistence and atomic work units for companion action records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def lock_for_update(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def claim_action(self, parameters):
        self.connection.execute("UPDATE companion_actions SET status='running' WHERE id=? AND status='needs_confirmation'", parameters)

    def action_for_approval(self, parameters):
        return self.connection.execute('SELECT * FROM companion_actions WHERE id=?', parameters).fetchone()

    def parent_for_run(self, parameters):
        return self.connection.execute('SELECT t.* FROM agent_tasks t JOIN agent_task_runs r ON t.id=r.task_id WHERE r.run_id=?', parameters).fetchone()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
