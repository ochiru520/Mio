"""Installer boundaries without network, CUDA, application settings or private data."""
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('anif_setup_worker_tests', SCRIPT_ROOT / 'aniflive_setup_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class Response(io.BytesIO):
    def __init__(self, content, *, status=200, headers=None):
        super().__init__(content)
        self.status = status
        self.headers = headers or {}


class InstallerFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.job_dir = self.root / 'jobs' / 'synthetic-job'
        self.job_dir.mkdir(parents=True)
        profile = dict(id='synthetic', reference_text='これはテストです。', reference_language='ja')
        for kind in ('gpt', 'sovits', 'reference'):
            path = self.root / ('original-' + kind)
            path.write_bytes(('synthetic-' + kind).encode())
            profile[kind] = str(path)
        job = dict(root=str(self.root), job_dir=str(self.job_dir), job_id='synthetic-job',
                   state_path=str(self.job_dir / 'status.json'),
                   cancel_path=str(self.job_dir / 'cancel'), scripts_dir=str(SCRIPT_ROOT),
                   profile=profile,
                   weight_sha256={kind: worker.digest(profile[kind]) for kind in ('gpt', 'sovits')},
                   reference_sha256=worker.digest(profile['reference']))
        self.job_path = self.job_dir / 'job.json'
        self.job_path.write_text(json.dumps(job), encoding='utf-8')
        self.installer = worker.Installer(self.job_path)
        self.installer.state.update(stage='downloading', message='synthetic test')
        self.installer.guard = lambda **_: None


class DownloadTests(InstallerFixture):
    def record(self, payload):
        return dict(url='https://example.invalid/fixed-file', bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest())

    def test_valid_cache_is_used_without_network(self):
        path = self.root / 'downloads' / 'cached'
        path.parent.mkdir()
        path.write_bytes(b'pinned')
        with patch.object(worker, 'urlopen') as network:
            self.assertEqual(self.installer.download(self.record(b'pinned'), path), path)
        network.assert_not_called()

    def test_range_resume_checks_position_and_final_hash(self):
        path = self.root / 'downloads' / 'resumed'
        path.parent.mkdir()
        path.with_suffix('.part').write_bytes(b'abc')
        with patch.object(worker, 'urlopen', return_value=Response(b'def', status=206,
                headers={'Content-Range': 'bytes 3-5/6'})) as network:
            self.installer.download(self.record(b'abcdef'), path)
        self.assertEqual(network.call_args.args[0].get_header('Range'), 'bytes=3-')
        self.assertEqual(path.read_bytes(), b'abcdef')
        self.assertFalse(path.with_suffix('.part').exists())

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        path = self.root / 'downloads' / 'restart'
        path.parent.mkdir()
        path.with_suffix('.part').write_bytes(b'old')
        with patch.object(worker, 'urlopen', return_value=Response(b'abcdef')):
            self.installer.download(self.record(b'abcdef'), path)
        self.assertEqual(path.read_bytes(), b'abcdef')

    def test_wrong_range_cannot_corrupt_a_partial_file(self):
        path = self.root / 'downloads' / 'wrong-range'
        path.parent.mkdir()
        path.with_suffix('.part').write_bytes(b'abc')
        with patch.object(worker, 'urlopen', return_value=Response(b'def', status=206,
                headers={'Content-Range': 'bytes 2-5/6'})):
            with self.assertRaisesRegex(RuntimeError, '续传位置'):
                self.installer.download(self.record(b'abcdef'), path)
        self.assertFalse(path.exists())
        self.assertEqual(path.with_suffix('.part').read_bytes(), b'abc')

    def test_bad_hash_retries_without_promoting_invalid_payload(self):
        path = self.root / 'downloads' / 'hash'
        with patch.object(worker, 'urlopen', side_effect=[Response(b'wrong!'), Response(b'pinned')]) as network:
            self.installer.download(self.record(b'pinned'), path)
        self.assertEqual(network.call_count, 2)
        self.assertEqual(path.read_bytes(), b'pinned')

    def test_download_cancel_preserves_partial_and_never_promotes(self):
        path = self.root / 'downloads' / 'cancelled'
        calls = 0
        def cancel_while_reading(**_):
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise worker.Cancelled()
        self.installer.guard = cancel_while_reading
        with patch.object(worker, 'urlopen', return_value=Response(b'pinned')):
            with self.assertRaises(worker.Cancelled):
                self.installer.download(self.record(b'pinned'), path)
        self.assertFalse(path.exists())
        self.assertTrue(path.with_suffix('.part').exists())

    def test_plain_http_and_escape_targets_are_rejected(self):
        record = self.record(b'pinned')
        record['url'] = 'http://example.invalid/file'
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            self.installer.download(record, self.root / 'downloads' / 'file')
        with self.assertRaisesRegex(ValueError, '超出'):
            self.installer.download(self.record(b'pinned'), self.root.parent / 'escape')


class ZipTests(InstallerFixture):
    def archive(self, entries):
        archive = self.root / 'test.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            for name, content in entries:
                bundle.writestr(name, content)
        return archive

    def test_traversal_windows_drive_and_absolute_paths_are_rejected(self):
        for name in ('../escape', 'root/../../escape', 'root\\..\\escape',
                     '/absolute', 'C:/escape', 'root/file:stream'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    worker.unpack(self.archive([(name, b'x')]), self.root / 'expanded')

    def test_case_insensitive_duplicates_and_symlinks_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '重复'):
            worker.unpack(self.archive([('root/A', b'1'), ('root/a', b'2')]),
                          self.root / 'expanded', strip_first=True)
        link = zipfile.ZipInfo('root/link')
        link.external_attr = (0o120777 << 16)
        with self.assertRaisesRegex(ValueError, '链接'):
            worker.unpack(self.archive([(link, b'../../elsewhere')]), self.root / 'expanded')

    def test_stripping_source_archive_root_keeps_portable_tree(self):
        worker.unpack(self.archive([('source-revision/folder/file.py', b'text')]),
                      self.root / 'expanded', strip_first=True)
        self.assertEqual((self.root / 'expanded/folder/file.py').read_bytes(), b'text')

    def test_declared_archive_bomb_is_rejected_before_any_write(self):
        bundle = SimpleNamespace(infolist=lambda: [SimpleNamespace(file_size=601 * 1024**2)])
        @contextmanager
        def opened(*_):
            yield bundle
        with patch.object(worker.zipfile, 'ZipFile', side_effect=opened):
            with self.assertRaisesRegex(ValueError, '过大'):
                worker.unpack(self.root / 'fake.zip', self.root / 'expanded')
        self.assertFalse((self.root / 'expanded').exists())


class ProcessTests(InstallerFixture):
    def test_cancelled_operation_stops_owned_process_tree(self):
        process = SimpleNamespace(poll=lambda: None, returncode=None)
        calls = 0
        def guard(**_):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise worker.Cancelled()
        self.installer.guard = guard
        with patch.object(worker.subprocess, 'Popen', return_value=process), \
             patch.object(worker, 'stop_process') as stopping:
            with self.assertRaises(worker.Cancelled):
                self.installer.run(['fake-command'], 'synthetic-cancel')
        stopping.assert_called_once_with(process)


class PipelineTests(InstallerFixture):
    def fake_pipeline(self, *, acceptance=True):
        self.source = self.root / 'source'
        (self.source / 'minimal_inference').mkdir(parents=True)
        self.assets = self.root / 'assets'
        self.assets.mkdir()
        self.installer.prepare_source = lambda: self.source
        self.installer.prepare_assets = lambda: self.assets
        self.installer.prepare_environment = lambda: self.root / 'environment/Scripts/python.exe'
        self.installer.fingerprint = lambda _: dict(gpu='synthetic', capability=[8, 9],
                                                    torch='2.7.1', tensorrt='11.2.1.2')
        self.calls = []
        def run(command, name, **_):
            self.calls.append(name)
            if name.startswith(('export-', 'build-')):
                engines = Path(command[command.index('--output') + 1])
                stage = command[command.index('--stage') + 1]
                if name.startswith('export-'):
                    # Match the converter's protective refusal to overwrite a partial export.
                    graph = engines / (stage + '.onnx')
                    if graph.exists():
                        raise FileExistsError(graph)
                    graph.write_bytes(('onnx-' + stage).encode())
                    (engines / 'config.json').write_text('{}')
                else:
                    path = engines / (stage + '.engine')
                    path.write_bytes(('engine-' + stage).encode())
                    worker.atomic_json(engines / (stage + '-build.json'),
                                       dict(engine_sha256=worker.digest(path)))
            elif name == 'synthesis-check' and acceptance:
                audio = {}
                for language in ('zh', 'ja'):
                    path = self.job_dir / f'preview-{language}.wav'
                    path.write_bytes(('synthetic-audio-' + language).encode())
                    audio[language] = dict(sha256=worker.digest(path))
                worker.atomic_json(self.job_dir / 'acceptance.json', dict(ok=True, audio=audio))
        self.installer.run = run

    def test_build_completion_never_means_ready_without_synthesis_evidence(self):
        self.fake_pipeline(acceptance=False)
        with self.assertRaises((OSError, RuntimeError)):
            self.installer.install()
        self.assertFalse(self.installer.state['ready'])
        self.assertIn('synthesis-check', self.calls)

    def test_complete_stage_cache_still_runs_synthesis_and_hashes_audio(self):
        self.fake_pipeline()
        self.installer.install()
        self.assertTrue(self.installer.state['ready'])
        self.calls.clear()
        self.installer.install()
        self.assertEqual(self.calls, ['synthesis-check'])

    def test_interrupted_build_discards_partial_export_and_can_retry(self):
        self.fake_pipeline()
        run = self.installer.run
        def interrupted(command, name, **kwargs):
            if name == 'build-ssl':
                raise RuntimeError('synthetic build interruption')
            return run(command, name, **kwargs)
        self.installer.run = interrupted
        with self.assertRaisesRegex(RuntimeError, 'interruption'):
            self.installer.install()
        bundle = next((self.root / 'bundles').iterdir())
        self.assertTrue((bundle / 'ssl.onnx').is_file())
        self.installer.run = run
        self.installer.install()
        self.assertTrue(self.installer.state['ready'])

    def test_tampered_cached_stage_is_rebuilt_before_synthesis(self):
        self.fake_pipeline()
        self.installer.install()
        bundle = next((self.root / 'bundles').iterdir())
        (bundle / 'ssl.engine').write_bytes(b'synthetic-tampering')
        self.calls.clear()
        self.installer.install()
        self.assertEqual(self.calls, ['export-ssl', 'build-ssl', 'synthesis-check'])

    def test_asset_lock_change_cannot_reuse_previous_gpu_bundle(self):
        self.fake_pipeline()
        self.installer.install()
        self.installer.lock['assets'][0]['sha256'] = '0' * 64
        self.calls.clear()
        self.installer.install()
        self.assertEqual(len(list((self.root / 'bundles').iterdir())), 2)
        self.assertIn('export-ssl', self.calls)
        self.assertIn('export-bert', self.calls)

    def test_changed_original_weights_stop_before_download_or_conversion(self):
        self.fake_pipeline()
        Path(self.installer.job['profile']['gpt']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '权重已改变'):
            self.installer.install()
        self.assertFalse(self.calls)
        self.assertFalse(self.installer.state['ready'])

    def test_audio_hash_mismatch_cannot_become_ready(self):
        self.fake_pipeline()
        run = self.installer.run
        def tampered(command, name, **kwargs):
            result = run(command, name, **kwargs)
            if name == 'synthesis-check':
                (self.job_dir / 'preview-ja.wav').write_bytes(b'synthetic-corruption')
            return result
        self.installer.run = tampered
        with self.assertRaisesRegex(RuntimeError, '试听文件校验失败'):
            self.installer.install()
        self.assertFalse(self.installer.state['ready'])

    def test_relative_bundle_manifest_survives_whole_tree_move(self):
        self.fake_pipeline()
        self.installer.install()
        bundle = next((self.root / 'bundles').iterdir())
        target = self.root / 'moved-bundle'
        shutil.copytree(bundle, target)
        manifest = json.loads((target / 'v2-manifest.json').read_text())
        records = list(manifest['stages'].values()) + list(manifest['source_weights'].values())
        for record in records:
            self.assertFalse(Path(record['path']).is_absolute())
            self.assertEqual(worker.digest(target / record['path']), record['sha256'])


if __name__ == '__main__':
    unittest.main()
