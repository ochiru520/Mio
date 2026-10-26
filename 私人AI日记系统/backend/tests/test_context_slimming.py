"""Supplementary-context selection against synthetic records and HTTP payloads."""
import asyncio
from contextlib import ExitStack
from datetime import date, datetime, timedelta
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import chat_service as chat, context_service as context, db, llm, memory_service as memory
from app.config import settings
from app.model_registry import ModelProfile
from app.prompt_budget import estimate_tokens, select_sections, complete_text
from test_runtime_safety_v2 import isolated


@pytest.fixture
def week(isolated, monkeypatch):
    monkeypatch.setattr(db, 'today_string', lambda: '2026-10-24')
    monkeypatch.setattr(db, 'now_iso', lambda: '2026-10-24T16:00:00+08:00')
    monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 272000)
    monkeypatch.setitem(settings.__dict__, 'memory_context_days', 7)
    monkeypatch.setitem(settings.__dict__, 'memory_context_max_chars', 5000)
    for days_ago in range(7):
        day = (date(2026, 10, 24) - timedelta(days=days_ago)).isoformat()
        event = f'琥珀摄影归档{days_ago}。' if days_ago else '今日已学习三十分钟。'
        if days_ago == 1:
            event = '星桥部署已完成，端口是3841。'
        db.upsert_daily_state(day, 'done', '平静', event, '', '休息。', event)
        db.upsert_diary(day, f'记录-{day}', event + '其他摄影事项。' * 80)
        db.upsert_daily_review(day, '旧回顾摄影观察。' * 70)
    return 'desktop_slim_test'


def test_unrelated_chat_only_loads_today_and_one_diary_index_without_history_duplicates(week):
    cid = week
    original = '今天已学习三十分钟。'
    source = db.save_message('user', original, conversation_id=cid)
    memory.save_memory_item(layer='L0', category='preference', memory_key='exact-source', content=original,
        source_conversation_id=cid, source_message_id=source, confidence=.99)
    rows = list(db.get_recent_messages(limit=20, conversation_id=cid))
    value = context.build_periodic_memory_context(cid, '下午好呀', rows)
    assert '当前状态板' in value and '今日已学习三十分钟' in value
    assert '相关历史状态' not in value and '相关AI回顾' not in value
    assert value.count('《') == 1
    assert original not in value and '相关用户原话线索' not in value
    assert estimate_tokens(value) <= 2200
    assert len(db.list_diaries_since('2026-10-18')) == 7
    assert db.get_message_by_id(source)['content'] == original


def test_history_requested_by_date_and_topic_remains_available(week):
    value = context.build_periodic_memory_context(week, '昨天星桥部署的端口呢？')
    assert '相关历史状态' in value and '2026-10-23' in value
    assert '星桥部署已完成，端口是3841' in value
    assert '琥珀摄影归档6' not in value and value.count('《') <= 2
    assert db.get_diary('2026-10-23')['markdown_content'].startswith('星桥部署')


def test_compound_memory_keeps_readback_index_instead_of_detaching_qualifications(week):
    body = '摄影归档只是旧计划，尚未完成。' * 30 + '星桥部署端口是3841，不能改成8080。'
    item = memory.save_memory_item(layer='L0', category='preference', memory_key='starbridge-deploy',
        content=body, source_conversation_id=week, confidence=.99)
    # A separate complete negative fact must survive both selection and metadata.
    memory.save_memory_item(layer='L0', category='preference', memory_key='do-not-publish',
        content='用户没有授权公开发布。', source_conversation_id=week, confidence=.99)
    value = memory.build_structured_memory_context(week, '星桥部署端口', max_tokens=1100)
    assert '完整条目未载入' in value and 'starbridge-deploy' in value
    assert '用户没有授权公开发布。' in value and 'search_memory' in value
    assert estimate_tokens(value) <= 1100
    assert db.get_structured_memory(item['id'])['content'] == body
    assert body in memory.build_structured_memory_context(week, '星桥部署端口')


def test_cross_sentence_denial_cannot_be_lost_by_local_extraction():
    body = ('以下说法全部不成立，不可当成用户授权。用户授权公开发布私人聊天以及'
            + '此处约定的相关资料' * 30 + '。以上是需要拒绝的说法。')
    assert estimate_tokens(body) > 320
    assert complete_text(body, 320) == ''


def test_corrected_source_does_not_hide_another_active_fact_during_dedup(week):
    from app import memory_revision_service as revisions
    source = db.save_message('user', '用户喜欢咖啡。用户每天都会散步三十分钟。', conversation_id=week)
    wrong = memory.save_memory_item(layer='L0', category='preference', memory_key='drink', content='用户喜欢咖啡。',
        source_conversation_id=week, source_message_id=source, confidence=.99)
    memory.save_memory_item(layer='L0', category='preference', memory_key='walk', content='用户每天都会散步三十分钟。',
        source_conversation_id=week, source_message_id=source, confidence=.99)
    revisions.revise(wrong['id'], 'correct', content='用户只喝温水。')
    db.save_message('user', '散步的安排呢？', conversation_id=week)
    rows = list(db.get_recent_messages(limit=10, conversation_id=week))
    visible_source = chat._annotate_history_content(rows[0], rows[0]['content'],
        datetime.fromisoformat(db.now_iso()), current_message_id=rows[-1]['id'])
    assert '用户每天都会散步三十分钟。' not in visible_source
    result = context.build_chat_context_snapshot(week, rows)
    assert '用户每天都会散步三十分钟。' in result.system_context
    assert '用户只喝温水。' in result.system_context
    assert '用户喜欢咖啡。' not in result.system_context


def test_legacy_multiline_memory_keeps_its_source_metadata_as_one_entry(week):
    item = memory.save_memory_item(layer='L0', category='preference', memory_key='legacy-multiline',
        content='临时占位。', source_conversation_id=week, confidence=.99)
    body = '这不是用户授权。\n不得公开私人聊天。'
    with db.get_conn() as conn:
        conn.execute('UPDATE structured_memories SET content=? WHERE id=?', (body, item['id']))
    value = memory.build_structured_memory_context(week, '私人聊天', max_tokens=450)
    assert body in value and '置信度 0.99' in value
    assert '时间状态 enduring' in value
    small = memory.build_structured_memory_context(week, '私人聊天', max_tokens=150)
    assert body not in small and '不得公开私人聊天' not in small


@pytest.mark.parametrize('kind', ['token_budget', 'summary_watermark', 'compression'])
def test_deduplication_uses_final_visible_history_after_trimming_or_compression(week, monkeypatch, kind):
    fact = '星桥部署端口固定为3841，不能改成8080。'
    source = db.save_message('user', fact, conversation_id=week)
    memory.save_memory_item(layer='L0', category='preference', memory_key='deploy-port', content=fact,
        source_conversation_id=week, source_message_id=source, confidence=.99)
    for i in range(24):
        db.save_message('user' if i % 2 == 0 else 'assistant', f'段落{i} ' + '占位内容' * 70, conversation_id=week)
    db.save_message('user', '星桥部署端口呢？', conversation_id=week)
    rows = list(db.get_recent_messages(limit=100, conversation_id=week))
    if kind == 'summary_watermark':
        db.replace_memory(context.SUMMARY_TYPE, context._summary_with_marker('其他事项已归档。', rows[-6]['id']),
                          importance=4, tags=week)
    elif kind == 'token_budget':
        monkeypatch.setitem(settings.__dict__, 'chat_context_max_tokens', 3000)
    if kind == 'compression':
        with patch.object(context, '_compress_old_messages', AsyncMock(return_value='其他事项已归档。')):
            result = asyncio.run(context.build_chat_context(week, rows, force=True))
    else:
        result = context.build_chat_context_snapshot(week, rows)
    assert source not in {row['id'] for row in result.raw_messages}
    assert fact in result.system_context
    assert result.used_tokens <= result.max_tokens


def test_budget_keeps_whole_facts_and_skips_oversized_single_sentence(week, monkeypatch):
    huge = '不能公布这一段私人资料' * 500
    assert complete_text(huge, 60) == ''
    value = select_sections([('事实：', [huge, '用户没有授权发布。'])], max_tokens=80, notice='其余事实需回读。')
    assert huge not in value and '用户没有授权发布。' in value and '其余事实需回读。' in value
    assert estimate_tokens(value) <= 80
    for i in range(16):
        memory.save_memory_item(layer='L0', category='preference', memory_key=f'large-{i}',
            content=f'完整约束{i}。' * 100, source_conversation_id=week, confidence=.99)
    value = context.build_periodic_memory_context(week, '完整约束')
    assert estimate_tokens(value) <= 2200 and len(value) <= 5000
    assert '未载入' in value and len(memory.retrieve_memory_items('完整约束', limit=40)) == 16


def test_group_chat_never_scans_private_supplementary_records(week):
    with patch.object(db, 'list_daily_states_since', side_effect=AssertionError('private read')):
        assert context.build_periodic_memory_context('qq_group_synthetic', '昨天') == ''


@pytest.mark.parametrize('api_mode', ['chat_completions', 'responses'])
def test_actual_chat_payload_uses_slim_context_and_keeps_existing_history_prefix(week, api_mode):
    cid = week
    db.save_message('user', '我在学习。', conversation_id=cid)
    db.save_message('assistant', '记得休息一下。', conversation_id=cid)
    profile = ModelProfile(id='synthetic', provider_name='Synthetic', display_name='Synthetic', model='test-model',
        api_key='synthetic-only', base_urls=('https://cache.example.test/v1',), api_mode=api_mode)
    payloads = []
    client_class = httpx.AsyncClient

    def handler(request):
        payloads.append(json.loads(request.content))
        body = ({'model': profile.model, 'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': '好的，继续。'}]}]} if api_mode == 'responses' else
                {'model': profile.model, 'choices': [{'message': {'content': '好的，继续。'}, 'finish_reason': 'stop'}]})
        return httpx.Response(200, json=body)

    async def complete(messages, **kwargs):
        result = await llm.call_chat_completion_result.__wrapped__(messages, model_id=profile.id)
        return result, 'off', ''

    with ExitStack() as stack:
        for module in ('app.chat_service', 'app.llm'):
            stack.enter_context(patch(module + '.resolve_model_id', return_value=profile.id))
            stack.enter_context(patch(module + '.require_configured'))
            stack.enter_context(patch(module + '.get_model_profile', return_value=profile))
        stack.enter_context(patch.object(llm, '_completion_routes', return_value=[
            llm.CompletionRoute(profile.base_urls[0], '', 'synthetic', api_mode=api_mode)]))
        stack.enter_context(patch.object(llm.httpx, 'AsyncClient', side_effect=lambda **kwargs:
            client_class(transport=httpx.MockTransport(handler), trust_env=False)))
        stack.enter_context(patch.object(chat, 'load_manuals', return_value=[]))
        stack.enter_context(patch.object(chat, 'build_system_prompt', return_value='固定角色规则'))
        stack.enter_context(patch.object(chat, 'normalize_model_reasoning', return_value='off'))
        stack.enter_context(patch.object(chat, 'perform_web_lookup', AsyncMock(return_value=None)))
        stack.enter_context(patch.object(chat.system_audio_service, 'chat_context', return_value=''))
        stack.enter_context(patch.object(chat, '_self_snapshot_context_for_message', AsyncMock(return_value='')))
        stack.enter_context(patch.object(chat, '_complete_chat_reply_with_single_fallback', new=complete))
        stack.enter_context(patch.object(chat, 'schedule_companion_actions'))
        stack.enter_context(patch('app.companion_service.set_pet_activity'))
        for text in ('下午好呀', '我继续学习。'):
            asyncio.run(chat._chat_with_ai_unlocked(text, conversation_id=cid, source='web', model_id=profile.id,
                agent_tools_enabled=False, persist=True))
    key = 'input' if api_mode == 'responses' else 'messages'
    first, second = [payload[key] for payload in payloads]
    assert first[:3] == second[:3]
    for actual in (first, second):
        envelope = json.loads(actual[-2]['content'])
        runtime = '\n'.join(envelope['应用运行资料'])
        assert estimate_tokens(runtime) < 4000
        assert '当前状态板' in runtime and '琥珀摄影归档6' not in runtime
        assert '内部消息时间' in actual[-1]['content']
        assert actual[-2]['role'] == 'user' and actual[-1]['role'] == 'user'
