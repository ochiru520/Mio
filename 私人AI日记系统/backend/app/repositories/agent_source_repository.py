"""Persistence and atomic work units for agent source records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def completed_search_steps(self, parameters):
        return self.connection.execute("""
            SELECT s.id,s.run_id,s.result_json FROM agent_run_steps s
            JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.id>? AND s.step_kind='tool_call'
              AND s.tool_name='knowledge_search' AND s.status='completed'
            ORDER BY s.id LIMIT ?
        """, parameters).fetchall()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
