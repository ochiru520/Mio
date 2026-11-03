"""Anif setup lifecycle in a synthetic runtime, without network/CUDA/private data."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import ctypes
import json
import os
from pathlib import Path
import tempfile
import sys
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

os.environ.setdefault('MIO_DISABLE_DOTENV', '1')
os.environ.setdefault('MIO_RUNTIME_ROOT', tempfile.mkdtemp(prefix='mio-anif-tests-'))

from app import anif_setup_service as service
from app import companion_service, local_voice_engines
from app.probe_process import ProbeProcess
from app.routes import model_management as routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


class AnifSetupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='mio-anif-setup-')))
        self.root = self.directory / 'voice' / 'aniflive-managed'
        self.scripts = self.directory / 'scripts'
        self.scripts.mkdir()
        (self.scripts / 'deps').mkdir()
        (self.scripts / 'deps/install-aniflive-runtime.ps1').write_text('# test', encoding='utf-8')
        (self.scripts / 'aniflive_setup_worker.py').write_text('# test', encoding='utf-8')
        self.manifest = self.directory / 'program/local-voice-engines.json'
        self.settings = SimpleNamespace(voice_training_dir=self.directory / 'voice', agent_control_scripts_dir=self.scripts)
        self.stack.enter_context(patch.object(service, 'settings', self.settings))
        self.stack.enter_context(patch.object(service, '_CHILD', None))
        self.stack.enter_context(patch.object(service, '_CHILD_JOB', None))
        self.stack.enter_context(patch.object(service, '_CLOSED', False))
        self.stack.enter_context(patch.object(service, '_LEASE', None))
        self.stack.enter_context(patch.object(service, '_TRANSACTION_LEASE', None))
        self.stack.enter_context(patch.object(service, '_RUN_GUARD', None))
        self.addCleanup(service.shutdown)
        self.stack.enter_context(patch.object(local_voice_engines, 'manifest_path', return_value=self.manifest))
        self.stack.enter_context(patch.object(service.resources, '_idle'))
        self.stack.enter_context(patch.object(service, '_checks', return_value=([], {'gpus': []})))
        self.stack.enter_context(patch.object(service.shutil, 'which', return_value='powershell.exe'))
        self.fake_process = Mock()
        self.fake_process.poll.return_value = None
        self.launch = self.stack.enter_context(patch.object(service, 'ProbeProcess', return_value=self.fake_process))
        self.stop = self.stack.enter_context(patch.object(local_voice_engines.manager, 'stop'))
        self.cancel_playback = self.stack.enter_context(patch.object(companion_service, 'cancel_speech_playback'))
        self.config = dict(voice_engine='gpt_sovits', local_voice_runtime='mio_v2',
                           aniflive_model='old-model', aniflive_voice_profile='old-voice',
                           aniflive_url='http://127.0.0.1:9882', voice_startup_enabled=False,
                           default_voice_profile_id='a', voice_profiles={'a': self.profile()})
        self.stack.enter_context(patch.object(companion_service, 'load_config', side_effect=lambda: copy.deepcopy(self.config)))
        self.save = self.stack.enter_context(patch.object(companion_service, 'save_config', side_effect=self.save_config))
        service._atomic(self.manifest, {'schema': 1, 'engines': {'mio_v2': {'marker': 'native'}}})

    def file(self, relative, data=b'fixture'):
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def profile(self):
        return dict(name='声音 A', gpt_sovits_gpt_weights=str(self.file('external/voice.ckpt')),
                    gpt_sovits_sovits_weights=str(self.file('external/voice.pth')),
                    gpt_sovits_ref_audio=str(self.file('external/reference.wav')),
                    gpt_sovits_prompt_text='こんにちは。', gpt_sovits_prompt_language='ja')

    def save_config(self, values):
        self.config.update(copy.deepcopy(values))
        return copy.deepcopy(self.config)

    def start(self):
        result = service.start('a')
        self.job = self.root / 'jobs' / result['job_id']
        return result

    def ready(self):
        self.start()
        for language in ('zh', 'ja'):
            with wave.open(str(self.job / f'preview-{language}.wav'), 'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(32000)
                writer.writeframes(b'\0\0' * 3200)
        for relative in ('environment/Scripts/python.exe', 'runtime-scripts/aniflive_v2_server.py',
                         'source/minimal_inference/a.py', 'assets/bert/config.json', 'engines/stage.engine',
                         'voices/voice.ckpt', 'voices/voice.pth', 'voices/reference.wav'):
            self.file('voice/aniflive-managed/' + relative)
        self.registration = dict(python=str(self.root / 'environment/Scripts/python.exe'),
            script=str(self.root / 'runtime-scripts/aniflive_v2_server.py'),
            cwd=str(self.root / 'source/minimal_inference'), url='http://127.0.0.1:9882',
            env={'NLTK_DATA': str(self.root / 'assets/nltk_data')},
            args=['--source', str(self.root / 'source'), '--engines', str(self.root / 'engines'),
                  '--bert', str(self.root / 'assets/bert'), '--gpt', str(self.root / 'voices/voice.ckpt'),
                  '--sovits', str(self.root / 'voices/voice.pth'), '--reference', str(self.root / 'voices/reference.wav'),
                  '--voice-profile', 'a', '--reference-text', 'こんにちは。', '--reference-language', 'ja'])
        service._atomic(self.job / 'status.json', dict(stage='ready', ready=True, percent=100,
                                                     message='中日语音已验收。', registration=self.registration))
        job = service._read(self.job / 'job.json')
        profile = job['profile']
        (self.root / 'voices/voice.ckpt').write_bytes(Path(profile['gpt']).read_bytes())
        (self.root / 'voices/voice.pth').write_bytes(Path(profile['sovits']).read_bytes())
        (self.root / 'voices/reference.wav').write_bytes(Path(profile['reference']).read_bytes())
        engine_root = self.root / 'engines'
        config = dict(model=dict(version='v2', semantic_frame_rate='25hz'), data=dict(sampling_rate=32000, hop_length=640))
        service._atomic(engine_root / 'config.json', config)
        stages = {}
        for name in ('ssl', 'bert', 'vq_encoder', 'gpt_encoder', 'gpt_step', 'spectrogram', 'sovits', 'sovits_stream'):
            path = self.file('voice/aniflive-managed/engines/' + name + '.engine')
            stages[name] = dict(path=path.name, sha256=service.resources._digest(path))
        service._atomic(engine_root / 'v2-manifest.json', dict(kind='mio-aniflive-v2-experimental', model_family='GPT-SoVITS V2',
            source_weights={name: dict(path=str(self.root / ('voices/voice.ckpt' if name == 'gpt' else 'voices/voice.pth')),
                sha256=profile['weight_sha256'][name]) for name in ('gpt', 'sovits')}, stages=stages,
            config_sha256=service.resources._digest(engine_root / 'config.json')))
        service._atomic(self.job / 'acceptance.json', dict(schema=1, job_id=self.job.name, ok=True,
            weight_sha256=profile['weight_sha256'], audio={language: dict(sha256=service.resources._digest(self.job / f'preview-{language}.wav'))
                                                       for language in ('zh', 'ja')}))
        self.fake_process.poll.return_value = 0

    def test_status_does_not_create_directory_or_start_worker(self):
        result = service.status()
        self.assertFalse(self.root.exists())
        self.launch.assert_not_called()
        self.assertEqual(result['voices'][0]['id'], 'a')
        self.assertTrue(result['voices'][0]['compatible'])
        self.assertFalse(result['can_activate'])
        self.assertIsNone(result['installed'])

    def test_incomplete_profile_cannot_start(self):
        self.config['voice_profiles']['a'].pop('gpt_sovits_gpt_weights')
        self.assertFalse(service.status()['can_start'])
        with self.assertRaisesRegex(ValueError, '配套 V2'):
            service.start('a')
        self.launch.assert_not_called()

    def test_unsupported_reference_language_is_rejected(self):
        self.config['voice_profiles']['a']['gpt_sovits_prompt_language'] = 'auto'
        with self.assertRaisesRegex(ValueError, '语言'):
            service.start('a')

    def test_missing_bootstrap_does_not_write_install_root(self):
        (self.scripts / 'deps/install-aniflive-runtime.ps1').unlink()
        with self.assertRaisesRegex(ValueError, '缺少'):
            service.start('a')
        self.assertFalse(self.root.exists())

    def test_preflight_failure_does_not_launch(self):
        with patch.object(service, '_checks', return_value=([dict(ok=False, message='显存不足')], {})):
            with self.assertRaisesRegex(ValueError, '显存不足'):
                service.start('a')
        self.launch.assert_not_called()

    def test_unowned_directory_is_not_overwritten_or_removable(self):
        protected = self.file('voice/aniflive-managed/foreign.txt')
        for operation in (lambda: service.start('a'), service.uninstall_preview):
            with self.assertRaisesRegex(ValueError, '未登记文件'):
                operation()
        self.assertTrue(protected.is_file())

    def test_job_uses_powershell_argv_hashes_and_no_selection_changes(self):
        before = copy.deepcopy(self.config)
        result = self.start()
        command = self.launch.call_args.args[0]
        self.assertEqual(command[0], 'powershell.exe')
        self.assertIn('-JobFile', command)
        job = service._read(self.job / 'job.json')
        self.assertEqual(len(job['profile']['weight_sha256']['gpt']), 64)
        self.assertEqual(job['profile']['reference_sha256'], service.resources._digest(Path(job['profile']['reference'])))
        self.assertTrue(result['job']['running'])
        self.assertTrue(service.busy())
        self.assertEqual(self.config, before)
        self.assertIsNone(service.status()['installed'])

    def test_rejects_parallel_jobs(self):
        self.start()
        with self.assertRaisesRegex(ValueError, '正在安装'):
            service.start('a')
        self.assertEqual(self.launch.call_count, 1)

    def test_busy_does_not_wait_on_setup_lock_while_lifecycle_is_owned(self):
        entered = threading.Event()
        release = threading.Event()
        def setup_transaction():
            with service._LOCK:
                entered.set()
                release.wait(3)
        worker = threading.Thread(target=setup_transaction, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            with local_voice_engines.manager.lifecycle_lock:
                before = time.monotonic()
                self.assertTrue(service.busy())
                self.assertLess(time.monotonic() - before, .2)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(service.busy())

    def test_start_waits_for_model_storage_transaction_before_selecting_root(self):
        entered = threading.Event()
        completed = threading.Event()
        failures = []
        def launch_after_storage():
            entered.set()
            try:
                service.start('a')
            except BaseException as error:
                failures.append(error)
            finally:
                completed.set()
        with service.resources._LOCK:
            worker = threading.Thread(target=launch_after_storage, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(completed.wait(.1))
            self.launch.assert_not_called()
            self.settings.voice_training_dir = self.directory / 'moved-voice'
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(failures)
        self.assertFalse(self.root.exists())
        self.assertTrue((self.directory / 'moved-voice/aniflive-managed/latest-job.json').is_file())
        service.cancel()

    @unittest.skipUnless(os.name == 'nt', 'Windows cross-process file lease')
    def test_other_backend_and_surviving_bootstrap_lease_block_all_mutations(self):
        self.ready()
        service.status()  # Reap the completed owned worker and release its lease.
        child_script = self.scripts / 'synthetic-lease.py'
        child_script.write_text(
            'import msvcrt,sys,time\n'
            'from pathlib import Path\n'
            'path=Path(sys.argv[1]);marker=Path(sys.argv[2])\n'
            'with path.open("a+b") as stream:\n'
            ' stream.seek(0)\n'
            ' if not stream.read(1): stream.write(b"0");stream.flush()\n'
            ' stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)\n'
            ' marker.write_text("ready")\n'
            ' time.sleep(120)\n', encoding='utf-8')
        for kind in ('operation', 'run'):
            with self.subTest(kind=kind):
                marker = self.directory / f'{kind}-ready.txt'
                child = ProbeProcess([str(Path(sys.executable).resolve()), str(child_script),
                    str(self.root.parent / f'.mio-anif-{kind}.lock'), str(marker)], cwd=str(self.scripts))
                try:
                    deadline = time.monotonic() + 5
                    while not marker.is_file() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue(marker.is_file())
                    result = service.status()
                    self.assertFalse(result['can_start'])
                    self.assertFalse(result['can_activate'])
                    self.assertFalse(result['can_uninstall'])
                    for operation in (lambda: service.start('a'), service.activate, service.cancel, service.uninstall_preview):
                        with self.assertRaises(ValueError):
                            operation()
                    state = service._read(self.job / 'status.json')
                    service._atomic(self.job / 'status.json', {**state, 'stage': 'building', 'ready': False})
                    foreign_job = service.status()['job']
                    self.assertTrue(foreign_job['running'])
                    self.assertFalse(foreign_job['can_cancel'])
                    service._atomic(self.job / 'status.json', state)
                finally:
                    child.close()
        self.assertTrue(service.status()['can_activate'])

    def test_cancel_closes_owned_tree_and_preserves_external_files(self):
        self.start()
        result = service.cancel()
        self.fake_process.close.assert_called_once()
        self.assertTrue((self.job / 'cancel.request').is_file())
        self.assertEqual(service.status()['job']['stage'], 'cancelled')
        self.assertFalse(service.busy())
        self.assertTrue((self.directory / 'external/voice.ckpt').is_file())
        self.assertTrue(result['ok'])

    def test_process_crash_becomes_failed_never_ready(self):
        self.start()
        self.fake_process.poll.return_value = 1
        state = service.status()
        self.assertEqual(state['job']['stage'], 'failed')
        self.assertFalse(state['can_activate'])

    def test_status_preserves_only_valid_download_byte_progress(self):
        self.start()
        state_path = self.job / 'status.json'
        state = service._read(state_path)
        service._atomic(state_path, {**state, 'downloaded_bytes': 1024**3, 'total_bytes': 4 * 1024**3})
        result = service.status()['job']
        self.assertEqual(result['downloaded_bytes'], 1024**3)
        self.assertEqual(result['total_bytes'], 4 * 1024**3)
        for invalid in (-1, '123', True, None, 1.5):
            with self.subTest(invalid=invalid):
                service._atomic(state_path, {**state, 'downloaded_bytes': invalid, 'total_bytes': invalid})
                result = service.status()['job']
                self.assertNotIn('downloaded_bytes', result)
                self.assertNotIn('total_bytes', result)

    def test_restart_does_not_claim_stale_job_is_running(self):
        self.start()
        service._CHILD = None
        service._CHILD_JOB = None
        service._release_lease()
        result = service.status()['job']
        self.assertEqual(result['stage'], 'interrupted')
        self.assertFalse(result['running'])

    def test_success_requires_explicit_activation(self):
        self.ready()
        result = service.status()
        self.assertTrue(result['can_activate'])
        self.assertIsNone(result['installed'])
        self.assertEqual(self.config['voice_engine'], 'gpt_sovits')
        self.assertEqual(set(result['job']['preview_urls']), {'zh', 'ja'})
        self.assertTrue(service.audio_path('ja', self.job.name).is_file())

    def test_activation_merges_registration_preserves_native_and_preferences(self):
        self.ready()
        service.activate()
        manifest = service._read(self.manifest)
        self.assertEqual(manifest['engines']['mio_v2'], {'marker': 'native'})
        self.assertEqual(self.config['voice_engine'], 'aniflive')
        self.assertEqual(self.config['aniflive_voice_profile'], 'a')
        self.assertEqual(self.config['local_voice_runtime'], 'mio_v2')
        self.assertFalse(self.config['voice_startup_enabled'])
        self.assertEqual(self.config['default_voice_profile_id'], 'a')
        self.assertFalse(manifest['engines']['aniflive']['numerical_verified'])
        self.assertTrue(service.status()['installed']['registered'])
        self.assertTrue(service.status()['selected'])

    def test_activation_cannot_register_external_paths(self):
        self.ready()
        state = service._read(self.job / 'status.json')
        state['registration']['python'] = str(self.file('external/python.exe'))
        service._atomic(self.job / 'status.json', state)
        with self.assertRaisesRegex(ValueError, '超出受管范围'):
            service.activate()
        self.assertEqual(self.config['voice_engine'], 'gpt_sovits')

    def test_activation_rejects_missing_or_wrong_acceptance(self):
        self.ready()
        acceptance = service._read(self.job / 'acceptance.json')
        for changes in ({'job_id': '0' * 32}, {'weight_sha256': {'gpt': '0' * 64, 'sovits': '0' * 64}}, {'ok': False}):
            with self.subTest(changes=changes):
                service._atomic(self.job / 'acceptance.json', {**acceptance, **changes})
                with self.assertRaisesRegex(ValueError, '验收记录'):
                    service.activate()
        (self.job / 'acceptance.json').unlink()
        with self.assertRaisesRegex(ValueError, '验收记录'):
            service.activate()
        self.assertEqual(self.config['voice_engine'], 'gpt_sovits')

    def test_activation_rejects_modified_audio_even_with_valid_header(self):
        self.ready()
        with wave.open(str(self.job / 'preview-ja.wav'), 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(32000)
            writer.writeframes(b'\1\1' * 3200)
        with self.assertRaisesRegex(ValueError, '试听音频与验收记录不符'):
            service.activate()

    def test_activation_rejects_modified_stage_and_missing_stage(self):
        self.ready()
        (self.root / 'engines/sovits.engine').write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, '引擎文件已变化'):
            service.activate()
        manifest_path = self.root / 'engines/v2-manifest.json'
        payload = service._read(manifest_path)
        payload['stages'].pop('sovits')
        service._atomic(manifest_path, payload)
        with self.assertRaisesRegex(ValueError, '八个 V2 阶段'):
            service.activate()

    def test_activation_rejects_reference_or_config_drift(self):
        self.ready()
        reference = self.root / 'voices/reference.wav'
        original = reference.read_bytes()
        reference.write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, '参考音频已变化'):
            service.activate()
        reference.write_bytes(original)
        (self.root / 'engines/config.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, '引擎文件已变化'):
            service.activate()

    def test_activation_rejects_wrong_runtime_profile(self):
        self.ready()
        state = service._read(self.job / 'status.json')
        args = state['registration']['args']
        args[args.index('--voice-profile') + 1] = 'other'
        service._atomic(self.job / 'status.json', state)
        with self.assertRaisesRegex(ValueError, '参考配置不符'):
            service.activate()

    def test_activation_rolls_back_manifest_if_saving_config_fails(self):
        self.ready()
        before = service._read(self.manifest)
        self.save.side_effect = [RuntimeError('write failure'), copy.deepcopy(self.config)]
        with self.assertRaisesRegex(RuntimeError, 'write failure'):
            service.activate()
        self.assertEqual(service._read(self.manifest), before)
        self.assertFalse((self.root / 'installed.json').exists())

    def test_audio_rejects_traversal_and_invalid_wav(self):
        self.ready()
        for language, identifier in [('en', self.job.name), ('ja', '../outside')]:
            with self.assertRaises(ValueError):
                service.audio_path(language, identifier)
        (self.job / 'preview-ja.wav').write_bytes(b'not a wave' * 10)
        with self.assertRaisesRegex(ValueError, '格式无效'):
            service.activate()

    def test_uninstall_is_previewed_and_preserves_original_voice(self):
        self.ready()
        service.activate()
        preview = service.uninstall_preview()
        self.assertGreater(preview['size_bytes'], 0)
        with self.assertRaisesRegex(ValueError, '已变化'):
            service.uninstall('0' * 64)
        result = service.uninstall(preview['token'])
        self.assertTrue(result['ok'])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.config['voice_engine'], 'gpt_sovits')
        self.assertEqual(service._read(self.manifest)['engines'], {'mio_v2': {'marker': 'native'}})
        self.assertTrue((self.directory / 'external/voice.ckpt').is_file())
        self.assertTrue((self.directory / 'external/reference.wav').is_file())

    def test_uninstall_restores_previous_external_registration(self):
        original = {'python': 'external-python', 'marker': 'existing'}
        service._atomic(self.manifest, {'schema': 1, 'engines': {'aniflive': original}})
        self.ready()
        service.activate()
        service.uninstall(service.uninstall_preview()['token'])
        self.assertEqual(service._read(self.manifest)['engines']['aniflive'], original)

    def test_partial_uninstall_failure_keeps_fallback_and_supports_retry(self):
        self.ready()
        service.activate()
        plan = service.uninstall_preview()
        with patch.object(service.shutil, 'rmtree', side_effect=PermissionError('locked')):
            result = service.uninstall(plan['token'])
        self.assertFalse(result['ok'])
        self.assertTrue(self.root.is_dir())
        self.assertEqual(self.config['voice_engine'], 'gpt_sovits')
        self.assertNotIn('aniflive', service._read(self.manifest)['engines'])
        self.assertTrue(service.uninstall(service.uninstall_preview()['token'])['ok'])
        self.assertFalse(self.root.exists())

    def test_uninstall_config_write_failure_restores_directory_and_manifest(self):
        self.ready()
        service.activate()
        original = service._read(self.manifest)
        plan = service.uninstall_preview()
        self.save.side_effect = [PermissionError('config locked'), copy.deepcopy(self.config)]
        with self.assertRaisesRegex(PermissionError, 'config locked'):
            service.uninstall(plan['token'])
        self.assertTrue(self.root.is_dir())
        self.assertEqual(service._read(self.manifest), original)

    def test_uninstall_preserves_later_external_registration_and_selection(self):
        self.ready()
        service.activate()
        foreign = {'python': 'new-external-python', 'marker': 'external'}
        payload = service._read(self.manifest)
        payload['engines']['aniflive'] = foreign
        service._atomic(self.manifest, payload)
        self.config['aniflive_voice_profile'] = 'external'
        service.uninstall(service.uninstall_preview()['token'])
        self.assertEqual(service._read(self.manifest)['engines']['aniflive'], foreign)
        self.assertEqual(self.config['aniflive_voice_profile'], 'external')

    def test_failed_unactivated_install_can_be_uninstalled(self):
        self.start()
        service.cancel()
        service.uninstall(service.uninstall_preview()['token'])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.config['voice_engine'], 'gpt_sovits')

    def test_preview_token_rejects_changes_since_preview(self):
        self.ready()
        plan = service.uninstall_preview()
        self.file('voice/aniflive-managed/newfile.txt', b'changed')
        with self.assertRaisesRegex(ValueError, '已变化'):
            service.uninstall(plan['token'])
        self.assertTrue(self.root.exists())

    def test_malformed_manifest_is_not_replaced(self):
        self.manifest.write_text('{broken', encoding='utf-8')
        self.ready()
        with self.assertRaisesRegex(ValueError, '注册清单无效'):
            service.activate()
        self.assertEqual(self.manifest.read_text(encoding='utf-8'), '{broken')

    def client(self):
        app = FastAPI()
        app.include_router(routes.router)
        return TestClient(app)

    def test_routes_status_start_cancel_and_request_validation(self):
        with self.client() as client:
            response = client.get('/api/model-management/anif/setup')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['voices'][0]['id'], 'a')
            self.assertEqual(client.post('/api/model-management/anif/setup', json={}).status_code, 422)
            response = client.post('/api/model-management/anif/setup', json={'voice_id': 'a'})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['job']['running'])
            self.assertEqual(client.post('/api/model-management/anif/setup', json={'voice_id': 'a'}).status_code, 409)
            self.assertEqual(client.post('/api/model-management/anif/setup/cancel').status_code, 200)

    def test_routes_serve_only_ready_job_audio_and_activate_then_uninstall(self):
        self.ready()
        with self.client() as client:
            response = client.get('/api/model-management/anif/setup/audio/ja', params={'job_id': self.job.name})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['content-type'], 'audio/wav')
            self.assertEqual(response.content[:4], b'RIFF')
            self.assertEqual(client.get('/api/model-management/anif/setup/audio/en', params={'job_id': self.job.name}).status_code, 409)
            self.assertEqual(client.post('/api/model-management/anif/setup/activate').status_code, 200)
            plan = client.post('/api/model-management/anif/setup/uninstall-preview').json()
            self.assertEqual(client.post('/api/model-management/anif/setup/uninstall', json={'token': plan['token']}).status_code, 200)
            self.assertFalse(self.root.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree containment')
    def test_real_powershell_cancel_stops_python_descendants(self):
        child_script = self.scripts / 'synthetic-child.py'
        child_script.write_text(
            'import json,os,subprocess,sys,time\n'
            'from pathlib import Path\n'
            'job=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))\n'
            'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(120)"])\n'
            'Path(job["job_dir"]).joinpath("pids.json").write_text(json.dumps([os.getpid(),child.pid]))\n'
            'time.sleep(120)\n', encoding='utf-8')
        literal_python = str(Path(sys.executable).resolve()).replace("'", "''")
        literal_script = str(child_script).replace("'", "''")
        (self.scripts / 'deps/install-aniflive-runtime.ps1').write_text(
            "param([string]$JobFile)\n& '" + literal_python + "' '" + literal_script + "' $JobFile\n",
            encoding='utf-8-sig')
        with patch.object(service, 'ProbeProcess', ProbeProcess), \
             patch.object(service.shutil, 'which', return_value='C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'):
            self.start()
            self.addCleanup(service.cancel)
            deadline = time.monotonic() + 12
            pid_file = self.job / 'pids.json'
            while not pid_file.is_file() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(pid_file.is_file(), 'synthetic descendants did not start')
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handles = [kernel.OpenProcess(0x00100000, False, pid) for pid in json.loads(pid_file.read_text())]
            self.assertTrue(all(handles))
            try:
                service.cancel()
                for handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0,
                                     'a Python descendant survived cancellation')
            finally:
                for handle in handles:
                    if handle:
                        kernel.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
