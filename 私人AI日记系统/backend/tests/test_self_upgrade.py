"""Disposable source snapshots and local authorization; no model or installed app."""
import asyncio
import json
import os
import sys
import threading
import py_compile
import shutil
import socket
import subprocess
import time
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, self_upgrade_service as service, self_upgrade_tools as tools
from app import workspace_service as workspace, code_workspace_service as code
from app.routes.self_upgrade import router
from test_runtime_safety_v2 import isolated


@pytest.fixture
def sources(tmp_path, monkeypatch):
    backend = tmp_path / 'backend-source'; desktop = tmp_path / 'desktop-source'
    (backend / 'backend/app').mkdir(parents=True); (desktop / 'desktop').mkdir(parents=True)
    (backend / 'backend/app/main.py').write_text('user_dirty_change = 42\n', encoding='utf-8')
    (desktop / 'package-lock.json').write_text('{}')
    (desktop / '构建Windows应用.ps1').write_text('# fixture')
    (desktop / 'desktop/build_manifest.py').write_text("from pathlib import Path\nimport hashlib\ndef repository_state(root):\n    files=sorted(p for p in root.rglob('*') if p.is_file() and p.name != '.env')\n    return {'commit':'fixture','dirty':True,'dirty_hash':hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest()}\n")
    monkeypatch.setattr(service, '_processes', {})
    monkeypatch.setattr(service, '_threads', {})
    monkeypatch.setattr(service, '_cancel', {})
    monkeypatch.setattr(service, '_installer', None)
    monkeypatch.setattr(service, 'installed_build', lambda: {'build_id': 'base-fixture', 'source_mode': True, 'sources': {}, 'program_root': str(tmp_path / 'installed')})
    config = service.configure(backend_root=str(backend), desktop_root=str(desktop), expected_revision=0,
                               python_executable=sys.executable)
    return backend, desktop, config


@pytest.mark.parametrize('writer', ['save', 'atomic'])
def test_job_read_waits_for_concurrent_json_replacement(tmp_path, monkeypatch, writer):
    job = {'id': 'upgrade_' + '1'*32, 'status': 'completed'}
    path = service._job_path(job['id'])
    service._atomic(path, job)
    replacing = threading.Event(); release = threading.Event()
    reader_started = threading.Event(); reader_done = threading.Event()
    errors = []; results = []
    original_replace = os.replace
    original_read = Path.read_text

    def paused_replace(source, destination):
        if Path(destination) == path:
            replacing.set()
            if not release.wait(5):
                raise TimeoutError('test did not release JSON replacement')
        return original_replace(source, destination)

    def guarded_read(target, *args, **kwargs):
        if target == path and replacing.is_set() and not release.is_set():
            raise PermissionError('simulated Windows replacement sharing conflict')
        return original_read(target, *args, **kwargs)

    def write():
        try:
            if writer == 'save':
                service._save(job, status='cancelled')
            else:
                service._atomic(path, dict(job, status='cancelled'))
        except Exception as exc:
            errors.append(exc)

    def read():
        reader_started.set()
        try:
            results.append(service.get(job['id']))
        except Exception as exc:
            errors.append(exc)
        finally:
            reader_done.set()

    monkeypatch.setattr(os, 'replace', paused_replace)
    monkeypatch.setattr(Path, 'read_text', guarded_read)
    writing = threading.Thread(target=write)
    reading = threading.Thread(target=read)
    writing.start()
    try:
        assert replacing.wait(3)
        reading.start()
        assert reader_started.wait(3)
        assert not reader_done.wait(0.2), 'reader entered an in-progress JSON replacement'
    finally:
        release.set()
        writing.join(3)
        if reading.ident is not None:
            reading.join(3)
    assert not writing.is_alive() and not reading.is_alive()
    assert not errors
    assert len(results) == 1 and results[0]['status'] == 'cancelled'


def test_json_read_preserves_missing_default_and_reports_permission_errors(tmp_path, monkeypatch):
    path = tmp_path / 'job.json'
    default = {'missing': True}
    assert service._read(path, default) is default

    def denied(*args, **kwargs):
        raise PermissionError('genuine access denial')

    monkeypatch.setattr(Path, 'read_text', denied)
    with pytest.raises(PermissionError, match='genuine access denial'):
        service._read(path, default)


def test_snapshot_keeps_dirty_source_excludes_credentials_data_dependencies(tmp_path):
    root = tmp_path / 'source'; root.mkdir()
    for name in ('src', 'node_modules', '数据', '.venv', 'generated'):
        (root / name).mkdir()
    (root / 'src/edit.py').write_text('uncommitted = True', encoding='utf-8')
    for name in ('.env', 'release-signing-key.dpapi', 'private.db', 'src/token.key', 'node_modules/code.js', '数据/diary.json', '.venv/python.exe', 'generated/out.json'):
        (root / name).write_text('DO NOT COPY', encoding='utf-8')
    result = service.snapshot(root, tmp_path / 'copy')
    assert [item['path'] for item in result['files']] == ['src/edit.py']
    assert (tmp_path / 'copy/src/edit.py').read_text() == 'uncommitted = True'


def test_create_binds_existing_code_tools_and_preserves_original(sources):
    backend, _, config = sources
    conversation = 'desktop_agent_self_upgrade_fixture'; db.create_agent_conversation(conversation)
    job = service.create(goal='fixture goal', acceptance=['source copied'], expected_revision=config['revision'], conversation_id=conversation)
    copied = Path(job['work_root']) / '私人AI日记系统/backend/app/main.py'
    assert copied.read_text() == (backend / 'backend/app/main.py').read_text()
    binding = workspace.project_for_conversation(conversation)
    assert binding['id'] == job['project_id']
    config = code.configuration(binding['id'])
    assert config['read_enabled'] and config['write_enabled'] and not config['execute_enabled']
    copied.write_text('candidate_change = True')
    assert 'user_dirty_change' in (backend / 'backend/app/main.py').read_text()
    with pytest.raises(ValueError, match='已有自身改造'):
        service.create(goal='parallel', acceptance=['no'], expected_revision=1)
    service.stop(job['id'])
    assert not (service._state_root() / 'development.lock').exists()


def test_config_revision_and_installed_source_mismatch_block_before_snapshot(sources, monkeypatch):
    backend, desktop, config = sources
    with pytest.raises(ValueError, match='另一窗口'):
        service.configure(backend_root=str(backend), desktop_root=str(desktop), expected_revision=0)
    monkeypatch.setattr(service, 'installed_build', lambda: {'build_id': 'production', 'source_mode': False, 'sources': {'backend': {'commit': 'wrong'}}})
    with pytest.raises(ValueError, match='不匹配'):
        service.create(goal='modify', acceptance=['works'], expected_revision=config['revision'])
    assert not (service._state_root() / 'development.lock').exists()


def test_ui_job_without_context_creates_real_development_conversation(sources):
    job = service.create(goal='new UI feature', acceptance=['real bound conversation'], expected_revision=1)
    assert job['conversation_id'].startswith('desktop_agent_self_upgrade_')
    assert workspace.project_for_conversation(job['conversation_id'])['id'] == job['project_id']
    service.stop(job['id'])


def test_actual_nonzero_process_is_saved_as_failure_and_environment_has_no_secret(sources, monkeypatch):
    _, _, config = sources
    job = service.create(goal='test failure', acceptance=['exit code recorded'], expected_revision=1)
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-not-a-real-key')
    env = service._environment(job)
    assert 'OPENAI_API_KEY' not in env and env['MIO_DISABLE_DOTENV'] == '1'
    with pytest.raises(ValueError, match='退出码 7'):
        service._command(job, 'failure', [sys.executable, '-c', "print('real failure output'); raise SystemExit(7)"], Path(job['work_root']))
    saved = service.get(job['id'])
    assert saved['checks'][-1]['exit_code'] == 7 and saved['checks'][-1]['ok'] is False
    assert 'real failure output' in saved['checks'][-1]['output_tail']
    service.stop(job['id'])


def test_interrupted_running_job_is_not_reported_passed(sources):
    job = service.create(goal='test interrupt', acceptance=['recover record'], expected_revision=1)
    service._save(job, status='building')
    recovered = service.get(job['id'])
    assert recovered['status'] == 'interrupted' and not recovered['candidate']
    assert not (service._state_root() / 'development.lock').exists()


def test_default_confirmation_does_not_call_installer_and_model_cannot_confirm(sources, monkeypatch):
    job = service.create(goal='confirm', acceptance=['user approval'], expected_revision=1)
    job['candidate'] = {'build_id': 'target'}
    job['preview'] = {'verified': True}
    service._save(job, status='preview_ready')
    monkeypatch.setitem(service._processes, job['id'], SimpleNamespace())
    called = []; monkeypatch.setattr(service, '_installer', lambda *args: called.append(args))
    result = service.apply(job['id'])
    assert result['needs_confirmation'] and not called and result['status'] == 'preview_ready'
    definition = next(item for item in tools.DEFINITIONS if item.name == 'self_upgrade_request_apply')
    with pytest.raises(ValueError):
        definition.validate_arguments({'job_id': job['id'], 'confirmed': True})
    with pytest.raises(ValueError):
        tools.Start.model_validate({'goal': 'x', 'acceptance': ['y'], 'expected_revision': 1, 'allow_auto_apply': True})


def test_strict_user_confirmation_and_configuration_api(sources):
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        response = client.post('/api/self-upgrade/jobs/upgrade_' + '1'*32 + '/apply', json={'confirmed': 'true'})
        assert response.status_code == 422


def test_remote_channel_cannot_start_self_modification(sources):
    context = SimpleNamespace(conversation_id='desktop_agent_fixture', source='wechat', task_id='')
    with pytest.raises(ValueError, match='本机桌面'):
        asyncio.run(tools.dispatch('self_upgrade_start', {'goal': 'x', 'acceptance': ['y'], 'expected_revision': 1}, context))


def test_real_candidate_worker_imports_compiled_modules_uses_synthetic_data_blocks_writes(sources, tmp_path):
    """Real uvicorn child on compiled backend fixture, not an EXE packaging claim."""
    program = tmp_path / 'compiled-preview'
    app_root = Path(service.__file__).parent
    compiled = program / '_internal/app'; compiled.mkdir(parents=True)
    for source in app_root.rglob('*.py'):
        target = compiled / source.relative_to(app_root).with_suffix('.pyc')
        target.parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(str(source), cfile=str(target), doraise=True)
    for folder in ('templates', 'static'):
        shutil.copytree(app_root / folder, compiled / folder)
    frontend = program / '_internal/agent_frontend'; frontend.mkdir()
    (frontend / 'index.html').write_text('<html>synthetic candidate preview fixture</html>', encoding='utf-8')
    (program / '构建清单.json').write_text(json.dumps({'schema_version': 1, 'build_id': 'worker-fixture', 'sources': {}}), encoding='utf-8')
    (program / '_internal/build_identity.json').write_text((program/'构建清单.json').read_text('utf-8'), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    job = service.create(goal='worker', acceptance=['compiled app starts'], expected_revision=1)
    log = tmp_path / 'worker.log'
    with log.open('wb') as output:
        # Frozen noarchive builds expose adjacent pyc, no source py required.
        process = subprocess.Popen([sys.executable, str(compiled/'self_upgrade_preview_worker.pyc'),
            '--program-root', str(program), '--port', str(port), '--parent-pid', str(os.getpid())],
            env=service._environment(job), stdout=output, stderr=subprocess.STDOUT,
            creationflags=0x08000000 if os.name == 'nt' else 0)
    base = 'http://127.0.0.1:' + str(port)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(log.read_text('utf-8', errors='replace'))
            try:
                with urlopen(base+'/health', timeout=1) as response:
                    health = json.load(response)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail('candidate worker never became healthy: ' + log.read_text('utf-8', errors='replace'))
        assert health['build_id'] == 'worker-fixture' and health['preview']
        with urlopen(base+'/agent-app/', timeout=2) as response:
            assert b'synthetic candidate preview fixture' in response.read()
        with urlopen(base+'/api/onboarding/status', timeout=2) as response:
            assert json.load(response)['completed'] is True
        with urlopen(base+'/api/agent/bootstrap', timeout=5) as response:
            bootstrap = json.load(response)
        assert bootstrap['onboarding']['mode'] == 'synthetic_candidate_preview'
        agent_conversation = next(item for item in bootstrap['conversations'] if item['kind'] == 'agent')
        assert agent_conversation['id'] == 'desktop_agent_candidate_preview'
        assert not bootstrap['qq']['enabled'] and bootstrap['qq']['diagnostic_code'] == 'candidate_preview'
        for endpoint in ('/api/agent/messages?conversation_id=desktop_agent_candidate_preview',
                         '/api/agent/context-usage?conversation_id=desktop_agent_candidate_preview',
                         '/api/companion/chat-settings', '/api/settings/profile', '/api/agent/models/catalog',
                         '/api/workspace', '/api/workspace/conversations', '/api/extensions',
                         '/api/agent/work', '/api/execution/policy', '/api/agent/surface', '/api/artifacts'):
            with urlopen(base + endpoint, timeout=5) as response:
                assert response.status == 200
                json.load(response)
        with pytest.raises(HTTPError) as failure:
            urlopen(Request(base+'/api/self-upgrade/jobs', data=b'{}', method='POST'), timeout=2)
        assert failure.value.code == 403
        assert Path(service._environment(job)['MIO_RUNTIME_ROOT']).is_relative_to(Path(job['work_root']).parent)
        # Reopening the same preview must retain synthetic state without a duplicate seed.
        service._terminate(process)
        with log.open('ab') as output:
            process = subprocess.Popen([sys.executable, str(compiled/'self_upgrade_preview_worker.pyc'),
                '--program-root', str(program), '--port', str(port), '--parent-pid', str(os.getpid())],
                env=service._environment(job), stdout=output, stderr=subprocess.STDOUT,
                creationflags=0x08000000 if os.name == 'nt' else 0)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(log.read_text('utf-8', errors='replace'))
            try:
                with urlopen(base+'/health', timeout=1) as response:
                    assert json.load(response)['build_id'] == 'worker-fixture'
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail('reopened preview never became healthy')
        with urlopen(base+'/api/agent/bootstrap', timeout=5) as response:
            reopened = json.load(response)
        assert sum(item['id'] == 'desktop_agent_candidate_preview' for item in reopened['conversations']) == 1
    finally:
        service._terminate(process); service.stop(job['id'])


def test_source_identity_matches_build_manifest_byte_algorithm(sources):
    backend, desktop, config = sources
    import importlib.util
    real = Path(__file__).resolve().parents[3] / '澪Agent应用/desktop/build_manifest.py'
    # Test fixture locations do not contain desktop repository; use source sibling for algorithm only.
    if not real.exists():
        real = Path(__file__).resolve().parents[3].parent / '澪Agent应用/desktop/build_manifest.py'
    if not real.exists():
        pytest.skip('desktop source not available in isolated copied backend')
    spec = importlib.util.spec_from_file_location('fixture_build_algorithm', real)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    for name, root in [('backend', backend), ('desktop', desktop)]:
        assert service._source_identity(config)[name] == module.repository_state(root)


def test_stop_task_cancels_owned_process_and_records_actual_cancel(sources):
    job = service.create(goal='cancel process', acceptance=['tree stopped'], expected_revision=1, task_id='task-fixture')
    def run(job):
        service._command(job, 'long-command', [sys.executable, '-c', 'import time; time.sleep(30)'], Path(job['work_root']), 60)
        service._save(job, status='validated')
    service._start(job['id'], 'validating', run)
    deadline = time.monotonic()+5
    while job['id'] not in service._processes and time.monotonic()<deadline:
        time.sleep(0.01)
    process = service._processes[job['id']]
    result = service.stop_task('task-fixture')
    thread = service._threads.get(job['id'])
    if thread:
        thread.join(3)
    assert process.poll() is not None and job['id'] in result['stopped']
    assert service.get(job['id'])['status'] == 'cancelled'


def test_local_install_identity_auto_binds_only_matching_complete_sources(sources, monkeypatch, tmp_path):
    backend, desktop, config = sources
    manifest = tmp_path / 'build.json'
    payload = {'schema_version': 1, 'build_id': 'installed-fixture', 'source_paths': {'backend': str(backend), 'desktop': str(desktop)},
               'sources': service._source_identity(config)}
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    monkeypatch.setattr('app.runtime_identity.runtime_identity', lambda: {'manifest_path': str(manifest)})
    (service._state_root() / 'configuration.json').unlink()
    bound = service.configuration()
    assert bound['revision'] == 1 and bound['backend_root'] == str(backend) and bound['allow_auto_apply'] is False
    (service._state_root() / 'configuration.json').unlink()
    (backend / 'backend/app/main.py').write_text('changed after build')
    assert service.configuration()['revision'] == 0


def test_frontend_lock_dependencies_are_installed_before_tests_once_and_reinstalled_on_change(sources, monkeypatch):
    job = service.create(goal='deps', acceptance=['clean snapshot can import Vue'], expected_revision=1)
    desktop = Path(job['work_root']) / '澪Agent应用'
    (desktop / 'package.json').write_text('{"dependencies":{"vue":"3.5.40"}}')
    calls = []
    def run(job, label, arguments, cwd, timeout=300):
        calls.append((label, arguments))
        (desktop / 'node_modules/vue').mkdir(parents=True, exist_ok=True)
        (desktop / 'node_modules/vue/package.json').write_text('{}')
        return {'ok': True}
    monkeypatch.setattr(service, '_command', run)
    service._frontend_dependencies(job); service._frontend_dependencies(job)
    assert len(calls) == 1 and '--ignore-scripts' in calls[0][1]
    (desktop / 'package-lock.json').write_text('{"lockfileVersion":3}')
    service._frontend_dependencies(job)
    assert len(calls) == 2
    service.stop(job['id'])


def _ready_application_job(sources, monkeypatch):
    job = service.create(goal='idle install', acceptance=['all operations idle'], expected_revision=1)
    service._save(job, status='preview_ready', preview={'verified': True}, candidate={
        'root': str(Path(job['work_root']).parent/'fixture-program'), 'manifest_path': str(Path(job['work_root']).parent/'fixture.json'),
        'manifest_sha256': 'a'*64, 'build_id': 'target'})
    # This isolates the queue contract; file/manifest validation is tested by the desktop suite.
    monkeypatch.setitem(service._processes, job['id'], SimpleNamespace(poll=lambda: None, pid=-1))
    monkeypatch.setattr(service, '_terminate', lambda process: None)
    monkeypatch.setattr(service, '_desktop_module', lambda name: SimpleNamespace(verify_candidate=lambda *args, **kwargs: {}))
    calls = []; monkeypatch.setattr(service, '_installer', lambda *args: calls.append(args) or {'state': 'installing'})
    return job, calls


def test_apply_queue_waits_for_self_and_other_operations_without_bypassing_installer(sources, monkeypatch):
    from app import model_runtime
    job, calls = _ready_application_job(sources, monkeypatch)
    active = ['own-agent', 'another-chat']
    monkeypatch.setattr(model_runtime, 'live_operations', lambda: list(active))
    result = service.apply(job['id'], confirmed=True)
    assert result['pending'] and result['application_queued'] and not calls
    active.remove('own-agent'); time.sleep(0.25)
    assert service.get(job['id'])['status'] == 'awaiting_idle' and not calls
    active.clear()
    deadline = time.monotonic()+3
    while not calls and time.monotonic()<deadline:
        time.sleep(0.05)
    assert len(calls) == 1 and service.get(job['id'])['status'] == 'installing'


def test_user_stop_cancels_waiting_install_before_any_native_install(sources, monkeypatch):
    from app import model_runtime
    job, calls = _ready_application_job(sources, monkeypatch)
    monkeypatch.setattr(model_runtime, 'live_operations', lambda: ['own-agent'])
    service.apply(job['id'], confirmed=True)
    service.stop(job['id'])
    deadline = time.monotonic()+3
    while job['id'] in service._threads and time.monotonic()<deadline:
        time.sleep(0.02)
    assert service.get(job['id'])['status'] == 'cancelled' and not calls
