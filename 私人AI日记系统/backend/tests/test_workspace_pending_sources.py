import asyncio
import json
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, workspace_service as workspace, agent_task_service as tasks
from app import knowledge_service as knowledge, agent_file_service as files, workspace_tools
from app import workspace_pending_service as pending, browser_service as browser, agent_source_service as sources
from test_runtime_safety_v2 import isolated


@pytest.fixture(autouse=True)
def setup(isolated):
    workspace.initialize()
    with patch('app.model_runtime.model_selection', return_value=('test-model', 'low')):
        yield


def make_task(project, status):
    task = workspace.enqueue_goal(status, project['id'])
    return tasks.update(task['id'], status=status, snapshot={'blocker': f'{status} 的原因'})


def index_file(tmp_path):
    project = workspace.save_project('可追溯资料')
    root = tmp_path / 'authorized'
    root.mkdir()
    files.roots([str(root)])
    path = root / 'source.md'
    path.write_text('第一行\n星空项目的可靠原文', encoding='utf-8')
    document = knowledge.index_document(project['id'], str(path))
    return project, path, document


def test_pending_aggregates_all_states_filters_project_and_resolves():
    a, b = workspace.save_project('甲'), workspace.save_project('乙')
    states = ['waiting_confirmation', 'waiting_user', 'waiting_jobs', 'failed', 'budget_exhausted']
    expected = [make_task(a, state) for state in states]
    other = make_task(b, 'waiting_user')
    for state in ('running', 'ready', 'paused', 'completed', 'cancelled'):
        make_task(a, state)
    data = pending.listing(a['id'])
    assert data['total'] == len(states)
    assert {item['task_id'] for item in data['items']} == {task['id'] for task in expected}
    assert not data['has_more']
    waiting = next(item for item in data['items'] if item['status'] == 'waiting_jobs')
    assert not waiting['requires_action']
    assert all(item['reason'] for item in data['items'])
    assert data['items'][-1]['status'] == 'waiting_jobs'
    tasks.update(expected[0]['id'], status='completed')
    tasks.update(expected[1]['id'], status='paused')
    assert pending.listing(a['id'])['total'] == 3
    assert pending.listing()['total'] == 4
    assert pending.listing(b['id'])['items'][0]['task_id'] == other['id']


def test_pending_paginates_beyond_existing_overview_limit():
    project = workspace.save_project('长待办')
    task = make_task(project, 'failed')
    with closing(db.get_conn()) as conn, conn:
        conn.executemany("""INSERT INTO agent_tasks SELECT ?,conversation_id,source,status,
            original_goal,snapshot_json,model_id,reasoning_level,allowed_tools_json,context_json,
            waiting_jobs_json,spent_yuan,budget_yuan,revision,created_at,updated_at
            FROM agent_tasks WHERE id=?""", [(f'task_extra_{i:03d}', task['id']) for i in range(110)])
    seen = []
    for offset in (0, 50, 100):
        data = pending.listing(project['id'], limit=50, offset=offset)
        assert data['total'] == 111
        assert data['has_more'] == (offset < 100)
        seen.extend(item['id'] for item in data['items'])
    assert len(set(seen)) == 111
    assert not pending.listing(project['id'], offset=111)['items']


def test_browser_confirmations_share_pending_view_and_stopped_tasks_disappear():
    project = workspace.save_project('浏览器确认')
    task = make_task(project, 'waiting_user')
    browser.initialize()
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT INTO workspace_browser_pending VALUES(?,?,?,?,?,?,?,?,?)',
                     ('confirmation_test', task['id'], 'page_test', 'https://example.com', 'hash',
                      json.dumps({'purpose': '提交测试', 'operation': 'click'}), 'pending', db.now_iso(), '{}'))
    item = next(item for item in pending.listing()['items'] if item['kind'] == 'browser')
    assert item['confirmation_id'] == 'confirmation_test'
    assert item['action']['operation'] == 'click'
    assert item['reason'] == '提交测试'
    tasks.update(task['id'], status='cancelled')
    assert pending.listing()['total'] == 0


def test_agent_never_receives_stale_excerpts_and_reindex_recovers(tmp_path):
    project, path, document = index_file(tmp_path)
    context = SimpleNamespace(conversation_id='test')
    args = {'project_id': project['id'], 'query': '星空'}
    fresh = asyncio.run(workspace_tools.dispatch('knowledge_search', args, context))
    assert fresh['results'][0]['source']['sha256'] == document['sha256']
    path.write_text('星空项目的最新原文', encoding='utf-8')
    assert knowledge.search(project['id'], '星空')['results'][0]['stale']  # Human API remains compatible.
    blocked = asyncio.run(workspace_tools.dispatch('knowledge_search', args, context))
    assert not blocked['results']
    assert len(blocked['blocked_sources']) == 1
    assert 'content' not in blocked['blocked_sources'][0]
    assert '可靠原文' not in json.dumps(blocked, ensure_ascii=False)
    knowledge.reindex_document(document['id'])
    refreshed = asyncio.run(workspace_tools.dispatch('knowledge_search', args, context))
    assert '最新原文' in refreshed['results'][0]['content']
    assert not refreshed['blocked_sources']
    assert refreshed['results'][0]['sha256'] != document['sha256']
    files.roots([])
    assert not knowledge.search_for_agent(project['id'], '星空')['results']
    with pytest.raises(ValueError):
        knowledge.reindex_document(document['id'])


def record_search(task, result, run_id='source_run', step_index=0):
    if not db.get_agent_run(run_id):
        db.create_agent_run(run_id, run_id, conversation_id=task['conversation_id'])
        with closing(db.get_conn()) as conn, conn:
            conn.execute('INSERT INTO agent_task_runs VALUES(?,?,?)', (run_id, task['id'], run_id))
    _, step = db.claim_agent_run_step(run_id, step_index, 'tool_call', f'{run_id}:{step_index}', tool_name='knowledge_search')
    db.update_agent_run_step(step['id'], 'completed', result_json=json.dumps(result, ensure_ascii=False))
    return step['id']


def test_task_sources_revalidate_versions_and_revoked_access(tmp_path):
    project, path, document = index_file(tmp_path)
    task = make_task(project, 'running')
    record_search(task, knowledge.search_for_agent(project['id'], '星空'))
    fresh = sources.listing(task['id'])['sources'][0]
    assert fresh['status'] == 'current' and fresh['url']
    assert fresh['sha256'] == document['sha256']
    path.write_text('星空项目发生变化', encoding='utf-8')
    resumed = tasks.observations(task['id'])[0]['result']
    assert not resumed['results']
    assert '可靠原文' not in json.dumps(resumed, ensure_ascii=False)
    stale = sources.listing(task['id'])['sources'][0]
    assert stale['status'] == 'stale' and not stale['url'] and stale['can_reindex']
    record_search(task, knowledge.search_for_agent(project['id'], '星空'), step_index=1)
    assert sources.listing(task['id'])['sources'][1]['status'] == 'blocked'
    files.roots([])
    unavailable = sources.listing(task['id'])['sources']
    assert all(item['status'] == 'unavailable' and not item['url'] and not item['can_reindex'] for item in unavailable)


def test_final_model_context_drops_sources_changed_after_retrieval(tmp_path):
    from app.agent_loop_service import AgentLoopResult, _planner_messages
    from app.agent_tool_service import ToolExecutionResult
    project, path, _ = index_file(tmp_path)
    observation = ToolExecutionResult('knowledge_search', 'completed', knowledge.search_for_agent(project['id'], '星空'), 1)
    agent = AgentLoopResult('run', 'completed', 'native', (observation,), (), False, 1)
    assert '可靠原文' in agent.model_context()
    path.write_text('已更正的星空项目', encoding='utf-8')
    assert '可靠原文' not in agent.model_context()
    messages = _planner_messages('test', '继续', 0, observations=[observation])
    assert '可靠原文' not in json.dumps(messages, ensure_ascii=False)


def test_sources_paginate_across_runs_without_leaking_other_task(tmp_path):
    project, _, _ = index_file(tmp_path)
    task, other = make_task(project, 'running'), make_task(project, 'running')
    result = knowledge.search_for_agent(project['id'], '星空')
    first_id = record_search(task, result)
    record_search(task, result, run_id='source_second')
    record_search(other, result, run_id='source_other')
    page = sources.listing(task['id'], limit=1)
    assert page['has_more'] and page['next_id'] == first_id
    last = sources.listing(task['id'], after_id=page['next_id'], limit=1)
    assert not last['has_more']
    assert last['sources'][0]['run_id'] == 'source_second'


def test_pending_and_reindex_http_endpoints(tmp_path):
    from app.routes.workspace import router
    project, path, document = index_file(tmp_path)
    task = make_task(project, 'waiting_user')
    path.write_text('星空新版', encoding='utf-8')
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get('/api/workspace/pending', params={'project_id': project['id']})
        assert response.status_code == 200
        assert response.json()['items'][0]['task_id'] == task['id']
        assert client.get('/api/workspace/pending?project_id=missing').status_code == 400
        assert client.post(f"/api/workspace/knowledge/{document['id']}/reindex").status_code == 200
        files.roots([])
        assert client.post(f"/api/workspace/knowledge/{document['id']}/reindex").status_code == 400


def test_paired_mobile_can_reindex_only_an_existing_authorized_document(tmp_path, monkeypatch):
    from app import mobile_gateway as mobile
    project, path, document = index_file(tmp_path)
    task = make_task(project, 'waiting_user')
    record_search(task, knowledge.search_for_agent(project['id'], '星空'))
    monkeypatch.setattr(mobile, '_pair', {})
    monkeypatch.setattr(mobile, '_attempts', {})
    mobile.configure(True, 8443)
    code = mobile.new_pairing()['code']
    headers = {'Origin': 'https://localhost:8443', 'X-Mio-Mobile': '1'}
    endpoint = f"/api/workspace/knowledge/{document['id']}/reindex"
    with TestClient(mobile.create_app(), base_url='https://localhost:8443') as phone:
        assert phone.get('/api/workspace/pending').status_code == 401
        assert phone.post(endpoint, headers=headers).status_code == 401
        assert phone.post('/api/mobile/pair', json={'code': code}, headers=headers).status_code == 200
        assert phone.get('/api/workspace/pending').json()['total'] == 1
        assert phone.get(f"/api/agent/work/{task['id']}/citations").json()['sources'][0]['status'] == 'current'
        path.write_text('星空新内容', encoding='utf-8')
        assert phone.post(endpoint).status_code == 403  # Origin/write header remains mandatory.
        assert phone.post(endpoint, headers=headers).status_code == 200
        assert phone.post(f"/api/workspace/projects/{project['id']}/knowledge", json={'path': str(path)}, headers=headers).status_code == 403
        files.roots([])
        assert phone.post(endpoint, headers=headers).status_code == 400
        assert phone.post('/api/mobile/logout', headers=headers).status_code == 200
        assert phone.get(f"/api/agent/work/{task['id']}/citations").status_code == 401
