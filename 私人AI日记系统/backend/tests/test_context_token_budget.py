"""Shared token budgets using synthetic files, history and model transports only."""
import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, context_service as chat, llm, agent_context_service as agent
from app.config import settings
from app.routes.settings import router
from test_agent_context_compaction import task, run, materials, completion
from test_runtime_safety_v2 import isolated


@pytest.mark.parametrize('old_value,expected', [(None, 272000), ('18000', 272000), ('64000', 64000)])
def test_startup_upgrades_only_legacy_environment_default(tmp_path, old_value, expected):
    env = dict(os.environ, MIO_RUNTIME_ROOT=str(tmp_path), MIO_DISABLE_DOTENV='1')
    env.pop('CHAT_CONTEXT_MAX_TOKENS', None)
    env.pop('\ufeffCHAT_CONTEXT_MAX_TOKENS', None)
    if old_value is not None:
        env['CHAT_CONTEXT_MAX_TOKENS'] = old_value
    result = subprocess.run([sys.executable, '-c',
        'from app.config import settings; print(settings.chat_context_max_tokens)'],
        env=env, capture_output=True, text=True, check=True)
    assert int(result.stdout.strip()) == expected


def test_runtime_api_saves_tokens_rejects_bounds_and_preserves_explicit_18000(isolated):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for budget in (272000, 18000, 2000000, 1000):
            result = client.patch('/api/settings/runtime', json={'chat_context_max_tokens': budget})
            assert result.status_code == 200
            assert result.json()['settings']['chat_context_max_tokens'] == budget
            assert config.load_runtime_settings()['chat_context_max_tokens'] == budget
        for budget in (999, 2000001):
            assert client.patch('/api/settings/runtime', json={'chat_context_max_tokens': budget}).status_code == 400
        assert settings.chat_context_max_tokens == 1000


def long_history():
    return [dict(id=i + 1, role='user' if i % 2 == 0 else 'assistant',
                 content='synthetic ' * 800, created_at='2026-10-24T10:00:00+08:00')
            for i in range(24)]


def test_legacy_character_limit_cannot_trim_or_trigger_chat_compression(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 272000)
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_chars', 4000)
    monkeypatch.setattr(chat, 'build_periodic_memory_context', lambda *args: '')
    rows = long_history()
    with patch.object(chat, '_compress_old_messages', AsyncMock()) as compress:
        result = asyncio.run(chat.build_chat_context('desktop_budget', rows))
    compress.assert_not_awaited()
    assert result.raw_messages == rows
    assert result.used_chars > 18000 and result.used_tokens > 18000
    assert result.max_tokens == 272000 and not result.compression_triggered
    snapshot = chat.build_chat_context_snapshot('desktop_budget', rows)
    assert snapshot.raw_messages == rows and snapshot.max_tokens == 272000
    usage = chat.preview_chat_context_usage('desktop_budget', rows)
    assert usage['percent'] == usage['token_percent'] == round(usage['used_tokens'] / 272000 * 100, 1)
    assert usage['estimated'] and not usage['warning']


def test_fast_snapshot_uses_same_token_budget_and_no_default_turn_cap(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 272000)
    for name in ('build_temporal_evidence_context', 'build_recent_user_timeline_context',
                 '_recent_diary_context', 'build_structured_memory_context'):
        monkeypatch.setattr(chat, name, lambda *args, **kwargs: '')
    rows = long_history()
    result = chat.build_fast_chat_context_snapshot('desktop_budget', rows)
    assert result.max_tokens == 272000 and result.raw_messages == rows


def test_agent_budget_ignores_placeholder_model_window_and_old_cached_limit(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 272000)
    monkeypatch.setattr('app.model_registry.get_model_profile',
                        lambda _: SimpleNamespace(context_window_tokens=32768, api_mode='chat_completions'))
    t = task()
    source = [{'role': 'user', 'content': 'synthetic ' * 16000}]
    with patch.object(llm, 'call_chat_completion_result', AsyncMock()) as model:
        output, completions = run(t, source, [], [])
    model.assert_not_awaited()
    assert source[0] in output and not completions
    usage = agent.usage_for_conversation(t['conversation_id'])
    assert usage['used_tokens'] > 18000 and usage['max_tokens'] == 272000
    assert usage['percent'] == usage['token_percent']
    # The settings budget is authoritative even when the last measurement is old.
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 64000)
    changed = agent.usage_for_conversation(t['conversation_id'])
    assert changed['max_tokens'] == 64000 and changed['used_tokens'] == usage['used_tokens']
    assert changed['percent'] == round(changed['used_tokens'] / 64000 * 100, 1)


def compression_history(count=28, size=8000):
    return [dict(id=i + 1, role='user' if i % 2 == 0 else 'assistant',
                 content=f'row:{i + 1} ' + '界' * size + f' tail-fact-{i + 1}',
                 created_at='2026-10-24T10:00:00+08:00') for i in range(count)]


def test_272k_compression_receives_full_text_and_next_turn_reuses_summary(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 272000)
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_chars', 4000)
    monkeypatch.setitem(settings.__dict__, 'chat_recent_keep_messages', 4)
    monkeypatch.setattr(chat, 'build_periodic_memory_context', lambda *args: '')
    rows = compression_history()
    model = AsyncMock(return_value='已核实旧历史，保留关键事实。')
    with patch.object(chat, 'call_chat_completion', model):
        first = asyncio.run(chat.build_chat_context('desktop_large_summary', rows))
        sent = model.call_args.args[0][1]['content']
        assert len(sent) > 9000 and rows[20]['content'] in sent
        assert 'tail-fact-24' in sent and 'tail-fact-25' not in sent
        stored = chat.db.get_latest_memory(chat.SUMMARY_TYPE, tags='desktop_large_summary')
        assert chat._summary_last_message_id(stored['content']) == 24
        assert first.compression_triggered and first.used_tokens < first.max_tokens
        assert [row['id'] for row in first.raw_messages] == [25, 26, 27, 28]
        next_row = dict(id=29, role='user', content='继续，保留原约束。', created_at=rows[-1]['created_at'])
        second = asyncio.run(chat.build_chat_context('desktop_large_summary', [*rows, next_row]))
    assert model.await_count == 1 and not second.compression_triggered
    assert '已核实旧历史' in second.system_context and second.raw_messages[-1] == next_row


def test_compression_batch_watermark_covers_only_sent_rows_and_failure_cannot_advance(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 20000)
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_chars', 4000)
    monkeypatch.setitem(settings.__dict__, 'chat_recent_keep_messages', 4)
    monkeypatch.setattr(chat, 'build_periodic_memory_context', lambda *args: '')
    rows = compression_history(size=1200)
    model = AsyncMock(return_value='当前批次已摘要。')
    with patch.object(chat, 'call_chat_completion', model):
        result = asyncio.run(chat.build_chat_context('desktop_batch_summary', rows))
    messages = model.call_args.args[0]
    assert sum(chat.estimate_tokens(item['content']) + 8 for item in messages) <= 20000
    sent_rows = [json.loads(line) for line in messages[1]['content'].splitlines() if line.startswith('{')]
    last_sent_id = int(sent_rows[-1]['content'].split()[0].split(':')[1])
    stored = chat.db.get_latest_memory(chat.SUMMARY_TYPE, tags='desktop_batch_summary')
    assert chat._summary_last_message_id(stored['content']) == last_sent_id < 24
    assert any(row['id'] == last_sent_id + 1 for row in result.raw_messages)
    for response in (OSError('synthetic summary failure'), ''):
        name = 'desktop_failed_summary_' + type(response).__name__
        failed = AsyncMock(side_effect=response) if isinstance(response, Exception) else AsyncMock(return_value=response)
        with patch.object(chat, 'call_chat_completion', failed):
            asyncio.run(chat.build_chat_context(name, rows))
        assert failed.await_count == 1
        assert chat.db.get_latest_memory(chat.SUMMARY_TYPE, tags=name) is None


def test_chat_token_trim_keeps_complete_user_turns(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 1000)
    rows = [dict(id=i + 1, role=role, content='界' * 350,
                 created_at='2026-10-24T10:00:00+08:00')
            for i, role in enumerate(('user', 'assistant', 'assistant', 'user', 'assistant'))]
    selected = chat._select_raw_messages(rows, '', '', 0, True)
    assert [row['id'] for row in selected] == [4, 5]


def test_explicit_chat_compression_runs_below_threshold_then_noops_without_deleting_messages(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 272000)
    monkeypatch.setitem(settings.__dict__, 'chat_recent_keep_messages', 4)
    monkeypatch.setattr(chat, 'build_periodic_memory_context', lambda *args: '')
    for i in range(10):
        chat.db.save_message('user' if i % 2 == 0 else 'assistant', f'合成旧事实{i}', conversation_id='desktop_force')
    before = list(chat.db.get_recent_messages(limit=100, conversation_id='desktop_force'))
    model = AsyncMock(return_value='压缩后的合成事实。')
    with patch.object(chat, 'call_chat_completion', model):
        first = asyncio.run(chat.force_compress_context('desktop_force', model_id='synthetic-only'))
        second = asyncio.run(chat.force_compress_context('desktop_force'))
    assert first['status'] == 'compressed' and second['status'] == 'noop'
    assert first['summary_through_message_id'] == before[5]['id']
    assert model.await_count == 1 and model.call_args.kwargs['model_id'] == 'synthetic-only'
    after = list(chat.db.get_recent_messages(limit=100, conversation_id='desktop_force'))
    assert [dict(row) for row in after] == [dict(row) for row in before]
    assert first['before']['max_tokens'] == first['after']['max_tokens'] == 272000


def test_explicit_chat_compression_failure_preserves_watermark_and_group_is_rejected(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_recent_keep_messages', 4)
    monkeypatch.setattr(chat, 'build_periodic_memory_context', lambda *args: '')
    for i in range(10):
        chat.db.save_message('user', f'合成事实{i}', conversation_id='desktop_force_fail')
    with patch.object(chat, 'call_chat_completion', AsyncMock(side_effect=OSError('synthetic failure'))):
        result = asyncio.run(chat.force_compress_context('desktop_force_fail'))
    assert result['status'] == 'failed' and result['summary_through_message_id'] == 0
    assert chat.db.get_latest_memory(chat.SUMMARY_TYPE, tags='desktop_force_fail') is None
    with pytest.raises(ValueError, match='群聊'):
        asyncio.run(chat.force_compress_context('qq_group_synthetic'))
    with pytest.raises(ValueError, match='群聊'):
        agent.request_force_compression('qq_group_synthetic')


def test_agent_force_queue_is_idempotent_deferred_and_consumed_by_next_tick(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 272000)
    t = task()
    source, protocol, observations = materials()
    first = agent.request_force_compression(t['conversation_id'])
    assert first['status'] == 'queued'
    assert agent.request_force_compression(t['conversation_id'])['request_id'] == first['request_id']
    with patch.object(llm, 'call_chat_completion_result', AsyncMock(return_value=completion())) as model:
        run(t, source, protocol, observations, allow_summary=False)
        assert model.await_count == 0
        assert agent.usage_for_conversation(t['conversation_id'])['force_compression_status'] == 'deferred'
        assert agent.request_force_compression(t['conversation_id'])['request_id'] == first['request_id']
        output, _ = run(t, source, protocol, observations)
    assert model.await_count == 1
    assert not any('已读旧资料' in item.get('content', '') for item in output)
    assert agent.usage_for_conversation(t['conversation_id'])['force_compression_status'] == 'compressed'
    with chat.db.get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_context_compression_requests').fetchone()[0] == 0


def test_agent_force_failure_keeps_source_and_explicit_retry_can_succeed(isolated, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 272000)
    t = task()
    source, protocol, observations = materials()
    agent.request_force_compression(t['conversation_id'])
    with patch.object(llm, 'call_chat_completion_result', AsyncMock(side_effect=OSError('synthetic failure'))):
        output, _ = run(t, source, protocol, observations)
    assert source[1] in output and agent._cache(t['id'])['through_step'] == 0
    assert agent.usage_for_conversation(t['conversation_id'])['force_compression_status'] == 'failed'
    agent.request_force_compression(t['conversation_id'])
    with patch.object(llm, 'call_chat_completion_result', AsyncMock(return_value=completion())) as model:
        run(t, source, protocol, observations)
    assert model.await_count == 1
    assert agent.usage_for_conversation(t['conversation_id'])['force_compression_status'] == 'compressed'
