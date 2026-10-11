"""Real file transactions on synthetic local builds; no installer or desktop launch."""
from __future__ import annotations
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from desktop.updates.local_candidate import LOCAL_KIND, PRODUCT_ID, inventory, sha256, verify_candidate
from desktop.updates.manifest import UpdateChannel, UpdateError, verify_manifest
from desktop.updates.helper import UpgradeTransaction
from desktop.updates.transport import atomic_json


class LocalCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mio-local-candidate-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'; self.runtime = self.state / 'runtime'
        self.runtime.mkdir(parents=True)
        self.program = self.root / 'installed'; (self.program / '_internal').mkdir(parents=True)
        (self.program / 'Mio.exe').write_bytes(b'old executable fixture')
        (self.program / '_internal/old.pyc').write_bytes(b'old module fixture')
        (self.program / '构建清单.json').write_text(json.dumps({'build_id': 'old-build', 'app_version': '0.11.0'}))
        (self.program / '数据目录.txt').write_text(str(self.state), encoding='utf-8')
        (self.runtime / 'fixture-diary.txt').write_bytes(b'user data kept')
        self.job = self.state / 'updates/jobs' / ('1'*32); self.job.mkdir(parents=True)
        self.candidate = self.root / 'candidate'
        files = {'Mio.exe': b'new executable fixture', '_internal/MioUpdater.exe': b'updater fixture',
                 '_internal/agent_frontend/index.html': b'<html>candidate</html>', '_internal/app/new.pyc': b'new module fixture'}
        identity = {'schema_version': 1, 'build_id': 'new-build', 'app_version': '0.11.0',
                    'sources': {'backend': {'commit': 'source-a'}, 'desktop': {'commit': 'source-b'}}}
        files['_internal/build_identity.json'] = json.dumps(identity).encode()
        for name, content in files.items():
            path = self.candidate / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        build = dict(identity, artifacts=[{'name': 'exe', 'path': 'Mio.exe', 'size': len(files['Mio.exe']),
                                          'sha256': hashlib.sha256(files['Mio.exe']).hexdigest().upper()}])
        (self.candidate / '构建清单.json').write_text(json.dumps(build), encoding='utf-8')
        self.manifest = {'kind': LOCAL_KIND, 'product_id': PRODUCT_ID, 'source_job_id': 'upgrade_'+'f'*32,
                         'base_build_id': 'old-build', 'build_id': 'new-build', 'sources': identity['sources'],
                         'app_version': '0.11.0', 'checks': [{'name': 'fixture-check', 'ok': True}],
                         'preview': {'verified': True}, 'files': inventory(self.candidate)}
        atomic_json(self.job / 'manifest.json', self.manifest)
        self.pin = sha256(self.job / 'manifest.json')
        cached = self.state / 'updates/local-candidates' / self.pin / 'program'
        from desktop.updates.helper import copy_managed_entry
        copy_managed_entry(self.candidate, cached)
        self.cached = cached
        atomic_json(self.state / 'updates/install.lock', {'job': str(self.job), 'pid': 12345})
        ticket = {'schema_version': 1, 'job': str(self.job), 'parent_pid': 12345, 'install_root': str(self.program),
                  'state_root': str(self.state), 'runtime_root': str(self.runtime), 'package': str(cached),
                  'backup_name': 'fixture.zip', 'backup_sha256': 'a'*64, 'previous_build_id': 'old-build',
                  'target_build_id': 'new-build', 'helper_sha256': 'b'*64, 'update_kind': 'local_candidate',
                  'local_manifest_sha256': self.pin, 'authorization': 'explicit_local_application'}
        atomic_json(self.job / 'ticket.json', ticket)
        self.channel = UpdateChannel('development', '0.11.0', '', {}, False)

    def transaction(self, failed_health=False):
        def command(arguments, **kwargs):
            if '--update-verify' in arguments:
                if failed_health:
                    return SimpleNamespace(returncode=1)
                atomic_json(self.job / 'verified.json', {'ok': True, 'build_id': 'new-build'})
            if '--update-restore' in arguments:
                atomic_json(self.job / 'restored.json', {'ok': True, 'build_id': 'old-build'})
            return SimpleNamespace(returncode=0)
        return UpgradeTransaction(self.job / 'ticket.json', self.channel, run=command, spawn=Mock(), is_alive=lambda pid: False)

    def test_local_integrity_and_source_binding(self):
        result = verify_candidate(self.candidate, self.job/'manifest.json', self.pin, base_build_id='old-build')
        self.assertEqual(result['build_id'], 'new-build')
        with self.assertRaises(UpdateError):
            verify_candidate(self.candidate, self.job/'manifest.json', self.pin, base_build_id='different-base')
        (self.candidate/'_internal/app/new.pyc').write_bytes(b'tampered')
        with self.assertRaises(UpdateError):
            verify_candidate(self.candidate, self.job/'manifest.json', self.pin)

    def test_local_manifest_never_passes_signed_remote_parser(self):
        with self.assertRaises(UpdateError):
            verify_manifest((self.job/'manifest.json').read_bytes(), self.channel)

    def test_non_program_content_cannot_enter_local_program_copy(self):
        (self.candidate/'private-data.json').write_text('{}')
        with self.assertRaises(UpdateError):
            inventory(self.candidate)

    def test_failed_check_or_preview_cannot_become_candidate(self):
        for field, value in [('checks', [{'ok': False}]), ('preview', {'verified': False})]:
            payload = dict(self.manifest); payload[field] = value
            path = self.root/'bad-manifest.json'; atomic_json(path, payload)
            with self.assertRaises(UpdateError):
                verify_candidate(self.candidate, path, sha256(path))

    def test_actual_managed_file_application_preserves_user_data(self):
        transaction = self.transaction(); transaction.execute()
        self.assertEqual((self.program/'Mio.exe').read_bytes(), b'new executable fixture')
        self.assertFalse((self.program/'_internal/old.pyc').exists())
        self.assertEqual((self.runtime/'fixture-diary.txt').read_bytes(), b'user data kept')
        self.assertEqual(json.loads((self.job/'result.json').read_text('utf-8'))['state'], 'completed')
        self.assertFalse((self.state/'updates/install.lock').exists())

    def test_health_failure_restores_program_and_keeps_fixture_data(self):
        transaction = self.transaction(failed_health=True); transaction.execute()
        self.assertEqual((self.program/'Mio.exe').read_bytes(), b'old executable fixture')
        self.assertTrue((self.program/'_internal/old.pyc').exists())
        self.assertEqual((self.runtime/'fixture-diary.txt').read_bytes(), b'user data kept')
        self.assertEqual(json.loads((self.job/'result.json').read_text('utf-8'))['state'], 'recovered')

    def test_interrupted_replacement_recovers_without_installing_again(self):
        from desktop.updates.helper import copy_managed_entry
        transaction = self.transaction(); transaction.backup.mkdir()
        originals = ['Mio.exe', '_internal', '构建清单.json', '数据目录.txt']
        for name in originals:
            copy_managed_entry(self.program/name, transaction.backup/name)
        (self.program/'Mio.exe').write_bytes(b'interrupted partial new fixture')
        transaction._phase('installing', original_names=originals, data_touched=False)
        transaction.execute(recovery_only=True)
        self.assertEqual((self.program/'Mio.exe').read_bytes(), b'old executable fixture')
        self.assertEqual(json.loads((self.job/'result.json').read_text('utf-8'))['state'], 'recovered')


if __name__ == '__main__':
    unittest.main()
