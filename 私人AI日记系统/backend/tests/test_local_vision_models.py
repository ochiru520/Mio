"""Upgrade preservation and bounded per-model operations using tiny fake assets."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app import dependency_installer
from app import local_vision_models as models
from app import local_vision_service as vision


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, 'settings', SimpleNamespace(local_vision_dir=tmp_path))
    monkeypatch.setattr(dependency_installer, '_install_running', lambda _: False)
    for key, value in dict(_server_process=None, _pull_process=None, _pull_model='', _server_url='',
                           _last_error='', _probe_at=0, _probe_cache={}, _inference_probe_at=0,
                           _inference_probe_result={}, _server_desired_running=False, _active_inferences=0).items():
        monkeypatch.setattr(vision, key, value)
    executable = vision._ollama_executable(); executable.parent.mkdir(); executable.write_bytes(b'runtime')
    monkeypatch.setattr(vision, 'start_server', lambda: {})
    monkeypatch.setattr(vision, 'stop_server', lambda: {})
    monkeypatch.setattr(vision, 'unload_model', lambda: {})
    return tmp_path


def install(model_id, content=b'weights', shared=b'config'):
    manifest = models.manifest_path(model_id); manifest.parent.mkdir(parents=True, exist_ok=True)
    blobs = models._root() / 'models' / 'blobs'; blobs.mkdir(parents=True, exist_ok=True)
    rows = []
    for payload in (shared, content):
        digest = hashlib.sha256(payload).hexdigest()
        (blobs / ('sha256-' + digest)).write_bytes(payload)
        rows.append(dict(digest='sha256:' + digest, size=len(payload)))
    manifest.write_text(json.dumps(dict(config=rows[0], layers=[rows[1]])), encoding='utf-8')
    return manifest


def verify(model_id, monkeypatch):
    def request(path, **kw):
        assert path == '/api/chat'
        assert kw['payload']['model'] == model_id
        assert kw['payload']['messages'][0]['images']
        return dict(message=dict(content='A small white image.'), done=True)
    monkeypatch.setattr(vision, '_request_json', request)
    return models.verify(model_id)


def test_upgrade_keeps_old_model_and_read_only_inventory_does_not_write(library):
    install(models.LEGACY_MODEL)
    inventory = models.inventory()
    assert inventory['selected_model'] == models.LEGACY_MODEL
    assert '旧视觉模型已保留' in inventory['migration_notice']
    assert vision.installation_status()['model_installed']
    assert vision._image_payload(prompt='p', image=b'i', system_prompt='s', stream=False)['model'] == models.LEGACY_MODEL
    assert not (library / models.STATE_FILENAME).exists()


def test_upgrade_with_both_models_keeps_legacy_and_current_2b_only_uses_recommended(library):
    old = install(models.LEGACY_MODEL); install(models.RECOMMENDED_MODEL)
    assert models.effective_model() == models.LEGACY_MODEL
    old.unlink()
    assert models.effective_model() == models.RECOMMENDED_MODEL


def test_explicit_missing_selection_is_not_silently_replaced(library):
    install(models.RECOMMENDED_MODEL)
    models._write_state(dict(selected_model=models.LEGACY_MODEL, selection_origin='user'))
    assert models.effective_model() == models.LEGACY_MODEL
    assert not vision.installation_status()['model_installed']
    item = next(row for row in models.inventory()['models'] if row['selected'])
    assert item['id'] == models.LEGACY_MODEL and item['status'] == 'missing'


def test_corrupt_selection_is_preserved_without_fallback(library):
    path = library / models.STATE_FILENAME; path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError, match='不会自动'):
        models.effective_model()
    assert path.read_text() == '{broken'


def test_download_never_switches_and_first_pins_legacy(library, monkeypatch):
    install(models.LEGACY_MODEL)
    start = Mock(); monkeypatch.setattr(vision, '_start_model_pull', start)
    models.start_download(models.RECOMMENDED_MODEL)
    start.assert_called_once_with(models.RECOMMENDED_MODEL)
    assert models._read_state()['selected_model'] == models.LEGACY_MODEL
    install(models.RECOMMENDED_MODEL)
    assert models.effective_model() == models.LEGACY_MODEL


def test_failed_image_verification_keeps_selection_and_cannot_switch(library, monkeypatch):
    install(models.LEGACY_MODEL); install(models.RECOMMENDED_MODEL)
    monkeypatch.setattr(vision, '_request_json', Mock(side_effect=OSError('no memory')))
    result = models.verify(models.RECOMMENDED_MODEL)
    assert not result['ok']
    assert models.effective_model() == models.LEGACY_MODEL
    with pytest.raises(ValueError, match='验证'):
        models.select(models.RECOMMENDED_MODEL)
    assert models.effective_model() == models.LEGACY_MODEL


def test_switch_requires_current_verified_files_and_keeps_old(library, monkeypatch):
    old = install(models.LEGACY_MODEL); new = install(models.RECOMMENDED_MODEL)
    with pytest.raises(ValueError, match='验证'):
        models.select(models.RECOMMENDED_MODEL)
    assert verify(models.RECOMMENDED_MODEL, monkeypatch)['ok']
    assert models.effective_model() == models.LEGACY_MODEL
    result = models.select(models.RECOMMENDED_MODEL)
    assert result['ok'] and old.exists() and new.exists()
    identity = models.selection_identity()
    assert identity[0] == models.RECOMMENDED_MODEL and identity[1]
    models.select(models.RECOMMENDED_MODEL)
    assert models.selection_identity()[1] != identity[1]
    assert vision.passive_status()['configured_model'] == models.RECOMMENDED_MODEL
    install(models.RECOMMENDED_MODEL, b'changed weights')
    with pytest.raises(ValueError, match='验证'):
        models.select(models.RECOMMENDED_MODEL)


def test_unknown_or_path_model_ids_are_rejected(library):
    for value in ('../../data', 'other:7b', '', 'qwen3.5:2b/../x'):
        with pytest.raises(ValueError):
            models.start_download(value)
        with pytest.raises(ValueError):
            models.removal_preview(value)


def test_current_model_cannot_be_deleted(library):
    install(models.LEGACY_MODEL)
    with pytest.raises(ValueError, match='当前使用'):
        models.removal_preview(models.LEGACY_MODEL)


def test_delete_old_preserves_shared_blobs_runner_and_new_model(library, monkeypatch):
    old = install(models.LEGACY_MODEL, b'old'); new = install(models.RECOMMENDED_MODEL, b'new')
    verify(models.RECOMMENDED_MODEL, monkeypatch); models.select(models.RECOMMENDED_MODEL)
    plan = models.removal_preview(models.LEGACY_MODEL)
    assert len(plan['shared_files']) == 1
    assert len(plan['paths']) == 2  # old manifest + its exclusive weights
    result = models.remove(models.LEGACY_MODEL, plan['token'])
    assert result['ok'] and not old.exists() and new.exists()
    assert vision._ollama_executable().exists()
    assert vision.installation_status()['model_installed']
    assert all(Path(path).is_file() for path in plan['shared_files'])
    assert models.effective_model() == models.RECOMMENDED_MODEL


def test_removal_stale_shared_reference_rejected(library, monkeypatch):
    install(models.LEGACY_MODEL, b'old'); install(models.RECOMMENDED_MODEL, b'new')
    verify(models.RECOMMENDED_MODEL, monkeypatch); models.select(models.RECOMMENDED_MODEL)
    plan = models.removal_preview(models.LEGACY_MODEL)
    # Another model starts referencing the old assets after preview.
    another = library / 'models/manifests/registry.ollama.ai/library/custom/alias'
    another.parent.mkdir(parents=True)
    another.write_bytes(models.manifest_path(models.LEGACY_MODEL).read_bytes())
    with pytest.raises(ValueError, match='重新预览'):
        models.remove(models.LEGACY_MODEL, plan['token'])
    assert models.manifest_path(models.LEGACY_MODEL).exists()


def test_malformed_other_manifest_blocks_delete_instead_of_guessing(library, monkeypatch):
    install(models.LEGACY_MODEL); install(models.RECOMMENDED_MODEL)
    verify(models.RECOMMENDED_MODEL, monkeypatch); models.select(models.RECOMMENDED_MODEL)
    another = library / 'models/manifests/registry.ollama.ai/library/custom/alias'
    another.parent.mkdir(parents=True); another.write_text('{}')
    with pytest.raises(ValueError, match='共享'):
        models.removal_preview(models.LEGACY_MODEL)


def test_selection_switch_and_delete_rejected_while_request_held(library, monkeypatch):
    install(models.LEGACY_MODEL); install(models.RECOMMENDED_MODEL)
    verify(models.RECOMMENDED_MODEL, monkeypatch)
    vision._begin_image_request()
    try:
        with pytest.raises(ValueError, match='正在识图'):
            models.select(models.RECOMMENDED_MODEL)
        with pytest.raises(ValueError, match='正在识图'):
            models.verify(models.RECOMMENDED_MODEL)
    finally:
        vision._end_image_request()
    assert models.select(models.RECOMMENDED_MODEL)['ok']


def test_async_image_uses_saved_legacy_model_and_releases_reservation(library, monkeypatch):
    install(models.LEGACY_MODEL)
    async def ready(): return {}
    monkeypatch.setattr(vision, 'ensure_ready', ready)
    monkeypatch.setattr(vision, '_owned_server_url', lambda: 'http://127.0.0.1:12345')
    def request(req):
        payload = json.loads(req.content)
        assert payload['model'] == models.LEGACY_MODEL
        with pytest.raises(ValueError, match='正在识图'):
            models.start_download(models.RECOMMENDED_MODEL)
        return httpx.Response(200, json=dict(message=dict(content='画面内容')))
    client = httpx.AsyncClient
    monkeypatch.setattr(vision.httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(request), **kw))
    result = asyncio.run(vision.analyze_image(prompt='p', image=b'i', system_prompt='s'))
    assert models.LEGACY_MODEL in result.model
    assert vision._active_inferences == 0


def test_cancel_download_stops_only_pull_and_preserves_selection(library, monkeypatch):
    install(models.LEGACY_MODEL); models.pin_current()
    process = Mock(); process.poll.return_value = None
    process.terminate.side_effect = lambda: setattr(process.poll, 'return_value', 0)
    monkeypatch.setattr(vision, '_pull_process', process)
    monkeypatch.setattr(vision, '_pull_model', models.RECOMMENDED_MODEL)
    assert models.download_status()['can_cancel']
    result = models.cancel_download()
    process.terminate.assert_called_once()
    assert not result['download']['active'] and models.effective_model() == models.LEGACY_MODEL


def test_download_exit_zero_without_complete_assets_reports_incomplete(library, monkeypatch):
    process = Mock(); process.poll.return_value = 0
    monkeypatch.setattr(vision, '_pull_process', process)
    monkeypatch.setattr(vision, '_pull_model', models.RECOMMENDED_MODEL)
    assert '不完整' in models.download_status()['error']
    install(models.RECOMMENDED_MODEL)
    assert not models.download_status()['error']


def test_environment_detects_legacy_without_changing_default(library):
    from app import environment_check_service
    install(models.LEGACY_MODEL)
    assert environment_check_service._ollama_model_ready(library / 'models')
    assert vision.DEFAULT_MODEL == models.RECOMMENDED_MODEL


def test_hardlinked_old_assets_are_preserved(library, monkeypatch):
    install(models.LEGACY_MODEL, b'old'); install(models.RECOMMENDED_MODEL, b'new')
    verify(models.RECOMMENDED_MODEL, monkeypatch); models.select(models.RECOMMENDED_MODEL)
    old_assets = models._assets(models.manifest_path(models.LEGACY_MODEL))
    exclusive = next(p for p in old_assets if p.read_bytes() == b'old')
    external = library / 'external-weight.bin'
    os.link(exclusive, external)
    plan = models.removal_preview(models.LEGACY_MODEL)
    assert str(exclusive) in plan['shared_files']
    assert models.remove(models.LEGACY_MODEL, plan['token'])['ok']
    assert exclusive.read_bytes() == external.read_bytes() == b'old'


def test_failed_staging_restores_all_files_and_selection(library, monkeypatch):
    install(models.LEGACY_MODEL, b'old'); install(models.RECOMMENDED_MODEL, b'new')
    verify(models.RECOMMENDED_MODEL, monkeypatch); models.select(models.RECOMMENDED_MODEL)
    plan = models.removal_preview(models.LEGACY_MODEL)
    rename = Path.rename
    def fail_second(path, destination):
        if str(path) == plan['paths'][1]:
            raise OSError('locked')
        return rename(path, destination)
    monkeypatch.setattr(Path, 'rename', fail_second)
    with pytest.raises(OSError, match='locked'):
        models.remove(models.LEGACY_MODEL, plan['token'])
    assert all(Path(p).exists() for p in plan['paths'])
    assert models.effective_model() == models.RECOMMENDED_MODEL
    assert not list(library.rglob('.mio-model-removal-*'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell installer')
def test_installer_preserves_complete_legacy_model_without_launch(library):
    install(models.LEGACY_MODEL)
    script = Path(__file__).resolve().parents[3] / '澪Agent应用/scripts/deps/install-ollama-vision.ps1'
    status = library / 'status.json'
    env = dict(os.environ, MIO_LOCAL_VISION_DIR=str(library), MIO_STATUS_FILE=str(status))
    result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                            env=env, capture_output=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    progress = json.loads(status.read_text(encoding='utf-8-sig'))
    assert progress['done'] and not progress['error']
    assert models._read_state()['selected_model'] == models.LEGACY_MODEL
    assert not models.manifest_path(models.RECOMMENDED_MODEL).exists()


@pytest.mark.parametrize('stream', [False, True])
def test_async_requests_never_block_api_event_loop_during_maintenance(library, monkeypatch, stream):
    """A slow background model check must not freeze unrelated FastAPI requests."""
    install(models.RECOMMENDED_MODEL)
    held, release = threading.Event(), threading.Event()
    def maintain():
        with vision._lifecycle_lock:
            held.set(); release.wait(timeout=2)
    worker = threading.Thread(target=maintain)
    worker.start()
    assert held.wait(timeout=1)
    async def run():
        started = time.monotonic()
        if stream:
            pieces = vision.iter_image_text(prompt='p', image=b'i', system_prompt='s')
            try:
                with pytest.raises(ValueError, match='正在安装、验证或切换'):
                    await asyncio.wait_for(anext(pieces), timeout=.2)
            finally:
                await pieces.aclose()
        else:
            with pytest.raises(ValueError, match='正在安装、验证或切换'):
                await asyncio.wait_for(vision.analyze_image(prompt='p', image=b'i', system_prompt='s'), timeout=.2)
        assert time.monotonic() - started < .2
    try:
        asyncio.run(run())
        assert vision._active_inferences == 0
    finally:
        release.set(); worker.join(timeout=2)
    assert not worker.is_alive()


@pytest.mark.parametrize('response', [dict(message=dict(content='text only without done')),
                                     dict(message=dict(content='unfinished'), done=False),
                                     dict(message=dict(content='truncated'), done=True, done_reason='length')])
def test_verification_requires_completed_image_response(library, monkeypatch, response):
    install(models.LEGACY_MODEL); install(models.RECOMMENDED_MODEL)
    monkeypatch.setattr(vision, '_request_json', lambda *args, **kw: response)
    assert not models.verify(models.RECOMMENDED_MODEL)['ok']
    assert models.effective_model() == models.LEGACY_MODEL
    with pytest.raises(ValueError, match='验证'):
        models.select(models.RECOMMENDED_MODEL)


def test_delete_state_write_failure_restores_files_before_unlink(library, monkeypatch):
    install(models.LEGACY_MODEL, b'old'); install(models.RECOMMENDED_MODEL, b'new')
    verify(models.RECOMMENDED_MODEL, monkeypatch); models.select(models.RECOMMENDED_MODEL)
    plan = models.removal_preview(models.LEGACY_MODEL)
    state = (library / models.STATE_FILENAME).read_bytes()
    monkeypatch.setattr(models, '_write_state', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        models.remove(models.LEGACY_MODEL, plan['token'])
    assert all(Path(raw).exists() for raw in plan['paths'])
    assert (library / models.STATE_FILENAME).read_bytes() == state
    assert not list(library.rglob('.mio-model-removal-*'))


def test_locked_blob_retains_manifest_and_can_be_deleted_on_retry(library, monkeypatch):
    install(models.LEGACY_MODEL, b'old'); install(models.RECOMMENDED_MODEL, b'new')
    verify(models.RECOMMENDED_MODEL, monkeypatch); models.select(models.RECOMMENDED_MODEL)
    plan = models.removal_preview(models.LEGACY_MODEL)
    unlink = Path.unlink
    blocked = {'once': True}
    def locked(path, *args, **kwargs):
        if path.name.startswith('.mio-model-removal-') and path.read_bytes() == b'old' and blocked['once']:
            blocked['once'] = False
            raise OSError('blob locked')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    result = models.remove(models.LEGACY_MODEL, plan['token'])
    assert not result['ok'] and result['remaining_files']
    assert models.manifest_path(models.LEGACY_MODEL).exists()
    item = next(item for item in result['catalog']['models'] if item['id'] == models.LEGACY_MODEL)
    assert item['can_remove']
    retry = models.removal_preview(models.LEGACY_MODEL)
    assert models.remove(models.LEGACY_MODEL, retry['token'])['ok']
    assert not models.manifest_path(models.LEGACY_MODEL).exists()


@pytest.mark.parametrize('digest', ['sha256:model', 'sha256:' + 'z' * 64, 'sha256:' + 'a' * 63])
def test_invalid_ollama_digest_is_missing_and_inventory_does_not_crash(library, digest):
    manifest = install(models.RECOMMENDED_MODEL)
    payload = json.loads(manifest.read_text())
    payload['layers'][0] = dict(digest=digest, size=3)
    manifest.write_text(json.dumps(payload))
    (library / 'models/blobs' / digest.replace(':', '-')).write_bytes(b'bad')
    models._write_state(dict(selected_model=models.RECOMMENDED_MODEL,
                             verified={models.RECOMMENDED_MODEL: dict(ready=True, fingerprint='previous')}))
    assert not vision.installation_status()['model_installed']
    item = next(item for item in models.inventory()['models'] if item['selected'])
    assert item['status'] == 'incomplete' and not item['verified']


def run_installer_without_network(library):
    """Run the real installer control flow with fake download/server boundaries."""
    import shutil
    scripts = Path(__file__).resolve().parents[3] / '澪Agent应用/scripts/deps'
    copy = library / 'installer'; copy.mkdir()
    shutil.copy2(scripts / 'common.ps1', copy / 'common.ps1')
    source = (scripts / 'install-ollama-vision.ps1').read_text(encoding='utf-8-sig')
    stubs = r'''
function Invoke-Download {
    $snapshot = @{ model = $modelName; profile = $env:USERPROFILE; home = $env:HOME; models = $env:OLLAMA_MODELS }
    [IO.File]::WriteAllText((Join-Path $visionDir "fake-runtime-download.json"), ($snapshot | ConvertTo-Json))
    New-Item -ItemType Directory -Force -Path $ollamaDir | Out-Null
    [IO.File]::WriteAllBytes($ollamaExe, [Text.Encoding]::UTF8.GetBytes("fake-runtime"))
}
function Expand-Archive { param($LiteralPath, $DestinationPath, [switch]$Force) }
function Start-Process { param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru); return [PSCustomObject]@{ HasExited = $true } }
function Wait-OllamaReady { param($Process, $HostAddress) }
function Invoke-OllamaPull {
    param($HostAddress, $Model)
    [IO.File]::WriteAllText((Join-Path $visionDir "fake-pulled-model.txt"), $Model)
    $parts = $Model.Split(":")
    $target = Join-Path $modelsDir ("manifests\registry.ollama.ai\library\" + $parts[0] + "\" + $parts[1])
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Copy-Item -LiteralPath (Join-Path $visionDir "fake-manifest.json") -Destination $target
}
'''
    source = source.replace('$serverProcess = $null', stubs + '\n$serverProcess = $null', 1)
    script = copy / 'install-ollama-vision.ps1'; script.write_text(source, encoding='utf-8-sig')
    status = library / 'simulated-install-status.json'
    env = dict(os.environ, MIO_LOCAL_VISION_DIR=str(library), MIO_STATUS_FILE=str(status))
    completed = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                               env=env, capture_output=True, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', errors='replace')
    result = json.loads(status.read_text(encoding='utf-8-sig'))
    assert result['done'] and not result['error']
    return result


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell installer')
def test_first_install_uses_private_profile_and_downloads_recommended_model(library):
    manifest = install(models.RECOMMENDED_MODEL)
    manifest.replace(library / 'fake-manifest.json')
    vision._ollama_executable().unlink()
    run_installer_without_network(library)
    snapshot = json.loads((library / 'fake-runtime-download.json').read_text())
    assert Path(snapshot['profile']) == library / 'home'
    assert Path(snapshot['home']) == library / 'home'
    assert Path(snapshot['models']) == library / 'models'
    assert snapshot['model'] == models.RECOMMENDED_MODEL
    assert (library / 'fake-pulled-model.txt').read_text() == models.RECOMMENDED_MODEL
    assert models._read_state()['selected_model'] == models.RECOMMENDED_MODEL


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell installer')
def test_empty_runtime_is_repaired_without_forcing_new_model(library):
    install(models.LEGACY_MODEL)
    vision._ollama_executable().write_bytes(b'')
    assert not vision.installation_status()['runtime_installed']
    run_installer_without_network(library)
    assert vision.installation_status()['runtime_installed']
    assert (library / 'fake-runtime-download.json').exists()
    assert not (library / 'fake-pulled-model.txt').exists()
    assert models._read_state()['selected_model'] == models.LEGACY_MODEL
