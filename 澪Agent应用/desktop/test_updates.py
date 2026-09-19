from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from desktop.updates.manifest import (PRODUCT_ID, UpdateChannel, UpdateError, canonical_json,
    read_json_bytes, validate_url, verify_file, verify_manifest, version_tuple)
from desktop.updates.transport import UpdateCancelled, UpdateTransport, atomic_json
from desktop.updates.service import UpdateService
from desktop.updates.helper import UpgradeTransaction
from desktop.updates.integration import UpdateBridgeMixin


class UpdateFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='mio-update-test-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.channel = UpdateChannel('stable', '0.3.0',
            'https://github.com/ochiru520/Mio/releases/latest/download/latest.json',
            {'test': base64.b64encode(public).decode('ascii')})
        self.content = b'MZ-test-installer-0123456789' * 100
        now = datetime.now(timezone.utc)
        self.payload = {'schema_version': 1, 'product_id': PRODUCT_ID, 'channel': 'stable',
            'version': '0.3.1', 'platform': 'windows-x64', 'minimum_updater_version': '1.0.0',
            'published_at': now.isoformat(), 'expires_at': (now+timedelta(days=30)).isoformat(),
            'build_id': 'mio-0.3.1.0-test-new', 'notes': '测试更新，非公开版本',
            'package': {'url': 'https://github.com/ochiru520/Mio/releases/download/v0.3.1/Mio-Setup.exe',
                'size': len(self.content), 'sha256': hashlib.sha256(self.content).hexdigest()}}

    def signed(self):
        return canonical_json({'payload': self.payload, 'key_id': 'test',
            'signature': base64.b64encode(self.key.sign(canonical_json(self.payload))).decode('ascii')})

    def release(self):
        return verify_manifest(self.signed(), self.channel)

    def transport(self, callback):
        return UpdateTransport(client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(callback), **kwargs))


class ManifestTests(UpdateFixture):
    def test_accepts_valid_signature_and_numeric_versions(self):
        self.assertEqual(self.release().version, '0.3.1')
        self.assertGreater(version_tuple('0.10.0'), version_tuple('0.9.0'))
        self.assertEqual(version_tuple('1.2.3'), version_tuple('1.2.3.0'))

    def test_rejects_unsigned_or_tampered_manifest(self):
        data = json.loads(self.signed()); data['payload']['notes'] = 'tampered'
        with self.assertRaisesRegex(UpdateError, '签名'):
            verify_manifest(canonical_json(data), self.channel)

    def test_rejects_untrusted_key(self):
        data = json.loads(self.signed()); data['key_id'] = 'attacker'
        with self.assertRaisesRegex(UpdateError, '密钥'):
            verify_manifest(canonical_json(data), self.channel)

    def test_rejects_duplicate_json_and_oversized_manifest(self):
        for data in (b'{"a":1,"a":2}', b'x' * 131073, b'{"a":NaN}', b'[]'):
            with self.subTest(data=data[:30]), self.assertRaises(UpdateError):
                read_json_bytes(data)

    def test_rejects_cross_product_channel_and_architecture(self):
        for name, value in (('product_id','other'),('channel','development'),('platform','macos-arm64')):
            with self.subTest(name=name):
                original=self.payload[name];self.payload[name]=value
                with self.assertRaises(UpdateError): self.release()
                self.payload[name]=original

    def test_rejects_expired_future_and_replayed_manifest(self):
        with self.assertRaisesRegex(UpdateError, '回退'):
            verify_manifest(self.signed(), self.channel, highest_seen='0.4.0')
        self.payload['expires_at']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(UpdateError, '过期'): self.release()

    def test_rejects_unsupported_updater(self):
        self.payload['minimum_updater_version']='2.0.0'
        with self.assertRaisesRegex(UpdateError, '升级助手'): self.release()

    def test_rejects_arbitrary_download_urls(self):
        for url in ('http://github.com/x','https://127.0.0.1/private','https://github.com.evil.test/x','https://u:p@github.com/a','https://github.com:444/a'):
            with self.subTest(url=url),self.assertRaises(UpdateError):validate_url(url)
        self.payload['package']['url']='https://github.com/attacker/repo/releases/download/v0.3.1/a.exe'
        with self.assertRaises(UpdateError):self.release()

    def test_file_size_and_hash_are_required(self):
        path=self.root/'package.exe';path.write_bytes(self.content)
        verify_file(path,self.release())
        path.write_bytes(b'x'*len(self.content))
        with self.assertRaisesRegex(UpdateError,'校验'): verify_file(path,self.release())


class TransportTests(UpdateFixture):
    def test_download_progress_and_verified_final_file(self):
        progress=[]
        transport=self.transport(lambda req:httpx.Response(200,content=self.content))
        target=transport.download(self.release(),self.root/'package.exe',lambda a,b:progress.append((a,b)),lambda:False)
        self.assertEqual(target.read_bytes(),self.content)
        self.assertEqual(progress[-1],(len(self.content),len(self.content)))

    def test_resume_is_bound_to_source_hash_and_size(self):
        release=self.release();target=self.root/'package.exe';partial=target.with_suffix('.part');partial.write_bytes(self.content[:100])
        atomic_json(target.with_suffix('.part.json'),{'url':release.url,'sha256':release.sha256,'size':release.size})
        def response(req):
            self.assertEqual(req.headers['range'],'bytes=100-')
            return httpx.Response(206,content=self.content[100:],headers={'content-range':f'bytes 100-{len(self.content)-1}/{len(self.content)}'})
        self.transport(response).download(release,target,lambda *_:None,lambda:False)
        self.assertEqual(target.read_bytes(),self.content)

    def test_server_ignoring_range_does_not_append_duplicate_content(self):
        release=self.release();target=self.root/'package.exe';target.with_suffix('.part').write_bytes(self.content[:20])
        atomic_json(target.with_suffix('.part.json'),{'url':release.url,'sha256':release.sha256,'size':release.size})
        self.transport(lambda req:httpx.Response(200,content=self.content)).download(release,target,lambda *_:None,lambda:False)
        self.assertEqual(target.read_bytes(),self.content)

    def test_corrupt_package_is_not_promoted(self):
        target=self.root/'package.exe'
        with self.assertRaises(UpdateError):
            self.transport(lambda req:httpx.Response(200,content=b'x'*len(self.content))).download(self.release(),target,lambda *_:None,lambda:False)
        self.assertFalse(target.exists()); self.assertFalse(target.with_suffix('.part').exists())

    def test_cancellation_makes_no_request(self):
        callback=Mock()
        with self.assertRaises(UpdateCancelled):
            self.transport(callback).download(self.release(),self.root/'package.exe',lambda *_:None,lambda:True)
        callback.assert_not_called()

    def test_redirect_to_untrusted_host_is_blocked(self):
        with self.assertRaises(UpdateError):
            self.transport(lambda req:httpx.Response(302,headers={'location':'https://evil.test/a'})).manifest(self.channel.feed_url,lambda:False)

    def test_wrong_content_range_is_rejected(self):
        with self.assertRaises(UpdateError):
            self.transport(lambda req:httpx.Response(206,content=self.content,headers={'content-range':'bytes 2-20/30'})).download(self.release(),self.root/'x.exe',lambda *_:None,lambda:False)

    def test_404_does_not_mean_up_to_date(self):
        with self.assertRaisesRegex(UpdateError,'不表示已是最新版'):
            self.transport(lambda req:httpx.Response(404)).manifest(self.channel.feed_url,lambda:False)


class ServiceTests(UpdateFixture):
    def service(self, transport=None, paused=False):
        return UpdateService(self.channel,state_root=self.root/'state',install_root=self.root/'app',
            runtime_root=self.root/'state/runtime',helper_source=self.root/'helper.exe',installed=False,
            privacy_paused=lambda:paused,prepare=Mock(),resume=Mock(),exit_app=Mock(),
            transport=transport or self.transport(lambda req:httpx.Response(200,content=self.signed())))

    def test_successful_check_only_exposes_verified_release(self):
        service=self.service();service.check();service._thread.join(5)
        self.assertEqual(service.status()['state'],'available')
        self.assertEqual(service.status()['release']['version'],'0.3.1')

    def test_failed_check_is_not_reported_up_to_date(self):
        service=self.service(self.transport(lambda req:httpx.Response(500)))
        service.check();service._thread.join(5)
        self.assertEqual(service.status()['state'],'failed')
        self.assertIsNone(service.status()['release'])

    def test_privacy_pause_blocks_check_without_request(self):
        callback=Mock();service=self.service(self.transport(callback),paused=True)
        with self.assertRaisesRegex(UpdateError,'隐私'):service.check()
        callback.assert_not_called()

    def test_preferences_do_not_accept_urls_or_commands(self):
        service=self.service()
        for data in ({'url':'https://evil.test'},{'auto_check':'true'},{'shell':'cmd'}):
            with self.subTest(data=data),self.assertRaises(UpdateError):service.configure(data)

    def test_source_preview_cannot_install(self):
        service=self.service();service.check();service._thread.join(5)
        package=self.root/'p.exe';package.write_bytes(self.content)
        service._package=package;service._set(state='ready')
        with self.assertRaisesRegex(UpdateError,'安装版'):service.install()

    def test_bridge_requires_explicit_confirmation(self):
        bridge=UpdateBridgeMixin();bridge._update_service=Mock()
        self.assertFalse(bridge.install_update()['ok'])
        bridge._update_service.install.assert_not_called()
        bridge.install_update(True);bridge._update_service.install.assert_called_once()


class TransactionTests(UpdateFixture):
    def transaction(self, mode='success'):
        install=self.root/'程序 目录';state=install/'个人数据';runtime=state/'运行数据'
        install.mkdir();runtime.mkdir(parents=True)
        (install/'Mio.exe').write_bytes(b'old-exe');(install/'_internal').mkdir();(install/'_internal/old.txt').write_text('old')
        (install/'数据目录.txt').write_text(str(state),'utf-8')
        (runtime/'keep.txt').write_text('private-data','utf-8')
        atomic_json(install/'构建清单.json',{'build_id':'mio-old','app_version':'0.3.0'})
        job=state/'updates/jobs'/('a'*32);job.mkdir(parents=True)
        package=state/'updates/downloads'/(self.release().sha256+'.exe');package.parent.mkdir();package.write_bytes(self.content)
        ticket={'schema_version':1,'job':str(job),'state_root':str(state),'runtime_root':str(runtime),
                'install_root':str(install),'parent_pid':999999,'package':str(package),
                'target_build_id':self.payload['build_id'],'previous_build_id':'mio-old',
                'backup_name':'backup.zip','backup_sha256':'0'*64}
        atomic_json(job/'ticket.json',ticket);(job/'manifest.json').write_bytes(self.signed())
        atomic_json(state/'updates/install.lock',{'job':str(job),'pid':999999})
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if str(args[0])==str(package):
                self.assertIn('/UPDATE=1',args)
                self.assertIn('/DataDir='+str(state),args)
                (install/'Mio.exe').write_bytes(b'new-exe');(install/'_internal').mkdir(exist_ok=True);(install/'_internal/new.txt').write_text('new')
                (install/'数据目录.txt').write_text(str(state),'utf-8')
                atomic_json(install/'构建清单.json',{'build_id':self.payload['build_id'],'app_version':'0.3.1'})
                return SimpleNamespace(returncode=1 if mode=='installer_fail' else 0)
            if '--update-verify' in args:
                if mode=='verify_fail':
                    (runtime/'keep.txt').write_text('migrated','utf-8')
                    return SimpleNamespace(returncode=1)
                atomic_json(job/'verified.json',{'ok':True,'build_id':self.payload['build_id']})
                return SimpleNamespace(returncode=0)
            if '--update-restore' in args:
                (runtime/'keep.txt').write_text('private-data','utf-8')
                return SimpleNamespace(returncode=0)
            raise AssertionError(args)
        spawn=Mock(return_value=SimpleNamespace(pid=123))
        t=UpgradeTransaction(job/'ticket.json',self.channel,run=run,spawn=spawn,is_alive=lambda _:False)
        return t,install,state,runtime,calls,spawn

    def test_success_keeps_data_and_old_program_backup(self):
        t,install,state,runtime,calls,spawn=self.transaction();t.execute()
        self.assertEqual((install/'Mio.exe').read_bytes(),b'new-exe')
        self.assertEqual((t.backup/'Mio.exe').read_bytes(),b'old-exe')
        self.assertEqual((runtime/'keep.txt').read_text('utf-8'),'private-data')
        self.assertFalse((state/'updates/install.lock').exists())
        self.assertEqual(json.loads((state/'updates/last-result.json').read_text('utf-8'))['state'],'completed')
        spawn.assert_called_once()

    def test_installer_failure_restores_old_files(self):
        t,install,state,runtime,calls,spawn=self.transaction('installer_fail');t.execute()
        self.assertEqual((install/'Mio.exe').read_bytes(),b'old-exe')
        self.assertTrue((install/'_internal/old.txt').exists());self.assertFalse((install/'_internal/new.txt').exists())
        self.assertEqual((runtime/'keep.txt').read_text('utf-8'),'private-data')
        self.assertFalse(any('--update-restore' in c for c in calls))

    def test_migration_failure_pairs_program_and_data_restore(self):
        t,install,state,runtime,calls,spawn=self.transaction('verify_fail');t.execute()
        self.assertEqual((install/'Mio.exe').read_bytes(),b'old-exe')
        self.assertEqual((runtime/'keep.txt').read_text('utf-8'),'private-data')
        self.assertTrue(any('--update-restore' in c for c in calls))

    def test_interrupted_move_recovers_only_managed_paths(self):
        t,install,state,runtime,calls,spawn=self.transaction()
        originals=[p.name for p in install.iterdir() if p.name!='个人数据']
        t._phase('backing_up_program',original_names=originals);t.backup.mkdir()
        (install/'Mio.exe').replace(t.backup/'Mio.exe')
        t.execute(recovery_only=True)
        self.assertEqual((install/'Mio.exe').read_bytes(),b'old-exe')
        self.assertEqual((runtime/'keep.txt').read_text('utf-8'),'private-data')

    def test_finished_journal_is_not_rolled_back_on_restart(self):
        t,install,state,runtime,calls,spawn=self.transaction();t._phase('completed');t.execute(recovery_only=True)
        self.assertEqual(calls,[])
        self.assertFalse((state/'updates/install.lock').exists())

    def test_modified_package_cannot_replace_program(self):
        t,install,state,runtime,calls,spawn=self.transaction()
        Path(t.ticket['package']).write_bytes(b'tampered')
        with self.assertRaises(UpdateError):t.execute()
        self.assertEqual((install/'Mio.exe').read_bytes(),b'old-exe');self.assertEqual(calls,[])


if __name__ == '__main__':
    unittest.main()
