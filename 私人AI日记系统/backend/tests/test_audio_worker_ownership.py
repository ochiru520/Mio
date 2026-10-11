from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import system_audio_service as audio
from app.routes import companion as routes
from app import subservice_health, call_session_service


class AudioWorkerOwnershipTests(unittest.TestCase):
    def test_call_worker_recovery_preserves_call_engine_and_capture_scope(self):
        health = {'services': {'asr_system_audio': {'state': 'failed', 'enabled': True}}}
        subservice_health.reset_recovery_state_for_tests()
        with patch.object(call_session_service.manager, 'status', return_value={'active': True}), \
            patch.object(routes, '_ensure_call_asr_ready') as ensure, \
            patch.object(audio, 'start') as generic:
            result = subservice_health.recover_failed(health)
        ensure.assert_called_once()
        generic.assert_not_called()
        self.assertIn('asr_system_audio', result['recovered'])
        subservice_health.reset_recovery_state_for_tests()

    def test_call_asr_keeps_file_recognition_when_system_audio_is_disabled(self):
        with patch.object(routes.companion_service, 'load_config', return_value={
            'screen_audio_enabled': False, 'pet_call_asr_engine': 'sensevoice',
        }), patch.object(routes.companion_service.window_observer, 'status', return_value={'running': True}), \
            patch.object(audio, 'start', return_value={'ready': True}) as start:
            self.assertTrue(routes._ensure_call_asr_ready()['ready'])
        self.assertTrue(start.call_args.kwargs['transcription_only'])
        self.assertEqual(start.call_args.args[0]['asr_engine'], 'sensevoice')

    def test_call_without_observer_does_not_open_loopback(self):
        with patch.object(routes.companion_service, 'load_config', return_value={'screen_audio_enabled': True}), \
            patch.object(routes.companion_service.window_observer, 'status', return_value={'running': False}), \
            patch.object(audio, 'start', return_value={'ready': True}) as start:
            routes._ensure_call_asr_ready()
        self.assertTrue(start.call_args.kwargs['transcription_only'])

    def test_file_transcription_can_start_with_system_capture_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python, worker, cache = root / 'python.exe', root / 'worker.py', root / 'cache'
            python.touch(); worker.touch()
            process = SimpleNamespace(poll=lambda: None, stdout=[], stdin=None)
            launch = {'engine': 'whisper', 'model': 'local-model', 'model_label': 'base', 'vad_model': ''}
            with patch.multiple(audio, _process=None, _ready=False, _phase='stopped',
                _model='', _transcription_only=False, _worker_engine='', _desired_running=False,
                _capture_stats={}, _capture_config={}, _reader_thread=None, _last_error='',
                _requested_model='', _model_fallback_reason='', _speaker=''), \
                patch.object(audio, '_runtime_paths', return_value=(python, worker, cache)), \
                patch.object(audio, '_worker_launch', return_value=launch), \
                patch.object(audio.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(audio.threading, 'Thread'):
                current = audio.start({'screen_audio_enabled': False}, transcription_only=True)
                self.assertTrue(current['running'])
                self.assertFalse(current['capture_loopback'])
                self.assertIn('--stdin-only', popen.call_args.args[0])

    def test_replaced_reader_cannot_publish_readiness_or_transcripts(self):
        replacement = SimpleNamespace(poll=lambda: None)

        class OldOutput:
            def __iter__(self):
                audio._process = replacement
                yield json.dumps({'type': 'ready', 'model': 'old-model'})
                yield json.dumps({'type': 'transcript', 'text': '旧进程迟到结果'})
                yield json.dumps({'type': 'error', 'message': '旧进程异常'})

        old = SimpleNamespace(stdout=OldOutput())
        with patch.multiple(audio, _process=old, _ready=False, _phase='starting', _model='new-model', _last_error=''):
            before = len(audio._transcripts)
            audio._reader()
            self.assertIs(audio._process, replacement)
            self.assertFalse(audio._ready)
            self.assertEqual(audio._phase, 'starting')
            self.assertEqual(audio._model, 'new-model')
            self.assertEqual(audio._last_error, '')
            self.assertEqual(len(audio._transcripts), before)

    def test_transcription_mode_does_not_claim_system_hearing(self):
        with patch.object(audio, 'status', return_value={
            'running': True, 'ready': True, 'capture_loopback': False, 'last_error': '',
        }), patch.object(audio, 'recent_transcript', return_value='不应暴露的回环结果'):
            context = audio.chat_context()
        self.assertIn('没有监听电脑扬声器', context)
        self.assertNotIn('不应暴露的回环结果', context)


if __name__ == '__main__':
    unittest.main()
