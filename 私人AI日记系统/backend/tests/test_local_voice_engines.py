"""Real child/HTTP lifecycle and streaming failures, without models or private assets."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import Mock, patch

import httpx
import pytest

from app import aniflive_tts_service
from app import local_voice_engines as engines
from app import companion_service, genie_tts_service
from app.config import settings


def free_port():
    with socket.socket() as value:
        value.bind(("127.0.0.1", 0))
        return value.getsockname()[1]


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError("Expected lifecycle transition did not occur")


@pytest.fixture
def installed(tmp_path, monkeypatch):
    script = tmp_path / "server.py"
    script.write_text('from http.server import BaseHTTPRequestHandler, HTTPServer\nimport sys\nclass H(BaseHTTPRequestHandler):\n def log_message(self,*args): pass\n def do_GET(self):\n  body=b\'{"ready":true,"model_version":"v5turbo"}\'\n  self.send_response(200);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)\nHTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()\n', encoding="utf-8")
    port = free_port()
    entry = {"python": sys.executable, "script": str(script), "cwd": str(tmp_path), "args": [str(port)], "url": f"http://127.0.0.1:{port}"}
    manifest = tmp_path / "engines.json"
    manifest.write_text(json.dumps({"schema": 1, "engines": {"gpt_sovits_v5": entry, "aniflive": {**entry, "url": f"http://127.0.0.1:{free_port()}", "args": []}}}), encoding="utf-8")
    monkeypatch.setenv("MIO_VOICE_ENGINES_MANIFEST", str(manifest))
    monkeypatch.setattr(engines, "resource_check", lambda _root: None)
    return entry, manifest


def test_owned_real_child_becomes_ready_then_releases_port(installed):
    entry, _ = installed
    manager = engines.LocalVoiceManager()
    try:
        manager.start("gpt_sovits_v5", {})
        child = manager.child
        until(lambda: not manager.status("gpt_sovits_v5", {})["service_loading"])
        assert manager.status("gpt_sovits_v5", {})["managed_running"]
        manager.start("gpt_sovits_v5", {})
        assert manager.child is child
        manager.stop("aniflive")
        assert child.poll() is None
        manager.stop("gpt_sovits_v5")
        assert child.poll() is not None
        assert not manager.status("gpt_sovits_v5", {})["managed_running"]
        def port_released():
            with socket.socket() as probe:
                return probe.connect_ex(("127.0.0.1", int(entry["args"][0]))) != 0
        until(port_released)
    finally:
        manager.stop_all()


def test_port_occupant_never_adopted_or_stopped(installed):
    entry, _ = installed
    port = int(entry["args"][0])
    manager = engines.LocalVoiceManager()
    with socket.socket() as independent:
        independent.bind(("127.0.0.1", port)); independent.listen()
        with pytest.raises(OSError, match="独立服务"):
            manager.start("gpt_sovits_v5", {})
        manager.stop_all()
        assert independent.getsockname()[1] == port
        assert manager.child is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object descendant shutdown')
def test_switch_cleans_dead_launcher_descendant_before_external_port_check(installed, monkeypatch):
    entry, manifest = installed
    launcher = Path(entry['cwd']) / 'launcher.py'
    launcher.write_text('import subprocess,sys\nsubprocess.Popen([sys.executable,"-u",*sys.argv[1:]])\n', encoding='utf-8')
    replacement = {**entry, 'url': f'http://127.0.0.1:{free_port()}'}
    replacement['args'] = [str(replacement['url'].rsplit(':', 1)[1])]
    manifest.write_text(json.dumps({'schema': 1, 'engines': {
        'gpt_sovits_v5': {**entry, 'script': str(launcher), 'args': [entry['script'], *entry['args']]},
        'indextts2': replacement,
    }}), encoding='utf-8')
    manager = engines.LocalVoiceManager()
    # Freeze health monitoring to deterministically exercise a dead launcher whose
    # still-owned descendant is listening before the monitor's next one-second tick.
    monkeypatch.setattr(manager, '_monitor', lambda *_: None)

    def listening(port):
        with socket.socket() as probe:
            probe.settimeout(.2)
            return probe.connect_ex(('127.0.0.1', port)) == 0

    old_port, new_port = int(entry['args'][0]), int(replacement['args'][0])
    try:
        manager.start('gpt_sovits_v5', {})
        original = manager.child
        until(lambda: original.poll() is not None and listening(old_port))
        manager.start('indextts2', {})
        assert original.job is None
        assert not listening(old_port), 'owned descendants must be gone when switch returns'
        until(lambda: listening(new_port))
        current = manager.child
        manager.stop_all()
        assert current.job is None
        assert not listening(new_port), 'stop must confirm the service exited before returning'
        # The same port can immediately be used again, without an arbitrary cooldown.
        manager.start('indextts2', {})
        until(lambda: listening(new_port))
    finally:
        manager.stop_all()


def test_shutdown_failure_retains_ownership_and_blocks_second_model(installed, monkeypatch):
    manager = engines.LocalVoiceManager()
    original = Mock()
    original.poll.return_value = 0
    original.close.side_effect = TimeoutError('owned descendants still exiting')
    manager.child, manager.engine = original, 'aniflive'
    spawn = Mock()
    monkeypatch.setattr(engines, 'OwnedProcess', spawn)
    with pytest.raises(OSError, match='未能完全退出'):
        manager.start('gpt_sovits_v5', {})
    spawn.assert_not_called()
    assert manager.child is original
    assert manager.closed.is_set()
    assert manager.status('aniflive', {})['managed_last_error']
    original.close.side_effect = None
    manager.stop_all()
    assert manager.child is None


def test_same_engine_start_retries_incomplete_stop_instead_of_reporting_success(installed, monkeypatch):
    manager = engines.LocalVoiceManager()
    original = Mock()
    original.poll.return_value = None
    original.close.side_effect = TimeoutError('shutdown incomplete')
    manager.child, manager.engine = original, 'gpt_sovits_v5'
    spawn = Mock()
    monkeypatch.setattr(engines, 'OwnedProcess', spawn)
    with pytest.raises(OSError, match='未能完全退出'):
        manager.stop_all()
    with pytest.raises(OSError, match='未能完全退出'):
        manager.start('gpt_sovits_v5', {})
    assert original.close.call_count == 2
    assert manager.child is original
    spawn.assert_not_called()
    original.close.side_effect = None
    manager.stop_all()


def test_owned_port_release_timeout_never_kills_or_adopts_listener(installed, monkeypatch):
    entry, _ = installed
    manager = engines.LocalVoiceManager()
    original = Mock()
    original.poll.return_value = 0
    manager.child, manager.engine, manager.url = original, 'gpt_sovits_v5', entry['url']
    with socket.socket() as independent:
        independent.bind(('127.0.0.1', int(entry['args'][0])))
        independent.listen()
        # Model a listener that rebinds a former owned URL during TCP teardown.
        # Expire only the shutdown clock, without a slow real five-second wait.
        with patch.object(engines.time, 'monotonic', side_effect=[0, 6]):
            with pytest.raises(OSError, match='未能完全退出'):
                manager.stop_all()
        assert manager.child is original
        assert independent.getsockname()[1] == int(entry['args'][0])
        assert engines.loopback_port_open(entry['url'])
    manager.stop_all()
    assert manager.child is None


def test_new_external_listener_after_owned_stop_is_not_adopted(installed, monkeypatch):
    entry, manifest = installed
    manager = engines.LocalVoiceManager()
    original = Mock()
    original.poll.return_value = 0
    manager.child, manager.engine, manager.url = original, 'aniflive', json.loads(manifest.read_text())['engines']['aniflive']['url']
    spawn = Mock()
    monkeypatch.setattr(engines, 'OwnedProcess', spawn)
    with socket.socket() as independent:
        def replace_with_external():
            independent.bind(('127.0.0.1', int(entry['args'][0])))
            independent.listen()
        original.close.side_effect = replace_with_external
        with pytest.raises(OSError, match='独立服务'):
            manager.start('gpt_sovits_v5', {})
        spawn.assert_not_called()
        manager.stop_all()
        assert independent.getsockname()[1] == int(entry['args'][0])
        assert manager.child is None


def test_invalid_machine_manifest_and_changed_remote_url_cannot_launch(installed):
    _, manifest = installed
    manager = engines.LocalVoiceManager()
    with pytest.raises(OSError, match="不一致"):
        manager.start("gpt_sovits_v5", {"gpt_sovits_v5_url": "http://remote.example:9885"})
    manifest.write_text("[]")
    assert not manager.status("gpt_sovits_v5", {})["managed_available"]
    assert manager.child is None


def test_failed_child_is_diagnosed_and_stopped(installed):
    _, manifest = installed
    manager = engines.LocalVoiceManager()
    try:
        manager.start("aniflive", {})
        until(lambda: bool(manager.status("aniflive", {})["managed_last_error"]))
        status = manager.status("aniflive", {})
        assert not status["managed_running"] and not status["service_loading"]
        assert "退出" in status["managed_last_error"]
    finally:
        manager.stop_all()


def test_late_original_warmup_never_stops_new_owned_engine():
    manager = engines.LocalVoiceManager()
    selected = {"voice_engine": "gpt_sovits", "local_voice_runtime": "gpt_sovits"}
    manager.selection_reader = lambda: selected
    entered, release = threading.Event(), threading.Event()
    child = Mock()
    child.poll.return_value = None
    def delayed_original_start():
        entered.set()
        assert release.wait(3)
        companion_service.start_gpt_sovits_service()
    with patch.object(engines, "manager", manager), patch.object(companion_service, "load_config", side_effect=lambda: selected), \
         patch.object(companion_service, "_external_voice_service", return_value=None), patch.object(companion_service, "_uses_genie_runtime", return_value=False), \
         patch.object(companion_service, "_ensure_gpt_sovits_service", side_effect=delayed_original_start), \
         patch.object(companion_service, "_start_gpt_sovits_service_impl") as original_spawn:
        worker = threading.Thread(target=companion_service.warm_voice_runtime)
        with companion_service._voice_runtime_metrics_lock:
            companion_service._voice_runtime_metrics["warmup_state"] = "idle"
        worker.start()
        assert entered.wait(3)
        selected = {"voice_engine": "aniflive"}
        manager.child, manager.engine = child, "aniflive"
        release.set(); worker.join(3)
        assert not worker.is_alive()
        original_spawn.assert_not_called()
        child.close.assert_not_called()
        assert manager.child is child


def test_saved_new_selection_blocks_original_spawn_even_before_child_exists():
    manager = engines.LocalVoiceManager()
    manager.selection_reader = lambda: {"voice_engine": "aniflive"}
    with patch.object(engines, "manager", manager), patch.object(genie_tts_service, "runtime_status") as assets, \
         patch.object(companion_service, "_start_gpt_sovits_service_impl") as original_spawn:
        with pytest.raises(OSError, match="已切换"):
            companion_service.start_gpt_sovits_service()
        with pytest.raises(OSError, match="已切换"):
            genie_tts_service._start_worker()
        original_spawn.assert_not_called()
        assets.assert_not_called()


@pytest.fixture
def bridge():
    spec = importlib.util.spec_from_file_location("mio_test_anif_bridge", settings.agent_control_scripts_dir / "aniflive_experimental_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from http.server import ThreadingHTTPServer
    servers = []

    def start(generate, **kwargs):
        server = ThreadingHTTPServer(("127.0.0.1", 0), module.handler_for(generate, **kwargs))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start(); servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}"
    yield start
    for server, thread in servers:
        server.shutdown(); server.server_close(); thread.join(2)


def test_experimental_bridge_real_adapter_language_and_session_cleanup(bridge):
    received = []
    def generate(segments, language, cancelled):
        received.append((segments, language))
        yield b"\x10\x00" * 160
    url = bridge(generate)
    status = aniflive_tts_service.runtime_status({"aniflive_url": url})
    assert status["experimental"] and not status["numerical_verified"]
    assert b"".join(aniflive_tts_service.iter_speech_pcm("こんにちは。", {"aniflive_url": url}, language="ja")) == b"\x10\x00" * 160
    assert received == [(("こんにちは。",), "ja")]
    with httpx.Client(trust_env=False) as client:
        assert client.post(url + "/v1/sessions", json={"model": "wrong"}).status_code == 400
        key = client.post(url + "/v1/sessions", json={}).json()["id"]
        assert client.get(url + f"/v1/sessions/{key}/audio").status_code == 409
        assert client.post(url + f"/v1/sessions/{key}/cancel", json={}).status_code == 200
        assert client.get(url + f"/v1/sessions/{key}/audio").status_code == 404


def test_reference_voice_identity_is_explicit_and_other_profiles_are_rejected(bridge):
    def generate(*_args):
        yield b"\x10\x00" * 160
    url = bridge(generate, voice_profile="mio-reference", reference_language="ja", reference_kind="user-provided")
    with httpx.Client(trust_env=False) as client:
        health = client.get(url + "/health").json()
        assert health["voice_profile"] == "mio-reference"
        assert health["reference_language"] == "ja"
        assert not health["public_synthetic_reference"]
        assert health["reference_kind"] == "user-provided"
        assert not health["numerical_verified"]
        # An explicit request for a different voice must not silently speak Mio.
        assert client.post(url + "/v1/sessions", json={"voice_profile": "default"}).status_code == 400
    config = {"aniflive_url": url, "aniflive_voice_profile": "mio-reference"}
    assert b"".join(aniflive_tts_service.iter_speech_pcm("声の確認です。", config, language="ja")) == b"\x10\x00" * 160


@pytest.mark.parametrize("prefix", [False, True])
def test_acoustic_exception_is_error_never_successful_silence(bridge, prefix):
    def generate(*_args):
        if prefix:
            yield b"\x10\x00" * 160
        raise RuntimeError("synthetic model failure")
    url = bridge(generate)
    with pytest.raises(OSError):
        b"".join(aniflive_tts_service.iter_speech_pcm("测试", {"aniflive_url": url}))
