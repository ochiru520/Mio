from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave

from fastapi import HTTPException

from app import call_session_service, call_speech_stream, companion_service
from app.routes import companion as route


def wav():
    output = BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(32000)
        writer.writeframes(b'\x01\x00' * 3200)
    return output.getvalue()


class CallSpeechTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = call_session_service.CallSessionManager()
        self.patch = patch.object(call_session_service, 'manager', self.manager)
        self.patch.start()
        self.patch2 = patch.object(call_speech_stream, 'manager', self.manager)
        self.patch2.start()
        self.session = self.manager.start()

    def tearDown(self):
        for stream in list(call_speech_stream._streams.values()):
            stream.abort('test finished')
        call_speech_stream._streams.clear()
        self.patch2.stop(); self.patch.stop()

    def turn(self):
        return self.manager.begin_turn(self.session['call_session_id'], 1)

    async def test_first_pcm_arrives_before_later_sentence_and_one_header(self):
        token = self.turn(); stream = call_speech_stream.create(token)
        self.assertTrue(stream.append('第一句。'))
        stream.claim()
        generated = []
        def synth(text, cancelled):
            generated.append(text)
            yield companion_service._streaming_pcm_wav_header()
            yield b'\x01\x00' * 160
        audio = call_speech_stream.audio_chunks(stream, synth)
        header = await asyncio.wait_for(anext(audio), 1)
        pcm = await asyncio.wait_for(anext(audio), 1)
        self.assertEqual(header[:4], b'RIFF')
        self.assertEqual(pcm, b'\x01\x00' * 160)
        self.assertFalse(stream.finished)
        self.assertEqual(generated, ['第一句。'])
        self.assertFalse(stream.append('第二句。'))
        stream.finish()
        tail = [chunk async for chunk in audio]
        self.assertEqual(tail, [pcm])
        self.assertFalse(stream.cancelled.is_set())
        self.assertIsNotNone(stream.first_pcm_at)

    async def test_disconnect_cancels_blocked_synthesizer(self):
        stream = call_speech_stream.create(self.turn()); stream.append('开始。')
        stopped = threading.Event()
        def synth(text, cancelled):
            try:
                yield companion_service._streaming_pcm_wav_header()
                cancelled.wait(2)
            finally:
                stopped.set()
        audio = call_speech_stream.audio_chunks(stream, synth)
        await asyncio.wait_for(anext(audio), 1)
        await audio.aclose()
        self.assertTrue(stream.cancelled.is_set())
        self.assertTrue(stopped.is_set())

    async def test_stale_session_and_duplicate_consumers_rejected(self):
        stream = call_speech_stream.create(self.turn()); stream.append('你好。')
        with self.assertRaises(call_session_service.CallSessionConflict):
            call_speech_stream.get(stream.token.response_id, 'wrong-session')
        stream.claim()
        with self.assertRaises(call_session_service.CallSessionConflict): stream.claim()
        self.manager.start()
        with self.assertRaises(call_session_service.CallSessionConflict): stream.append('旧回复。')

    async def test_interrupt_invalidates_late_commit_and_audio_ack(self):
        token = self.turn()
        self.assertEqual(self.manager.interrupt(self.session['call_session_id'], token.response_id), token.response_id)
        self.assertFalse(self.manager.is_current(token))
        with self.assertRaises(call_session_service.CallSessionConflict): self.manager.commit_if_current(token, lambda: self.fail('late commit'))
        self.assertFalse(self.manager.record_voice_started(token.response_id, {}))
        self.assertFalse(self.manager.record_voice_ended(token.response_id, {}))

    async def test_early_interrupt_reserves_turn_before_post_arrives(self):
        result = await route.companion_call_interrupt(route.CompanionCallControlRequest(call_session_id=self.session['call_session_id'], turn_id=1))
        self.assertTrue(result['interrupted']); self.assertEqual(result['next_turn_id'], 2)
        with self.assertRaises(call_session_service.CallSessionConflict): self.manager.begin_turn(self.session['call_session_id'], 1)
        self.assertEqual(self.manager.begin_turn(self.session['call_session_id'], 2).turn_id, 2)

    async def test_commit_preserves_early_playback_or_finished_state(self):
        token = self.turn()
        self.manager.record_voice_started(token.response_id, {})
        self.manager.update_turn(token, 'committing', reply='你好')
        self.manager.commit_if_current(token, lambda: None)
        self.assertEqual(self.manager.status()['stage'], 'speaking')
        self.manager.record_voice_ended(token.response_id, {'reason':'finished'})
        self.manager.commit_if_current(token, lambda: None)
        self.assertEqual(self.manager.status()['stage'], 'listening')
        self.assertFalse(self.manager.record_voice_started(token.response_id, {}))
        self.assertEqual(self.manager.status()['stage'], 'listening')

    async def test_commit_preserves_early_playback_failure(self):
        token = self.turn()
        self.manager.record_voice_ended(token.response_id, {'reason':'error'})
        self.manager.update_turn(token, 'committing', reply='你好')
        self.manager.commit_if_current(token, lambda: None)
        self.assertEqual(self.manager.status()['stage'], 'error')

    async def test_phone_first_sentence_before_model_completion_and_no_repeat(self):
        await self.phone_stream_scenario(disconnect=False)

    async def test_audio_disconnect_before_model_completion_prevents_commit(self):
        await self.phone_stream_scenario(disconnect=True)

    async def phone_stream_scenario(self, *, disconnect):
        first = asyncio.Event(); finish = asyncio.Event()
        async def model(message, **kwargs):
            await kwargs['on_reply_delta']('你好。', '你好。')
            first.set()
            await finish.wait()
            if not disconnect:
                await kwargs['on_reply_delta']('接着说。', '你好。接着说。')
            return SimpleNamespace(reply='你好\n\n接着说', replies=['你好','接着说'],request_id=kwargs['request_id'],model_id='test',reasoning_level='off',first_token_latency_ms=10)
        with ExitStack() as stack:
            stack.enter_context(patch.object(route.system_audio_service,'status',return_value={'ready':True}))
            stack.enter_context(patch.object(route.system_audio_service,'transcribe_wav_for_quality',return_value={'text':'你好','language':'zh','accepted':True}))
            stack.enter_context(patch.object(route.companion_service,'load_config',return_value={'voice_enabled':True,'pet_chat_model_id':'test'}))
            stack.enter_context(patch.object(route.companion_service,'_uses_genie_runtime',return_value=True))
            stack.enter_context(patch.object(route,'resolve_model_id',return_value='test'))
            stack.enter_context(patch.object(route,'_pet_call_screen_context',return_value=''))
            stack.enter_context(patch.object(route,'chat_with_ai',side_effect=model))
            persist = stack.enter_context(patch.object(route,'persist_generated_chat_result'))
            publish = stack.enter_context(patch.object(route.pet_event_service,'publish'))
            stack.enter_context(patch.object(route,'_record_chat_route_success'))
            task = asyncio.create_task(route.companion_call_turn(route.CompanionCallTurnRequest(wav_base64=base64.b64encode(wav()).decode(),call_session_id=self.session['call_session_id'],turn_id=1)))
            await asyncio.wait_for(first.wait(),1)
            self.assertFalse(task.done()); persist.assert_not_called()
            event = publish.call_args.args[1]
            self.assertTrue(event['speech_stream'])
            stream = call_speech_stream.get(event['response_id'],self.session['call_session_id'])
            self.assertEqual(list(stream.pending),['你好。'])
            self.assertEqual(self.manager.status()['reply'],'你好。')
            if disconnect:
                stream.abort('HTTP disconnected')
                finish.set()
                with self.assertRaises(HTTPException) as error:
                    await asyncio.wait_for(task, 1)
                self.assertEqual(error.exception.status_code, 409)
                persist.assert_not_called()
                self.assertTrue(any(c.args[0] == 'speech_interrupt' for c in publish.call_args_list))
                return
            finish.set(); result=await asyncio.wait_for(task,1)
            self.assertTrue(result['speech_stream'])
            self.assertEqual(list(stream.pending),['你好。','接着说。'])
            self.assertTrue(stream.finished)
            self.assertEqual(sum(c.args[0]=='speak' for c in publish.call_args_list),1)
            persist.assert_called_once()

    async def test_stream_error_is_not_replaced_by_complete_audio(self):
        stream = call_speech_stream.create(self.turn());stream.append('你好。');stream.finish()
        def broken(text, cancelled):
            yield companion_service._streaming_pcm_wav_header()
            raise OSError('local synthesis failed')
        audio=call_speech_stream.audio_chunks(stream,broken)
        await anext(audio)
        with self.assertRaisesRegex(OSError,'local synthesis failed'): await anext(audio)

    async def test_queue_preserves_text_over_legacy_600_character_limit(self):
        stream = call_speech_stream.create(self.turn())
        text = '这是一段比较长的回复' * 79
        stream.append(text); stream.finish()
        observed = list(stream.texts())
        self.assertEqual(''.join(observed), text)
        self.assertTrue(all(len(piece) <= 600 for piece in observed))

    async def test_wrong_sample_rate_rejected_before_playback(self):
        stream = call_speech_stream.create(self.turn()); stream.append('你好。'); stream.finish()
        def synth(text, cancelled):
            header = bytearray(companion_service._streaming_pcm_wav_header())
            struct.pack_into('<I', header, 24, 16000)
            yield bytes(header)
        audio = call_speech_stream.audio_chunks(stream, synth)
        with self.assertRaises(ValueError): await anext(audio)
        self.assertTrue(stream.cancelled.is_set())

    async def test_combined_wav_chunk_records_pcm_and_has_one_header(self):
        stream = call_speech_stream.create(self.turn()); stream.append('你好。'); stream.finish()
        def synth(text, cancelled): yield wav()
        chunks = [part async for part in call_speech_stream.audio_chunks(stream, synth)]
        self.assertEqual(b''.join(chunks), wav())
        self.assertIsNotNone(stream.first_pcm_at)


class CallPcmQualityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        config = {'voice_engine':'gpt_sovits', 'engine':'gpt_sovits', 'local_voice_runtime':'genie', 'voice_volume':85}
        self.config = config
        self.stack.enter_context(patch.object(companion_service, 'load_config', return_value=config))
        self.stack.enter_context(patch.object(companion_service, 'resolve_voice_profile', return_value=('test',config)))
        self.stack.enter_context(patch.object(companion_service, '_emotion_reference', return_value=(Path('synthetic-reference.wav'),'公开合成参考','zh')))
        self.stack.enter_context(patch.object(companion_service, '_prepare_speech_input', side_effect=lambda text, config: (text,'zh',False)))
        self.stack.enter_context(patch.object(companion_service, '_reference_audio_candidates', return_value=[]))
        self.stack.enter_context(patch.object(companion_service, '_record_reference_audio_block'))
        self.legacy = self.stack.enter_context(patch('app.genie_tts_service.synthesize_wav'))

    def test_pcm_quality_rejects_repeated_reference_copy_before_release(self):
        pcm = wav()[44:]
        def generate_pcm(*args, **kwargs): yield pcm
        generate = self.stack.enter_context(patch('app.genie_tts_service.iter_speech_pcm', side_effect=generate_pcm))
        self.stack.enter_context(patch.object(companion_service, '_looks_like_any_reference_audio', return_value=(True,1.0,Path('synthetic-reference.wav'))))
        with self.assertRaisesRegex(OSError, '已阻止播放参考音频'):
            companion_service.synthesize_speech_wav('你好',_prepared_language='zh',_stream_cancel_event=threading.Event())
        self.assertEqual(generate.call_count,2)
        self.legacy.assert_not_called()

    def test_quality_retry_cancellation_never_uses_uncancellable_fallback(self):
        cancelled = threading.Event()
        calls = []
        def pcm(text, config, *, cancel_event):
            calls.append(text)
            self.assertIs(cancel_event,cancelled)
            if len(calls) == 2:
                cancelled.set()
            yield wav()[44:]
        self.stack.enter_context(patch('app.genie_tts_service.iter_speech_pcm', side_effect=pcm))
        self.stack.enter_context(patch.object(companion_service, '_looks_like_any_reference_audio', return_value=(True,1.0,Path('synthetic-reference.wav'))))
        with self.assertRaisesRegex(OSError,'已取消'):
            companion_service.synthesize_speech_wav('你好',_prepared_language='zh',_stream_cancel_event=cancelled)
        self.assertEqual(len(calls),2)
        self.legacy.assert_not_called()

    def test_cloud_and_conversion_engines_keep_legacy_path(self):
        self.config['voice_engine'] = 'cloud'
        self.assertFalse(companion_service.supports_call_speech_stream(config=self.config))
        self.config['voice_engine'] = 'gpt_sovits'; self.config['engine'] = 'so_vits_svc'
        self.assertFalse(companion_service.supports_call_speech_stream(config=self.config))
        with self.assertRaisesRegex(ValueError,'音色已变化'):
            list(companion_service.iter_speech_wav_stream('你好',cancel_event=threading.Event()))


if __name__ == '__main__': unittest.main()
