"""Verify incremental visibility, completion and cancellation on real HTTP streams."""
import asyncio
import json
import threading

import httpx
import pytest

from app import local_vision_service as vision


class Lines(httpx.AsyncByteStream):
    def __init__(self, events, pause=None):
        self.events = events
        self.pause = pause
        self.closed = False

    async def __aiter__(self):
        for event in self.events:
            yield (json.dumps(event) + '\n').encode()
        if self.pause is not None:
            await self.pause.wait()

    async def aclose(self):
        self.closed = True


def install_client(monkeypatch, stream):
    async def ready():
        return {}
    monkeypatch.setattr(vision, 'ensure_ready', ready)
    monkeypatch.setattr(vision, '_owned_server_url', lambda: 'http://127.0.0.1:23456')
    def respond(request):
        payload = json.loads(request.content)
        assert payload['stream'] is True and payload['think'] is False
        assert payload['model'] == 'qwen3.5:2b'
        assert payload['messages'][1]['images'] == ['aW1hZ2U=']
        return httpx.Response(200, stream=stream)
    client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(vision.httpx, 'AsyncClient', lambda **kw: client(transport=transport, **kw))


def test_visible_piece_arrives_before_terminal_event(monkeypatch):
    async def run():
        gate = asyncio.Event()
        stream = Lines([{'message': {'thinking': 'private'}}, {'message': {'content': '已暂停。'}}], gate)
        install_client(monkeypatch, stream)
        pieces = vision.iter_image_text(prompt='状态', image=b'image', system_prompt='只读图')
        assert await anext(pieces) == '已暂停。'
        assert not gate.is_set()
        await pieces.aclose()
        assert stream.closed
    asyncio.run(run())


@pytest.mark.parametrize('events', [
    [{'message': {'content': '未结束。'}}],
    [{'message': {'thinking': 'hidden'}}, {'done': True}],
    [{'error': 'out of memory'}],
    [{'message': {'content': '截断。'}}, {'done': True, 'done_reason': 'length'}],
])
def test_incomplete_empty_or_failed_stream_is_rejected(monkeypatch, events):
    async def run():
        stream = Lines(events)
        install_client(monkeypatch, stream)
        with pytest.raises(RuntimeError, match='流式识别失败'):
            _ = [piece async for piece in vision.iter_image_text(prompt='状态', image=b'image', system_prompt='只读图')]
        assert stream.closed
    asyncio.run(run())


def test_completed_stream_ignores_thinking_and_joins_visible_text(monkeypatch):
    async def run():
        stream = Lines([{'message': {'thinking': 'hidden', 'content': '已'}},
                        {'message': {'content': '暂停。'}, 'done': True, 'done_reason': 'stop'}])
        install_client(monkeypatch, stream)
        pieces = [piece async for piece in vision.iter_image_text(prompt='状态', image=b'image', system_prompt='只读图')]
        assert ''.join(pieces) == '已暂停。'
        assert stream.closed
        assert vision._inference_probe_result['ready'] is True
        assert vision._inference_probe_at > 0
    asyncio.run(run())


def test_cancel_closes_blocked_transport(monkeypatch):
    async def run():
        stream = Lines([], asyncio.Event())
        install_client(monkeypatch, stream)
        cancel = threading.Event()
        async def consume():
            return [p async for p in vision.iter_image_text(prompt='状态', image=b'image', system_prompt='只读图', cancel_event=cancel)]
        task = asyncio.create_task(consume())
        await asyncio.sleep(.02)
        cancel.set()
        with pytest.raises(InterruptedError, match='取消'):
            await asyncio.wait_for(task, timeout=1)
        assert stream.closed
    asyncio.run(run())
