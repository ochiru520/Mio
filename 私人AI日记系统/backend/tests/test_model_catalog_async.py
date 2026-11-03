"""The catalog endpoint must stay responsive while optional discovery stalls."""
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from app import model_management_service as service
from app.routes.model_management import router


@pytest.fixture
def catalog_state(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'settings', SimpleNamespace(voice_training_dir=tmp_path/'voice', local_vision_dir=tmp_path/'vision', companion_dir=tmp_path/'companion'))
    monkeypatch.setattr(service, '_CATALOG_KEY', None)
    monkeypatch.setattr(service, '_CATALOG_CACHE', {})
    monkeypatch.setattr(service, '_CATALOG_RUNNING', set())
    monkeypatch.setattr(service, '_CATALOG_REPEAT', set())
    monkeypatch.setattr(service, '_catalog_storage', lambda: [])
    monkeypatch.setattr(service, 'hardware', lambda: {'gpus': []})
    yield
    deadline = time.monotonic() + 3
    while service._CATALOG_RUNNING and time.monotonic() < deadline:
        time.sleep(.01)
    assert not service._CATALOG_RUNNING


def finished():
    deadline = time.monotonic() + 3
    while service._CATALOG_RUNNING and time.monotonic() < deadline:
        time.sleep(.01)
    assert not service._CATALOG_RUNNING


def test_slow_probe_does_not_block_list_or_duplicate_workers(catalog_state, monkeypatch):
    release = threading.Event(); calls = []
    def probe():
        calls.append(1)
        assert release.wait(3)
        return [{'id': 'observed', 'installed': True}]
    monkeypatch.setattr(service, '_catalog_items', probe)
    try:
        start = time.monotonic(); result = service.catalog()
        assert time.monotonic() - start < .5
        assert result['items'] and all(row['installed'] is None for row in result['items'])
        assert result['sections']['items']['state'] == 'checking'
        for _ in range(20): service.catalog(refresh=True)
        assert len(calls) == 1
    finally: release.set(); finished()
    assert service.catalog()['items'][0]['id'] == 'observed'


def test_failure_keeps_previous_observation_and_other_sections(catalog_state, monkeypatch):
    monkeypatch.setattr(service, '_catalog_items', lambda: [{'id': 'kept', 'installed': True}])
    service.catalog(); finished()
    def failure(): raise OSError('private path or detail')
    monkeypatch.setattr(service, '_catalog_items', failure)
    service.catalog(refresh=True); finished()
    result = service.catalog()
    assert result['items'][0]['id'] == 'kept'
    assert result['sections']['items']['state'] == 'error'
    assert result['sections']['items']['stale']
    assert result['sections']['hardware']['state'] == 'ready'
    assert 'private path' not in json.dumps(result)


def test_localhost_http_returns_before_all_slow_discovery(catalog_state, monkeypatch, tmp_path):
    release = threading.Event()
    def blocked():
        assert release.wait(5)
        return []
    for field in ('_catalog_items', '_catalog_storage', 'hardware'):
        monkeypatch.setattr(service, field, blocked)
    import socket
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(128)
    port = listener.getsockname()[1]
    app = FastAPI(); app.include_router(router)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True); worker.start()
    deadline = time.monotonic() + 3
    while not server.started and time.monotonic() < deadline: time.sleep(.01)
    try:
        with httpx.Client(timeout=2) as client:
            start = time.perf_counter(); response = client.get(f'http://127.0.0.1:{port}/api/model-management')
            elapsed = time.perf_counter() - start
        assert response.status_code == 200 and elapsed < 1
        assert len(response.json()['items']) > 3
        assert response.json()['checking']
        print(f'localhost catalog with three blocked workers: {elapsed:.4f}s')
    finally:
        release.set(); finished(); server.should_exit = True; worker.join(3); listener.close()
    assert not worker.is_alive()
