"""Owner-scoped native PCM sessions for selected GPT-SoVITS V2 voices."""
from __future__ import annotations

import hashlib
from urllib.parse import quote
import uuid

import httpx

from . import tts_http_audio as audio

DEFAULT_URL = 'http://127.0.0.1:9880'


def service_url(config: dict) -> str:
    return audio.service_url(config.get('gpt_sovits_url'), DEFAULT_URL)


def iter_speech_pcm(text: str, config: dict, *, language='zh', cancel_event=None):
    audio.check_cancel(cancel_event)
    if language not in {'zh', 'ja'}:
        raise ValueError('GPT-SoVITS V2当前已验证的输出语言为中文和日语。')
    url = service_url(config)
    metadata = _health(config)
    _validate_profile(metadata, config)
    identity = {'model': 'mio-v2-native', 'voice_profile': metadata.get('voice_profile') or 'gpt-sovits-v2-selected'}
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
                raise OSError('Mio V2没有返回有效语音会话。')
            session_id = str(created_data.get('id') or '')
            if not session_id or len(session_id) > 160:
                raise OSError('Mio V2没有返回有效语音会话。')
            if created_data.get('voice_profile') != identity['voice_profile']:
                raise OSError('V2服务返回的音色身份与所选音色不符。')
            session_url = f'{url}/v1/sessions/{quote(session_id, safe="")}'
            audio.check_cancel(cancel_event)
            segment = client.post(f'{session_url}/segments', json={
                'segment_id': f'mio-{uuid.uuid4().hex}', 'text': text[:600],
                'language': language,
            })
            segment.raise_for_status()
            flushed = client.post(f'{session_url}/flush', json={})
            flushed.raise_for_status()
            with client.stream('GET', f'{session_url}/audio') as response:
                yield from audio.pcm_chunks(response, cancel_event)
            completed = True
    except httpx.HTTPError as exc:
        raise OSError(f'Mio V2语音服务不可用或协议不符（{type(exc).__name__}）。') from exc
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


def _health(config: dict) -> dict:
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f'{service_url(config)}/health')
            response.raise_for_status()
            metadata = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OSError('无法连接GPT-SoVITS V2服务，请启动音色服务。') from exc
    if (not isinstance(metadata, dict) or metadata.get('service') != 'mio-v2-native'
            or metadata.get('engine') != 'mio_v2' or metadata.get('version') != 'v2'
            or metadata.get('ready') is not True):
        raise OSError('Mio V2服务未就绪或服务身份不符。')
    return metadata


def _validate_profile(metadata: dict, config: dict) -> None:
    active = metadata.get('active_weights') or {}
    if (config.get('voice_profile_id') and metadata.get('voice_profile') not in
            {config['voice_profile_id'], 'mio-original-v2', 'gpt-sovits-v2-selected'}):
        raise OSError('当前V2音色标识与运行中的音色不符；请保存并重新加载所选音色。')
    def canonical(value):
        return str(value or '').replace('\\', '/').casefold()
    for name, field in (('gpt', 'gpt_sovits_gpt_weights'), ('sovits', 'gpt_sovits_sovits_weights')):
        requested = config.get(field)
        if requested and canonical(requested) != canonical(active.get(name)):
            raise OSError('当前音色权重与已加载的V2权重不符；请保存并重新加载所选音色。')
    reference = config.get('gpt_sovits_ref_audio')
    if reference and canonical(reference) != canonical(metadata.get('reference_audio')):
        raise OSError('当前参考音频与已加载的V2音色不符；请保存并重新加载所选音色。')
    prompt = str(config.get('gpt_sovits_prompt_text') or '')
    if prompt and hashlib.sha256(prompt.encode('utf-8')).hexdigest() != metadata.get('reference_text_sha256'):
        raise OSError('当前参考原文与已加载的V2音色不符；请保存并重新加载所选音色。')
    language = config.get('gpt_sovits_prompt_language')
    if language and language != metadata.get('reference_language'):
        raise OSError('当前参考语言与已加载的V2音色不符；请保存并重新加载所选音色。')


def runtime_status(config: dict) -> dict:
    metadata, running, error = {}, False, ''
    try:
        metadata = _health(config)
        _validate_profile(metadata, config)
        running = True
    except (httpx.HTTPError, ValueError, OSError, AttributeError) as exc:
        error = str(exc) if isinstance(exc, OSError) else '无法连接GPT-SoVITS V2服务，请启动音色服务。'
    active = metadata.get('active_weights') or {}
    return {
        'engine': 'gpt_sovits', 'engine_label': 'GPT-SoVITS V2 · CUDA FP16 · 32 token流式',
        'local_voice_runtime': 'mio_v2', 'service_url': service_url(config),
        'service_running': running, 'service_ready': running,
        'model_ready': bool(metadata),
        'service_loading': False, 'managed_running': False,
        'default_profile_id': config.get('default_voice_profile_id', ''),
        'profiles': config.get('voice_profiles') or {},
        'model_version': 'v2' if metadata else 'unknown',
        'streaming_mode': '32_token_pcm', 'reference_ready': running, 'prompt_ready': running,
        'reference_name': config.get('voice_profile_name') or config.get('default_voice_profile_id') or '所选V2音色',
        'last_error': error, 'weights': {'gpt': [], 'sovits': []},
        'active_weights': {'gpt': active.get('gpt', ''), 'sovits': active.get('sovits', '')},
        'active_profile_id': metadata.get('voice_profile') or '',
        'active_reference_audio': metadata.get('reference_audio') or '',
        'verified_reference_text': str(config.get('gpt_sovits_prompt_text') or '') if running else '',
        'active_reference_language': metadata.get('reference_language') or '',
        'warmup_state': 'ready' if running else 'idle', 'warmup_error': '',
        'warmup_seconds': metadata.get('warmup_seconds'),
        'warmup_languages': metadata.get('warmup_languages') or [],
        'last_first_audio_ms': metadata.get('last_first_audio_ms'),
        'load_seconds': metadata.get('load_seconds'),
        'experimental': False, 'fallback_engine': '',
    }
