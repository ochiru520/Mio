"""Explicit synthetic probes. No microphone, screenshot, QQ send or generation job."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import wave

import httpx


class ProbeFailure(Exception):
    def __init__(self, code):
        self.code = code


def audio_valid(content: bytes) -> bool:
    try:
        with wave.open(io.BytesIO(content), 'rb') as wav:
            frames = wav.readframes(wav.getnframes())
            return (wav.getnframes() / max(1, wav.getframerate()) >= .2
                    and wav.getsampwidth() in {1, 2, 3, 4} and bool(frames)
                    and any(value != (128 if wav.getsampwidth() == 1 else 0) for value in frames))
    except (wave.Error, EOFError, ValueError):
        return False


async def chat() -> dict:
    from .model_registry import get_model_profile
    from .routes.models import _request_chat_via_routes
    try:
        profile = get_model_profile()
    except ValueError:
        raise ProbeFailure('not_configured') from None
    if not profile.api_key or profile.api_key_error:
        raise ProbeFailure('authentication')
    response, *_ = await _request_chat_via_routes(profile.base_urls[0], profile.api_key,
        profile.model, profile.auth_scheme, profile.api_mode)
    if response.status_code in {401, 403}:
        raise ProbeFailure('authentication')
    response.raise_for_status()
    data = response.json()
    texts = [data.get('output_text', '')]
    for choice in data.get('choices', []):
        texts.append((choice.get('message') or {}).get('content', ''))
    for item in data.get('output', []):
        for part in item.get('content', []):
            texts.append(part.get('text', ''))
    if not any(isinstance(text, str) and text.strip() for text in texts):
        raise ProbeFailure('invalid_output')
    return {'ok': True, 'level': 'inference'}


def tts() -> dict:
    from . import companion_service, genie_tts_service
    try:
        content = companion_service.synthesize_speech_wav('你好，这是一次语音功能测试。',
            require_configured_engine=True, language='zh', _prepared_language='zh')
        if not audio_valid(content):
            raise ProbeFailure('invalid_output')
        return {'ok': True, 'level': 'inference'}
    finally:
        # Only this isolated process's worker is stopped.
        genie_tts_service.stop_worker()


def asr(directory: Path) -> dict:
    from .environment_check_service import find_whisper_runtime
    layout = find_whisper_runtime()
    if not layout:
        raise ProbeFailure('runtime_missing')
    wav_path = directory / 'asr-synthetic.wav'
    environment = {**os.environ, 'MIO_PROBE_WAV': str(wav_path), 'HF_HUB_OFFLINE': '1',
                   'TRANSFORMERS_OFFLINE': '1', 'PYTHONUTF8': '1'}
    # Windows 10 includes .NET Framework / SAPI. Require an English voice explicitly.
    script = "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Speech; " \
        "$voice=New-Object System.Speech.Synthesis.SpeechSynthesizer; " \
        "try { $voice.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::NotSet," \
        "[System.Speech.Synthesis.VoiceAge]::NotSet,0,[Globalization.CultureInfo]'en-US'); " \
        "$voice.SetOutputToWaveFile($env:MIO_PROBE_WAV); " \
        "$voice.Speak('This is a speech recognition test.'); } finally { $voice.Dispose() }"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=25, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or not wav_path.is_file() or not audio_valid(wav_path.read_bytes()):
        raise ProbeFailure('sample_unavailable')
    code = """import sys,re
from difflib import SequenceMatcher
from faster_whisper import WhisperModel
model=WhisperModel(sys.argv[1],device='cpu',compute_type='int8',local_files_only=True)
segments,_=model.transcribe(sys.argv[2],language='en',beam_size=1)
text=' '.join(s.text for s in segments)
normal=lambda s: re.sub(r'[^a-z ]','',s.lower()).strip()
assert SequenceMatcher(None,normal(text),'this is a speech recognition test').ratio() >= .65
"""
    result = subprocess.run([str(layout['python']), '-I', '-B', '-X', 'utf8', '-c', code,
                            str(layout['model']), str(wav_path)], env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=110, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ProbeFailure('invalid_output')
    return {'ok': True, 'level': 'inference'}


async def vision(base_url: str) -> dict:
    from PIL import Image
    from .local_vision_service import DEFAULT_MODEL
    buffer = io.BytesIO()
    Image.new('RGB', (96, 96), (240, 20, 20)).save(buffer, format='PNG')
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        response = await client.post(base_url + '/api/chat', json={
            'model': DEFAULT_MODEL, 'stream': False, 'keep_alive': '30s',
            'messages': [{'role': 'user', 'content': 'What is the main color? Reply with one English color word.',
                          'images': [base64.b64encode(buffer.getvalue()).decode('ascii')]}],
            'options': {'temperature': 0, 'num_ctx': 512, 'num_predict': 12}})
        response.raise_for_status()
        content = str((response.json().get('message') or {}).get('content', '')).lower()
        if 'red' not in content:
            raise ProbeFailure('invalid_output')
    return {'ok': True, 'level': 'inference'}


async def comfyui() -> dict:
    from .creation_service import comfyui_health
    result = await comfyui_health(include_nodes=True)
    if not result.get('ok') or not result.get('node_count'):
        raise ProbeFailure('unavailable')
    return {'ok': True, 'level': 'connection'}


def main(arguments: list[str]) -> int:
    if len(arguments) != 2 or arguments[0] not in {'chat', 'tts', 'asr', 'comfyui'}:
        return 2
    capability, output = arguments[0], Path(arguments[1])
    try:
        if capability == 'chat':
            result = asyncio.run(chat())
        elif capability == 'tts':
            result = tts()
        elif capability == 'asr':
            result = asr(output.parent)
        else:
            result = asyncio.run(comfyui())
    except ProbeFailure as exc:
        result = {'ok': False, 'error_code': exc.code}
    except (TimeoutError, subprocess.TimeoutExpired, httpx.TimeoutException):
        result = {'ok': False, 'error_code': 'timeout'}
    except FileNotFoundError:
        result = {'ok': False, 'error_code': 'runtime_missing'}
    except Exception:
        result = {'ok': False, 'error_code': 'probe_failed'}
    output.write_text(json.dumps(result), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
