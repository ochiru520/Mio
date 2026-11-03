"""HTTP adapter for AnifLive's owner-scoped speech sessions, not its Rust SDK."""
from __future__ import annotations

from urllib.parse import quote
import math
import re
import uuid

import httpx

from . import tts_http_audio as audio

DEFAULT_URL = 'http://127.0.0.1:9882'


def service_url(config: dict) -> str:
    return audio.service_url(config.get('aniflive_url'), DEFAULT_URL)


def iter_speech_pcm(text: str, config: dict, *, language='zh', cancel_event=None):
    url = service_url(config)
    identity = {}
    if config.get('aniflive_model'):
        identity['model'] = str(config['aniflive_model'])
    if config.get('aniflive_voice_profile'):
        identity['voice_profile'] = str(config['aniflive_voice_profile'])
    session_id = ''
    completed = False

    def cancel_owned():
        if session_id:
            with httpx.Client(timeout=2, trust_env=False) as cancel_client:
                cancel_client.post(f'{url}/v1/sessions/{quote(session_id, safe="")}/cancel', json={})

    try:
        with audio.cancellable_client(cancel_event, on_cancel=cancel_owned) as client:
            created = client.post(f'{url}/v1/sessions', json=identity)
            created.raise_for_status()
            created_data = created.json()
            if not isinstance(created_data, dict):
                raise OSError('AnifLive没有返回有效语音会话。')
            session_id = str(created_data.get('id') or '')
            if not session_id or len(session_id) > 160:
                raise OSError('AnifLive没有返回有效语音会话。')
            session_url = f'{url}/v1/sessions/{quote(session_id, safe="")}'
            audio.check_cancel(cancel_event)
            segment = client.post(f'{session_url}/segments', json={
                'segment_id': f'mio-{uuid.uuid4().hex}', 'text': text[:600],
                'language': language if language in {'zh', 'ja', 'en', 'yue', 'ko'} else 'zh',
            })
            segment.raise_for_status()
            flushed = client.post(f'{session_url}/flush', json={})
            flushed.raise_for_status()
            with client.stream('GET', f'{session_url}/audio') as response:
                yield from audio.pcm_chunks(response, cancel_event)
            completed = True
    except httpx.HTTPError as exc:
        raise OSError(f'AnifLive语音服务不可用或协议不符（{type(exc).__name__}）。') from exc
    finally:
        if session_id and not completed:
            try:
                cancel_owned()
            except (httpx.HTTPError, ValueError, OSError):
                pass


def synthesize_wav(text: str, config: dict, *, language='zh', cancel_event=None) -> bytes:
    pieces = iter_speech_pcm(text, config, language=language, cancel_event=cancel_event)
    try:
        return audio.pcm_wav(b''.join(pieces))
    finally:
        pieces.close()


def runtime_status(config: dict) -> dict:
    running, error, metadata = False, '', {}
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f'{service_url(config)}/health')
        metadata = response.json() if response.is_success else {}
        if not isinstance(metadata, dict):
            metadata = {}
        running = (metadata.get('ready') is True
                   and str(metadata.get('service') or '').lower().replace('_', '-').startswith('aniflive'))
        if not running:
            error = 'AnifLive服务未就绪或服务身份不符。'
    except (httpx.HTTPError, ValueError, AttributeError):
        error = '无法连接AnifLive服务，请先启动已安装的语音模型。'
    weights = metadata.get('active_weights')
    weights = {name: str(weights.get(name) or '') for name in ('gpt', 'sovits')} if isinstance(weights, dict) else {'gpt': '', 'sovits': ''}
    configured = {name: str(config.get(key) or '').strip() for name, key in
                  (('model', 'aniflive_model'), ('voice_profile', 'aniflive_voice_profile'))}
    actual = {name: str(metadata.get(name) or '').strip() for name in configured}
    missing = [name for name in configured if configured[name] and not actual[name]]
    mismatch = [name for name in configured if configured[name] and actual[name] and configured[name] != actual[name]]
    identity_state = 'unavailable' if not running else 'mismatch' if mismatch else 'unknown' if missing else 'matched'
    identity_ready = running and identity_state == 'matched'
    if running and (mismatch or missing):
        labels = {'model': '模型', 'voice_profile': '音色'}
        error = ('AnifLive实际加载的' + '、'.join(labels[name] for name in mismatch) + '与当前选择不一致，请重新加载所选配置。'
                 if mismatch else 'AnifLive未报告所选' + '、'.join(labels[name] for name in missing) + '的实际身份，尚不能确认已加载。')
    hashes = metadata.get('weight_sha256')
    hashes = {name: value.lower() for name in ('gpt', 'sovits')
              if isinstance(hashes, dict) and isinstance(value := hashes.get(name), str)
              and re.fullmatch(r'[0-9a-fA-F]{64}', value)}
    # Only observed, scalar generation controls are exposed; no invented defaults
    # or profile-name inference about which checkpoint produced the audio.
    generation = metadata.get('generation')
    generation = {key: value for key, value in generation.items()
                  if isinstance(key, str) and isinstance(value, (str, int, float, bool))
                  and (not isinstance(value, float) or math.isfinite(value))} if isinstance(generation, dict) else {}
    return {
        'engine': 'aniflive', 'engine_label': 'AnifLive-TTS',
        'local_voice_runtime': 'aniflive', 'service_url': config.get('aniflive_url', DEFAULT_URL),
        'service_running': running, 'service_loading': False, 'managed_running': False,
        'model_version': metadata.get('model_version') or metadata.get('model_family') or 'unknown',
        'model_family': metadata.get('model_family') or 'unknown',
        'runtime': metadata.get('runtime') or 'unknown',
        'model': actual['model'], 'voice_profile': actual['voice_profile'],
        'active_model': actual['model'], 'active_voice_profile': actual['voice_profile'],
        'configured_model': configured['model'], 'configured_voice_profile': configured['voice_profile'],
        'identity_state': identity_state, 'identity_matches_selection': identity_ready,
        'selected_identity_ready': identity_ready,
        'service_version': metadata.get('version') or 'unknown',
        'experimental': metadata.get('experimental') is True,
        'numerical_verified': metadata.get('numerical_verified') is True,
        'formal_numerical_gate': metadata.get('formal_numerical_gate') or '',
        'streaming_mode': 'audio_chunks', 'reference_ready': identity_ready, 'prompt_ready': identity_ready,
        'reference_name': metadata.get('reference_name') or actual['voice_profile'] or 'unknown',
        'original_mio_weights_loaded': running and metadata.get('original_mio_weights_loaded') is True,
        'uses_original_finetuned_weights': running and metadata.get('uses_original_finetuned_weights') is True,
        'weight_sha256': hashes if running else {},
        'generation': generation if running else {},
        'backend': metadata.get('backend') or '',
        'last_error': error, 'weights': {'gpt': [], 'sovits': []},
        'active_weights': weights if running else {'gpt': '', 'sovits': ''},
    }
