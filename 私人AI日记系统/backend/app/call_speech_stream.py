"""One bounded speech stream per live call response, shared with its audio request."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from collections import deque
import queue
import struct
import threading
import time
from typing import AsyncIterator, Callable, Iterator

from .call_session_service import CallSessionConflict, CallTurnToken, manager


class CallSpeechStream:
    def __init__(self, token: CallTurnToken, model_id: str = "", runtime_config: dict | None = None) -> None:
        self.token = token
        self.model_id = model_id
        self.runtime_config = deepcopy(runtime_config) if runtime_config is not None else None
        self.created = time.monotonic()
        self.changed = threading.Condition()
        self.pending: deque[str] = deque()
        self.cancelled = threading.Event()
        self.finished = False
        self.claimed = False
        self.announced = False
        self.sentences = 0
        self.characters = 0
        self.first_sentence_at: float | None = None
        self.first_pcm_at: float | None = None
        self.error = ""

    def append(self, text: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        manager.require_current(self.token)
        with self.changed:
            if self.finished or self.cancelled.is_set():
                raise CallSessionConflict("电话语音流已结束。")
            parts = [text[offset:offset + 600] for offset in range(0, len(text), 600)]
            if self.characters + len(text) > 8000 or len(self.pending) + len(parts) > 64:
                raise ValueError("电话语音流超过单轮限制。")
            self.pending.extend(parts)
            self.sentences += len(parts)
            self.characters += len(text)
            self.first_sentence_at = self.first_sentence_at or time.monotonic()
            first = not self.announced
            self.announced = True
            self.changed.notify_all()
            return first

    def finish(self) -> None:
        with self.changed:
            self.finished = True
            self.changed.notify_all()

    def abort(self, reason: str = "电话已中断") -> None:
        with self.changed:
            self.error = str(reason)[:300]
            self.cancelled.set()
            self.pending.clear()
            self.finished = True
            self.changed.notify_all()

    def claim(self) -> None:
        manager.require_current(self.token)
        with self.changed:
            if self.claimed or self.cancelled.is_set():
                raise CallSessionConflict("电话语音流已被消费或取消。")
            self.claimed = True

    def texts(self) -> Iterator[str]:
        while True:
            manager.require_current(self.token)
            with self.changed:
                if self.cancelled.is_set():
                    raise CallSessionConflict(self.error or "电话已中断")
                if self.pending:
                    text = self.pending.popleft()
                elif self.finished:
                    return
                elif time.monotonic() - self.created > 180:
                    raise TimeoutError("等待电话回复超时。")
                else:
                    self.changed.wait(.1)
                    continue
            yield text


_lock = threading.Lock()
_streams: dict[str, CallSpeechStream] = {}


def create(token: CallTurnToken, model_id: str = "", *, runtime_config: dict | None = None) -> CallSpeechStream:
    manager.require_current(token)
    with _lock:
        now = time.monotonic()
        for key, stream in list(_streams.items()):
            if now - stream.created > 600:
                stream.abort("电话语音流已过期")
                _streams.pop(key, None)
        if len(_streams) >= 256:
            for key, stream in list(_streams.items()):
                if stream.finished:
                    _streams.pop(key)
                    break
            else:
                raise RuntimeError("电话语音流数量超限。")
        stream = CallSpeechStream(token, model_id, runtime_config)
        _streams[token.response_id] = stream
        return stream


def get(response_id: str, call_session_id: str) -> CallSpeechStream:
    with _lock:
        stream = _streams.get(response_id)
    if stream is None or stream.token.call_session_id != call_session_id:
        raise CallSessionConflict("电话语音流不存在或会话已变化。")
    manager.require_current(stream.token)
    return stream


def announced(response_id: str) -> bool:
    with _lock:
        stream = _streams.get(response_id)
        return bool(stream and stream.announced)


def abort(response_id: str, reason: str = "电话已中断") -> None:
    with _lock:
        stream = _streams.get(response_id)
    if stream:
        stream.abort(reason)


async def audio_chunks(stream: CallSpeechStream, synthesize: Callable) -> AsyncIterator[bytes]:
    """Bridge blocking local inference without stranding it on HTTP disconnect."""
    output: queue.Queue = queue.Queue(maxsize=8)
    stopped = threading.Event()
    sentinel = object()

    def put(item) -> bool:
        while not stopped.is_set() and not stream.cancelled.is_set():
            try:
                output.put(item, timeout=.1)
                return True
            except queue.Full:
                pass
        return False

    def produce() -> None:
        try:
            header_sent = False
            for text in stream.texts():
                parts = iter(synthesize(text, stream.cancelled))
                try:
                    first = next(parts)
                    # This protocol is enabled only for Genie mono 32kHz PCM WAV.
                    if (len(first) < 44 or first[:4] != b"RIFF" or first[8:16] != b"WAVEfmt "
                            or struct.unpack_from("<I", first, 16)[0] != 16
                            or struct.unpack_from("<HHIIHH", first, 20) != (1, 1, 32000, 64000, 2, 16)
                            or first[36:40] != b"data"):
                        raise ValueError("电话音频流缺少有效WAV头。")
                    if not header_sent:
                        if not put(first[:44]):
                            return
                        header_sent = True
                    if len(first) > 44:
                        manager.require_current(stream.token)
                        stream.first_pcm_at = stream.first_pcm_at or time.monotonic()
                        if not put(first[44:]):
                            return
                    for chunk in parts:
                        if not chunk:
                            continue
                        manager.require_current(stream.token)
                        stream.first_pcm_at = stream.first_pcm_at or time.monotonic()
                        if not put(chunk):
                            return
                finally:
                    close = getattr(parts, "close", None)
                    if close:
                        close()
            put(sentinel)
        except BaseException as exc:
            put(exc)

    thread = threading.Thread(target=produce, name="mio-call-audio", daemon=True)
    thread.start()
    completed = False
    try:
        while True:
            if stream.cancelled.is_set():
                raise CallSessionConflict(stream.error or "电话已中断")
            try:
                item = await asyncio.to_thread(output.get, True, .1)
            except queue.Empty:
                continue
            if item is sentinel:
                completed = True
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stopped.set()
        if not completed:
            stream.abort("电话音频连接已关闭")
        if thread.is_alive():
            await asyncio.to_thread(thread.join, 2)
