"""Cross-feature regressions use synthetic files and an isolated database only."""
import asyncio
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_runtime_safety_v2 import isolated, remote_job
from app import db, dependency_removal as removal, dependency_installer as deps, dependency_probe as probe
from app import creation_service as creation, agent_task_service as tasks, memory_revision_service as revisions
from app.config import settings
from app.memory_service import save_memory_item
from app.routes.memory import router as memory_router
from app.routes.dependencies import router as dependency_router


@pytest.fixture
def managed(tmp_path, monkeypatch):
    object.__setattr__(settings, 'voice_training_dir', tmp_path/'音色训练')
    object.__setattr__(settings, 'local_vision_dir', tmp_path/'本地视觉')
    monkeypatch.setattr(deps, '_running_installs', {})
    monkeypatch.setattr(deps, '_package_source', lambda _: {})
    monkeypatch.setattr(deps, '_install_running', lambda _: False)
    monkeypatch.setattr('app.genie_tts_service.stop_worker', lambda: None)
    monkeypatch.setattr('app.local_vision_service.stop_server', lambda: {})
    monkeypatch.setattr('app.system_audio_service.stop', lambda: {})
    probe.invalidate()
    return settings.voice_training_dir


def put(path, data=b'synthetic'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize('dep_id,relative', [('genie_runtime','.genie-env/Scripts/python.exe'),
    ('gpt_sovits','models/genie/mio-v1/model.onnx'), ('whisper','cache/faster-whisper/model.bin'),
    ('ollama_vision','models/blobs/test')])
def test_uninstall_preserves_shared_files_and_supports_reinstall(managed, dep_id, relative):
    root = settings.local_vision_dir if dep_id == 'ollama_vision' else managed
    target = put(root/relative)
    preserved = [put(managed/'.voice-env/Scripts/python.exe'), put(managed/'materials/personal.wav'),
                 put(managed/'models/genie/custom/model.onnx')]
    before_db = db.list_structured_memories(status='active')
    plan = removal.preview(dep_id)
    assert plan['size_bytes'] == len(b'synthetic')
    assert removal.uninstall(dep_id, plan['token'])['uninstalled']
    assert not target.exists()
    assert all(path.read_bytes() == b'synthetic' for path in preserved)
    assert db.list_structured_memories(status='active') == before_db
    assert not removal.availability(dep_id)['can_uninstall']
    assert deps._read_status(dep_id)['stage'] == 'uninstalled'
    # Reinstall uses the same fixed layout, without a permanent disabled flag.
    put(target)
    assert removal.availability(dep_id)['can_uninstall']
    assert removal.preview(dep_id)['paths']


def test_uninstall_changed_plan_and_install_in_progress_are_rejected(managed):
    target = put(managed/'cache/faster-whisper/model.bin')
    plan = removal.preview('whisper')
    target.write_bytes(b'changed')
    with pytest.raises(ValueError, match='变化'):
        removal.uninstall('whisper', plan['token'])
    plan = removal.preview('whisper')
    with patch.object(deps, '_install_running', return_value=True), pytest.raises(ValueError, match='正在安装'):
        removal.uninstall('whisper', plan['token'])
    assert target.exists()


def test_owned_bundled_whisper_is_removable_but_external_runtime_is_explained(managed):
    target = put(managed/'models/faster-whisper-base/model.bin')
    plan = removal.preview('whisper')
    removal.uninstall('whisper', plan['token'])
    assert not target.exists()
    with patch.object(deps, '_detect_status', return_value={'status':'unverified','detail':'external runtime'}):
        item = next(item for item in deps.list_dependencies() if item['id'] == 'whisper')
    assert not item['can_uninstall'] and '外部' in item['uninstall_blocker']


def test_uninstall_rejects_escaping_symlink(managed, tmp_path):
    outside = tmp_path/'outside'; put(outside/'keep.bin')
    link = managed/'cache/faster-whisper'; link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        # Junction creation does not require Windows symlink privileges.
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        assert result.returncode == 0
    try:
        with pytest.raises(ValueError): removal.preview('whisper')
        assert not removal.availability('whisper')['can_uninstall']
        assert (outside/'keep.bin').exists()
    finally:
        if link.is_symlink(): link.unlink()
        else: link.rmdir()


def test_uninstall_rejects_shared_root_and_active_call(managed):
    object.__setattr__(settings, 'local_vision_dir', settings.data_dir)
    with pytest.raises(ValueError, match='独立模型目录'):
        removal.preview('ollama_vision')
    put(managed/'cache/faster-whisper/model.bin')
    plan = removal.preview('whisper')
    with patch('app.call_session_service.manager.status', return_value={'active':True}), pytest.raises(ValueError, match='通话'):
        removal.uninstall('whisper', plan['token'])


def test_uninstall_api_requires_plan_and_reports_partial_failure(managed):
    target = put(managed/'cache/faster-whisper/model.bin')
    app = FastAPI(); app.include_router(dependency_router)
    with TestClient(app) as client:
        assert client.post('/api/dependencies/whisper/uninstall', json={}).status_code == 422
        assert client.get('/api/dependencies/cloud_model/uninstall-preview').status_code == 400
        plan = client.get('/api/dependencies/whisper/uninstall-preview').json()
        with patch.object(removal.shutil, 'rmtree', side_effect=PermissionError('locked')):
            response = client.post('/api/dependencies/whisper/uninstall', json={'token': plan['token']})
        assert response.status_code == 500 and '未全部完成' in response.json()['detail']
        assert target.exists()
        assert client.post('/api/dependencies/whisper/uninstall', json={'token': plan['token']}).json()['uninstalled']


def test_user_created_fact_sleep_wake_and_conflict_are_consistent():
    app = FastAPI(); app.include_router(memory_router)
    with TestClient(app) as client:
        response = client.post('/api/memory/items', json={'layer':'L0','category':'preference',
            'memory_key':'drink','content':'只喝温水','conversation_id':'desktop_test','confidence':1})
        assert response.status_code == 200, response.text
        mid = response.json()['memory']['id']
        assert revisions.evidence(mid)['kind'] == 'user_confirmed'
        conflict = save_memory_item(layer='L0',category='preference',memory_key='drink',content='喜欢咖啡',
                                    source_conversation_id='desktop_test',confidence=.95)
        assert conflict['outcome'] == 'candidate'
        assert db.get_structured_memory(mid)['status'] == 'active'
        assert client.post(f'/api/memory/items/{mid}/sleep').status_code == 200
        assert '只喝温水' not in revisions.correction_context()
        assert '只喝温水' not in revisions.sanitize_history('只喝温水')
        save_memory_item(layer='L0',category='preference',memory_key='drink',content='只喝温水',
                         source_conversation_id='desktop_test',confidence=1)
        assert db.get_structured_memory(mid)['status'] == 'sleeping'
        assert client.post(f'/api/memory/items/{mid}/wake').status_code == 200
        assert '只喝温水' in revisions.correction_context()
        assert client.post(f'/api/memory/items/{mid}/sleep').status_code == 200
        assert client.delete(f'/api/memory/items/{mid}').status_code == 200
        assert '只喝温水' not in revisions.correction_context()


def parent_for(job):
    task = tasks.prepare(run_id='audit-run', request_id='audit-request', conversation_id='desktop_test',
        source='desktop', user_message='create', model_id='mock', reasoning_level='off', allowed_tools=[], context=[])
    task = tasks.update(task['id'], status='waiting_jobs', waiting_jobs=[job['id']])
    with db.get_conn() as conn:
        conn.execute("INSERT INTO agent_run_steps(run_id,step_index,step_kind,tool_name,status,idempotency_key,result_json,created_at,updated_at) VALUES(?,0,'tool_call','create','completed','audit-step',?,?,?)",
                     ('audit-run', json.dumps({'job':job}), db.now_iso(), db.now_iso()))
    return task


def test_retry_completion_reconnects_parent_observations_and_survives_late_original():
    job, connection = remote_job(); creation._mark_remote_unknown(job['id'], 'synthetic')
    task = parent_for(job)
    assert not tasks.ready_to_continue(task)
    with patch.object(creation, '_provider_connection', return_value=connection):
        retry = creation.retry_job(job['id'])
        assert creation.retry_job(job['id'])['id'] == retry['id']
    assert tasks.get(task['id'])['waiting_jobs'] == [retry['id']]
    assert tasks.observations(task['id'])[0]['result']['job']['id'] == retry['id']
    assert tasks.resume(task['id'])['status'] == 'waiting_jobs'
    assert not tasks.ready_to_continue(tasks.get(task['id']))
    with patch.object(creation, 'schedule_job'): creation.confirm_job(retry['id'])
    creation._update_job(retry['id'], status='completed', stage='completed')
    creation._update_job(job['id'], status='failed', stage='late_result')
    tasks.recover_interrupted()
    assert tasks.observations(task['id'])[0]['result']['job']['status'] == 'completed'
    assert tasks.ready_to_continue(tasks.get(task['id']))
    assert tasks.resume(task['id'])['status'] == 'ready'


def test_cancelled_retry_does_not_hide_original_unknown():
    job, connection = remote_job(); creation._mark_remote_unknown(job['id'], 'synthetic')
    task = parent_for(job)
    with patch.object(creation, '_provider_connection', return_value=connection):
        retry = creation.retry_job(job['id'])
        asyncio.run(creation.cancel_job(retry['id']))
        with pytest.raises(ValueError, match='仍未知'): tasks.resume(task['id'])
        next_retry = creation.retry_job(job['id'])
        assert next_retry['id'] != retry['id']


def test_concurrent_retry_requests_create_one_attempt():
    job, connection = remote_job(); creation._mark_remote_unknown(job['id'], 'synthetic')
    with patch.object(creation, '_provider_connection', return_value=connection), ThreadPoolExecutor(2) as pool:
        attempts = list(pool.map(lambda _: creation.retry_job(job['id']), range(2)))
    assert attempts[0]['id'] == attempts[1]['id']


def test_verification_cache_tracks_package_and_resource_changes_and_expiry(managed):
    python = put(managed/'.genie-env/Scripts/python.exe')
    package = put(managed/'.genie-env/Lib/site-packages/jieba/__init__.py')
    model = put(managed/'GenieData/chinese-hubert-base/model.onnx')
    resource = put(managed/'GenieData/G2P/test.txt')
    with patch.object(probe.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
        probe.verify('genie_runtime', python, model)
        assert probe.cached('genie_runtime', python, model)['ok']
        package.unlink()
        assert not probe.cached('genie_runtime', python, model)
        probe.verify('genie_runtime', python, model)
        resource.write_bytes(b'repaired resource')
        assert not probe.cached('genie_runtime', python, model)
        probe.verify('genie_runtime', python, model)
        with patch.object(probe.time, 'monotonic', return_value=10**15):
            assert not probe.cached('genie_runtime', python, model)
        probe.invalidate()
        assert not probe.cached('genie_runtime', python, model)


def test_dependency_list_and_diagnostics_share_verification(managed):
    from app.diagnostic_export import snapshot
    for name in ('.genie-env/Scripts/python.exe', 'GenieData/chinese-hubert-base/chinese-hubert-base.onnx',
                 'GenieData/G2P/ChineseG2P/opencpop-strict.txt','GenieData/G2P/ChineseG2P/polyphonic.pickle'):
        put(managed/name)
    verification = {'ok':True,'checked_at':'2026-09-19T10:00:00+00:00','verification_level':'model_load','error_code':''}
    with patch.object(probe, 'cached', return_value=verification), patch('app.local_vision_service.passive_status', return_value={}):
        item = next(item for item in deps.list_dependencies() if item['id'] == 'genie_runtime')
        diagnostic = next(item for item in snapshot()['dependencies'] if item['id'] == 'genie_runtime')
    assert item['verified'] and diagnostic['verified']
    assert diagnostic['checked_at'] == verification['checked_at']
    with patch.object(deps, 'list_dependencies', return_value=[{'id':'whisper','status':'degraded',
        'verification':{'ok':False,'error_code':'missing_runtime_dependency','detail':'private path'}}]), \
        patch('app.local_vision_service.passive_status', return_value={}):
        diagnostic = snapshot()['dependencies'][0]
    assert diagnostic['has_error'] and diagnostic['error_code'] == 'missing_runtime_dependency'
    assert 'private' not in str(diagnostic)
