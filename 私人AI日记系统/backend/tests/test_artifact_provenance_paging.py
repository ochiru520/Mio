"""Real persistence, ownership ambiguity, deep history and paired mobile boundaries."""
from contextlib import closing
import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, workspace_service as workspace, agent_task_service as tasks
from app import artifact_service as artifacts, creation_service as creation, creation_provenance as provenance
from app.creation_models import CreationJobRequest
from app.routes.artifacts import router
from test_runtime_safety_v2 import isolated


@pytest.fixture(autouse=True)
def setup(isolated):
    workspace.initialize()
    with patch('app.model_runtime.model_selection', return_value=('test-model', 'low')), patch.object(creation, 'schedule_job'):
        yield


def project_task():
    project = workspace.save_project('合成验收项目')
    return project, workspace.enqueue_goal('合成目标', project['id'])


def make_job(task, key='generation-test', **kwargs):
    return creation.create_job(CreationJobRequest(prompt='synthetic', idempotency_key=key),
        conversation_id=task['conversation_id'], task_id=task['id'], **kwargs)[0]


def archive(tmp_path, task, job_id='', **kwargs):
    source = tmp_path / 'synthetic.txt'
    source.write_text('synthetic result', encoding='utf-8')
    return artifacts.import_file(source, source_root=tmp_path, producer='test',
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        task_id=task['id'], conversation_id=task['conversation_id'], job_id=job_id, **kwargs)


def clone(conn, table, original, **changes):
    item = {**dict(original), **changes}
    conn.execute(f"INSERT INTO {table} ({','.join(item)}) VALUES ({','.join('?' for _ in item)})", tuple(item.values()))


def evidence(task, job_id, run_id):
    db.create_agent_run(run_id, run_id, conversation_id=task['conversation_id'])
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT INTO agent_task_runs VALUES(?,?,?)', (run_id, task['id'], run_id))
    _, step = db.claim_agent_run_step(run_id, 0, 'tool_call', run_id + ':job', tool_name='create_image')
    db.update_agent_run_step(step['id'], 'completed', result_json=json.dumps({'job': {'id': job_id}}))


def test_conversation_files_resolve_task_and_job_only_sources_without_crossing_explicit_owner(tmp_path):
    project, task = project_task()
    foreign_project, foreign = project_task()
    job = make_job(task)
    source = tmp_path / 'task-only.txt'
    source.write_text('synthetic task-only output', encoding='utf-8')
    common = dict(source_root=tmp_path, producer='conversation_ownership',
                  expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    task_only = artifacts.import_file(source, **common, task_id=task['id'])
    job_only = artifacts.import_file(source, **common, job_id=job['id'])
    explicit_foreign = artifacts.import_file(source, **common, name='explicit-foreign.txt', task_id=task['id'], conversation_id=foreign['conversation_id'])
    own = {item['id'] for item in artifacts.list_artifacts(conversation_id=task['conversation_id'])}
    assert own == {task_only['id'], job_only['id']}
    other = {item['id'] for item in artifacts.list_artifacts(conversation_id=foreign['conversation_id'])}
    assert other == {explicit_foreign['id']}
    assert {item['id'] for item in artifacts.list_artifacts(project_id=project['id'])} == own
    assert {item['id'] for item in artifacts.list_artifacts(project_id=foreign_project['id'])} == other


def test_recorded_ownership_retry_and_idempotency_cannot_be_reassigned():
    project, task = project_task()
    job = make_job(task)
    assert job['task_id'] == task['id'] and job['provenance_status'] == 'recorded'
    other = workspace.enqueue_goal('另一个任务', project['id'])
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE agent_tasks SET conversation_id=? WHERE id=?', (task['conversation_id'], other['id']))
    other = tasks.get(other['id'])
    with pytest.raises(ValueError, match='来源任务'):
        make_job(other)
    creation._update_job(job['id'], status='failed')
    retry = creation.retry_job(job['id'])
    assert retry['task_id'] == task['id'] and retry['parent_job_id'] == job['id']
    assert make_job(task)['id'] == job['id']


def test_foreign_run_and_conversation_are_rejected():
    _, task = project_task()
    _, foreign = project_task()
    db.create_agent_run('foreign_run', 'foreign_request', conversation_id=foreign['conversation_id'])
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT INTO agent_task_runs VALUES(?,?,?)', ('foreign_run', foreign['id'], 'foreign_request'))
    with pytest.raises(ValueError, match='轮次'):
        make_job(task, run_id='foreign_run')
    with pytest.raises(ValueError, match='当前会话'):
        creation.create_job(CreationJobRequest(prompt='synthetic'), task_id=task['id'], conversation_id=foreign['conversation_id'])


def test_legacy_receipts_must_be_unique_and_task_artifact_filter_uses_them(tmp_path):
    project, task = project_task()
    job = creation.create_job(CreationJobRequest(prompt='synthetic'), conversation_id=task['conversation_id'])[0]
    assert job['task_id'] == ''
    legacy_task = {**task, 'id': ''}
    artifact = archive(tmp_path, legacy_task, job['id'])
    evidence(task, job['id'], 'legacy_one')
    assert creation.get_job(job['id'])['task_id'] == task['id']
    assert artifacts.list_artifacts(task_id=task['id'])[0]['id'] == artifact['id']
    other = workspace.enqueue_goal('第二个来源', project['id'])
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE agent_tasks SET conversation_id=? WHERE id=?', (task['conversation_id'], other['id']))
    evidence(tasks.get(other['id']), job['id'], 'legacy_two')
    assert provenance.owner(job['id']) == {'task_id': '', 'provenance_status': 'conflicting'}
    assert artifacts.list_artifacts(task_id=task['id']) == []
    with pytest.raises(ValueError, match='来源任务'):
        workspace.select_result(project['id'], artifact_id=artifact['id'], task_id=task['id'])
    assert workspace.select_result(project['id'], artifact_id=artifact['id'])['task_id'] == ''


def test_quality_review_is_persistent_and_independent_of_corrupt_file(tmp_path):
    _, task = project_task()
    artifact = archive(tmp_path, task)
    approved = artifacts.review(artifact['id'], 'user_approved', '清晰')
    assert approved['integrity_status'] == 'verified' and approved['quality_note'] == '清晰'
    blob, _ = artifacts.file(artifact['id'])
    blob.write_bytes(b'corrupt')
    checked = artifacts.verify(artifact['id'])
    assert checked['integrity_status'] == 'failed' and checked['quality_status'] == 'user_approved'
    assert checked['verified_at'] and checked['reviewed_at']
    with pytest.raises(ValueError, match='校验失败'):
        artifacts.file(artifact['id'])
    reset = artifacts.review(artifact['id'], 'not_reviewed')
    assert reset['integrity_status'] == 'failed' and reset['quality_note'] == ''


def test_deep_history_project_filter_and_selection_do_not_depend_on_first_page(tmp_path):
    project, task = project_task()
    job = make_job(task)
    artifact = archive(tmp_path, task, job['id'])
    foreign_project, foreign_task = project_task()
    foreign_artifact = archive(tmp_path, foreign_task, name='foreign.txt')
    with closing(db.get_conn()) as conn, conn:
        task_row = conn.execute('SELECT * FROM agent_tasks WHERE id=?', (task['id'],)).fetchone()
        job_row = conn.execute('SELECT * FROM creation_jobs WHERE id=?', (job['id'],)).fetchone()
        artifact_row = conn.execute('SELECT * FROM artifacts WHERE id=?', (artifact['id'],)).fetchone()
        for index in range(235):
            clone(conn, 'agent_tasks', task_row, id=f'task_deep_{index:04d}', updated_at='2099-01-01')
        for index in range(125):
            clone(conn, 'creation_jobs', job_row, id=f'job_deep_{index:04d}', idempotency_key=f'deep_{index}', created_at='2099-01-01')
            clone(conn, 'artifacts', artifact_row, id=f'artifact_deep_{index:04d}', name=f'深页{index}.txt', created_at='2099-01-01')
    assert len(workspace.task_page(project['id'], limit=200)['tasks']) == 200
    assert len(workspace.task_page(project['id'], limit=200, offset=200)['tasks']) == 36
    assert workspace.job_page(project['id'], limit=100)['has_more']
    assert len(workspace.job_page(project['id'], limit=100, offset=100)['jobs']) == 26
    assert len(artifacts.list_artifacts(project_id=project['id'], limit=100, offset=100)) == 26
    assert workspace.select_result(project['id'], artifact_id=artifact['id'])['task_id'] == task['id']
    with pytest.raises(ValueError, match='不属于'):
        workspace.select_result(project['id'], artifact_id=foreign_artifact['id'])
    assert len(artifacts.list_artifacts(project_id=foreign_project['id'])) == 1
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        page = client.get(f"/api/artifacts?project_id={project['id']}&limit=100&offset=100").json()
        assert len(page['artifacts']) == 26 and not page['has_more'] and page['next_offset'] == 126


def test_mobile_artifact_review_requires_pairing_origin_and_write_header(tmp_path, monkeypatch):
    from app import mobile_gateway as mobile
    monkeypatch.setattr(mobile, '_pair', {}); monkeypatch.setattr(mobile, '_attempts', {})
    project, task = project_task()
    artifact = archive(tmp_path, task)
    mobile.configure(True, 8443)
    code = mobile.new_pairing()['code']
    headers = {'Origin': 'https://localhost:8443', 'X-Mio-Mobile': '1'}
    review_url = f"/api/artifacts/{artifact['id']}/review"
    with TestClient(mobile.create_app(), base_url='https://localhost:8443') as phone:
        assert phone.post(review_url, json={'status': 'user_approved'}, headers=headers).status_code == 401
        assert phone.post('/api/mobile/pair', json={'code': code}, headers=headers).status_code == 200
        assert phone.post(review_url, json={'status': 'user_approved'}).status_code == 403
        assert phone.post(review_url, json={'status': 'user_approved'}, headers={**headers, 'Origin': 'https://foreign.invalid'}).status_code == 403
        assert phone.post(review_url, json={'status': 'user_rejected', 'note': '合成评价'}, headers=headers).json()['quality_status'] == 'user_rejected'
        assert phone.post(f"/api/artifacts/{artifact['id']}/verify", headers=headers).json()['integrity_status'] == 'verified'
        assert phone.get(f"/api/workspace/tasks?project_id={project['id']}").json()['tasks'][0]['id'] == task['id']
        assert phone.get(f"/api/workspace/projects/{project['id']}/jobs").status_code == 200
        assert phone.get(f"/api/workspace/projects/{project['id']}/history/notes").status_code == 200


def test_all_project_collections_have_exact_last_pages_and_project_isolation():
    from app import creative_batch_service as batches, media_compose_service as media, knowledge_service as knowledge
    from app.routes.workspace import router as workspace_router
    project, task = project_task()
    foreign, other = project_task()
    batches.initialize(); media.initialize(); knowledge.initialize()
    with closing(db.get_conn()) as conn, conn:
        for owner, count in ((task, 100), (other, 1)):
            pid = project['id'] if owner is task else foreign['id']
            for index in range(count):
                key = owner['id'] + str(index)
                conn.execute('INSERT INTO workspace_notes VALUES(?,?,?,?,?,?,?)', (key, pid, key, '合成记录', 'user', '2026', '2026'))
                conn.execute('INSERT INTO workspace_selections VALUES(?,?,?,?,?,?,?,?)', (key, pid, owner['id'], '', '', 0, '合成选用', '2026'))
                conn.execute('INSERT INTO knowledge_documents VALUES(?,?,?,?,?,?,?,?)', (key, pid, key, '合成资料', 'a'*64, 'ready', '', '2026'))
                conn.execute('INSERT INTO creative_batches VALUES(?,?,?,?,?,?,?)', (key, owner['id'], owner['conversation_id'], '合成批次', 'paused', '2026', '2026'))
                conn.execute('INSERT INTO media_assemblies VALUES(?,?,?,?,?,?,?,?)', (key, owner['id'], '{"name":"合成成片"}', 'failed', '', '合成验收', '2026', '2026'))
    app = FastAPI(); app.include_router(workspace_router)
    with TestClient(app) as client:
        detail = client.get('/api/workspace/projects/' + project['id']).json()
        for kind in ('notes', 'selections', 'documents', 'batches', 'assemblies'):
            assert len(detail[kind]) == 50 and detail[kind + '_has_more']
            first = workspace.collection_page(project['id'], kind)
            last = client.get(f"/api/workspace/projects/{project['id']}/history/{kind}?offset=50").json()
            assert len(last[kind]) == 50 and not last['has_more'] and last['next_offset'] == 100
            assert not {row['id'] for row in first[kind]} & {row['id'] for row in last[kind]}
            assert len(workspace.collection_page(foreign['id'], kind)[kind]) == 1
        assert client.get(f"/api/workspace/projects/{project['id']}/history/not_a_table").status_code == 422
