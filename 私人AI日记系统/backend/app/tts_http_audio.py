"""Bounded audio transport shared by optional independently managed TTS services."""
from __future__ import annotations

import audioop
from contextlib import contextmanager
from io import BytesIO
import struct
import threading
from urllib.parse import urlsplit
import wave

import httpx

MAX_AUDIO_BYTES = 64 * 1024 * 1024


def service_url(value: str, default: str) -> str:
    url = str(value or default).strip().rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('语音服务地址必须为不含账号、查询参数或片段的 HTTP(S) 地址。')
    return url


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError('语音合成已取消。')


@contextmanager
def cancellable_client(cancel_event: threading.Event | None = None, *, on_cancel=None, timeout=120):
    """Close this request's transport when cancelled, including before first audio."""
    check_cancel(cancel_event)
    client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5), trust_env=False)
    done = threading.Event()
    watcher = None
    if cancel_event is not None:
        def watch():
            while not done.wait(.05):
                if cancel_event.is_set():
                    if on_cancel is not None:
                        try:
                            on_cancel()
                        except (OSError, ValueError, httpx.HTTPError):
                            pass
                    client.close()
                    return
        watcher = threading.Thread(target=watch, name='mio-tts-http-cancel', daemon=True)
        watcher.start()
    try:
        with client as active_client:
            yield active_client
    except (httpx.HTTPError, OSError):
        check_cancel(cancel_event)
        raise
    finally:
        done.set()
        client.close()
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(3)


def read_audio(response: httpx.Response, cancel_event=None) -> bytes:
    data = bytearray()
    for chunk in response.iter_bytes():
        check_cancel(cancel_event)
        data.extend(chunk)
        if len(data) > MAX_AUDIO_BYTES:
            raise OSError('语音响应超过64MB限制。')
    check_cancel(cancel_event)
    if not response.is_success:
        raise OSError(f'语音服务返回HTTP {response.status_code}。')
    return bytes(data)


def wav_pcm(content: bytes) -> tuple[bytes, int, int, int]:
    try:
        if len(content) > MAX_AUDIO_BYTES:
            raise ValueError('too large')
        with wave.open(BytesIO(content), 'rb') as audio:
            channels, width, rate = audio.getnchannels(), audio.getsampwidth(), audio.getframerate()
            expected = audio.getnframes() * channels * width
            if (channels not in {1, 2} or width not in {1, 2, 3, 4} or not 8000 <= rate <= 192000
                    or audio.getcomptype() != 'NONE' or not 0 < expected <= MAX_AUDIO_BYTES):
                raise ValueError('unsupported PCM')
            pcm = audio.readframes(audio.getnframes())
            if len(pcm) != expected:
                raise ValueError('truncated PCM')
    except (wave.Error, EOFError, ValueError) as exc:
        raise OSError('语音服务没有返回完整、可播放的PCM WAV。') from exc
    return pcm, rate, channels, width


def normalize_wav(content: bytes) -> bytes:
    pcm, rate, channels, width = wav_pcm(content)
    if width == 1:
        pcm = audioop.bias(pcm, 1, -128)
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, .5, .5)
    if rate != 32000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 32000, None)
    return pcm_wav(pcm)


def pcm_wav(pcm: bytes) -> bytes:
    if not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_BYTES:
        raise OSError('语音PCM为空、帧不完整或超过限制。')
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(32000)
        out.writeframes(pcm)
    return buffer.getvalue()


def streaming_header() -> bytes:
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 0x7ffffff0 + 36, b'WAVE',
                       b'fmt ', 16, 1, 1, 32000, 64000, 2, 16, b'data', 0x7ffffff0)


def pcm_chunks(response: httpx.Response, cancel_event=None):
    """Normalize arbitrary S16LE transport chunk boundaries, retaining resampler state."""
    check_cancel(cancel_event)
    if not response.is_success:
        # Error JSON intentionally has no PCM headers. Do not disguise a failed
        # inference as a transport format error or echo a server's private body.
        if response.status_code == 422:
            raise OSError('这段内容没有可朗读的文字，或超出当前语音模型的输入范围（HTTP 422）。')
        if response.status_code == 409:
            raise OSError('语音会话已取消、失效或正在忙碌，请重试（HTTP 409）。')
        raise OSError(f'语音服务合成失败（HTTP {response.status_code}），请重试或查看引擎状态。')
    try:
        rate = int(response.headers['X-TTS-Sample-Rate'])
        channels = int(response.headers['X-TTS-Channels'])
    except (KeyError, ValueError) as exc:
        raise OSError('语音PCM流缺少采样率或声道信息。') from exc
    if (response.headers.get('X-TTS-Sample-Format') != 's16le'
            or not 8000 <= rate <= 192000 or channels not in {1, 2}
            or 'application/octet-stream' not in response.headers.get('content-type', '')):
        raise OSError('语音服务返回了不受支持的PCM格式。')
    carry, state, received = b'', None, 0
    for chunk in response.iter_bytes():
        check_cancel(cancel_event)
        received += len(chunk)
        if received > MAX_AUDIO_BYTES:
            raise OSError('语音响应超过64MB限制。')
        data = carry + chunk
        boundary = len(data) // (channels * 2) * channels * 2
        data, carry = data[:boundary], data[boundary:]
        if not data:
            continue
        if channels == 2:
            data = audioop.tomono(data, 2, .5, .5)
        if rate != 32000:
            data, state = audioop.ratecv(data, 2, 1, rate, 32000, state)
        if data:
            yield data
    check_cancel(cancel_event)
    if carry or not received:
        raise OSError('语音PCM流为空或末尾音频帧不完整。')
