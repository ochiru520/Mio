import dataclasses
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import dependency_package_service as packages


class PackageSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = dataclasses.replace(packages.settings, data_dir=self.root/'data', voice_training_dir=self.root/'voice',
                                            workspace_root=self.root/'workspace', agent_control_scripts_dir=self.root/'program/_internal/agent_scripts')
        self.mock = patch.object(packages, 'settings', self.settings)
        self.mock.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.mock.stop)

    def test_persistent_urls_cannot_replace_pinned_checksum(self):
        source = {'urls': [], 'sha256': 'a'*64, 'size_bytes': 22, 'file_name': 'engine.zip'}
        packages.save_urls('genie_runtime', ['https://example.com/engine.zip', 'https://example.com/engine.zip'])
        self.assertEqual(packages.effective_source('genie_runtime', source), {**source, 'urls': ['https://example.com/engine.zip']})
        self.assertEqual(json.loads(packages._config_path().read_text('utf-8'))['genie_runtime']['urls'], ['https://example.com/engine.zip'])
        packages.save_urls('genie_runtime', [])
        self.assertEqual(packages.effective_source('genie_runtime', source), source)

    def test_invalid_sources_rejected_before_write(self):
        for value in ['http://example.com/a', 'file:///c:/a', 'https://user:pass@example.com/a', 'https://example.com/a\n--help', 'https://example.com/a#fragment']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                packages.save_urls('genie_runtime', [value])
        self.assertFalse(packages._config_path().exists())
        with self.assertRaises(ValueError): packages.save_urls('unknown', [])

    def test_only_named_package_in_controlled_directories_is_discovered(self):
        directory = self.settings.workspace_root/'模型'
        directory.mkdir(parents=True)
        path = directory/'engine.zip'
        path.write_bytes(b'package')
        found = packages.find_local_package({'file_name':'engine.zip'})
        self.assertIsNotNone(found)
        # Windows may spell the same temp directory with a long or 8.3 name.
        self.assertTrue(found.samefile(path))
        self.assertIsNone(packages.find_local_package({'file_name':'../engine.zip'}))
        self.assertIsNone(packages.find_local_package({'file_name':'missing.zip'}))

    def test_installer_companion_package_location_is_resumed_without_disk_scan(self):
        source = self.root/'installation-media'
        source.mkdir()
        (source/'engine.zip').write_bytes(b'package')
        self.settings.data_dir.mkdir()
        (self.settings.data_dir/'安装来源目录.txt').write_text(str(source),encoding='utf-8')
        found = packages.find_local_package({'file_name':'engine.zip'})
        self.assertIsNotNone(found)
        self.assertTrue(found.samefile(source/'engine.zip'))

    def test_corrupt_saved_configuration_is_not_reported_as_no_sources(self):
        self.settings.data_dir.mkdir()
        packages._config_path().write_text('{bad',encoding='utf-8')
        with self.assertRaises(ValueError): packages.effective_source('genie_runtime', {'urls': []})
        packages.save_urls('genie_runtime', ['https://example.com/repaired.zip'])
        self.assertEqual(packages.effective_source('genie_runtime', {'urls': []})['urls'], ['https://example.com/repaired.zip'])
        backup = list(self.settings.data_dir.glob('组件下载源.json.invalid-*'))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].read_text('utf-8'), '{bad')

    def test_package_routes_save_sources_and_keep_pinned_metadata(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import dependency_installer
        from app.routes.dependencies import router
        directory = self.settings.agent_control_scripts_dir/'deps'
        directory.mkdir(parents=True)
        pinned = {'format':'mio-genie-runtime-source', 'file_name':'engine.zip', 'size_bytes':123, 'sha256':'a'*64, 'urls':[]}
        (directory/'mio-genie-runtime-source.json').write_text(json.dumps(pinned), encoding='utf-8')
        app = FastAPI()
        app.include_router(router)
        with patch.object(dependency_installer, 'settings', self.settings), TestClient(app) as client:
            info = client.get('/api/dependencies/genie_runtime/package')
            self.assertEqual(info.status_code, 200)
            self.assertEqual(info.json()['urls'], [])
            self.assertEqual(client.put('/api/dependencies/genie_runtime/package-source', json={'urls':['http://example.com/a']}).status_code,400)
            result = client.put('/api/dependencies/genie_runtime/package-source', json={'urls':['https://example.com/a.zip']})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['sha256'], pinned['sha256'])
            self.assertEqual(result.json()['size_bytes'], pinned['size_bytes'])
            self.assertEqual(client.get('/api/dependencies/genie_runtime/package').json()['urls'], ['https://example.com/a.zip'])


if __name__ == '__main__': unittest.main()
