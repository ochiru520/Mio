"""SQLite persistence and transaction scopes for conversation state."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class ConversationStateRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def initialize_create_conversation_sidebar_state(self):
        """initialize persistence: create conversation_sidebar_state."""
        cursor = self._conn.execute("CREATE TABLE IF NOT EXISTS conversation_sidebar_state(\n          conversation_id TEXT PRIMARY KEY,pinned INTEGER NOT NULL DEFAULT 0,\n          unread INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,\n          parent_id TEXT NOT NULL DEFAULT '')")
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def update_insert_conversation_sidebar_state(self, parameters):
        """update persistence: insert conversation_sidebar_state."""
        cursor = self._conn.execute('INSERT OR IGNORE INTO conversation_sidebar_state(conversation_id) VALUES(?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def update_f_conversation_sidebar_state(self, key, parameters):
        """update persistence: f conversation_sidebar_state."""
        if key not in {'pinned', 'unread', 'archived'}:
            raise ValueError('无效侧栏状态。')
        self._conn.execute(f'UPDATE conversation_sidebar_state SET {key}=? WHERE conversation_id=?', parameters)


    def fork_insert_conversation_sidebar_state(self, parameters):
        """fork persistence: insert conversation_sidebar_state."""
        cursor = self._conn.execute('INSERT INTO conversation_sidebar_state(conversation_id,parent_id) VALUES(?,?)', parameters)
        return WriteResult(cursor.rowcount, cursor.lastrowid)


    def states_find_conversation_sidebar_state(self):
        """states persistence: find conversation_sidebar_state."""
        return self._conn.execute('SELECT * FROM conversation_sidebar_state').fetchall()


    def markdown_find_messages(self, parameters):
        """markdown persistence: find messages."""
        return self._conn.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id', parameters).fetchall()


    def fork_find_messages(self, parameters):
        """fork persistence: find messages."""
        return self._conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id', parameters).fetchall()

    def initialize_rewinds(self):
        self._conn.executescript('''CREATE TABLE IF NOT EXISTS conversation_rewinds(
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, anchor_message_id INTEGER NOT NULL,
          mode TEXT NOT NULL, revision INTEGER NOT NULL, archived_messages_json TEXT NOT NULL,
          created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS conversation_invalidated_messages(
          message_id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, rewind_id TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS conversation_invalidated_requests(
          request_id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,rewind_id TEXT NOT NULL);''')

    def rewind(self, conversation_id, anchor_id, mode, content, expected_latest, now, rewind_id):
        import json
        import re
        self._conn.execute('BEGIN IMMEDIATE')
        rows = self.fork_find_messages((conversation_id,))
        if expected_latest is not None and (int(rows[-1]['id']) if rows else 0) != expected_latest:
            raise ValueError('对话已更新，请刷新后再编辑。')
        anchor = next((r for r in rows if r['id'] == anchor_id and r['role'] == 'user'), None)
        if anchor is None:
            raise ValueError('只能从当前有效的用户消息重新回答。')
        summaries = self._conn.execute("SELECT content FROM memories WHERE type='conversation_summary' AND tags=?", (conversation_id,)).fetchall()
        for summary in summaries:
            marker = re.search(r'<!--\s*last_message_id:(\d+)\s*-->', summary['content'])
            if marker is None or anchor_id <= int(marker[1]):
                raise ValueError('该消息已进入压缩摘要，请先清除会话摘要或创建分支后编辑。')
        tail = [r for r in rows if r['id'] > anchor_id or (mode == 'edit' and r['id'] == anchor_id)]
        revision = self._conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM conversation_rewinds WHERE conversation_id=?', (conversation_id,)).fetchone()[0]
        self._conn.execute('INSERT INTO conversation_rewinds VALUES(?,?,?,?,?,?,?)',
            (rewind_id, conversation_id, anchor_id, mode, revision, json.dumps([dict(r) for r in tail], ensure_ascii=False), now))
        self._conn.executemany('INSERT OR IGNORE INTO conversation_invalidated_messages VALUES(?,?,?)',
            [(r['id'], conversation_id, rewind_id) for r in tail])
        requests = {str(row['request_id']) for row in tail if row['request_id']}
        if anchor['request_id']:
            requests.add(str(anchor['request_id']))
        requests.update(str(row[0]) for row in self._conn.execute("SELECT request_id FROM agent_runs WHERE conversation_id=? AND status IN ('running','planning','waiting','cancelled','paused')", (conversation_id,)) if row[0])
        self._conn.executemany('INSERT OR IGNORE INTO conversation_invalidated_requests VALUES(?,?,?)',
            [(request, conversation_id, rewind_id) for request in requests])
        controlled = self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='memory_user_controls'").fetchone()
        protection = ' AND id NOT IN (SELECT memory_id FROM memory_user_controls)' if controlled else ''
        for row in tail:
            self._conn.execute("UPDATE structured_memories SET status='archived',updated_at=? WHERE source_conversation_id=? AND source_message_id=? AND status IN ('active','candidate')" + protection, (now, conversation_id, row['id']))
            self._conn.execute('DELETE FROM messages WHERE id=? AND conversation_id=?', (row['id'], conversation_id))
        self._conn.execute("DELETE FROM memories WHERE type='conversation_summary' AND tags=?", (conversation_id,))
        if self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_context_cache'").fetchone():
            self._conn.execute('DELETE FROM agent_context_cache WHERE task_id IN (SELECT id FROM agent_tasks WHERE conversation_id=?)', (conversation_id,))
        if mode == 'edit':
            cursor = self._conn.execute("INSERT INTO messages(role,content,source,conversation_id,created_at,attachments_json) VALUES('user',?,'history_edit',?,?,?)", (content, conversation_id, now, anchor['attachments_json']))
            anchor_id = cursor.lastrowid
        else:
            self._conn.execute("UPDATE messages SET request_id='' WHERE id=?", (anchor_id,))
        current = self._conn.execute('SELECT * FROM messages WHERE id=?', (anchor_id,)).fetchone()
        return dict(current), [r['id'] for r in tail], revision

    def claim_replay(self, conversation_id, message_id, request_id):
        self._conn.execute('BEGIN IMMEDIATE')
        row = self._conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1', (conversation_id,)).fetchone()
        if row is None or row['id'] != message_id or row['role'] != 'user':
            raise ValueError('重新回答锚点已变化，请刷新后重试。')
        if row['request_id'] and row['request_id'] != request_id:
            raise ValueError('该用户消息已经开始重新回答。')
        self._conn.execute('UPDATE messages SET request_id=? WHERE id=?', (request_id, message_id))
        return dict(row)

    def revision(self, conversation_id):
        return int(self._conn.execute('SELECT COALESCE(MAX(revision),0) FROM conversation_rewinds WHERE conversation_id=?', (conversation_id,)).fetchone()[0])

    def invalidate_messages(self, conversation_id, message_ids, mode, now, rewind_id):
        """Invalidate selected source rows inside the caller's write transaction.

        Unlike a history rewind, task amendment edits must preserve unrelated
        later messages. A fresh revision also invalidates in-flight derivations
        when an older amendment had no separately published message yet.
        """
        import json
        rows = []
        for message_id in dict.fromkeys(message_ids):
            row = self._conn.execute('SELECT * FROM messages WHERE id=? AND conversation_id=?', (message_id, conversation_id)).fetchone()
            if row is not None:
                rows.append(row)
        revision = self.revision(conversation_id) + 1
        self._conn.execute('INSERT INTO conversation_rewinds VALUES(?,?,?,?,?,?,?)', (
            rewind_id, conversation_id, int(message_ids[0]) if message_ids else 0,
            mode, revision, json.dumps([dict(row) for row in rows], ensure_ascii=False), now))
        protected = self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='memory_user_controls'").fetchone()
        protection = ' AND id NOT IN (SELECT memory_id FROM memory_user_controls)' if protected else ''
        for row in rows:
            self._conn.execute('INSERT OR IGNORE INTO conversation_invalidated_messages VALUES(?,?,?)', (row['id'], conversation_id, rewind_id))
            if row['request_id']:
                self._conn.execute('INSERT OR IGNORE INTO conversation_invalidated_requests VALUES(?,?,?)', (row['request_id'], conversation_id, rewind_id))
            self._conn.execute("UPDATE structured_memories SET status='archived',updated_at=? WHERE source_conversation_id=? AND source_message_id=? AND status IN ('active','candidate')" + protection, (now, conversation_id, row['id']))
            self._conn.execute('DELETE FROM messages WHERE id=? AND conversation_id=?', (row['id'], conversation_id))
        self._conn.execute("DELETE FROM memories WHERE type='conversation_summary' AND tags=?", (conversation_id,))
        if self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_context_cache'").fetchone():
            self._conn.execute('DELETE FROM agent_context_cache WHERE task_id IN (SELECT id FROM agent_tasks WHERE conversation_id=?)', (conversation_id,))
        return revision

    def replay_message(self, conversation_id, message_id):
        return self._conn.execute("SELECT * FROM messages WHERE conversation_id=? AND id=? AND role='user'", (conversation_id, message_id)).fetchone()

    def turn_index(self, conversation_id, before_id, limit):
        return self._conn.execute("SELECT id,role,SUBSTR(content,1,220) AS content FROM messages WHERE conversation_id=? AND role='user' AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?", (conversation_id, before_id, before_id, limit)).fetchall()


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield ConversationStateRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield ConversationStateRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, ConversationStateRepository) else ConversationStateRepository(connection)
