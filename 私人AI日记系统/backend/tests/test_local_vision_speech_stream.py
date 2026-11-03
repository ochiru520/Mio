from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import local_vision_speech_stream as speech
from app.routes import companion as route


PCM = b'\x20\x00' * 320
CONFIG = {'voice_engine': 'aniflive', 'aniflive_url': 'http://127.0.0.1:9882', 'voice_volume': 100}


class VisionSpeechStreamTests(unittest.IsolatedAsyncioTestCase):
    async def consume(self, deltas, *, synth=None):
        spoken = []
        async def vision(**kwargs):
            self.assertIn('不输出JSON', kwargs['system_prompt'])
            for delta in deltas:
                yield delta
        def synthesize(text, config, **kwargs):
            spoken.append(text)
            yield PCM
        with patch.object(speech.local_vision_service, 'iter_image_text', vision, create=True), \
             patch.object(speech.aniflive_tts_service, 'iter_speech_pcm', synth or synthesize):
            result = [part async for part in speech.iter_description_pcm(prompt='看状态', image=b'png', config=CONFIG)]
        return spoken, result

    async def test_decimal_and_split_filename_remain_whole_with_final_tail(self):
        spoken, audio = await self.consume(['大小1.', '25MB；文件名test.', 'wav。', '最后没有标点'])
        self.assertEqual(spoken, ['大小1.25MB；', '文件名test.wav。', '最后没有标点'])
        self.assertEqual(audio, [PCM] * 3)

    async def test_split_hidden_thinking_is_never_spoken(self):
        spoken, _ = await self.consume(['<thi', 'nk>秘密。', '</think>状态已暂停。'])
        self.assertEqual(spoken, ['状态已暂停。'])

    async def test_json_payload_is_not_read_aloud(self):
        for text in ['{"状态":"暂停。"}', '["暂停。"]', '```json\n{"状态":"暂停"}']:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '结构化'):
                await self.consume([text])

    async def test_first_audio_precedes_later_vision_and_failure_is_not_silent(self):
        release = asyncio.Event()
        closed = asyncio.Event()
        async def vision(**kwargs):
            try:
                yield '第一句。'
                await release.wait()
                raise OSError('模型流末尾丢失')
            finally:
                closed.set()
        def synth(text, config, **kwargs):
            yield PCM
        with patch.object(speech.local_vision_service, 'iter_image_text', vision, create=True), \
             patch.object(speech.aniflive_tts_service, 'iter_speech_pcm', synth):
            audio = speech.iter_description_pcm(prompt='读图', image=b'png', config=CONFIG)
            self.assertEqual(await asyncio.wait_for(anext(audio), 1), PCM)
            self.assertFalse(closed.is_set())
            release.set()
            with self.assertRaisesRegex(OSError, '末尾丢失'):
                await asyncio.wait_for(anext(audio), 1)
            self.assertTrue(closed.is_set())

    async def test_backpressure_and_disconnect_close_both_producers(self):
        generated = []
        vision_closed = asyncio.Event()
        synth_closed = threading.Event()
        async def vision(**kwargs):
            try:
                for i in range(30):
                    generated.append(i)
                    yield f'第{i}句。'
            finally:
                vision_closed.set()
        def synth(text, config, *, cancel_event, **kwargs):
            try:
                yield PCM
                cancel_event.wait(3)
            finally:
                synth_closed.set()
        with patch.object(speech.local_vision_service, 'iter_image_text', vision, create=True), \
             patch.object(speech.aniflive_tts_service, 'iter_speech_pcm', synth):
            audio = speech.iter_description_pcm(prompt='读图', image=b'png', config=CONFIG)
            await asyncio.wait_for(anext(audio), 1)
            await asyncio.sleep(.05)
            self.assertLessEqual(len(generated), speech.TEXT_QUEUE_SIZE + 2)
            await audio.aclose()
        self.assertTrue(vision_closed.is_set())
        self.assertTrue(synth_closed.is_set())

    async def test_pcm_buffer_is_bounded_and_oversized_chunks_are_split(self):
        chunks = []
        def synth(text, config, **kwargs):
            yield PCM * 200
        _, chunks = await self.consume(['第一句。'], synth=synth)
        self.assertEqual(b''.join(chunks), PCM * 200)
        self.assertLessEqual(max(map(len, chunks)), speech.PCM_CHUNK_BYTES)

    async def test_partial_pcm_followed_by_synthesis_error_fails(self):
        def synth(text, config, **kwargs):
            yield PCM
            raise OSError('合成失败')
        with self.assertRaisesRegex(OSError, '合成失败'):
            await self.consume(['第一句。'], synth=synth)

    async def test_empty_or_odd_pcm_is_rejected(self):
        for bad in [b'', b'\x00']:
            def synth(text, config, **kwargs):
                yield bad
            with self.subTest(pcm=bad), self.assertRaises(ValueError):
                await self.consume(['第一句。'], synth=synth)

    async def test_no_arbitrary_cut_of_overlong_sentence(self):
        with self.assertRaisesRegex(ValueError, '单句过长'):
            await self.consume(['文' * 601])

    async def test_externally_cancelled_request_does_not_start_tts(self):
        cancelled = threading.Event();cancelled.set()
        async def vision(**kwargs):
            yield '不能播出。'
        with patch.object(speech.local_vision_service, 'iter_image_text', vision, create=True), \
             patch.object(speech.aniflive_tts_service, 'iter_speech_pcm') as synth:
            audio = speech.iter_description_pcm(prompt='读图', image=b'png', config=CONFIG, cancel_event=cancelled)
            with self.assertRaises(InterruptedError):
                await anext(audio)
            synth.assert_not_called()

    async def test_requested_language_controls_model_description_and_tts(self):
        for language, expected_prompt, answer in [
            ('zh', '只用自然中文', '画面已暂停。'),
            ('ja', '只用自然日语', '画面は一時停止中です。'),
        ]:
            async def vision(**kwargs):
                self.assertIn(expected_prompt, kwargs['system_prompt'])
                if language == 'ja':
                    self.assertIn('即使截图含中文，也用日语描述', kwargs['system_prompt'])
                yield answer
            def synth(text, config, **kwargs):
                self.assertEqual(text, answer)
                self.assertEqual(kwargs['language'], language)
                yield PCM
            with self.subTest(language=language), \
                 patch.object(speech.local_vision_service, 'iter_image_text', vision, create=True), \
                 patch.object(speech.aniflive_tts_service, 'iter_speech_pcm', synth):
                audio = speech.iter_description_pcm(prompt='读图', image=b'png', config=CONFIG, language=language)
                self.assertEqual([part async for part in audio], [PCM])

    async def test_external_cancel_stops_workers_while_consumer_is_paused(self):
        cancelled = threading.Event()
        closed = threading.Event()
        async def vision(**kwargs):
            yield '第一句。'
            await asyncio.Event().wait()
        def synth(text, config, *, cancel_event, **kwargs):
            try:
                yield PCM
                cancel_event.wait(2)
            finally:
                closed.set()
        with patch.object(speech.local_vision_service, 'iter_image_text', vision, create=True), \
             patch.object(speech.aniflive_tts_service, 'iter_speech_pcm', synth):
            audio = speech.iter_description_pcm(prompt='读图', image=b'png', config=CONFIG, cancel_event=cancelled)
            await asyncio.wait_for(anext(audio), 1)
            cancelled.set()
            # No next audio pull: the cancellation watcher must still run.
            self.assertTrue(await asyncio.to_thread(closed.wait, 1))
            with self.assertRaises(InterruptedError):
                await anext(audio)

    async def test_pcm_config_is_a_stable_snapshot(self):
        config = dict(CONFIG)
        async def vision(**kwargs):
            config['aniflive_url'] = 'http://127.0.0.1:9999'
            yield '第一句。'
        def synth(text, snapshot, **kwargs):
            self.assertEqual(snapshot['aniflive_url'], CONFIG['aniflive_url'])
            yield PCM
        with patch.object(speech.local_vision_service, 'iter_image_text', vision, create=True), \
             patch.object(speech.aniflive_tts_service, 'iter_speech_pcm', synth):
            audio = speech.iter_description_pcm(prompt='读图', image=b'png', config=config)
            self.assertEqual([part async for part in audio], [PCM])


class VisionSpeechRouteTests(unittest.IsolatedAsyncioTestCase):
    def payload(self):
        return route.LocalVisionDescriptionRequest(image_data_url='data:image/png;base64,cG5n', question='读图')

    async def test_wrong_engine_rejected_without_start_or_capture(self):
        with patch.object(route.companion_service, 'load_config', return_value={'voice_engine': 'gpt_sovits'}), \
             patch.object(route.companion_service, 'start_voice_service') as start:
            with self.assertRaises(HTTPException) as exc:
                await route.companion_local_vision_description_audio(self.payload(), SimpleNamespace())
            self.assertEqual(exc.exception.status_code, 409)
            start.assert_not_called()

    async def test_explicit_image_stream_and_cleanup(self):
        from app import aniflive_tts_service, local_voice_engines, screen_frame_processor
        closed = asyncio.Event()
        async def pcm(**kwargs):
            self.assertEqual(kwargs['image'], b'validated-image')
            self.assertEqual(kwargs['config'], CONFIG)
            self.assertIn('cancel_event', kwargs)
            try:
                yield PCM
                yield PCM
            finally:
                closed.set()
        with patch.object(route.companion_service, 'load_config', return_value=CONFIG), \
             patch.object(route.companion_service, 'start_voice_service'), \
             patch.object(route, 'image_attachment_from_data_url', return_value=SimpleNamespace(content=b'png')), \
             patch.object(screen_frame_processor, 'process_frame', return_value=SimpleNamespace(content=b'validated-image')), \
             patch.object(local_voice_engines.manager, 'status', return_value={'managed_available': True, 'managed_running': True}), \
             patch.object(aniflive_tts_service, 'runtime_status', return_value={'service_running': True}), \
             patch.object(route.local_vision_speech_stream, 'iter_description_pcm', pcm):
            response = await route.companion_local_vision_description_audio(self.payload(), SimpleNamespace(is_disconnected=AsyncMock(return_value=False)))
            self.assertEqual(response.headers['x-tts-sample-format'], 's16le')
            self.assertEqual(response.headers['x-mio-experimental'], '1')
            self.assertEqual([part async for part in response.body_iterator], [PCM, PCM])
            self.assertTrue(closed.is_set())

    async def test_initial_model_error_is_http_error(self):
        from app import aniflive_tts_service, local_voice_engines, screen_frame_processor
        async def fail(**kwargs):
            raise OSError('图像流失败')
            yield
        with patch.object(route.companion_service, 'load_config', return_value=CONFIG), \
             patch.object(route.companion_service, 'start_voice_service'), \
             patch.object(route, 'image_attachment_from_data_url', return_value=SimpleNamespace(content=b'png')), \
             patch.object(screen_frame_processor, 'process_frame', return_value=SimpleNamespace(content=b'validated-image')), \
             patch.object(local_voice_engines.manager, 'status', return_value={'managed_available': True, 'managed_running': True}), \
             patch.object(aniflive_tts_service, 'runtime_status', return_value={'service_running': True}), \
             patch.object(route.local_vision_speech_stream, 'iter_description_pcm', fail):
            with self.assertRaises(HTTPException) as exc:
                await route.companion_local_vision_description_audio(self.payload(), SimpleNamespace(is_disconnected=AsyncMock(return_value=False)))
            self.assertEqual(exc.exception.status_code, 503)

    async def test_invalid_image_does_not_start_voice(self):
        with patch.object(route.companion_service, 'load_config', return_value=CONFIG), \
             patch.object(route, 'image_attachment_from_data_url', side_effect=RuntimeError('坏图片')), \
             patch.object(route.companion_service, 'start_voice_service') as start:
            with self.assertRaises(HTTPException) as exc:
                await route.companion_local_vision_description_audio(self.payload(), SimpleNamespace())
            self.assertEqual(exc.exception.status_code, 400)
            start.assert_not_called()

    async def test_late_audio_error_aborts_body_instead_of_clean_success(self):
        from app import aniflive_tts_service, local_voice_engines, screen_frame_processor
        async def fail(**kwargs):
            yield PCM
            raise OSError('后半段合成失败')
        with patch.object(route.companion_service, 'load_config', return_value=CONFIG), \
             patch.object(route.companion_service, 'start_voice_service'), \
             patch.object(route, 'image_attachment_from_data_url', return_value=SimpleNamespace(content=b'png')), \
             patch.object(screen_frame_processor, 'process_frame', return_value=SimpleNamespace(content=b'validated-image')), \
             patch.object(local_voice_engines.manager, 'status', return_value={'managed_available': True, 'managed_running': True}), \
             patch.object(aniflive_tts_service, 'runtime_status', return_value={'service_running': True}), \
             patch.object(route.local_vision_speech_stream, 'iter_description_pcm', fail):
            response = await route.companion_local_vision_description_audio(self.payload(), SimpleNamespace(is_disconnected=AsyncMock(return_value=False)))
            self.assertEqual(await anext(response.body_iterator), PCM)
            with self.assertRaisesRegex(OSError, '后半段'):
                await anext(response.body_iterator)
