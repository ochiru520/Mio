"""Message streaming cancellation releases only that request's synthesizer."""
import asyncio
import threading
from unittest.mock import patch

from app.routes import companion as route


def test_message_stream_sends_first_audio_before_generation_finishes():
    async def scenario():
        released = threading.Event()
        def generate(*_args, **kwargs):
            assert kwargs['cancel_event'] is not None
            yield b'RIFF-header'
            assert released.wait(2)
            yield b'pcm'
        with patch.object(route.companion_service, 'supports_call_speech_stream', return_value=True), patch.object(route.companion_service, 'iter_speech_wav_stream', side_effect=generate):
            response = await route.companion_voice_stream(route.SpeechRequest(text='固定测试。', language='zh'))
            assert await anext(response.body_iterator) == b'RIFF-header'
            released.set()
            assert await anext(response.body_iterator) == b'pcm'
            assert [chunk async for chunk in response.body_iterator] == []
    asyncio.run(scenario())


def test_disconnect_during_pending_pcm_cancels_and_closes_worker_generator():
    async def scenario():
        pulling, closed, events = threading.Event(), threading.Event(), []
        def generate(*_args, **kwargs):
            cancel = kwargs['cancel_event']; events.append(cancel)
            try:
                yield b'RIFF-header'
                pulling.set()
                assert cancel.wait(3)
                raise InterruptedError('cancelled fixed synthetic request')
            finally:
                closed.set()
        with patch.object(route.companion_service, 'supports_call_speech_stream', return_value=True), patch.object(route.companion_service, 'iter_speech_wav_stream', side_effect=generate):
            response = await route.companion_voice_stream(route.SpeechRequest(text='固定测试。', language='ja'))
            assert await anext(response.body_iterator) == b'RIFF-header'
            pending = asyncio.create_task(anext(response.body_iterator))
            assert await asyncio.to_thread(pulling.wait, 2)
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass
            assert events[0].is_set()
            assert await asyncio.to_thread(closed.wait, 2)
    asyncio.run(scenario())


def test_non_native_engine_retains_complete_wav_compatibility():
    async def scenario():
        with patch.object(route.companion_service, 'supports_call_speech_stream', return_value=False), patch.object(route.companion_service, 'iter_speech_wav_stream', return_value=iter([b'fixed WAV'])) as generate:
            response = await route.companion_voice_stream(route.SpeechRequest(text='固定测试。', language='zh'))
            assert [chunk async for chunk in response.body_iterator] == [b'fixed WAV']
            assert generate.call_args.kwargs['cancel_event'] is None
    asyncio.run(scenario())
