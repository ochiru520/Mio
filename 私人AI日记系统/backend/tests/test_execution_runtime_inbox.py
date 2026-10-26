"""Durable task inbox and controlled browser tests use disposable storage only."""
import asyncio
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, agent_input_service as inputs, agent_task_service as tasks
from app import browser_service as browser, terminal_service as terminal
from app.repositories import agent_tasks_repository, terminal_repository
from app.routes.agent_tasks import router
from test_runtime_safety_v2 import isolated
from test_local_execution import project, completed


def make_task(run='runtime-inbox', conversation='desktop_agent_inbox'):
    db.create_agent_run(run, run, conversation_id=conversation, model_id='synthetic')
    return tasks.prepare(run_id=run, request_id=run, conversation_id=conversation, source='desktop',
        user_message='合成任务', model_id='synthetic', reasoning_level='low', allowed_tools=[], context=[])


def test_input_edit_withdraw_and_consumption_race():
    task = make_task()
    item = inputs.append(task['id'], '旧标题', 'one')
    edited = inputs.revise(task['id'], item['id'], expected_revision=1, content='新标题')
    assert edited['revision'] == 2 and edited['state'] == 'received'
    with pytest.raises(inputs.InputChanged):
        inputs.consume(task['id'], [item], 'runtime-inbox', 0)
    assert inputs.has_pending(task['id'])
    inputs.consume(task['id'], [edited], 'runtime-inbox', 1)
    with pytest.raises(ValueError):
        inputs.revise(task['id'], item['id'], expected_revision=2, withdraw=True)
    other = inputs.append(task['id'], '可撤回', 'two')
    withdrawn = inputs.revise(task['id'], other['id'], expected_revision=1, withdraw=True)
    assert withdrawn['state'] == 'withdrawn'
    assert not inputs.has_pending(task['id'])
    assert '可撤回' not in tasks.get(task['id'])['snapshot']['updates']
    assert inputs.read(task['id'])['inputs'][0]['state'] == 'adopted'


def test_notifications_dismiss_is_durable_and_business_resolves():
    task = make_task()
    tasks.update(task['id'], status='waiting_user')
    first = agent_tasks_repository.notifications()[0]
    agent_tasks_repository.present_notification(task['id'], first['id'])
    agent_tasks_repository.dismiss_notification(task['id'], first['id'])
    tasks.update(task['id'], status='waiting_user')
    items = agent_tasks_repository.notifications()
    assert len(items) == 1 and items[0]['dismissed'] == 1 and items[0]['resolved'] == 0
    assert items[0]['presented_at']
    tasks.update(task['id'], status='ready')
    assert agent_tasks_repository.notifications()[0]['resolved'] == 1
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        value = client.get('/work/notifications').json()['notifications'][0]
        assert value['target']['task_id'] == task['id']


def test_recovery_distinguishes_not_started_and_unknown_write():
    task = make_task()
    _, pending = db.claim_agent_run_step('runtime-inbox', 0, 'tool_call', 'pending', tool_name='write', permission='low_risk_write')
    _, running = db.claim_agent_run_step('runtime-inbox', 1, 'tool_call', 'running', tool_name='write', permission='low_risk_write')
    db.update_agent_run_step(running['id'], 'running')
    tasks.recover_interrupted()
    assert db.get_agent_run_step(pending['id'])['status'] == 'not_started'
    assert db.get_agent_run_step(running['id'])['status'] == 'unknown'
    with pytest.raises(ValueError, match='结果尚未确认'):
        tasks.resume(task['id'])


def test_input_interrupts_job_wait_without_stopping_process():
    task = make_task()
    tasks.update(task['id'], status='waiting_jobs', snapshot={'waiting_terminal': ['running-job']})
    inputs.append(task['id'], '增加要求')
    with patch.object(terminal, 'stop_task') as stop:
        assert tasks.ready_to_continue(tasks.get(task['id']))
        stop.assert_not_called()


def test_duplicate_background_completion_does_not_reenter_planner():
    task = make_task()
    tasks.update(task['id'], status='waiting_jobs')
    task = tasks.get(task['id'])
    tasks.update(task['id'], snapshot={'background_delivery_key': tasks.background_delivery_key(task)})
    assert not tasks.ready_to_continue(tasks.get(task['id']))
    inputs.append(task['id'], '新的用户补充')
    assert tasks.ready_to_continue(tasks.get(task['id']))


def test_background_saved_message_recovers_without_second_delivery():
    task = make_task()
    receipt = {'id': 'durable-receipt', 'state_key': '[]', 'status': 'waiting_jobs'}
    tasks.update(task['id'], status='ready', snapshot={'pending_background_delivery': receipt})
    message_id = db.save_message('assistant', '后台完成回执', conversation_id=task['conversation_id'],
                                 delivery_key='task-result:durable-receipt')
    assert not tasks.ready_to_continue(tasks.get(task['id']))
    recovered = tasks.get(task['id'])
    assert recovered['status'] == 'waiting_jobs' and recovered['snapshot']['pending_background_delivery'] is None
    assert not tasks.ready_to_continue(recovered)
    assert db.get_message_by_id(message_id)['content'] == '后台完成回执'


def test_approval_wait_uses_frozen_execution_clock():
    from app import agent_loop_service as loop, llm
    task = make_task()
    tasks.limits({'enabled': True})
    with db.get_conn() as conn, conn:
        conn.execute('UPDATE agent_runs SET deadline_at=? WHERE run_id=?', ((datetime.fromisoformat(db.now_iso()) - timedelta(days=1)).isoformat(), 'runtime-inbox'))
    tasks.update(task['id'], status='waiting_confirmation', snapshot={'execution_clock': {
        'run_id': 'runtime-inbox', 'remaining_seconds': 10, 'paused': True, 'reason': 'waiting_confirmation'}})
    result = llm.CompletionResult(content='等待你的确认。', model='synthetic', prompt_tokens=3, cached_prompt_tokens=0,
                                  completion_tokens=2, reasoning_tokens=0, cost_yuan=0, cost_source='synthetic')
    callback = AsyncMock(return_value=result)
    agent = loop.AgentLoopResult('runtime-inbox', 'waiting_confirmation', 'native', (), (), False, 1, task_id=task['id'])
    actual = asyncio.run(loop.final_model_call(agent, callback, [{'role': 'user', 'content': '合成问题'}]))
    assert actual.content == result.content and callback.await_count == 1


def test_context_reference_tool_pages_original_and_rejects_foreign_scope():
    from app.repositories import context_offload_repository as offloads
    from app.agent_tool_service import ToolExecutionContext, execute_tool_call
    from app.tool_registry import tool_registry
    task = make_task()
    identifier = offloads.save(task['id'], '原始证据' * 5000, 'tool_result')
    context = ToolExecutionContext('runtime-inbox', 'runtime-inbox', '', task['conversation_id'], 0, '回读省略结果', 0, task_id=task['id'])
    result = asyncio.run(execute_tool_call('context_read_input_reference',
        {'reference_id': identifier, 'offset': 100, 'max_chars': 1000}, context))
    assert result.status == 'completed' and result.result['text'] == ('原始证据' * 5000)[100:1100]
    assert result.result['has_more'] and result.result['next_offset'] == 1100
    from app.context_reference_service import read
    with pytest.raises(ValueError):
        read(SimpleNamespace(task_id='', conversation_id='foreign'), identifier)


def test_context_image_reference_never_exposes_base64_as_tool_text():
    from app.repositories import context_offload_repository as offloads
    from app.context_reference_service import read, image
    task = make_task()
    identifier = offloads.save(task['id'], {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGVsbG8='}}, 'image')
    context = SimpleNamespace(task_id=task['id'], conversation_id=task['conversation_id'])
    result = read(context, identifier)
    assert result['image_included'] is False and 'aGVsbG8=' not in json.dumps(result)
    assert image(context, identifier) == (b'hello', 'image/png')
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        value = client.get(f'/work/{task["id"]}/input-references/{identifier}').json()
        assert value['preview_url'].endswith('/image')
        assert client.get(f'/work/{task["id"]}/input-references/{identifier}/image').content == b'hello'


@pytest.fixture
def fake_page(monkeypatch):
    page = MagicMock()
    page.url = 'https://example.invalid/form'
    page.is_closed.return_value = False
    page.title = AsyncMock(return_value='合成网页')
    page.screenshot = AsyncMock(return_value=b'synthetic-png')
    page.evaluate = AsyncMock(return_value={'tag': 'button', 'name': '提交', 'role': '', 'type': 'submit'})
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.evaluate = AsyncMock(return_value={'tag': 'button', 'name': '服务端名称'})
    page.locator.return_value = locator
    item = {'id': 'fixture-page', 'page': page, 'task_id': 'fixture-task', 'conversation_id': 'desktop_agent_fixture',
            'project_id': 'fixture-project', 'manual': False, 'lock': asyncio.Lock(), 'selections': {},
            'control': {'revision': 1, 'visible': False, 'active': False, 'owner': 'user', 'expires': 0}}
    monkeypatch.setattr(browser, '_pages', {'fixture-page': item})
    monkeypatch.setattr(browser, 'fingerprint', AsyncMock(return_value='server-fingerprint'))
    return item, SimpleNamespace(task_id='fixture-task', conversation_id='desktop_agent_fixture')


def test_browser_lease_hidden_takeover_and_expiry(fake_page):
    item, context = fake_page
    async def run():
        with pytest.raises(ValueError): browser._require_control(item)
        state = await browser.set_control(item['id'], context, expected_revision=1, visible=True, active=True, owner='agent')
        browser._require_control(item)
        same = await browser.set_control(item['id'], context, expected_revision=2, visible=True, active=True, owner='agent')
        assert same['control']['revision'] == state['control']['revision']
        await browser.set_control(item['id'], context, expected_revision=2, visible=False, active=False, owner='agent')
        with pytest.raises(ValueError): browser._require_control(item)
        await browser.set_control(item['id'], context, expected_revision=3, visible=True, active=True, owner='user')
        with pytest.raises(ValueError): browser._require_control(item)
        item['control']['owner'] = 'agent'; item['control']['expires'] = 0
        with pytest.raises(ValueError): browser._require_control(item)
    asyncio.run(run())


def test_browser_selection_revalidated_not_client_text(fake_page):
    item, context = fake_page
    async def run():
        await browser.set_control(item['id'], context, expected_revision=1, visible=True, active=True, owner='agent')
        selection = await browser.select_element(item['id'], context, x=10, y=15, expected_revision=2)
        selection['element']['name'] = '客户端伪造'
        verified = await browser.validate_element_reference(selection, conversation_id=context.conversation_id, task_id=context.task_id)
        assert verified['element']['name'] == '服务端名称'
        with pytest.raises(ValueError):
            await browser.validate_element_reference(selection, conversation_id='foreign', task_id=context.task_id)
        item['selections'][selection['selector'].split('"')[1]]['expires'] = 0
        with pytest.raises(ValueError, match='过期'):
            await browser.validate_element_reference(selection, conversation_id=context.conversation_id, task_id=context.task_id)
    asyncio.run(run())


def test_browser_switch_revokes_other_page(fake_page):
    item, context = fake_page
    second = {**item, 'id': 'second-page', 'control': dict(item['control'])}
    browser._pages[second['id']] = second
    async def run():
        await browser.set_control(item['id'], context, expected_revision=1, visible=True, active=True, owner='agent')
        await browser.set_control(second['id'], context, expected_revision=1, visible=True, active=True, owner='agent')
        assert not browser.control_state(item)['active']
        with pytest.raises(ValueError): browser._require_control(item)
    asyncio.run(run())


def test_browser_user_takeover_has_real_pointer_input(fake_page):
    item, context = fake_page
    item['page'].mouse.click = AsyncMock()
    item['page'].wait_for_timeout = AsyncMock()
    async def run():
        await browser.set_control(item['id'], context, expected_revision=1, visible=True, active=True, owner='agent')
        with pytest.raises(ValueError):
            await browser.user_action(item['id'], context, expected_revision=2, operation='click', x=11, y=17)
        await browser.set_control(item['id'], context, expected_revision=2, visible=True, active=True, owner='user')
        result = await browser.user_action(item['id'], context, expected_revision=3, operation='click', x=11, y=17)
        assert result['input_sent'] and not item['manual']
        item['page'].mouse.click.assert_awaited_once_with(11, 17)
    asyncio.run(run())


def test_browser_confirm_stale_element_is_not_started(fake_page):
    from app.repositories import browser_repository
    item, context = fake_page
    task = make_task(conversation=context.conversation_id)
    item['task_id'] = task['id']; context.task_id = task['id']
    browser.initialize()
    arguments = {'operation': 'click', 'selector': '[data-mio-selected="expired"]'}
    with browser_repository.unit_of_work() as conn:
        conn.insert_preview(('fixture-confirm', task['id'], item['id'], item['page'].url, 'server-fingerprint',
                             json.dumps(arguments), 'pending', db.now_iso(), '{}'))
    async def run():
        await browser.set_control(item['id'], context, expected_revision=1, visible=True, active=True, owner='agent')
        result = await browser.confirm('fixture-confirm')
        assert result['status'] == 'not_started' and result['result']['input_sent'] is False
        assert not agent_tasks_repository.unresolved_browser_write(task['id'])
    asyncio.run(run())


def test_user_pointed_page_is_handed_to_next_real_agent_task(fake_page):
    from app.agent_loop_service import run_agent_loop
    from app.llm import ToolCall
    from test_agent_model_first import _completion
    item, context = fake_page
    old_task = make_task(conversation=context.conversation_id)
    item['task_id'] = old_task['id']; context.task_id = old_task['id']
    item['page'].locator.return_value.click = AsyncMock()
    item['page'].wait_for_timeout = AsyncMock()
    async def run():
        await browser.set_control(item['id'], context, expected_revision=1, visible=True, active=True, owner='user')
        selection = await browser.select_element(item['id'], context, x=10, y=20, expected_revision=2)
        canonical = await browser.prepare_element_handoff(selection, conversation_id=context.conversation_id,
            task_id=context.task_id, request_id='pointed-next')
        assert canonical['control_owner'] == 'agent' and canonical['control_revision'] == 3
        assert browser.control_state(item)['owner'] == 'agent'
        planner = AsyncMock(side_effect=[_completion(ToolCall('pointed-action', 'browser_action', json.dumps({
            'page_id': item['id'], 'operation': 'click', 'selector': canonical['selector']}))), _completion()])
        with patch.object(browser, 'project_for', return_value=item['project_id']), \
             patch.object(browser, 'read_page', AsyncMock(return_value={'page_id': item['id'], 'verified': True})), \
             patch('app.agent_loop_service.call_chat_completion_result', planner):
            result = await run_agent_loop(conversation_id=context.conversation_id, source='desktop', user_message='点击这里',
                source_message_id=0, request_id='pointed-next', trace_id='pointed-next', model_id='synthetic',
                reasoning_level='low', allowed_tool_names={'browser_action'})
        assert item['task_id'] == result.task_id and result.task_id != old_task['id']
        assert result.observations[0].status == 'completed'
        item['page'].locator.return_value.click.assert_awaited_once()
        with pytest.raises(ValueError): browser.require_page(item['id'], context)
    asyncio.run(run())


def test_manual_handback_preserves_current_selection_but_hidden_page_does_not(fake_page):
    item, context = fake_page
    async def run():
        await browser.set_control(item['id'], context, expected_revision=1, visible=True, active=True, owner='user')
        selection = await browser.select_element(item['id'], context, x=10, y=20, expected_revision=2)
        handback = await browser.set_control(item['id'], context, expected_revision=2, visible=True, active=True, owner='agent')
        canonical = await browser.validate_element_reference(selection, conversation_id=context.conversation_id, task_id=context.task_id)
        assert canonical['control_revision'] == 3 and handback['selection_references']
        await browser.set_control(item['id'], context, expected_revision=3, visible=False, active=False, owner='agent')
        await browser.set_control(item['id'], context, expected_revision=4, visible=True, active=True, owner='agent')
        with pytest.raises(ValueError, match='控制版本'):
            await browser.validate_element_reference(selection, conversation_id=context.conversation_id, task_id=context.task_id)
    asyncio.run(run())


def test_real_interactive_terminal_stdin_and_one_delivery(project):
    root, _, config, context = project
    (root / 'prompt.py').write_text('print("READY", flush=True)\nvalue=input()\nprint("REPLY="+value, flush=True)\n', encoding='utf-8')
    command = "& '" + sys.executable.replace("'", "''") + "' -u 'prompt.py'"
    async def run():
        job = await terminal.start(context, command, workspace_revision=config['revision'], interactive=True, timeout_seconds=30)
        assert job['interactive']
        marked = terminal.send_input(context, job['job_id'], waiting_input=True)
        assert marked['interaction_state'] == 'waiting_input'
        assert terminal.read(context, job['job_id'])['interaction_state'] == 'waiting_input'
        await asyncio.to_thread(terminal.send_input, context, job['job_id'], 'hello\n', close=True)
        final = await completed(context, job)
        assert final['status'] == 'completed' and final['interaction_state'] == 'exited'
        assert 'REPLY=hello' in final['stdout']
        terminal.read(context, job['job_id'])
        with terminal_repository.unit_of_work() as conn:
            assert not conn.deliver(job['job_id'], 'duplicate')
        with pytest.raises(ValueError): terminal.send_input(context, job['job_id'], 'late\n')
    asyncio.run(run())
