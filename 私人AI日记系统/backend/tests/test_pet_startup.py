import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import BackgroundTasks, HTTPException, WebSocketDisconnect
from app import companion_service as pets, pet_event_service
from app.companion.pet_startup import startup_status
from app.routes import companion as routes


class PetStartupTests(unittest.TestCase):
    def snapshot(self, **kwargs):
        args = dict(alive=True, runtime_kind='electron_live2d', renderers=[], launch_id='new',
                    show_request_id='', elapsed=1)
        return startup_status(**(args | kwargs))

    def test_process_socket_and_old_ready_never_prove_model_visible(self):
        for renderer in ([], [{'runtime': 'browser', 'model_ready': True, 'visible': True}],
                         [{'runtime': 'electron', 'launch_id': 'old', 'model_ready': True, 'visible': True}],
                         [{'runtime': 'electron', 'launch_id': 'new'}],
                         [{'runtime': 'electron', 'launch_id': 'new', 'model_ready': True, 'visible': False}]):
            with self.subTest(renderer=renderer):
                self.assertEqual(self.snapshot(renderers=renderer)['state'], 'starting')

    def test_current_model_visible_and_matching_show_receipt_required(self):
        renderer = dict(runtime='electron', launch_id='new', model_ready=True, visible=True, show_request_id='r2')
        self.assertTrue(self.snapshot(renderers=[renderer])['ready'])
        self.assertFalse(self.snapshot(renderers=[renderer], show_request_id='r1')['ready'])
        self.assertTrue(self.snapshot(renderers=[renderer], show_request_id='r2')['ready'])
        self.assertEqual(self.snapshot(renderers=[renderer], last_error='process exited')['state'], 'failed')

    def test_timeout_model_failure_and_fallback_are_distinct(self):
        self.assertEqual(self.snapshot(elapsed=16)['state'], 'failed')
        self.assertIn('进程存在', self.snapshot(elapsed=16)['message'])
        self.assertEqual(self.snapshot(renderers=[dict(runtime='electron', launch_id='new', startup_error='WebGL失败')])['message'], 'WebGL失败')
        self.assertEqual(self.snapshot(runtime_kind='python_fallback')['state'], 'unverified')
        self.assertEqual(self.snapshot(alive=False)['state'], 'stopped')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.names = ('_pet_process', '_pet_runtime_kind', '_pet_launch_id', '_pet_show_request_id', '_pet_started_at', '_pet_last_error')
        self.original = {name: getattr(pets, name) for name in self.names}
        pets._pet_process = None
        pets._pet_runtime_kind = pets._pet_launch_id = pets._pet_show_request_id = pets._pet_last_error = ''
        pets._pet_started_at = 0

    def tearDown(self):
        for key, value in self.original.items():
            setattr(pets, key, value)
        self.temp.cleanup()

    def test_start_waits_for_actual_readiness_and_captures_process_diagnostics(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        answers = iter([{'startup': {'state': 'starting'}}, {'startup': {'state': 'ready'}}])
        with patch.dict(os.environ, {'MIO_DESKTOP_STATE_DIR': self.temp.name}), \
             patch.object(pets, 'load_config', return_value={'pet_renderer': 'live2d'}), \
             patch.object(pets, '_external_pet_renderer_connected', return_value=False), \
             patch.object(pets, '_pet_command', return_value=(['synthetic.exe'], 'electron_live2d')), \
             patch.object(pets.subprocess, 'Popen', return_value=process) as spawn, \
             patch.object(pets, 'pet_status', side_effect=lambda: next(answers)), \
             patch.object(pets.time, 'sleep') as delay:
            result = pets.start_pet()
        self.assertEqual(result['startup']['state'], 'ready')
        delay.assert_called_once_with(.1)
        env = spawn.call_args.kwargs['env']
        self.assertEqual(env['MIO_PET_LAUNCH_ID'], pets._pet_launch_id)
        self.assertTrue(env['MIO_PET_LAUNCH_ID'])
        self.assertEqual(spawn.call_args.kwargs['stderr'], pets.subprocess.STDOUT)
        self.assertTrue((Path(self.temp.name) / 'Live2D桌宠/startup-process.log').is_file())

    def test_existing_renderer_receives_show_request_without_duplicate_spawn(self):
        ready = {'startup': {'state': 'ready'}}
        with patch.object(pets, 'load_config', return_value={'pet_renderer': 'live2d'}), \
             patch.object(pets, '_external_pet_renderer_connected', return_value=True), \
             patch.object(pets, 'pet_status', return_value=ready), \
             patch.object(pet_event_service, 'publish') as publish, \
             patch.object(pets.subprocess, 'Popen') as spawn:
            self.assertEqual(pets.start_pet(), ready)
        publish.assert_called_once_with('show_pet', {'request_id': pets._pet_show_request_id})
        self.assertTrue(pets._pet_show_request_id)
        spawn.assert_not_called()

    def test_model_failure_stops_start_route_before_observation_warmup_or_wake(self):
        with patch.object(pets, 'start_pet', side_effect=OSError('模型加载失败')), \
             patch.object(pets, 'pet_running', return_value=False), \
             patch.object(routes, '_start_pet_with_screen_observation') as observe:
            tasks = BackgroundTasks()
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(routes.companion_start(tasks))
        self.assertIn('模型加载失败', caught.exception.detail)
        observe.assert_not_called()
        self.assertEqual(tasks.tasks, [])

    def test_process_exit_diagnostic_survives_repeated_status_poll(self):
        process = Mock(returncode=9)
        process.poll.return_value = 9
        pets._pet_process = process
        with patch.object(pets, 'load_config', return_value={}), \
             patch.object(pets, 'pet_sprite_manifest', return_value=dict(states=[], ready=False, expected_count=6, version='')), \
             patch.object(pets, 'default_avatar_path', return_value=None), \
             patch.object(pets, '_electron_pet_command', return_value=None), \
             patch.object(pets, 'available_live2d_models', return_value=[]), \
             patch.object(pet_event_service, 'status', return_value={}):
            first, second = pets.pet_status(), pets.pet_status()
        self.assertEqual(first['startup'], second['startup'])
        self.assertIn('退出码 9', second['startup']['message'])

    def test_hub_tracks_only_active_desktop_startup_and_ignores_speech_errors(self):
        class Socket:
            def __init__(self):
                self.messages = iter([
                    {'type': 'renderer_loading', 'payload': {'runtime': 'electron', 'launch_id': 'new'}},
                    {'type': 'renderer_error', 'payload': {'runtime': 'electron', 'launch_id': 'new', 'stage': 'startup', 'message': '模型缺失'}},
                    {'type': 'renderer_ready', 'payload': {'runtime': 'electron', 'launch_id': 'new', 'model_ready': True, 'visible': True}},
                    {'type': 'renderer_error', 'payload': {'message': '语音失败'}},
                ])
                self.snapshots = []
            async def accept(self): pass
            async def send_json(self, payload): pass
            async def receive_json(self):
                self.snapshots.append(hub.status())
                try: return next(self.messages)
                except StopIteration: raise WebSocketDisconnect()
        hub = pet_event_service.PetEventHub()
        socket = Socket()
        with patch('app.db.get_latest_message_id', return_value=0), \
             patch.object(pets, 'load_config', return_value={}), \
             patch('app.screen_observation_service.status', return_value={}):
            asyncio.run(hub.serve(socket))
        snapshots = socket.snapshots
        self.assertFalse(snapshots[1]['desktop_renderers'][0]['model_ready'])
        self.assertEqual(snapshots[2]['desktop_renderers'][0]['startup_error'], '模型缺失')
        self.assertTrue(snapshots[4]['desktop_renderers'][0]['model_ready'])
        self.assertNotIn('startup_error', snapshots[4]['desktop_renderers'][0])
        self.assertEqual(hub.status()['desktop_renderers'], [])


if __name__ == '__main__':
    unittest.main()
