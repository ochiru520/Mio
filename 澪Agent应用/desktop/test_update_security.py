"""Independent adversarial tests for the update trust boundary.

All signatures are ephemeral test keys. Network responses and executable payloads
are fixtures; this suite never contacts a release server or launches an installer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from updates.manifest import (
    PRODUCT_ID, UpdateChannel, UpdateError, canonical_json, read_json_bytes,
    verify_manifest, verify_file, version_tuple,
)
from updates.transport import UpdateTransport, UpdateCancelled


class ManifestBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.channel = UpdateChannel('stable', '0.2.1',
            'https://github.com/ochiru520/Mio/releases/latest/download/latest.json',
            {'test': base64.b64encode(public).decode('ascii')})
        self.now = datetime.now(timezone.utc)
        self.bytes = b'MZ-fixture-not-an-executable'
        self.payload = {
            'schema_version': 1, 'product_id': PRODUCT_ID, 'channel': 'stable',
            'platform': 'windows-x64', 'minimum_updater_version': '1.0.0',
            'version': '0.3.0', 'build_id': 'mio-0.3.0.0-test',
            'published_at': (self.now - timedelta(minutes=1)).isoformat(),
            'expires_at': (self.now + timedelta(days=7)).isoformat(),
            'notes': '仅用于隔离测试。',
            'package': {'url': 'https://github.com/ochiru520/Mio/releases/download/v0.3.0/Mio-0.3.0-Windows-x64-Setup.exe',
                        'size': len(self.bytes), 'sha256': hashlib.sha256(self.bytes).hexdigest()},
        }

    def signed(self, payload=None, key=None):
        payload = self.payload if payload is None else payload
        key = key or self.key
        return json.dumps({'key_id': 'test', 'payload': payload,
                           'signature': base64.b64encode(key.sign(canonical_json(payload))).decode('ascii')},
                          ensure_ascii=False).encode('utf-8')

    def verified(self):
        return verify_manifest(self.signed(), self.channel, now=self.now)

    def test_version_order_is_numeric_not_lexicographic(self):
        self.assertGreater(version_tuple('0.2.10'), version_tuple('0.2.9'))
        self.assertEqual(version_tuple('0.3.0.0'), version_tuple('0.3.0'))
        for malformed in ('0.03.0', '0.3', 'v0.3.0', '../0.3.0', '0.3.0 & calc.exe'):
            with self.subTest(malformed=malformed), self.assertRaises(UpdateError):
                version_tuple(malformed)

    def test_valid_signature_and_installer_hash(self):
        release = self.verified()
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'fixture.exe'; file.write_bytes(self.bytes)
            verify_file(file, release)
            file.write_bytes(b'X' * len(self.bytes))
            with self.assertRaises(UpdateError):
                verify_file(file, release)

    def test_modified_payload_cannot_reuse_signature(self):
        envelope = json.loads(self.signed())
        envelope['payload']['notes'] = '篡改后的内容'
        with self.assertRaises(UpdateError):
            verify_manifest(json.dumps(envelope).encode(), self.channel, now=self.now)

    def test_wrong_signing_key_is_rejected(self):
        with self.assertRaises(UpdateError):
            verify_manifest(self.signed(key=Ed25519PrivateKey.generate()), self.channel, now=self.now)

    def test_unknown_key_and_invalid_signature_are_rejected(self):
        for field, value in [('key_id', 'not-trusted'), ('signature', 'broken!')]:
            envelope = json.loads(self.signed()); envelope[field] = value
            with self.subTest(field=field), self.assertRaises(UpdateError):
                verify_manifest(json.dumps(envelope).encode(), self.channel, now=self.now)

    def test_duplicate_json_and_nonfinite_values_are_rejected(self):
        for raw in (b'{"payload":{},"payload":{}}', b'{"value":NaN}', b'[]', b'{}' * 100000):
            with self.subTest(raw=raw[:80]), self.assertRaises(UpdateError):
                read_json_bytes(raw)

    def test_expiry_future_channel_and_platform_guards(self):
        mutations = [
            ('expires_at', (self.now - timedelta(seconds=1)).isoformat()),
            ('published_at', (self.now + timedelta(hours=1)).isoformat()),
            ('channel', 'development'), ('platform', 'windows-arm64'),
            ('product_id', 'another.application'), ('minimum_updater_version', '999.0.0'),
        ]
        for field, value in mutations:
            payload = {**self.payload, field: value}
            with self.subTest(field=field), self.assertRaises(UpdateError):
                verify_manifest(self.signed(payload), self.channel, now=self.now)

    def test_replayed_older_version_is_rejected(self):
        with self.assertRaises(UpdateError):
            verify_manifest(self.signed(), self.channel, now=self.now, highest_seen='0.4.0')

    def test_untrusted_package_urls_never_verify(self):
        urls = ['http://github.com/ochiru520/Mio/releases/download/v0.3.0/Mio.exe',
                'https://attacker.invalid/Mio.exe', 'file:///C:/Mio.exe',
                'https://github.com@attacker.invalid/Mio.exe',
                'https://github.com/other/repository/releases/download/v0.3.0/Mio.exe',
                'https://github.com/ochiru520/Mio/releases/download/v0.3.0/Mio.exe#unsafe',
                'https://github.com:444/ochiru520/Mio/releases/download/v0.3.0/Mio.exe']
        for url in urls:
            payload = {**self.payload, 'package': {**self.payload['package'], 'url': url}}
            with self.subTest(url=url), self.assertRaises(UpdateError):
                verify_manifest(self.signed(payload), self.channel, now=self.now)

    def test_invalid_package_size_and_digest_rejected(self):
        for field, value in [('size', True), ('size', 0), ('size', 2**40), ('sha256', '0' * 63)]:
            payload = {**self.payload, 'package': {**self.payload['package'], field: value}}
            with self.subTest(field=field, value=value), self.assertRaises(UpdateError):
                verify_manifest(self.signed(payload), self.channel, now=self.now)


class DownloadBoundaryTests(ManifestBoundaryTests):
    def transport(self, handler):
        return UpdateTransport(client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs))

    def test_manifest_oversize_and_untrusted_redirect_rejected(self):
        for handler in (
            lambda req: httpx.Response(200, content=b'x' * 140000),
            lambda req: httpx.Response(302, headers={'location': 'https://attacker.invalid/update.json'}),
            lambda req: httpx.Response(302, headers={'location': 'http://127.0.0.1:8000/api/private'}),
        ):
            with self.subTest(handler=handler), self.assertRaises(UpdateError):
                self.transport(handler).manifest(self.channel.feed_url, lambda: False)

    def test_disabled_network_does_not_request(self):
        seen = []
        def handler(req):
            seen.append(req); return httpx.Response(200, content=self.signed())
        with self.assertRaises(UpdateCancelled):
            self.transport(handler).manifest(self.channel.feed_url, lambda: True)
        self.assertEqual(seen, [])

    def test_successful_download_matches_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'Mio.exe'; progress = []
            out = self.transport(lambda req: httpx.Response(200, content=self.bytes)).download(
                self.verified(), dest, lambda done,total: progress.append((done,total)), lambda: False)
            self.assertEqual(out.read_bytes(), self.bytes)
            self.assertEqual(progress[-1], (len(self.bytes), len(self.bytes)))
            self.assertFalse(dest.with_suffix('.part').exists())

    def test_corrupt_complete_download_removed_for_clean_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'Mio.exe'
            with self.assertRaises(UpdateError):
                self.transport(lambda req: httpx.Response(200, content=b'X'*len(self.bytes))).download(
                    self.verified(), dest, lambda *_:None, lambda:False)
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_suffix('.part').exists())

    def partial(self, dest, value):
        release = self.verified()
        dest.with_suffix('.part').write_bytes(value)
        dest.with_suffix('.part.json').write_text(json.dumps({'url':release.url, 'sha256':release.sha256, 'size':release.size}), 'utf-8')
        return release

    def test_ignored_range_restarts_instead_of_appending(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'Mio.exe';release=self.partial(dest,self.bytes[:5]);requests=[]
            def handler(req):
                requests.append(req.headers.get('Range'))
                return httpx.Response(200,content=self.bytes)
            self.transport(handler).download(release,dest,lambda *_:None,lambda:False)
            self.assertEqual(requests,['bytes=5-'])
            self.assertEqual(dest.read_bytes(),self.bytes)

    def test_valid_partial_range_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'Mio.exe';release=self.partial(dest,self.bytes[:5])
            self.transport(lambda req:httpx.Response(206,content=self.bytes[5:],headers={
                'content-range':f'bytes 5-{len(self.bytes)-1}/{len(self.bytes)}'})).download(
                    release,dest,lambda *_:None,lambda:False)
            self.assertEqual(dest.read_bytes(),self.bytes)

    def test_wrong_partial_range_does_not_replace_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'Mio.exe';release=self.partial(dest,self.bytes[:5])
            with self.assertRaises(UpdateError):
                self.transport(lambda req:httpx.Response(206,content=self.bytes[5:],headers={
                    'content-range':f'bytes 0-{len(self.bytes)-6}/{len(self.bytes)}'})).download(
                        release,dest,lambda *_:None,lambda:False)
            self.assertFalse(dest.exists())

    def test_truncated_response_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'Mio.exe'
            with self.assertRaises(UpdateError):
                self.transport(lambda req:httpx.Response(200,content=self.bytes[:5])).download(
                    self.verified(),dest,lambda *_:None,lambda:False)
            self.assertFalse(dest.exists())
            self.assertTrue(dest.with_suffix('.part').exists())

    def test_oversize_response_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'Mio.exe'
            with self.assertRaises(UpdateError):
                self.transport(lambda req:httpx.Response(200,content=self.bytes+b'overflow')).download(
                    self.verified(),dest,lambda *_:None,lambda:False)
            self.assertFalse(dest.exists())


if __name__ == '__main__':
    unittest.main()
