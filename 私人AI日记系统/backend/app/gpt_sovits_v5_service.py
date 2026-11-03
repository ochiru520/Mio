"""Independent official GPT-SoVITS /tts service; no legacy-process ownership."""
from __future__ import annotations

from pathlib import Path

import httpx

from . import tts_http_audio as audio

DEFAULT_URL = 'http://127.0.0.1:9885'


def service_url(config: dict) -> str:
    return audio.service_url(config.get('gpt_sovits_v5_url'), DEFAULT_URL)


def synthesize_wav(text: str, config: dict, *, language='zh', cancel_event=None) -> bytes:
    reference = str(config.get('gpt_sovits_ref_audio') or '').strip()
    prompt = str(config.get('gpt_sovits_prompt_text') or '').strip()
    if not reference or not prompt:
        raise ValueError('GPT-SoVITS v5需要参考音频路径和准确原文。')
    # The official API opens this file on its own host; an absolute path on Mio
    # is not uploaded automatically, so remote deployments must share the path.
    payload = {
        'text': text[:600], 'text_lang': language if language in {'zh','ja','en','ko','yue'} else 'zh',
        'ref_audio_path': reference, 'prompt_text': prompt,
        'prompt_lang': str(config.get('gpt_sovits_prompt_language') or 'zh'),
        'media_type': 'wav', 'streaming_mode': False, 'use_cuda_graph': True,
    }
    try:
        with audio.cancellable_client(cancel_event) as client:
            with client.stream('POST', f'{service_url(config)}/tts', json=payload) as response:
                content = audio.read_audio(response, cancel_event)
                if not response.headers.get('content-type', '').lower().startswith('audio/wav'):
                    raise OSError('GPT-SoVITS没有返回WAV音频。')
        audio.wav_pcm(content)
        return content
    except httpx.HTTPError as exc:
        raise OSError(f'无法连接GPT-SoVITS v5独立服务（{type(exc).__name__}）。') from exc


def runtime_status(config: dict) -> dict:
    api_available, verified, error, metadata = False, False, '', {}
    reported_version = 'unknown'
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f'{service_url(config)}/health')
            if response.is_success:
                metadata = response.json()
                if not isinstance(metadata, dict):
                    metadata = {}
                reported_version = str(metadata.get('model_version') or 'unknown')
                verified = reported_version.lower() in {'v5', '5', 'gsv-v5', 'v5dev', 'v5turbo'}
            if verified:
                api_available = metadata.get('ready') is True
            else:
                response = client.get(f'{service_url(config)}/openapi.json')
                schema = response.json() if response.is_success else {}
                paths = schema.get('paths') if isinstance(schema, dict) else None
                api_available = (isinstance(paths, dict) and '/tts' in paths
                                 and metadata.get('ready') is not False)
        if reported_version != 'unknown' and not verified:
            api_available = False
            error = f'服务报告模型版本{reported_version}，与GPT-SoVITS v5不匹配。'
        elif not api_available:
            error = 'GPT-SoVITS独立服务未就绪。'
        elif not verified:
            error = '已找到/tts接口，服务没有报告实际模型版本，尚未验证为v5。'
    except (httpx.HTTPError, ValueError, AttributeError):
        error = '无法连接GPT-SoVITS v5独立服务。'
    reference = str(config.get('gpt_sovits_ref_audio') or '')
    return {
        'engine': 'gpt_sovits_v5', 'engine_label': 'GPT-SoVITS v5（独立服务）',
        'local_voice_runtime': 'gpt_sovits_v5', 'service_url': config.get('gpt_sovits_v5_url', DEFAULT_URL),
        'service_running': api_available, 'service_loading': False, 'managed_running': False,
        'model_version': reported_version,
        'version_verified': verified, 'streaming_mode': 'text_segments',
        'reference_ready': bool(reference), 'reference_name': Path(reference).name if reference else '',
        'prompt_ready': bool(config.get('gpt_sovits_prompt_text')), 'last_error': error,
        'weights': {'gpt': [], 'sovits': []}, 'active_weights': {'gpt': '', 'sovits': ''},
    }
