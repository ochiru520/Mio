"""Original Mio V2 runtime integration: real HTTP framing, identity and lifecycle guards."""
import importlib.util
import hashlib
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from unittest.mock import Mock, patch

import pytest

from app import companion_service as companion, local_voice_engines as engines, mio_v2_tts_service as native
from app.config import settings
from app.routes.companion import CompanionSettingsRequest


@pytest.fixture
def config():
    return companion.load_normalized_config({
        'voice_engine': 'gpt_sovits', 'local_voice_runtime': 'mio_v2',
        'gpt_sovits_gpt_weights': 'D:/models/mio.ckpt',
        'gpt_sovits_sovits_weights': 'D:/models/mio.pth',
        'gpt_sovits_ref_audio': 'D:/voices/reference.wav',
        'gpt_sovits_prompt_text': 'こんにちは。', 'voice_volume': 100,
    })


@pytest.fixture
def bridge(config):
    spec = importlib.util.spec_from_file_location('native_v2_test_bridge', settings.agent_control_scripts_dir / 'mio_v2_stream_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    servers = []
    def start(generate, **metadata):
        base = {'active_weights': {'gpt': config['gpt_sovits_gpt_weights'], 'sovits': config['gpt_sovits_sovits_weights']},
                'reference_audio': config['gpt_sovits_ref_audio'],
                'reference_text_sha256': hashlib.sha256(config['gpt_sovits_prompt_text'].encode('utf-8')).hexdigest(),
                'reference_language': config['gpt_sovits_prompt_language']}
        server = ThreadingHTTPServer(('127.0.0.1', 0), module.handler_for(generate, metadata={**base, **metadata}))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        config['gpt_sovits_url'] = f'http://127.0.0.1:{server.server_port}'
        return config
    yield start
    for server, thread in servers:
        server.shutdown(); server.server_close(); thread.join(2)


def test_config_keeps_native_runtime_and_route_accepts_it(config):
    assert config['local_voice_runtime'] == 'mio_v2'
    assert CompanionSettingsRequest(local_voice_runtime='mio_v2').local_voice_runtime == 'mio_v2'
    assert companion.supports_call_speech_stream(config=config)


@pytest.mark.parametrize('language,text', [('zh', '今天一起散步吧。'), ('ja', '一緒に散歩しましょう。')])
def test_native_real_http_first_chunk_is_released_before_generation_finishes(bridge, config, language, text):
    release, finished = threading.Event(), threading.Event()
    received = []
    def generate(segments, lang, cancelled):
        received.append((segments, lang))
        yield b'\x10\x00' * 160
        assert release.wait(3)
        finished.set()
        yield b'\x20\x00' * 160
    bridge(generate)
    assert native.runtime_status(config)['local_voice_runtime'] == 'mio_v2'
    pieces = native.iter_speech_pcm(text, config, language=language)
    try:
        assert next(pieces) == b'\x10\x00' * 160
        assert not finished.is_set()
        release.set()
        assert b''.join(pieces) == b'\x20\x00' * 160
    finally:
        release.set(); pieces.close()
    assert received == [((text,), language)]


def test_profile_mismatch_never_speaks_another_voice(bridge, config):
    generate = Mock(return_value=iter([b'\x10\x00' * 160]))
    bridge(generate)
    config['gpt_sovits_sovits_weights'] = 'D:/models/other.pth'
    status = native.runtime_status(config)
    assert status['model_ready'] and not status['service_running']
    with pytest.raises(OSError, match='权重不符'):
        b''.join(native.iter_speech_pcm('测试', config))
    generate.assert_not_called()


def test_inference_failure_after_pcm_is_not_success(bridge, config):
    def generate(*_):
        yield b'\x10\x00' * 160
        raise RuntimeError('synthetic failure')
    bridge(generate)
    with pytest.raises(OSError):
        native.synthesize_wav('测试', config)


def test_iterator_close_cancels_owned_inference(bridge, config):
    observed = threading.Event()
    def generate(segments, language, cancelled):
        yield b'\x10\x00' * 160
        assert cancelled.wait(3)
        observed.set()
    bridge(generate)
    pieces = native.iter_speech_pcm('测试', config)
    assert next(pieces)
    pieces.close()
    assert observed.wait(3)


def test_managed_native_start_and_stop_never_launch_legacy_genie(config):
    with patch.object(companion, 'load_config', return_value=config), patch.object(engines, 'load_entry'), \
         patch.object(engines.manager, 'start') as start, patch.object(engines.manager, 'stop') as stop, \
         patch.object(companion, '_stop_owned_original_voice') as release, \
         patch.object(companion, 'voice_runtime_status', return_value={}), \
         patch.object(companion, 'start_gpt_sovits_service') as legacy:
        companion.start_voice_service()
        start.assert_called_once_with('mio_v2', config)
        release.assert_called_once()
        companion.stop_voice_service()
        stop.assert_called_once_with('mio_v2')
        legacy.assert_not_called()


def test_late_native_request_never_displaces_another_owned_engine(config):
    manager = engines.LocalVoiceManager()
    manager.selection_reader = lambda: config
    child = Mock(); child.poll.return_value = None
    manager.child, manager.engine = child, 'aniflive'
    with patch.object(manager, 'start') as start:
        with pytest.raises(OSError, match='显式切换'):
            manager.ensure_mio_v2(config)
        start.assert_not_called()
    manager.child = None
    manager.selection_reader = lambda: {**config, 'local_voice_runtime': 'genie'}
    with pytest.raises(OSError, match='已切换'):
        manager.ensure_mio_v2(config)


def test_late_genie_request_is_rejected_after_native_selection(config):
    manager = engines.LocalVoiceManager()
    manager.selection_reader = lambda: config
    with pytest.raises(OSError, match='已切换'):
        manager.require_original_selected()


def test_native_facade_uses_profile_language_and_pcm_stream(config):
    pcm = b'\x20\x00' * 100
    with patch.object(companion, 'load_config', return_value=config), \
         patch.object(companion, '_prepare_speech_input', return_value=('確認です。', 'ja', {})), \
         patch.object(companion, '_ensure_mio_v2_service') as ensure, \
         patch.object(native, 'iter_speech_pcm', side_effect=lambda *args, **kwargs: (item for item in [pcm])) as generate:
        output = b''.join(companion.iter_speech_wav_stream('確認です。', language='ja', config_snapshot=config))
    assert output[:4] == b'RIFF' and output[44:] == pcm
    assert generate.call_args.kwargs['language'] == 'ja'
    assert ensure.call_args.args[0]['gpt_sovits_sovits_weights'] == config['gpt_sovits_sovits_weights']


def test_cancel_before_generation_does_not_start_native(config):
    cancelled = threading.Event(); cancelled.set()
    with patch.object(companion, 'load_config', return_value=config), patch.object(engines.manager, 'ensure_mio_v2') as start:
        assert list(companion.iter_speech_wav_stream('测试', cancel_event=cancelled, config_snapshot=config)) == []
        start.assert_not_called()


@pytest.mark.parametrize('field,value', [('gpt_sovits_prompt_text', '別の原文。'), ('gpt_sovits_prompt_language', 'zh')])
def test_changed_reference_text_or_language_cannot_be_silently_ignored(bridge, config, field, value):
    generate = Mock(return_value=iter([b'\x10\x00' * 160]))
    bridge(generate)
    config[field] = value
    with pytest.raises(OSError, match='参考'):
        b''.join(native.iter_speech_pcm('测试', config))
    generate.assert_not_called()


def test_unsupported_output_language_never_falls_back_to_chinese(config):
    with pytest.raises(ValueError, match='中文和日语'):
        b''.join(native.iter_speech_pcm('Hello', config, language='en'))
