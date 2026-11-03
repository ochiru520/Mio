"""Independent-service contracts use synthetic audio, no model or private assets."""
from io import BytesIO
import json
import struct
import threading
from unittest.mock import Mock, patch
import wave

import httpx
import pytest

from app import aniflive_tts_service as anif
from app import companion_service as companion
from app import gpt_sovits_v5_service as v5
from app import indextts2_service as index
from app import tts_http_audio as audio
from app.config import settings


def synthetic_wav(rate=24000, channels=1):
    out = BytesIO()
    with wave.open(out, 'wb') as target:
        target.setparams((channels, 2, rate, 0, 'NONE', ''))
        target.writeframes(struct.pack('<hh', 1200, -1200) * 300 * channels)
    return out.getvalue()


class OddChunks(httpx.SyncByteStream):
    def __init__(self, content):
        self.content = content

    def __iter__(self):
        for offset in range(0, len(self.content), 7):
            yield self.content[offset:offset + 7]


@pytest.fixture
def http_service(monkeypatch):
    real_client = httpx.Client

    def install(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(transport=transport, **kwargs))
    return install


def pcm_response(content=None, rate=24000, channels=2):
    if content is None:
        content = struct.pack('<hhhh', 2400, -1200, -1200, 2400) * 300
    return httpx.Response(200, stream=OddChunks(content), headers={
        'content-type': 'application/octet-stream', 'X-TTS-Sample-Format': 's16le',
        'X-TTS-Sample-Rate': str(rate), 'X-TTS-Channels': str(channels),
    })


def test_pcm_resampling_preserves_split_frames_and_state():
    raw = struct.pack('<hhhh', 2400, -1200, -1200, 2400) * 300
    streamed = b''.join(audio.pcm_chunks(pcm_response(raw)))
    assert len(streamed) % 2 == 0
    out = BytesIO()
    with wave.open(out, 'wb') as target:
        target.setparams((2, 2, 24000, 0, 'NONE', ''))
        target.writeframes(raw)
    assert streamed == audio.normalize_wav(out.getvalue())[44:]
    assert len(audio.streaming_header()) == 44
    assert audio.wav_pcm(audio.pcm_wav(streamed))[1:] == (32000, 1, 2)


@pytest.mark.parametrize('response', [
    pcm_response(b'\x00'), pcm_response(b''),
    httpx.Response(200, content=b'not PCM'),
    pcm_response(rate=999999), pcm_response(channels=3),
])
def test_pcm_rejects_empty_partial_or_unknown_audio(response):
    with pytest.raises(OSError):
        b''.join(audio.pcm_chunks(response))


@pytest.mark.parametrize('status', [400, 404, 409, 422, 500, 503])
def test_pcm_http_error_is_not_reported_as_missing_audio_headers(status):
    response = httpx.Response(status, json={'detail': 'private-reference-and-request'})
    with pytest.raises(OSError, match=f'HTTP {status}') as failed:
        next(audio.pcm_chunks(response))
    assert '缺少采样率' not in str(failed.value)
    assert 'private' not in str(failed.value)


def test_cancelled_http_error_keeps_cancellation_semantics():
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(InterruptedError):
        next(audio.pcm_chunks(httpx.Response(500), cancelled))


def test_aniflive_session_append_flush_audio_and_owner_cancel(http_service):
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path, json.loads(request.content) if request.content else {}))
        if request.url.path == '/v1/sessions':
            return httpx.Response(200, json={'id': 'owned-session'})
        if request.url.path.endswith('/audio'):
            return pcm_response()
        return httpx.Response(200, json={})

    http_service(handler)
    config = {'aniflive_model': 'compatible-model', 'aniflive_voice_profile': 'voice-a'}
    speech = anif.iter_speech_pcm('同一段测试', config, language='ja')
    assert next(speech)
    speech.close()
    assert requests[0][2] == {'model': 'compatible-model', 'voice_profile': 'voice-a'}
    assert [row[1] for row in requests] == [
        '/v1/sessions', '/v1/sessions/owned-session/segments',
        '/v1/sessions/owned-session/flush', '/v1/sessions/owned-session/audio',
        '/v1/sessions/owned-session/cancel',
    ]
    assert requests[1][2]['language'] == 'ja'
    assert requests[1][2]['text'] == '同一段测试'
    requests.clear()
    assert audio.wav_pcm(anif.synthesize_wav('你好', config))[1:] == (32000, 1, 2)
    assert not any(path.endswith('/cancel') for _, path, _ in requests)


def test_pre_cancelled_request_performs_no_http(http_service):
    handler = Mock(side_effect=AssertionError('must not perform HTTP'))
    http_service(handler)
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(InterruptedError):
        anif.synthesize_wav('你好', {}, cancel_event=cancelled)
    with pytest.raises(InterruptedError):
        v5.synthesize_wav('你好', {'gpt_sovits_ref_audio': 'synthetic.wav', 'gpt_sovits_prompt_text': '你好'}, cancel_event=cancelled)
    handler.assert_not_called()


def test_aniflive_server_failure_cleans_only_owned_session(http_service):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == '/v1/sessions':
            return httpx.Response(200, json={'id': 'only-mine'})
        if request.url.path.endswith('/segments'):
            return httpx.Response(500, text='synthetic-private-details')
        return httpx.Response(200, json={})

    http_service(handler)
    with pytest.raises(OSError) as failure:
        anif.synthesize_wav('你好', {})
    assert 'private' not in str(failure.value)
    assert requests[-1] == '/v1/sessions/only-mine/cancel'
    assert '/v1/audio/cancel' not in requests


@pytest.mark.parametrize('model_version', ['v5dev', 'v5turbo'])
def test_v5_real_version_is_accepted_and_complete_wav_contract(http_service, model_version):
    payloads = []

    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json={'ready': True, 'model_version': model_version})
        assert request.url.path == '/tts'
        payloads.append(json.loads(request.content))
        return httpx.Response(200, content=synthetic_wav(), headers={'content-type': 'audio/wav'})

    http_service(handler)
    config = {'gpt_sovits_ref_audio': 'synthetic.wav', 'gpt_sovits_prompt_text': '参考原文', 'gpt_sovits_prompt_language': 'zh'}
    status = v5.runtime_status(config)
    assert status['service_running'] and status['version_verified']
    assert status['model_version'] == model_version
    assert v5.synthesize_wav('你好', config, language='ja') == synthetic_wav()
    assert payloads == [{
        'text': '你好', 'text_lang': 'ja', 'ref_audio_path': 'synthetic.wav',
        'prompt_text': '参考原文', 'prompt_lang': 'zh', 'media_type': 'wav',
        'streaming_mode': False, 'use_cuda_graph': True,
    }]


def test_official_tts_api_presence_is_not_model_version_proof(http_service):
    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(404)
        return httpx.Response(200, json={'paths': {'/tts': {}}})
    http_service(handler)
    status = v5.runtime_status({})
    assert status['service_running'] and not status['version_verified']
    assert status['model_version'] == 'unknown'
    assert '尚未验证' in status['last_error']


@pytest.mark.parametrize('health,schema,expected_version', [
    ({'ready': True}, {'paths': {'/unrelated': {}}}, 'unknown'),
    ({'ready': False}, {'paths': {'/tts': {}}}, 'unknown'),
    ({'ready': True, 'model_version': 'v2'}, {'paths': {'/tts': {}}}, 'v2'),
])
def test_v5_health_requires_matching_model_or_tts_api_evidence(http_service, health, schema, expected_version):
    http_service(lambda request: httpx.Response(200, json=health if request.url.path == '/health' else schema))
    status = v5.runtime_status({})
    assert not status['service_running'] and not status['version_verified']
    assert status['model_version'] == expected_version


def test_v5_rejects_raw_or_truncated_wav(http_service):
    http_service(lambda request: httpx.Response(200, content=synthetic_wav()[:-3], headers={'content-type': 'audio/wav'}))
    with pytest.raises(OSError, match='完整'):
        v5.synthesize_wav('你好', {'gpt_sovits_ref_audio': 'synthetic.wav', 'gpt_sovits_prompt_text': '参考'})


def test_index25_requested_version_language_and_actual_identity(tmp_path, http_service):
    reference = tmp_path / 'synthetic.wav'; reference.write_bytes(synthetic_wav())
    payloads = []

    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json={'protocol': index.PROTOCOL, 'ready': True, 'model_version': '2.5', 'languages': ['zh', 'ja']})
        payloads.append(json.loads(request.content))
        return httpx.Response(200, content=synthetic_wav(), headers={'content-type': 'audio/wav'})

    http_service(handler)
    config = {'indextts2_ref_audio': str(reference), 'indextts2_version': '2.5', 'indextts2_language': 'ja'}
    status = index.runtime_status(config)
    assert status['model_version'] == '2.5' and status['version_verified']
    assert index.synthesize_wav('こんにちは', config) == synthetic_wav()
    assert payloads[0]['model_version'] == '2.5' and payloads[0]['language'] == 'ja'
    mismatch = index.runtime_status({**config, 'indextts2_version': '2'})
    assert not mismatch['service_running'] and '2.5' in mismatch['last_error']


def test_missing_model_identity_stays_unknown(http_service):
    http_service(lambda request: httpx.Response(200, json={'protocol': index.PROTOCOL, 'ready': True}))
    status = index.runtime_status({'indextts2_version': '2.5'})
    assert status['model_version'] == 'unknown' and not status['version_verified']
    assert 'unknown' in status['last_error']


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    previous = {key: getattr(settings, key) for key in ['data_dir', 'companion_dir', 'companion_config_path']}
    for key, value in {'data_dir': tmp_path, 'companion_dir': tmp_path / 'companion', 'companion_config_path': tmp_path / 'companion.json'}.items():
        object.__setattr__(settings, key, value)
    monkeypatch.setattr(companion, '_live2d_model_ids', lambda: {'hiyori'})
    yield
    for key, value in previous.items():
        object.__setattr__(settings, key, value)


def test_switch_save_reload_preserves_voice_assets_and_external_fields(isolated_settings):
    fields = {'gpt_sovits_ref_audio': 'synthetic-reference.wav', 'gpt_sovits_prompt_text': '参考原文',
              'gpt_sovits_gpt_weights': 'synthetic.ckpt', 'gpt_sovits_sovits_weights': 'synthetic.pth',
              'indextts2_version': '2.5', 'indextts2_language': 'ja', 'aniflive_model': 'model-a',
              'aniflive_voice_profile': 'speaker-a', 'gpt_sovits_v5_url': 'http://127.0.0.1:19885/'}
    companion.save_config(fields)
    for engine in ['aniflive', 'gpt_sovits_v5', 'indextts2', 'gpt_sovits', 'cloud']:
        companion.save_config({'voice_engine': engine})
        config = companion.load_config()
        assert config['voice_engine'] == engine
        for key, expected in fields.items():
            assert config[key] == expected.rstrip('/')


def test_existing_index2_config_keeps_its_model_version(isolated_settings):
    settings.companion_config_path.write_text(json.dumps({
        'voice_engine': 'indextts2', 'indextts2_url': 'http://127.0.0.1:11996',
        'indextts2_ref_audio': 'synthetic-reference.wav',
    }), encoding='utf-8')
    config = companion.load_config()
    assert config['indextts2_version'] == '2'
    assert config['indextts2_ref_audio'] == 'synthetic-reference.wav'
    assert companion.save_config({'indextts2_version': '2.5'})['indextts2_version'] == '2.5'


def test_new_index_install_uses_current_version_default(isolated_settings):
    assert companion.load_config()['indextts2_version'] == '2.5'


@pytest.mark.parametrize('engine,module', [('aniflive', anif), ('gpt_sovits_v5', v5), ('indextts2', index)])
def test_external_synthesis_snapshot_and_management_isolation(engine, module):
    config = {'voice_engine': engine, 'voice_volume': 100, 'pet_speech_language': 'zh'}
    changed_config = {'voice_engine': 'gpt_sovits', 'local_voice_runtime': 'genie'}
    with patch.object(companion, 'load_config', return_value=changed_config), patch.object(module, 'synthesize_wav', return_value=synthetic_wav()) as synth, patch.object(companion, '_ensure_gpt_sovits_service') as legacy:
        if engine == 'aniflive':
            def pieces(*args, **kwargs):
                yield b'\x10\x00' * 80
            with patch.object(module, 'iter_speech_pcm', side_effect=pieces):
                content = b''.join(companion.iter_speech_wav_stream('你好', config_snapshot=config, cancel_event=threading.Event()))
        else:
            content = b''.join(companion.iter_speech_wav_stream('你好', config_snapshot=config, cancel_event=threading.Event()))
            assert synth.call_args.args[1]['voice_engine'] == engine
        assert content[:44] == audio.streaming_header()
        assert len(content) > 44
        legacy.assert_not_called()
    with patch.object(companion, 'load_config', return_value=config), patch.object(companion, 'start_gpt_sovits_service') as start, patch.object(companion, 'stop_gpt_sovits_service') as stop, patch.object(companion.threading, 'Thread') as thread:
        assert companion.warm_voice_runtime_async() is False
        assert companion.warm_voice_language_async('zh') is False
        assert companion.warm_voice_runtime()['warmup_state'] == 'idle'
        for control in [companion.start_voice_service, companion.restart_voice_service]:
            with pytest.raises(OSError): control()
        with patch.object(module, 'runtime_status', return_value={}):
            companion.stop_voice_service()  # No owned process: stop is a no-op, never kills an external service.
        start.assert_not_called(); stop.assert_not_called(); thread.assert_not_called()


def test_explicit_test_playback_bypasses_realtime_bridge():
    with patch.object(companion, 'load_config', return_value={'voice_enabled': True}), patch.object(companion, '_bridged_speak', return_value=True) as bridge, patch.object(companion.threading, 'Thread') as thread:
        assert companion.speak_text('你好', allow_realtime_bridge=False)
        bridge.assert_not_called()
        thread.return_value.start.assert_called_once()


def test_other_engine_error_cannot_hide_active_service_version_diagnostic():
    config = {'voice_engine': 'gpt_sovits_v5'}
    with patch.object(companion, 'load_config', return_value=config), patch.object(companion, '_gpt_sovits_last_error', '旧Genie加载失败'), patch.object(companion, '_external_voice_last_errors', {'aniflive': '另一个服务失败'}), patch.object(v5, 'runtime_status', return_value={'last_error': '尚未验证v5版本'}):
        assert companion.voice_runtime_status()['last_error'] == '尚未验证v5版本'
