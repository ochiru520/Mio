"""Real isolated storage boundaries for Agent context persistence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from app import db, agent_context_service as context
from app.repositories import agent_context_repository as repository
from test_agent_context_compaction import task
from test_agent_result_recovery import store
from test_runtime_safety_v2 import isolated


def test_legacy_cache_migration_preserves_summary_and_remains_idempotent():
    current_task = task()
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE agent_context_cache (
            task_id TEXT PRIMARY KEY REFERENCES agent_tasks(id) ON DELETE CASCADE,
            summary TEXT NOT NULL DEFAULT '', source_hash TEXT NOT NULL DEFAULT '',
            through_step INTEGER NOT NULL DEFAULT 0, compression_count INTEGER NOT NULL DEFAULT 0,
            failed_hash TEXT NOT NULL DEFAULT '', usage_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL)''')
        conn.execute('INSERT INTO agent_context_cache VALUES(?,?,?,?,?,?,?,?)',
                     (current_task['id'], '旧摘要与不可重放约束', 'source-hash', 9, 2, '',
                      '{"used_tokens":123}', '2026-10-24T09:00:00+08:00'))
    context.initialize()
    context.initialize()
    saved = repository.get_cache(current_task['id'])
    assert saved['summary'] == '旧摘要与不可重放约束' and saved['through_step'] == 9
    assert saved['compression_count'] == 2
    assert saved['last_compressed_at'] == saved['evidence_hash'] == ''
    repository.save_cache(current_task['id'], {**saved, 'evidence_hash': 'new-evidence'},
                          '{"used_tokens":321}', '2026-10-24T10:00:00+08:00')
    usage = context.usage_for_conversation(current_task['conversation_id'])
    assert usage['used_tokens'] == 321
    assert repository.get_cache(current_task['id'])['evidence_hash'] == 'new-evidence'


def test_concurrent_force_requests_share_one_durable_queue_entry():
    conversation = 'desktop_concurrent_context'
    context.initialize()
    barrier = Barrier(4)

    def queue(_index):
        barrier.wait(timeout=10)
        return context.request_force_compression(conversation)['request_id']

    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = list(pool.map(queue, range(4)))
    assert len(set(identifiers)) == 1
    assert repository.pending_compression(conversation) == identifiers[0]
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_context_compression_requests').fetchone()[0] == 1


def test_acknowledging_old_request_cannot_clear_a_new_or_other_conversation_request():
    context.initialize()
    conversation = 'desktop_context_ack'
    first = repository.queue_compression(conversation, 'first', db.now_iso())
    repository.clear_compression(conversation, first)
    repository.queue_compression(conversation, 'second', db.now_iso())
    repository.queue_compression('desktop_other_context', 'first', db.now_iso())
    repository.clear_compression(conversation, first)
    assert repository.pending_compression(conversation) == 'second'
    assert repository.pending_compression('desktop_other_context') == 'first'


def test_action_ledger_with_empty_write_set_keeps_only_durable_action_ids():
    current_task = task()
    read_step = store(current_task, {'text': 'read'}, run_id='context-read')
    action_step = store(current_task, {'text': 'effect'}, run_id='context-effect')
    db.update_agent_run_step(action_step, 'failed', action_id=73, error='实际失败，不能当完成')
    rows, count = repository.action_ledger(current_task['id'], set())
    assert count == 1 and [row['id'] for row in rows] == [action_step]
    assert rows[0]['status'] == 'failed' and read_step != action_step


def test_evidence_connection_closes_when_live_result_validation_fails(monkeypatch):
    current_task = task()
    step_id = store(current_task, {'text': 'read'}, run_id='context-failed-validation')
    connections = []
    original = db.get_conn

    def connect():
        conn = original()
        connections.append(conn)
        return conn

    def invalid(*_args):
        raise ValueError('合成重校验失败')

    monkeypatch.setattr(db, 'get_conn', connect)
    monkeypatch.setattr('app.agent_result_context.current_result', invalid)
    with pytest.raises(ValueError, match='合成重校验失败'):
        context._evidence_fingerprint(current_task['id'], step_id, [])
    assert connections
    for conn in connections:
        with pytest.raises(Exception, match='closed'):
            conn.execute('SELECT 1')
