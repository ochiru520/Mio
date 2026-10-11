import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import capability_checks as checks, capability_probe_worker as probes, maintenance_service, model_runtime
from app.config import settings
from app.routes.capabilities import router


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    previous = dict(vars(settings))
    for key in ('data_dir', 'voice_training_dir', 'local_vision_dir'):
        object.__setattr__(settings, key, tmp_path / key)
    for key in ('runtime_config_path','model_profiles_path','companion_config_path'):
        object.__setattr__(settings, key, tmp_path / (key + '.json'))
    monkeypatch.setattr(checks, '_active', {})
    monkeypatch.setattr(model_runtime, 'privacy_blocked', lambda: False)
    from app import dependency_installer
    monkeypatch.setattr(dependency_installer, '_running_installs', {})
    maintenance_service.reset_runtime_state()
    try:
        yield
    finally:
        maintenance_service.reset_runtime_state()
        for key, value in previous.items():
            object.__setattr__(settings, key, value)


def row(key='chat'):
    return next(item for item in checks.snapshot()['capabilities'] if item['id'] == key)


async def finish():
    await asyncio.gather(*list(checks._active.values()))


def test_get_never_runs_a_probe_or_creates_state(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(checks, '_execute', execute)
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        value = client.get('/api/capabilities').json()
    assert len(value['capabilities']) == 6
    assert all(item['status'] == 'unchecked' for item in value['capabilities'])
    execute.assert_not_called()
    assert not checks._path().exists()


def test_api_rejects_missing_consent_and_unknown_capability():
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        assert client.post('/api/capabilities/chat/check', json={}).status_code == 409
        assert client.post('/api/capabilities/unknown/check', json={'consent':True}).status_code == 404
    assert not checks._path().exists()


def test_success_survives_restart_and_configuration_change_invalidates(monkeypatch):
    monkeypatch.setattr(checks, '_execute', AsyncMock(return_value={'ok':True,'level':'inference'}))
    async def run():
        await checks.start('chat', consent=True)
        await finish()
    asyncio.run(run())
    assert row()['status'] == 'passed'
    assert row()['level'] == 'inference'
    monkeypatch.setattr(checks, '_active', {})
    assert row()['status'] == 'passed'
    settings.model_profiles_path.write_text('{"test":"changed"}', encoding='utf-8')
    assert row()['status'] == 'stale'
    assert row()['error_code'] == 'configuration_changed'


def test_result_expires_and_unknown_running_result_is_interrupted():
    checks._save('chat', {'status':'passed','checked_at':'2000-01-01T00:00:00+00:00','fingerprint':checks.fingerprint()})
    assert row()['error_code'] == 'expired'
    checks._save('chat', {'status':'running'})
    assert row()['status'] == 'interrupted'


def test_cancel_before_task_starts_releases_slot(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(checks, '_execute', execute)
    async def run():
        await checks.start('tts', consent=True)
        await checks.cancel('tts', row('tts')['run_id'])
        assert not checks._active
        assert row('tts')['status'] == 'cancelled'
    asyncio.run(run())
    execute.assert_not_called()


def test_timeout_cleans_up_and_allows_retry(monkeypatch):
    cleaned = []
    async def execute(_):
        try: await asyncio.sleep(10)
        finally: cleaned.append(True)
    monkeypatch.setattr(checks, '_execute', execute)
    monkeypatch.setattr(checks, 'TIMEOUT_SECONDS', .01)
    async def run():
        await checks.start('tts', consent=True)
        await finish()
        assert row('tts')['error_code'] == 'timeout'
        assert not checks._active
        await checks.start('tts', consent=True)
        await checks.cancel_all()
    asyncio.run(run())
    assert cleaned


def test_late_success_after_configuration_change_is_not_passed(monkeypatch):
    async def execute(_):
        settings.companion_config_path.write_text('{}', encoding='utf-8')
        return {'ok':True,'level':'inference'}
    monkeypatch.setattr(checks, '_execute', execute)
    async def run():
        await checks.start('tts', consent=True)
        await finish()
    asyncio.run(run())
    assert row('tts')['status'] == 'stale'


def test_duplicate_and_stale_cancel_do_not_affect_current_check(monkeypatch):
    async def execute(_): await asyncio.sleep(10)
    monkeypatch.setattr(checks, '_execute', execute)
    async def run():
        await checks.start('chat', consent=True)
        with pytest.raises(ValueError): await checks.start('chat', consent=True)
        with pytest.raises(ValueError): await checks.cancel('chat', 'f' * 32)
        assert checks._active
        await checks.cancel_all()
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['privacy', 'maintenance'])
def test_sensitive_pause_blocks_execution(kind, monkeypatch):
    if kind == 'privacy': monkeypatch.setattr(model_runtime, 'privacy_blocked', lambda: True)
    else: maintenance_service.begin('test')
    with pytest.raises(ValueError): asyncio.run(checks.start('chat', consent=True))


def test_raw_errors_are_not_persisted_or_exported(monkeypatch):
    async def fail(_): raise RuntimeError('secret-key and private conversation and /private/path')
    monkeypatch.setattr(checks, '_execute', fail)
    async def run():
        await checks.start('chat', consent=True)
        await finish()
    asyncio.run(run())
    assert row()['error_code'] == 'probe_failed'
    assert 'secret-key' not in checks._path().read_text(encoding='utf-8')
    assert 'fingerprint' not in json.dumps(checks.snapshot())


def test_qq_connection_is_not_send_evidence(monkeypatch):
    from app.routes import onebot
    object.__setattr__(settings, 'napcat_account', '123')
    monkeypatch.setattr(onebot,'runtime_health_status',lambda: {'enabled':True,'connected_accounts':['999']})
    assert not asyncio.run(checks._execute('qq'))['ok']
    monkeypatch.setattr(onebot,'runtime_health_status',lambda: {'enabled':True,'connected_accounts':['123']})
    result = asyncio.run(checks._execute('qq'))
    assert result['ok'] and result['level'] == 'connection'


def test_audio_checks_reject_silent_or_invalid_output():
    data = io.BytesIO()
    with wave.open(data, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 16000)
    assert not probes.audio_valid(data.getvalue())
    assert not probes.audio_valid(b'not audio')


@pytest.mark.parametrize('payload,success', [({'choices':[{'message':{'content':''}}]}, False),
                                           ({'choices':[{'message':{'content':'OK'}}]}, True)])
def test_chat_requires_actual_text(payload, success, monkeypatch):
    from app import model_registry
    from app.routes import models
    monkeypatch.setattr(model_registry, 'get_model_profile', lambda: SimpleNamespace(api_key='test',
        api_key_error='',base_urls=['https://invalid.example'],model='test',auth_scheme='bearer',api_mode='chat_completions'))
    response = httpx.Response(200, json=payload, request=httpx.Request('POST','https://invalid.example'))
    monkeypatch.setattr(models,'_request_chat_via_routes',AsyncMock(return_value=(response,)))
    if success: assert asyncio.run(probes.chat())['ok']
    else:
        with pytest.raises(probes.ProbeFailure): asyncio.run(probes.chat())


def test_actual_worker_can_finish_with_isolated_empty_configuration(tmp_path, monkeypatch):
    # The subprocess reloads configuration; parent-only settings mocks do not isolate it.
    monkeypatch.setenv('MIO_DISABLE_DOTENV', '1')
    monkeypatch.setenv('MIO_RUNTIME_ROOT', str(tmp_path / 'worker-runtime'))
    monkeypatch.setenv('MIO_COMFYUI_ROOT', str(tmp_path / 'missing-comfyui'))
    monkeypatch.setenv('MIO_COMFYUI_BASE_URL', 'http://127.0.0.1:1')
    result = asyncio.run(checks._run_worker('comfyui'))
    assert result['ok'] is False
    assert result['error_code'] in {'unavailable', 'probe_failed'}


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object integration')
def test_closing_probe_job_terminates_owned_child_tree(tmp_path):
    from app.probe_process import ProbeProcess
    marker = tmp_path / 'child.json'
    script = ('import subprocess,sys,time,json; from pathlib import Path; '
              'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
              'Path(sys.argv[1]).write_text(json.dumps({"pid":child.pid})); time.sleep(60)')
    proc = ProbeProcess([sys.executable,'-c',script,str(marker)],cwd=str(tmp_path))
    try:
        deadline = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < deadline: time.sleep(.05)
        assert marker.exists()
        child_id = json.loads(marker.read_text())['pid']
    finally:
        proc.close()
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x100000,False,child_id)
    if handle:
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        assert kernel.WaitForSingleObject(handle,5000) == 0
        kernel.CloseHandle(handle)
