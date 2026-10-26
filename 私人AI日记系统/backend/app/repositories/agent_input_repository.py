"""Persistence and atomic work units for agent input records.

Services own permission checks and external effects; this module owns SQL and connection lifetime.
"""
from contextlib import closing, contextmanager, nullcontext
import hashlib
import uuid

from .. import db


class Store:
    def __init__(self, connection):
        self.connection = connection

    def lock_for_update(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def ensure_inputs_table(self):
        self.connection.executescript("""CREATE TABLE IF NOT EXISTS agent_task_inputs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,version INTEGER NOT NULL,
            request_key TEXT NOT NULL,content TEXT NOT NULL,fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,
            consumed_run_id TEXT NOT NULL DEFAULT '',consumed_round INTEGER,consumed_at TEXT NOT NULL DEFAULT '',
            UNIQUE(task_id,version),UNIQUE(task_id,request_key));
            CREATE INDEX IF NOT EXISTS idx_task_inputs_pending ON agent_task_inputs(task_id,consumed_run_id,version);""")
        from .conversation_state_service_repository import bind
        bind(self.connection).initialize_rewinds()
        self.connection.execute('BEGIN IMMEDIATE')
        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(agent_task_inputs)')}
        for name, declaration in [('state', "TEXT NOT NULL DEFAULT 'received'"), ('revision', 'INTEGER NOT NULL DEFAULT 1'), ('updated_at', "TEXT NOT NULL DEFAULT ''"),
                                  ('message_id', 'INTEGER NOT NULL DEFAULT 0'), ('request_fingerprint', "TEXT NOT NULL DEFAULT ''")]:
            if name not in columns:
                self.connection.execute(f'ALTER TABLE agent_task_inputs ADD COLUMN {name} {declaration}')
        self.connection.execute("UPDATE agent_task_inputs SET state='adopted' WHERE consumed_run_id!='' AND state='received'")
        legacy = self.connection.execute("SELECT i.id,i.fingerprint,m.content AS original_content FROM agent_task_inputs i LEFT JOIN messages m ON m.delivery_key='task-input:'||i.id WHERE i.request_fingerprint=''").fetchall()
        for row in legacy:
            fingerprint = hashlib.sha256(row['original_content'].encode()).hexdigest() if row['original_content'] is not None else row['fingerprint']
            self.connection.execute('UPDATE agent_task_inputs SET request_fingerprint=? WHERE id=?', (fingerprint, row['id']))
        self.connection.execute("UPDATE agent_task_inputs SET message_id=COALESCE((SELECT id FROM messages WHERE delivery_key='task-input:'||agent_task_inputs.id),0) WHERE message_id=0")
        # Upgrade pre-fix rows too: their visible history still held the first
        # submitted text after an edit/withdrawal. Preserve the old source in the
        # same invalidation archive used for new revisions, never delete a tail.
        inconsistent = self.connection.execute("SELECT i.*,t.conversation_id FROM agent_task_inputs i JOIN agent_tasks t ON t.id=i.task_id JOIN messages m ON m.delivery_key='task-input:'||i.id AND m.conversation_id=t.conversation_id WHERE i.state='withdrawn' OR m.content!=i.content").fetchall()
        for item in inconsistent:
            self.revise_message(item, item['conversation_id'], item['content'], item['state'] == 'withdrawn', db.now_iso(), uuid.uuid4().hex)

    def insert_input(self, parameters):
        return self.connection.execute('INSERT INTO agent_task_inputs(task_id,version,request_key,content,fingerprint,request_fingerprint,created_at) VALUES(?,?,?,?,?,?,?)', parameters).lastrowid

    def publish_input(self, item, conversation_id, stamp):
        """Publish the persisted version under the same lock as its inbox row."""
        import json
        if item['state'] == 'withdrawn':
            return
        key = f'task-input:{item["id"]}'
        current = self.connection.execute('SELECT id FROM messages WHERE delivery_key=? AND conversation_id=?', (key, conversation_id)).fetchone()
        if current is None:
            if item['message_id']:
                raise ValueError('补充来源历史已失效，不能通过重试恢复旧消息。')
            # Legacy inbox rows may predate atomic publication/message_id. A
            # history rewind must not turn an idempotent retry into resurrection.
            archived = self.connection.execute('SELECT archived_messages_json FROM conversation_rewinds WHERE conversation_id=?', (conversation_id,)).fetchall()
            if any(row.get('delivery_key') == key for entry in archived for row in json.loads(entry[0])):
                raise ValueError('补充来源历史已失效，不能通过重试恢复旧消息。')
            cursor = self.connection.execute("INSERT INTO messages(role,content,source,conversation_id,created_at,delivery_key) VALUES('user',?,'workspace',?,?,?)", (item['content'], conversation_id, stamp, key))
            message_id = cursor.lastrowid
        else:
            message_id = current['id']
        self.connection.execute('UPDATE agent_task_inputs SET message_id=? WHERE id=?', (message_id, item['id']))

    def revise_message(self, item, conversation_id, content, withdrawn, stamp, revision_id):
        from .conversation_state_service_repository import bind
        row = self.connection.execute('SELECT id FROM messages WHERE conversation_id=? AND delivery_key=?', (conversation_id, f'task-input:{item["id"]}')).fetchone()
        if item['message_id'] and (row is None or row['id'] != item['message_id']):
            raise ValueError('补充来源历史已失效，请刷新后再操作。')
        bind(self.connection).invalidate_messages(conversation_id, [row['id']] if row else [],
            'task_input_withdraw' if withdrawn else 'task_input_edit', stamp, revision_id)
        message_id = 0
        if not withdrawn:
            cursor = self.connection.execute("INSERT INTO messages(role,content,source,conversation_id,created_at,delivery_key) VALUES('user',?,'workspace',?,?,?)", (content, conversation_id, stamp, f'task-input:{item["id"]}'))
            message_id = cursor.lastrowid
        self.connection.execute('UPDATE agent_task_inputs SET message_id=? WHERE id=?', (message_id, item['id']))

    def update_task_snapshot(self, parameters):
        self.connection.execute('UPDATE agent_tasks SET snapshot_json=?,revision=revision+1,updated_at=? WHERE id=?', parameters)

    def consume_input(self, parameters):
        self.connection.execute("UPDATE agent_task_inputs SET consumed_run_id=?,consumed_round=?,consumed_at=?,state='adopted' WHERE task_id=? AND id=? AND consumed_run_id='' AND state='received' AND revision=?", parameters)

    def revise_input(self, task_id, input_id, revision, content, fingerprint, state, stamp):
        return self.connection.execute("UPDATE agent_task_inputs SET content=?,fingerprint=?,state=?,revision=revision+1,updated_at=? WHERE task_id=? AND id=? AND revision=? AND consumed_run_id='' AND state='received'", (content, fingerprint, state, stamp, task_id, input_id, revision)).rowcount

    def active_inputs(self, task_id):
        return self.connection.execute("SELECT content FROM agent_task_inputs WHERE task_id=? AND state!='withdrawn' ORDER BY version", (task_id,)).fetchall()

    def task_for_amendment(self, parameters):
        return self.connection.execute('SELECT * FROM agent_tasks WHERE id=?', parameters).fetchone()

    def input_for_request(self, parameters):
        return self.connection.execute('SELECT * FROM agent_task_inputs WHERE task_id=? AND request_key=?', parameters).fetchone()

    def recent_inputs(self, parameters):
        return self.connection.execute('SELECT * FROM agent_task_inputs WHERE task_id=? ORDER BY version DESC LIMIT 20', parameters).fetchall()

    def read_inputs(self, parameters):
        return self.connection.execute("""SELECT * FROM agent_task_inputs WHERE task_id=? AND version>?
            AND (?=0 OR (consumed_run_id='' AND state='received')) ORDER BY version LIMIT ?""", parameters).fetchall()

    def has_pending_input(self, parameters):
        return self.connection.execute("SELECT 1 FROM agent_task_inputs WHERE task_id=? AND consumed_run_id='' AND state='received' LIMIT 1", parameters).fetchone()

    def task_owns_run(self, parameters):
        return self.connection.execute('SELECT 1 FROM agent_task_runs WHERE task_id=? AND run_id=?', parameters).fetchone()

    def next_input_version(self, parameters):
        return self.connection.execute('SELECT COALESCE(MAX(version),0)+1 FROM agent_task_inputs WHERE task_id=?', parameters).fetchone()

    def input_by_id(self, parameters):
        return self.connection.execute('SELECT * FROM agent_task_inputs WHERE id=?', parameters).fetchone()

    def invalidate_unclaimed_approvals(self, task_id):
        run_ids = 'SELECT run_id FROM agent_task_runs WHERE task_id=?'
        for table, column in (('companion_actions', 'agent_run_id'),
                              ('agent_run_steps', 'run_id'),
                              ('tool_execution_receipts', 'agent_run_id')):
            self.connection.execute(f"UPDATE {table} SET status='cancelled' WHERE {column} IN ({run_ids}) AND status='needs_confirmation'", (task_id,))
        if self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_browser_pending'").fetchone():
            self.connection.execute("UPDATE workspace_browser_pending SET status='expired' WHERE task_id=? AND status='pending'", (task_id,))


@contextmanager
def unit_of_work(*, write=True):
    with closing(db.get_conn()) as connection, connection if write else nullcontext():
        yield Store(connection)
