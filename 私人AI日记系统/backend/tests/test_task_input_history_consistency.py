"""Amendment edits preserve inbox/history/source atomicity on disposable data."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import threading
from unittest.mock import AsyncMock, patch

import pytest

from app import db, agent_input_service as inputs, agent_task_service as tasks
from app import conversation_state_service as history, memory_service as memories
from app.repositories import agent_input_repository
from test_runtime_safety_v2 import isolated
from test_conversation_mainline import owner_client


def make_task():
    conversation = 'desktop_task_input_history'
    db.create_agent_conversation(conversation, '合成补充历史')
    db.create_agent_run('input-history', 'input-history', conversation_id=conversation, model_id='synthetic')
    return tasks.prepare(run_id='input-history', request_id='input-history', conversation_id=conversation,
        source='desktop', user_message='整理合成资料', model_id='synthetic', reasoning_level='low',
        allowed_tools=[], context=[])


def messages(task):
    return db.get_recent_messages(100, task['conversation_id'])


def test_edit_and_withdraw_reach_real_chat_payload_without_stale_instructions(isolated):
    task = make_task()
    edited = inputs.append(task['id'], '合成报告使用旧标题', 'edit')
    inputs.revise(task['id'], edited['id'], expected_revision=1, content='合成报告使用新标题')
    withdrawn = inputs.append(task['id'], '删除合成附件', 'withdraw')
    inputs.revise(task['id'], withdrawn['id'], expected_revision=1, withdraw=True)
    with owner_client() as (client, payloads):
        response = client.post('/api/agent/chat', json={'conversation_id': task['conversation_id'],
            'message': '说明资料情况', 'model_id': 'synthetic', 'client_request_id': 'history-after-amendment'})
        assert response.status_code == 200, response.text
        payload = json.dumps(payloads[-1]['messages'], ensure_ascii=False)
        assert '合成报告使用新标题' in payload
        assert '合成报告使用旧标题' not in payload
        assert '删除合成附件' not in payload


def test_retry_retains_original_request_identity_without_resurrecting_content(isolated):
    task = make_task()
    item = inputs.append(task['id'], '原补充', 'retry-key')
    changed = inputs.revise(task['id'], item['id'], expected_revision=1, content='修订后的补充')
    retry = inputs.append(task['id'], '原补充', 'retry-key')
    assert retry['id'] == item['id'] and retry['content'] == changed['content']
    assert [row['content'] for row in messages(task)] == ['修订后的补充']
    inputs.revise(task['id'], item['id'], expected_revision=2, withdraw=True)
    assert inputs.append(task['id'], '原补充', 'retry-key')['state'] == 'withdrawn'
    assert messages(task) == []
    with pytest.raises(ValueError, match='相同请求标识'):
        inputs.append(task['id'], '其他补充', 'retry-key')


def test_legacy_inbox_migrates_message_identity_without_duplicate_publication(isolated):
    import hashlib
    task = make_task()
    fingerprint = hashlib.sha256('旧库补充'.encode()).hexdigest()
    with closing(db.get_conn()) as conn, conn:
        conn.execute('DROP TABLE IF EXISTS agent_task_inputs')
        conn.execute('''CREATE TABLE agent_task_inputs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,version INTEGER NOT NULL,
            request_key TEXT NOT NULL,content TEXT NOT NULL,fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,
            consumed_run_id TEXT NOT NULL DEFAULT '',consumed_round INTEGER,consumed_at TEXT NOT NULL DEFAULT '',
            UNIQUE(task_id,version),UNIQUE(task_id,request_key))''')
        conn.execute('INSERT INTO agent_task_inputs(task_id,version,request_key,content,fingerprint,created_at) VALUES(?,1,?,?,?,?)',
            (task['id'], 'legacy', '旧库补充', fingerprint, db.now_iso()))
    message_id = db.save_message('user', '旧库补充', source='workspace', conversation_id=task['conversation_id'], delivery_key='task-input:1')
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: inputs.initialize(), range(2)))
    current = inputs.append(task['id'], '旧库补充', 'legacy')
    assert current['message_id'] == message_id and current['request_fingerprint'] == fingerprint
    changed = inputs.revise(task['id'], current['id'], expected_revision=1, content='旧库修订')
    assert changed['message_id'] != message_id
    assert [row['content'] for row in messages(task)] == ['旧库修订']


@pytest.mark.parametrize('state', ['received', 'adopted', 'withdrawn'])
def test_pre_fix_edited_and_withdrawn_rows_repair_history_atomically(isolated, state):
    import hashlib
    task = make_task()
    item = inputs.append(task['id'], '旧库最初补充', 'pre-fix')
    old_message_id = item['message_id']
    db.save_message('user', '后来无关的有效消息', conversation_id=task['conversation_id'])
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE agent_task_inputs SET state=?,content=?,fingerprint=?,request_fingerprint=? WHERE id=?',
            (state, '旧库修订后的补充', hashlib.sha256('旧库修订后的补充'.encode()).hexdigest(), '', item['id']))
    source = history.capture_source(task['conversation_id'])
    inputs.initialize()
    current = inputs.read(task['id'])['inputs'][0]
    assert current['state'] == state
    assert current['request_fingerprint'] == hashlib.sha256('旧库最初补充'.encode()).hexdigest()
    assert not history.source_is_current(source)
    visible = [row['content'] for row in messages(task)]
    assert '旧库最初补充' not in visible and '后来无关的有效消息' in visible
    assert ('旧库修订后的补充' in visible) == (state != 'withdrawn')
    assert db.get_message_by_id(old_message_id) is None
    # Running initialization again must not produce another history revision.
    repaired = history.capture_source(task['conversation_id'])
    inputs.initialize()
    assert history.source_is_current(repaired)
    if state != 'adopted':
        assert inputs.append(task['id'], '旧库最初补充', 'pre-fix')['state'] == state


@pytest.mark.parametrize('state', ['received', 'withdrawn'])
def test_real_runtime_startup_repairs_old_inputs_before_first_chat(isolated, state):
    from contextlib import ExitStack
    from app import main, artifact_service
    task = make_task()
    item = inputs.append(task['id'], '升级前的旧补充', 'startup-legacy')
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE agent_task_inputs SET content=?,state=? WHERE id=?',
            ('升级前已修订的新补充', state, item['id']))
    assert [row['content'] for row in messages(task)] == ['升级前的旧补充']
    observed = []
    def observe_next_initialization():
        # This is the next startup stage after the explicit input migration.
        # It reads history directly, with no lazy inbox initialization.
        observed.append([row['content'] for row in messages(task)])
    with ExitStack() as stack:
        stack.enter_context(patch.object(main.model_runtime, 'initialize', side_effect=observe_next_initialization))
        for module, name in ((main.companion_service, 'cleanup_legacy_preview'),
                             (artifact_service, 'initialize'), (main.migration_service, 'run_migrations'),
                             (main.onboarding_service, 'prepare_first_launch_defaults'),
                             (db, 'cleanup_screen_observation_history'), (db, 'refresh_manual_memories'),
                             (main, 'backfill_explicit_structured_memories')):
            stack.enter_context(patch.object(module, name))
        stack.enter_context(patch.object(main, 'load_manuals', return_value=[]))
        main.initialize_runtime()
    expected = ['升级前已修订的新补充'] if state == 'received' else []
    assert observed == [expected]
    assert [row['content'] for row in messages(task)] == expected
    with owner_client() as (client, payloads):
        response = client.post('/api/agent/chat', json={'conversation_id': task['conversation_id'],
            'message': '升级后第一次普通聊天', 'model_id': 'synthetic', 'client_request_id': 'first-chat-after-upgrade'})
        assert response.status_code == 200, response.text
        payload = json.dumps(payloads[-1]['messages'], ensure_ascii=False)
        assert '升级前的旧补充' not in payload
        assert ('升级前已修订的新补充' in payload) == (state == 'received')


def test_edit_invalidates_old_sources_preserves_manual_memory_and_other_messages(isolated):
    task = make_task()
    item = inputs.append(task['id'], '旧补充事实', 'source')
    old_id = item['message_id']
    memory = memories.save_memory_item(layer='L1', category='plan', memory_key='automatic', content='自动旧记忆',
        source_conversation_id=task['conversation_id'], source_message_id=old_id, confidence=.95)
    manual = memories.save_memory_item(layer='L1', category='plan', memory_key='manual', content='人工保留记忆',
        source_conversation_id=task['conversation_id'], source_message_id=old_id, confidence=.95)
    from app.memory_revision_service import revise
    manual_id = revise(manual['id'], 'correct', content='用户人工修订并保留的记忆')
    other = db.save_message('user', '另一个有效问题', conversation_id=task['conversation_id'])
    answer = db.save_message('assistant', '另一个有效回答', conversation_id=task['conversation_id'])
    db.replace_memory('conversation_summary', f'旧摘要<!-- last_message_id:{answer} -->', tags=task['conversation_id'])
    source = history.capture_source(task['conversation_id'])
    changed = inputs.revise(task['id'], item['id'], expected_revision=1, content='新补充事实')
    assert changed['message_id'] != old_id
    assert [row['id'] for row in messages(task)] == [other, answer, changed['message_id']]
    assert not history.source_is_current(source)
    assert db.get_structured_memory(memory['id'])['status'] == 'archived'
    assert db.get_structured_memory(manual_id)['status'] == 'active'
    assert db.get_latest_memory('conversation_summary', tags=task['conversation_id']) is None
    with pytest.raises(ValueError, match='拒绝迟到'):
        memories.save_memory_item(layer='L1', category='plan', memory_key='late', content='迟到旧记忆',
            source_conversation_id=task['conversation_id'], source_message_id=old_id, confidence=.95)
    with pytest.raises(ValueError, match='历史已变化'):
        db.save_message('assistant', '迟到回复', conversation_id=task['conversation_id'], expected_history_revision=source['revision'])


def test_summary_write_rechecks_revision_inside_transaction_after_precheck(isolated):
    task = make_task()
    item = inputs.append(task['id'], '旧摘要来源', 'summary-source')
    # The summary's last message stays valid: checking only this marker would
    # miss a concurrent edit of the earlier amendment.
    last_id = db.save_message('user', '末端仍然有效', conversation_id=task['conversation_id'])
    source = history.capture_source(task['conversation_id'])
    assert history.source_is_current(source)
    inputs.revise(task['id'], item['id'], expected_revision=1, content='新摘要来源')
    with pytest.raises(ValueError, match='摘要来源历史已变化'):
        db.replace_memory('conversation_summary', f'迟到旧摘要<!-- last_message_id:{last_id} -->',
            tags=task['conversation_id'], expected_history_revision=source['revision'])
    assert db.get_latest_memory('conversation_summary', tags=task['conversation_id']) is None


def test_real_context_compression_does_not_publish_summary_after_precheck_race(isolated):
    from app import context_service as context
    from app.config import settings
    task = make_task()
    item = inputs.append(task['id'], '旧摘要来源', 'compression-race')
    for index in range(10):
        db.save_message('user', f'其他合成消息{index}', conversation_id=task['conversation_id'])
    object.__setattr__(settings, 'chat_recent_keep_messages', 4)
    rows = messages(task)
    original_check = history.source_is_current
    def edit_after_successful_precheck(source):
        valid = original_check(source)
        inputs.revise(task['id'], item['id'], expected_revision=1, content='新摘要来源')
        return valid
    with patch.object(context, '_compress_old_messages', new=AsyncMock(return_value='不应持久或返回的过期摘要')), \
         patch.object(history, 'source_is_current', side_effect=edit_after_successful_precheck):
        result = asyncio.run(context.build_chat_context(task['conversation_id'], rows, force=True, model_id='synthetic'))
    assert db.get_latest_memory('conversation_summary', tags=task['conversation_id']) is None
    assert '不应持久或返回的过期摘要' not in result.system_context


def test_append_and_publication_rollback_together(isolated):
    task = make_task()
    with patch.object(agent_input_repository.Store, 'publish_input', side_effect=OSError('合成发布失败')):
        with pytest.raises(OSError):
            inputs.append(task['id'], '不能半保存', 'atomic')
    assert inputs.read(task['id'])['inputs'] == []
    assert messages(task) == []
    assert tasks.get(task['id'])['snapshot']['updates'] == [task['original_goal']]


def test_edit_publication_failure_rolls_back_inbox_history_and_revision(isolated):
    task = make_task()
    item = inputs.append(task['id'], '原补充', 'rollback')
    source = history.capture_source(task['conversation_id'])
    original = agent_input_repository.Store.revise_message
    def fail_after_invalidation(store, *args):
        original(store, *args)
        raise OSError('合成事务失败')
    with patch.object(agent_input_repository.Store, 'revise_message', fail_after_invalidation):
        with pytest.raises(OSError):
            inputs.revise(task['id'], item['id'], expected_revision=1, content='不应保存的新补充')
    assert history.source_is_current(source)
    assert inputs.read(task['id'])['inputs'][0]['revision'] == 1
    assert [row['content'] for row in messages(task)] == ['原补充']


def test_append_and_concurrent_revision_share_publication_lock(isolated):
    task = make_task()
    entered, release = threading.Event(), threading.Event()
    selected = {}
    original = agent_input_repository.Store.publish_input
    def delayed(store, item, *args):
        selected['id'] = item['id']
        entered.set()
        assert release.wait(5)
        return original(store, item, *args)
    with patch.object(agent_input_repository.Store, 'publish_input', delayed), ThreadPoolExecutor(2) as pool:
        append = pool.submit(inputs.append, task['id'], '并发旧补充', 'concurrent')
        assert entered.wait(5)
        revise = pool.submit(inputs.revise, task['id'], selected['id'], expected_revision=1, content='并发新补充')
        release.set()
        append.result(timeout=10)
        revised = revise.result(timeout=10)
    assert revised['revision'] == 2
    assert [row['content'] for row in messages(task)] == ['并发新补充']


def test_adopted_input_cannot_change_and_history_rewind_retry_cannot_republish(isolated):
    task = make_task()
    adopted = inputs.append(task['id'], '已采用补充', 'adopted')
    inputs.consume(task['id'], [adopted], 'input-history', 0)
    with pytest.raises(ValueError, match='已采用'):
        inputs.revise(task['id'], adopted['id'], expected_revision=1, withdraw=True)
    pending = inputs.append(task['id'], '随后回退的补充', 'rewound')
    history.rewind(task['conversation_id'], adopted['message_id'], mode='edit', content='新历史')
    with pytest.raises(ValueError, match='来源历史已失效'):
        inputs.append(task['id'], '随后回退的补充', 'rewound')
    assert [row['content'] for row in messages(task)] == ['新历史']


def test_background_reply_during_amendment_edit_is_rejected_and_task_diagnosed(isolated):
    from app import agent_loop_service as loop, llm
    task = make_task()
    tasks.update(task['id'], status='ready')
    async def plan(**kwargs):
        tasks.update(task['id'], status='completed')
        return loop.AgentLoopResult('input-history', 'completed', 'synthetic', (), (), False, 1, task_id=task['id'])
    async def reply(*args, **kwargs):
        item = inputs.append(task['id'], '生成中收到的旧补充', 'during-reply')
        inputs.revise(task['id'], item['id'], expected_revision=1, content='生成中修订的新补充')
        return llm.CompletionResult(content='过期后台回答', model='synthetic', prompt_tokens=1,
            cached_prompt_tokens=0, completion_tokens=1, reasoning_tokens=0, cost_yuan=0, cost_source='synthetic')
    with patch.object(loop, 'run_agent_loop', new=plan), patch.object(loop, 'final_model_call', new=reply):
        with pytest.raises(ValueError, match='历史已变化'):
            asyncio.run(tasks.continue_task(task['id']))
    assert not [row for row in messages(task) if row['role'] == 'assistant']
    current = tasks.get(task['id'])
    assert current['status'] == 'failed' and '历史已变化' in current['snapshot']['blocker']
