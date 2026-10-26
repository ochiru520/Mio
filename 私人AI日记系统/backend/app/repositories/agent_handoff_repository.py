"""Persistence and atomic work units for agent handoff records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def lock_for_update(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def insert_conversation(self, parameters):
        self.connection.execute('INSERT INTO agent_conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)', parameters)

    def insert_task(self, parameters):
        self.connection.execute('INSERT INTO agent_tasks(id,conversation_id,source,status,original_goal,snapshot_json,model_id,reasoning_level,allowed_tools_json,context_json,budget_yuan,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', parameters)

    def insert_task_run(self, parameters):
        self.connection.execute('INSERT INTO agent_task_runs VALUES(?,?,?)', parameters)

    def existing_handoff(self, parameters):
        return self.connection.execute('SELECT t.id,t.conversation_id,t.status FROM agent_tasks t JOIN agent_task_runs r ON r.task_id=t.id WHERE r.run_id=?', parameters).fetchone()

    def deleted_handoff_conversation(self, parameters):
        return self.connection.execute('SELECT 1 FROM deleted_conversations WHERE id IN (?,?)', parameters).fetchone()

    def latest_handoff(self, parameters):
        return self.connection.execute("SELECT id,conversation_id,status FROM agent_tasks WHERE source='chat_handoff' AND json_extract(snapshot_json,'$.source_conversation_id')=? ORDER BY created_at DESC,id DESC LIMIT 1", parameters).fetchone()

    def unbound_mobile_attachments(self, parameters):
        return self.connection.execute("SELECT id,name,path FROM mobile_task_attachments WHERE self_id=? AND user_id=? AND task_id=''", parameters).fetchall()


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
