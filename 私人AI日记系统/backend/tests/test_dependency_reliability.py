from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('MIO_DISABLE_DOTENV', '1')
os.environ.setdefault('MIO_RUNTIME_ROOT', tempfile.mkdtemp(prefix='mio-dependency-check-'))
from app import dependency_installer as deps, dependency_probe as probe
from app import environment_check_service as env


class ReliabilityTests(unittest.TestCase):
    def test_invalid_genie_files_are_never_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('.genie-env/Scripts/python.exe', 'GenieData/chinese-hubert-base/chinese-hubert-base.onnx',
                         'GenieData/G2P/ChineseG2P/opencpop-strict.txt', 'GenieData/G2P/ChineseG2P/polyphonic.pickle'):
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'invalid')
            with patch.object(deps, 'settings', Mock(voice_training_dir=root)):
                self.assertEqual(deps._detect_status({'id': 'genie_runtime'})['status'], 'unverified')
                result = deps.verify_dependency('genie_runtime')
                self.assertFalse(result['ok'])
                self.assertEqual(deps._detect_status({'id': 'genie_runtime'})['status'], 'degraded')

    def test_empty_whisper_model_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / 'cache/faster-whisper/models--faster-whisper-base/snapshots/main'
            model.mkdir(parents=True)
            for name in env.WHISPER_REQUIRED_FILES:
                (model / name).touch()
            self.assertIsNone(env._whisper_model_directory(root))

    def test_screen_capture_honors_failed_environment_check(self):
        self.assertEqual(deps._detect_status({'id':'screen_capture', 'kind':'builtin'},
            {'optional':[{'id':'screen_capture', 'status':'unsupported', 'detail':'missing mss'}]})['status'], 'missing')

    def test_offline_package_checks_size_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'model.zip'; p.write_bytes(b'package')
            source={'file_name':p.name, 'size_bytes':7, 'sha256':hashlib.sha256(b'package').hexdigest()}
            deps._validate_package(source,p)
            p.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                deps._validate_package(source,p)
            p.write_bytes(b'x')
            with self.assertRaisesRegex(ValueError,'大小'):
                deps._validate_package(source,p)

    def test_offline_source_rejects_before_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts=Path(tmp); (scripts/'install-genie-runtime.ps1').touch()
            with patch.object(deps,'_scripts_dir',return_value=scripts), patch.object(deps.subprocess,'Popen') as spawn:
                with self.assertRaisesRegex(ValueError,'离线'):
                    deps.install_dependency('genie_runtime')
                spawn.assert_not_called()

    def test_whisper_load_is_local_and_timeout_is_reported(self):
        with patch.object(probe.subprocess,'run',side_effect=subprocess.TimeoutExpired('python',60)) as run:
            result=probe.verify('whisper',Path('python.exe'),Path('model'))
        self.assertFalse(result['ok'])
        self.assertIn('local_files_only=True',run.call_args.args[0][3])
        self.assertEqual(run.call_args.kwargs['env']['HF_HUB_OFFLINE'],'1')

    def test_every_installer_has_log_and_no_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts=Path(tmp); (scripts/'install-whisper.ps1').touch()
            with patch.object(deps,'_scripts_dir',return_value=scripts), patch.object(deps,'_status_dir',return_value=scripts), \
                 patch.object(deps,'_pid_alive',return_value=False), patch.object(deps.threading,'Thread'), \
                 patch.object(deps.subprocess,'Popen') as spawn:
                spawn.return_value.pid=13579
                deps.install_dependency('whisper')
                self.assertTrue((scripts/'whisper-install.log').exists())
                self.assertIn('stdout',spawn.call_args.kwargs)
                self.assertIn('stderr',spawn.call_args.kwargs)
                self.assertEqual(spawn.call_args.kwargs['creationflags'],getattr(subprocess,'CREATE_NO_WINDOW',0))
                deps._running_installs.pop('whisper',None)
