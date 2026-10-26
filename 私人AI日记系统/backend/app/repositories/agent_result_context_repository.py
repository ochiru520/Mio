"""Persistence operations for agent result context; callers retain model and permission decisions."""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def tool_result_step(self, parameters):
        return self.connection.execute("""SELECT s.* FROM agent_run_steps s JOIN agent_task_runs r ON r.run_id=s.run_id
                WHERE r.task_id=? AND s.id=? AND s.step_kind='tool_call' """, parameters).fetchone()

    def tool_result_index(self, parameters):
        return self.connection.execute("""SELECT s.id,s.run_id,s.tool_name,s.status,s.error FROM agent_run_steps s
                JOIN agent_task_runs r ON r.run_id=s.run_id WHERE r.task_id=? AND s.step_kind='tool_call' AND s.id>?
                ORDER BY s.id LIMIT ?""", parameters).fetchall()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
