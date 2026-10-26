"""Persistence operations for agent protocol history; callers retain model and permission decisions."""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def protocol_turns(self, parameters):
        return self.connection.execute("""SELECT h.content_json FROM agent_run_messages h JOIN agent_task_runs r ON r.run_id=h.run_id
            WHERE r.task_id=? AND h.role='protocol_turn' ORDER BY h.id DESC""", parameters).fetchall()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
