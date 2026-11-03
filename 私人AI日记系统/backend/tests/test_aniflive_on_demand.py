"""Isolated cold Anif lifecycle tests: inert registration, synthetic HTTP state."""
import copy
import json
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import aniflive_tts_service as anif, companion_service as companion, local_voice_engines as engines


@pytest.fixture
def cold(tmp_path, monkeypatch):
    config = {'voice_engine': 'aniflive', 'aniflive_url': 'http://127.0.0.1:19882',
              'aniflive_model': 'mio-v2-aniflive', 'aniflive_voice_profile': 'mio',
              'voice_startup_enabled': False, 'voice_volume': 100}
    saved = copy.deepcopy(config)
    script = tmp_path/'inert.py'; script.write_text('# never executed')
    entry = {'python': sys.executable, 'script': str(script), 'cwd': str(tmp_path),
             'url': config['aniflive_url'], 'args': [], 'startup_timeout': 2}
    manifest = tmp_path/'engines.json'; manifest.write_text(json.dumps({'schema': 1, 'engines': {'aniflive': entry}}))
    monkeypatch.setenv('MIO_VOICE_ENGINES_MANIFEST', str(manifest))
    monkeypatch.setattr(companion, 'load_config', lambda: copy.deepcopy(saved))
    manager = engines.LocalVoiceManager()
    start = Mock()
    def started(engine, config):
        start(engine, config); manager.engine = engine; manager.url = config['aniflive_url']
        manager.child = Mock(); manager.child.poll.return_value = None
    monkeypatch.setattr(manager, 'start', started)
    monkeypatch.setattr(engines, 'manager', manager)
    stop = Mock(); monkeypatch.setattr(companion, '_stop_owned_original_voice', stop)
    port = Mock(return_value=False); monkeypatch.setattr(engines, 'loopback_port_open', port)
    health = Mock(return_value={'service_running': True, 'identity_matches_selection': True})
    monkeypatch.setattr(anif, 'runtime_status', health)
    yield SimpleNamespace(config=config, saved=saved, manifest=manifest, manager=manager,
                          start=start, stop=stop, health=health, port=port, entry=entry)


def test_cold_start_keeps_startup_setting_and_uses_saved_snapshot(cold):
    companion._ensure_aniflive_service(cold.config)
    cold.stop.assert_called_once(); cold.start.assert_called_once_with('aniflive', cold.config)
    assert cold.saved['voice_startup_enabled'] is False
    assert cold.health.call_count == 1


@pytest.mark.parametrize('field,value', [('voice_engine', 'gpt_sovits'), ('aniflive_url', 'http://127.0.0.1:19889'),
                                       ('aniflive_model', 'new-model'), ('aniflive_voice_profile', 'other')])
def test_stale_snapshot_never_stops_or_starts_a_new_selection(cold, field, value):
    cold.saved[field] = value
    with pytest.raises(OSError, match='已切换'):
        companion._ensure_aniflive_service(cold.config)
    cold.stop.assert_not_called(); cold.start.assert_not_called(); cold.health.assert_not_called()


def test_selection_changed_while_waiting_cannot_restart_or_stop_new_engine(cold):
    def changed(_config):
        cold.saved['voice_engine'] = 'cloud'
        return {'service_running': True, 'identity_matches_selection': True}
    cold.health.side_effect = changed
    with pytest.raises(OSError, match='已切换'):
        companion._ensure_aniflive_service(cold.config)
    assert cold.start.call_count == cold.stop.call_count == 1


def test_pre_cancelled_request_never_launches(cold):
    cancel = threading.Event(); cancel.set()
    with pytest.raises(InterruptedError): companion._ensure_aniflive_service(cold.config, cancel_event=cancel)
    cold.stop.assert_not_called(); cold.start.assert_not_called(); cold.health.assert_not_called()


def test_cancel_while_loading_exits_without_stopping_shared_or_new_service(cold):
    cancel = threading.Event()
    def waiting(_config): cancel.set(); return {'service_running': False}
    cold.health.side_effect = waiting
    with pytest.raises(InterruptedError): companion._ensure_aniflive_service(cold.config, cancel_event=cancel)
    assert cold.start.call_count == cold.stop.call_count == 1


def test_independent_listening_service_is_used_without_ownership(cold):
    cold.port.return_value = True
    companion._ensure_aniflive_service(cold.config)
    cold.stop.assert_not_called(); cold.start.assert_not_called(); cold.health.assert_not_called()
    assert cold.manager.child is None


@pytest.mark.parametrize('kind', ['missing', 'invalid', 'custom', 'remote'])
def test_unregistered_custom_and_remote_keep_direct_http(cold, kind):
    if kind == 'missing': cold.manifest.unlink()
    if kind == 'invalid': cold.manifest.write_text('[]')
    if kind == 'custom': cold.config['aniflive_url'] = 'http://127.0.0.1:19889'
    if kind == 'remote': cold.config['aniflive_url'] = 'https://example.invalid'
    companion._ensure_aniflive_service(cold.config)
    cold.stop.assert_not_called(); cold.start.assert_not_called(); cold.health.assert_not_called()


def test_ready_wrong_identity_is_a_clear_failure(cold):
    cold.health.return_value = {'service_running': True, 'identity_matches_selection': False, 'last_error': '模型或音色不匹配'}
    with pytest.raises(OSError, match='不匹配'): companion._ensure_aniflive_service(cold.config)


def test_timeout_and_owned_failure_are_not_reported_as_ready(cold, monkeypatch):
    cold.health.return_value = {'service_running': False}
    with pytest.raises(TimeoutError, match='加载超时'): companion._ensure_aniflive_service(cold.config, timeout_seconds=0)
    monkeypatch.setattr(cold.manager, 'status', lambda *_: {'managed_running': False, 'managed_last_error': '内存保护已停止模型'})
    with pytest.raises(OSError, match='内存保护'): companion._ensure_aniflive_service(cold.config)


def test_revalidation_failure_cannot_release_original_model(cold, monkeypatch):
    real_load = engines.load_entry; calls = []
    def changed(*args):
        calls.append(1)
        if len(calls) == 2: raise OSError('注册已变化')
        return real_load(*args)
    monkeypatch.setattr(engines, 'load_entry', changed)
    with pytest.raises(OSError, match='注册已变化'): companion._ensure_aniflive_service(cold.config)
    cold.stop.assert_not_called(); cold.start.assert_not_called()


def test_real_manager_resource_guard_still_blocks_launch(cold, monkeypatch):
    manager = engines.LocalVoiceManager(); monkeypatch.setattr(engines, 'manager', manager)
    guard = Mock(side_effect=OSError('内存不足，保护停止')); monkeypatch.setattr(engines, 'resource_check', guard)
    spawn = Mock(); monkeypatch.setattr(engines, 'OwnedProcess', spawn)
    with pytest.raises(OSError, match='内存不足'): companion._ensure_aniflive_service(cold.config)
    guard.assert_called_once(); spawn.assert_not_called()


def test_pcm_and_wav_both_wait_for_selected_anif_before_generation(cold, monkeypatch):
    calls = []
    monkeypatch.setattr(companion, '_ensure_aniflive_service', lambda config, **kwargs: calls.append(('ensure', config)))
    def pcm(*args, **kwargs): calls.append(('pcm', args[1])); yield b'\x10\x00'*80
    monkeypatch.setattr(anif, 'iter_speech_pcm', pcm)
    content = b''.join(companion.iter_speech_wav_stream('你好', config_snapshot=cold.config))
    assert content.startswith(b'RIFF') and [row[0] for row in calls] == ['ensure', 'pcm']
    calls.clear()
    from app import tts_http_audio
    def wav(*args, **kwargs): calls.append(('wav', args[1])); return tts_http_audio.pcm_wav(b'\x10\x00'*80)
    monkeypatch.setattr(anif, 'synthesize_wav', wav)
    assert companion.synthesize_speech_wav('你好').startswith(b'RIFF')
    assert [row[0] for row in calls] == ['ensure', 'wav']
