"""Synthetic HTTP identity observations; no assets, settings, or GPU access."""
import json

import httpx
import pytest

from app import aniflive_tts_service as service


@pytest.fixture
def health(monkeypatch):
    real_client = httpx.Client
    def install(metadata, status=200):
        def handle(request):
            assert request.method == 'GET' and request.url.path == '/health'
            return httpx.Response(status, json=metadata)
        monkeypatch.setattr(service.httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    return install


def original():
    return {'ready': True, 'service': 'aniflive-mio-experimental', 'model': 'mio-v2-aniflive',
            'voice_profile': 'original', 'reference_name': '原音色', 'model_family': 'GPT-SoVITS V2',
            'model_version': 'v2', 'runtime': 'aniflive_v2', 'version': 'fixture',
            'active_weights': {'gpt': '<FIXTURE>/original.ckpt', 'sovits': '<FIXTURE>/original.pth'},
            'weight_sha256': {'gpt': 'a' * 64, 'sovits': 'B' * 64},
            'uses_original_finetuned_weights': True, 'original_mio_weights_loaded': True,
            'generation': {'top_k': 10, 'top_p': .88, 'temperature': .68, 'chunk_length': 32,
                           'first_chunk_tokens': 17, 'first_lookahead_tokens': 8}}


def test_actual_identity_and_generation_are_preserved_from_health(health):
    metadata = original(); health(metadata)
    result = service.runtime_status({'aniflive_model': 'mio-v2-aniflive', 'aniflive_voice_profile': 'original'})
    assert result['service_running'] and result['reference_ready'] and result['prompt_ready']
    assert result['identity_matches_selection'] and result['identity_state'] == 'matched'
    assert result['model_version'] == 'v2' and result['model_family'] == 'GPT-SoVITS V2'
    assert result['runtime'] == 'aniflive_v2'
    assert result['model'] == result['active_model'] == metadata['model']
    assert result['voice_profile'] == result['active_voice_profile'] == 'original'
    assert result['active_weights'] == metadata['active_weights']
    assert result['weight_sha256'] == {'gpt': 'a'*64, 'sovits': 'b'*64}
    assert result['generation'] == metadata['generation']
    assert result['original_mio_weights_loaded'] and result['uses_original_finetuned_weights']


@pytest.mark.parametrize('config,label', [
    ({'aniflive_model': 'other'}, '模型'),
    ({'aniflive_voice_profile': 'different'}, '音色'),
    ({'aniflive_model': 'other', 'aniflive_voice_profile': 'different'}, '模型、音色'),
])
def test_ready_service_does_not_claim_selected_mismatched_identity(health, config, label):
    health(original()); result = service.runtime_status(config)
    assert result['service_running']  # Actual ready process remains visible.
    assert not result['identity_matches_selection'] and result['identity_state'] == 'mismatch'
    assert not result['reference_ready'] and not result['prompt_ready'] and not result['selected_identity_ready']
    assert label in result['last_error'] and '不一致' in result['last_error']
    assert result['active_model'] == 'mio-v2-aniflive' and result['active_voice_profile'] == 'original'
    assert result['reference_name'] == '原音色' and result['weight_sha256']


def test_missing_reported_identity_is_unknown_and_never_copied_from_selection(health):
    health({'ready': True, 'service': 'aniflive-fixture'})
    result = service.runtime_status({'aniflive_model': 'mio-v2-aniflive', 'aniflive_voice_profile': 'Mio'})
    assert result['service_running'] and result['identity_state'] == 'unknown'
    assert not result['reference_ready'] and not result['identity_matches_selection']
    assert result['active_model'] == result['active_voice_profile'] == ''
    assert result['reference_name'] == result['runtime'] == result['model_version'] == 'unknown'
    assert result['configured_voice_profile'] == 'Mio' and '未报告' in result['last_error']


def test_profile_named_mio_does_not_imply_original_weights_or_17_plus_8(health):
    health({'ready': True, 'service': 'aniflive-fixture', 'model_family': 'GPT-SoVITS V2ProPlus',
            'voice_profile': 'mio', 'generation': {'chunk_length': 16}})
    result = service.runtime_status({'aniflive_voice_profile': 'mio'})
    assert result['identity_matches_selection']
    assert not result['original_mio_weights_loaded'] and not result['uses_original_finetuned_weights']
    assert result['weight_sha256'] == {} and result['runtime'] == 'unknown'
    assert result['generation'] == {'chunk_length': 16}


@pytest.mark.parametrize('metadata,status', [([], 200), ({'ready': True, 'service': 'different'}, 200), (original(), 503), ({**original(), 'ready': False}, 200)])
def test_offline_or_wrong_service_never_exposes_active_weights_as_ready(health, metadata, status):
    health(metadata, status); result = service.runtime_status({})
    assert not result['service_running'] and not result['reference_ready']
    assert result['identity_state'] == 'unavailable'
    assert result['active_weights'] == {'gpt': '', 'sovits': ''}
    assert result['weight_sha256'] == result['generation'] == {}
    assert not result['original_mio_weights_loaded'] and result['last_error']


def test_malformed_optional_metadata_is_not_treated_as_weights_or_generation(health):
    data = original(); data['weight_sha256'] = {'gpt': 'not-a-sha', 'sovits': None}
    data['generation'] = ['not controls']; data['active_weights'] = []
    health(data); result = service.runtime_status({})
    assert result['weight_sha256'] == result['generation'] == {}
    assert result['active_weights'] == {'gpt': '', 'sovits': ''}


def test_identity_is_sent_on_session_without_preflight_or_wrong_audio(monkeypatch):
    requests = []; real_client = httpx.Client
    def handle(request):
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(400, json={'detail': 'synthetic private model path'})
    monkeypatch.setattr(service.httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(OSError) as failed:
        list(service.iter_speech_pcm('固定文本', {'aniflive_model': 'selected-model', 'aniflive_voice_profile': 'selected-voice'}))
    assert requests == [('/v1/sessions', {'model': 'selected-model', 'voice_profile': 'selected-voice'})]
    assert 'private' not in str(failed.value)
