"""Project removal is a durable UI grouping change, never a file deletion."""
from contextlib import closing
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, workspace_service as workspace, agent_task_service as tasks
from app import routine_service as routines
from test_runtime_safety_v2 import isolated


@pytest.fixture(autouse=True)
def setup(isolated):
    workspace.initialize()
    with patch('app.model_runtime.model_selection', return_value=('test-model', 'low')):
        yield


def client():
    from app.routes.workspace import router
    from app.local_security import LocalControlMiddleware
    app = FastAPI()
    app.add_middleware(LocalControlMiddleware)
    app.include_router(router)
    return TestClient(app)


def rows(table):
    with closing(db.get_conn()) as conn:
        return [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]


def completed_task(project):
    task = workspace.enqueue_goal('synthetic completed work', project['id'])
    tasks.update(task['id'], status='completed')
    return task


def test_delete_preserves_files_conversations_tasks_and_outputs(tmp_path):
    import hashlib
    from app import artifact_service, knowledge_service, agent_file_service

    root = tmp_path / 'project-files'
    project = workspace.save_project('Synthetic project', directory=str(root), create_directory=True)
    source = root / 'source.md'
    source.write_text('synthetic source content', encoding='utf-8')
    task = completed_task(project)
    second = completed_task(project)
    workspace.save_conversation_mode(task['conversation_id'], 'learn')
    agent_file_service.roots([str(root)])
    document = knowledge_service.index_document(project['id'], str(source))
    artifact = artifact_service.import_file(
        source, source_root=root, producer='test', task_id=task['id'],
        conversation_id=task['conversation_id'], name='source.md', mime_type='text/markdown',
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    workspace.add_note(project['id'], 'Synthetic note', 'Synthetic saved note')
    workspace.select_result(project['id'], artifact_id=artifact['id'], task_id=task['id'])
    routine = routines.save('Synthetic timer', project['id'], 'schedule',
                            {'mode': 'interval', 'minutes': 10}, 'Synthetic goal', enabled=True)
    preserved_tables = ('agent_conversations', 'agent_tasks', 'messages', 'workspace_notes',
                        'workspace_note_state', 'workspace_selections', 'knowledge_documents',
                        'knowledge_chunks', 'artifacts', 'workspace_conversation_modes',
                        'workspace_project_directories')
    before = {table: rows(table) for table in preserved_tables}
    with client() as api:
        response = api.delete(f"/api/workspace/projects/{project['id']}")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['removed'] and not result['already_removed']
    assert result['unbound_conversations'] == 2
    assert result['disabled_routines'] == 1
    assert result['directory_deleted'] is False
    assert source.read_text(encoding='utf-8') == 'synthetic source content'
    assert {table: rows(table) for table in preserved_tables} == before
    assert workspace.project_for_conversation(task['conversation_id']) is None
    assert workspace.project_for_conversation(second['conversation_id']) is None
    assert workspace.conversation_index()['modes'][task['conversation_id']] == 'learn'
    disabled = next(item for item in routines.listing() if item['id'] == routine['id'])
    assert not disabled['enabled'] and disabled['next_run_at'] == ''
    with pytest.raises(ValueError):
        routines.set_enabled(routine['id'], True)
    assert knowledge_service.list_documents(project['id'])[0]['id'] == document['id']
    assert workspace.require_project(project['id'])['archived']
    assert not workspace.projects(include_archived=True)


def test_deleted_group_survives_restart_and_cannot_be_resurrected():
    project = workspace.save_project('Deleted group')
    workspace.delete_project(project['id'])
    workspace.initialize()
    routines.initialize()
    assert workspace.projects() == workspace.projects(include_archived=True) == []
    with pytest.raises(ValueError, match='已删除'):
        workspace.save_project('Restore attempt', project_id=project['id'], archived=False)
    with pytest.raises(ValueError):
        workspace.enqueue_goal('New work', project['id'])
    with client() as api:
        again = api.delete(f"/api/workspace/projects/{project['id']}")
    assert again.status_code == 200
    assert again.json()['already_removed']
    assert again.json()['unbound_conversations'] == again.json()['disabled_routines'] == 0


@pytest.mark.parametrize('status', ['ready', 'running', 'responding', 'waiting_jobs', 'waiting_confirmation'])
def test_active_project_work_refuses_removal_without_partial_changes(status):
    project = workspace.save_project('Busy group')
    task = workspace.enqueue_goal('Synthetic busy work', project['id'])
    tasks.update(task['id'], status=status)
    routine = routines.save('Busy timer', project['id'], 'schedule',
                            {'mode': 'interval', 'minutes': 10}, 'Synthetic goal', enabled=True)
    with client() as api:
        response = api.delete(f"/api/workspace/projects/{project['id']}")
    assert response.status_code == 409
    assert not workspace.require_project(project['id'])['archived']
    assert workspace.project_for_conversation(task['conversation_id'])['id'] == project['id']
    assert next(item for item in routines.listing() if item['id'] == routine['id'])['enabled']
    assert rows('workspace_deleted_projects') == []


def test_paused_task_with_inflight_worker_refuses_removal(monkeypatch):
    project = workspace.save_project('Inflight group')
    task = completed_task(project)
    tasks.update(task['id'], status='paused')
    monkeypatch.setattr(tasks, 'in_flight', lambda task_id: task_id == task['id'])
    with pytest.raises(workspace.ProjectBusyError):
        workspace.delete_project(project['id'])
    assert workspace.project_for_conversation(task['conversation_id'])['id'] == project['id']


@pytest.mark.parametrize('status', ['running', 'unknown'])
def test_terminal_outliving_paused_task_refuses_removal(status):
    from app import terminal_service
    project = workspace.save_project('Command group')
    task = completed_task(project)
    terminal_service.initialize()
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''INSERT INTO terminal_jobs(id,project_id,task_id,conversation_id,command,shell,cwd,
            workspace_revision,status,stdout_path,stderr_path,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('synthetic_command', project['id'], task['id'], task['conversation_id'], 'unused',
             'powershell', '', 1, status, '', '', db.now_iso()))
    with pytest.raises(workspace.ProjectBusyError):
        workspace.delete_project(project['id'])
    assert not workspace.require_project(project['id'])['archived']


@pytest.mark.parametrize('producer', ['creation', 'batch', 'assembly'])
def test_background_output_producer_refuses_removal(producer):
    from app import creative_batch_service, media_compose_service
    project = workspace.save_project('Output group')
    task = completed_task(project)
    creative_batch_service.initialize()
    media_compose_service.initialize()
    now = db.now_iso()
    with closing(db.get_conn()) as conn, conn:
        if producer == 'creation':
            conn.execute('''INSERT INTO creation_jobs(id,conversation_id,media_type,backend,status,
                idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)''',
                ('synthetic_output', task['conversation_id'], 'image', 'unused', 'running',
                 'synthetic_output', now, now))
        elif producer == 'batch':
            conn.execute('INSERT INTO creative_batches VALUES(?,?,?,?,?,?,?)',
                         ('synthetic_batch', task['id'], task['conversation_id'], 'Unused', 'queued', now, now))
        else:
            conn.execute('''INSERT INTO media_assemblies(id,task_id,spec_json,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?)''', ('synthetic_assembly', task['id'], '{}', 'running', now, now))
    with pytest.raises(workspace.ProjectBusyError):
        workspace.delete_project(project['id'])
    assert not workspace.require_project(project['id'])['archived']


@pytest.mark.parametrize('operation', ['bind', 'enqueue'])
def test_stale_project_read_cannot_rebind_or_enqueue_after_deletion(monkeypatch, operation):
    project = workspace.save_project('Stale read group')
    task = completed_task(project)
    original = workspace.require_project

    def delete_after_validation(project_id, **kwargs):
        result = original(project_id, **kwargs)
        workspace.delete_project(project_id)
        return result

    monkeypatch.setattr(workspace, 'require_project', delete_after_validation)
    before = rows('agent_tasks')
    with pytest.raises(ValueError, match='已删除'):
        if operation == 'bind':
            workspace.bind_conversation(project['id'], task['conversation_id'])
        else:
            workspace.enqueue_goal('Late queued work', project['id'])
    assert rows('workspace_conversations') == []
    assert rows('agent_tasks') == before


def test_only_target_group_is_removed_and_unrelated_busy_work_keeps_running():
    target = workspace.save_project('Target')
    other = workspace.save_project('Other')
    target_task = completed_task(target)
    other_task = workspace.enqueue_goal('Unrelated busy work', other['id'])
    workspace.delete_project(target['id'])
    assert [item['id'] for item in workspace.projects()] == [other['id']]
    assert workspace.project_for_conversation(target_task['conversation_id']) is None
    assert workspace.project_for_conversation(other_task['conversation_id'])['id'] == other['id']
    assert tasks.get(other_task['id'])['status'] == 'ready'


def test_cross_origin_and_mobile_cannot_delete_project():
    from app import mobile_gateway
    project = workspace.save_project('Owner only')
    path = f"/api/workspace/projects/{project['id']}"
    with client() as api:
        response = api.delete(path, headers={'Origin': 'https://untrusted.invalid'})
        missing = api.delete('/api/workspace/projects/project_missing')
    assert response.status_code == 403
    assert missing.status_code == 400
    assert not mobile_gateway.allowed('DELETE', path)
    assert [item['id'] for item in workspace.projects()] == [project['id']]


def test_mio_created_directory_can_be_deleted_and_unbound(tmp_path):
    from app import code_workspace_service as code
    root = tmp_path / 'mio-created'
    project = workspace.save_project('可删除目录', directory=str(root), create_directory=True)
    (root / 'generated.txt').write_text('owned output', encoding='utf-8')
    assert project['directory_created'] == 1
    code.configure(project['id'], str(root), True, True, True, {}, 0)
    with client() as api:
        response = api.delete(f"/api/workspace/projects/{project['id']}/directory")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['deleted'] and result['directory_deleted']
    assert not root.exists()
    current = workspace.require_project(project['id'])
    assert current['directory'] == '' and current['directory_created'] == 0
    assert code.configuration(project['id'])['root'] == ''
    assert not workspace.projects()[0]['directory']


def test_user_selected_directory_cannot_be_deleted(tmp_path):
    root = tmp_path / 'user-folder'
    root.mkdir()
    project = workspace.save_project('用户目录', directory=str(root))
    with client() as api:
        response = api.delete(f"/api/workspace/projects/{project['id']}/directory")
    assert response.status_code == 400
    assert '由 Mio 创建' in response.text
    assert root.is_dir()
    assert workspace.require_project(project['id'])['directory'] == str(root)


def test_missing_mio_directory_is_cleaned_from_binding(tmp_path):
    root = tmp_path / 'gone'
    project = workspace.save_project('已移除目录', directory=str(root), create_directory=True)
    root.rmdir()
    result = workspace.delete_project_directory(project['id'])
    assert result['already_missing'] and not result['directory_deleted']
    assert workspace.require_project(project['id'])['directory'] == ''


def test_directory_delete_refuses_active_project_work(tmp_path):
    root = tmp_path / 'busy-folder'
    project = workspace.save_project('忙碌目录', directory=str(root), create_directory=True)
    task = workspace.enqueue_goal('仍在运行', project['id'])
    tasks.update(task['id'], status='running')
    with pytest.raises(workspace.ProjectBusyError):
        workspace.delete_project_directory(project['id'])
    assert root.is_dir()
