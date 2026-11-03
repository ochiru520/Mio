from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import call_session_service, call_speech_stream, companion_service
from app.routes import companion as route


class VoiceEngineRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_test_text_and_selected_engine_reach_actual_playback(self):
        config = {'voice_engine': 'aniflive', 'pet_speech_language': 'zh'}
        with patch.object(companion_service, 'load_config', return_value=config), patch.object(companion_service, 'speak_text', return_value=True) as play:
            result = await route.companion_voice_test(route.VoiceTestRequest(text='同一段文字用于比较。'))
        self.assertEqual(play.call_args.args, ('同一段文字用于比较。',))
        self.assertTrue(play.call_args.kwargs['wait'])
        self.assertFalse(play.call_args.kwargs['allow_realtime_bridge'])
        self.assertEqual(result, {'spoken':True,'engine':'aniflive'})

    async def test_failed_playback_cannot_be_reported_as_success(self):
        with patch.object(companion_service, 'load_config', return_value={}), patch.object(companion_service, 'speak_text', return_value=False), patch.object(companion_service, 'voice_runtime_status', return_value={'last_error':'服务未连接'}):
            with self.assertRaises(HTTPException) as raised:
                await route.companion_voice_test(route.VoiceTestRequest(text='测试'))
        self.assertEqual(raised.exception.status_code,500)
        self.assertIn('服务未连接',raised.exception.detail)

    async def test_call_audio_keeps_the_config_of_its_turn_across_engine_switch(self):
        manager = call_session_service.CallSessionManager()
        settings = {'voice_engine':'aniflive','aniflive_model':'voice-a','voice_profiles':{'mio':{'name':'old'}}}
        with patch.object(call_speech_stream, 'manager', manager):
            session = manager.start();token = manager.begin_turn(session['call_session_id'],1)
            stream = call_speech_stream.create(token,runtime_config=settings)
            stream.append('第一句。'); stream.finish()
            settings['voice_engine']='indextts2'
            settings['voice_profiles']['mio']['name']='changed'
            received=[]
            def synthesize(text, **kwargs):
                received.append(kwargs)
                yield companion_service._streaming_pcm_wav_header()
                yield b'\x01\x00' * 160
            try:
                with patch.object(companion_service,'supports_call_speech_stream',return_value=True) as supported, patch.object(companion_service,'iter_speech_wav_stream',side_effect=synthesize):
                    response = await route.companion_voice_stream(route.SpeechRequest(text='第一句。',speech_stream=True,response_id=token.response_id,call_session_id=token.call_session_id))
                    audio=b''.join([part async for part in response.body_iterator])
                self.assertEqual(len(audio),364)
                self.assertEqual(received[0]['config_snapshot']['voice_engine'],'aniflive')
                self.assertEqual(received[0]['config_snapshot']['voice_profiles']['mio']['name'],'old')
                self.assertEqual(supported.call_args.kwargs['config']['aniflive_model'],'voice-a')
            finally:
                stream.abort('test finished')
                call_speech_stream._streams.pop(token.response_id,None)

    async def test_configuration_accepts_three_new_choices_and_rejects_mistyped_versions(self):
        for name in ['gpt_sovits','indextts2','aniflive','gpt_sovits_v5','cloud']:
            with self.subTest(name=name):
                request=route.CompanionSettingsRequest(voice_engine=name,indextts2_version='2.5',indextts2_language='ja')
                self.assertEqual(request.voice_engine,name)
        for changes in [{'voice_engine':'gpt_sovits_v50'},{'indextts2_version':'5'},{'indextts2_language':'invalid'}]:
            with self.assertRaises(ValidationError):route.CompanionSettingsRequest(**changes)
        with self.assertRaises(ValidationError):route.VoiceTestRequest(text='字'*601)


if __name__=='__main__':unittest.main()
