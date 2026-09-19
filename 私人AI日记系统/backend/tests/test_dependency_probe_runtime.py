from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import dependency_probe as probe


class ProbeRuntimeTests(unittest.TestCase):
    def test_isolated_child_uses_utf8_and_explicit_genie_resources(self):
        # Exercise an actual isolated Python process, including Unicode output;
        # substitute only heavy third-party model libraries for this regression.
        with tempfile.TemporaryDirectory(prefix='mio-中文验证-') as tmp:
            root = Path(tmp)
            data = root / '音色训练/GenieData'
            model = data / 'chinese-hubert-base/chinese-hubert-base.onnx'
            model.parent.mkdir(parents=True)
            model.write_bytes(b'fixture')
            (data / 'speaker_encoder.onnx').write_bytes(b'fixture')
            modules = root / 'modules'; modules.mkdir()
            (modules / 'genie_tts.py').write_text(
                'import os, sys\nfrom pathlib import Path\n'
                'assert sys.flags.isolated and sys.flags.utf8_mode\n'
                'root = Path(sys.argv[1]).resolve().parent.parent\n'
                'assert Path(os.environ["GENIE_DATA_DIR"]) == root\n'
                'assert Path(os.environ["HUBERT_MODEL_DIR"]) == root / "chinese-hubert-base"\n'
                'assert Path(os.environ["SV_MODEL"]) == root / "speaker_encoder.onnx"\n'
                'assert os.environ["HF_HUB_OFFLINE"] == "1"\n'
                'print("⚠️ 中文输出正常")\n', encoding='utf-8')
            (modules / 'jieba.py').write_text('', encoding='utf-8')
            (modules / 'numpy.py').write_text('__version__="1.26.4"', encoding='utf-8')
            (modules / 'onnxruntime.py').write_text(
                'def InferenceSession(path, providers):\n'
                ' assert providers == ["CPUExecutionProvider"]\n', encoding='utf-8')
            real_run = subprocess.run
            captured = []

            def run(command, **kwargs):
                command = list(command)
                index = command.index('-c') + 1
                command[index] = 'import sys; sys.path.insert(0, ' + repr(str(modules)) + '); ' + command[index]
                # Simulate hostile legacy encoding settings and a different cwd.
                kwargs['env'].update(PYTHONUTF8='0', PYTHONIOENCODING='gbk', GENIE_DATA_DIR='wrong')
                kwargs['cwd'] = str(modules)
                result = real_run(command, **kwargs); captured.append(result)
                return result

            with patch.object(probe.subprocess, 'run', side_effect=run):
                outcome = probe.verify('genie_runtime', Path(sys.executable), model)
            self.assertTrue(outcome['ok'], captured[0].stderr)
            self.assertIn('⚠️ 中文输出正常', captured[0].stdout)
            self.assertEqual(outcome['verification_level'], 'model_load')

    def test_missing_resources_fail_without_interactive_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcome = probe.verify('genie_runtime', Path(sys.executable), Path(tmp) / 'GenieData/chinese-hubert-base/model.onnx')
        self.assertFalse(outcome['ok'])
        self.assertEqual(outcome['error_code'], 'missing_resources')
        self.assertNotIn('Traceback', outcome['detail'])

    def test_traceback_is_logged_but_not_returned_to_ui(self):
        result = subprocess.CompletedProcess([], 1, '', 'Traceback\nFile "C:/private/模型/file.py"\nUnicodeEncodeError: gbk')
        with patch.object(probe.subprocess, 'run', return_value=result), self.assertLogs(probe.logger, 'WARNING') as logs:
            outcome = probe.verify('whisper', Path(sys.executable), Path('model'))
        self.assertEqual(outcome['error_code'], 'encoding_error')
        self.assertNotIn('private', json.dumps(outcome))
        self.assertNotIn('Traceback', outcome['detail'])
        self.assertIn('Traceback', '\n'.join(logs.output))

    def test_timeout_is_actionable_and_contains_no_raw_exception(self):
        with patch.object(probe.subprocess, 'run', side_effect=subprocess.TimeoutExpired('private-command', 60)):
            outcome = probe.verify('whisper', Path(sys.executable), Path('model'))
        self.assertEqual(outcome['error_code'], 'timeout')
        self.assertIn('60 秒', outcome['detail'])
        self.assertNotIn('private-command', outcome['detail'])
