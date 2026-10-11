"""Small local fixtures exercise the actual Windows download/status helpers."""
from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

COMMON = Path(__file__).with_name('common.ps1')


def literal(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(shutil.which('powershell'), 'PowerShell 5.1 is required')
class CommonRuntimeTests(unittest.TestCase):
    def run_ps(self, body, status, *, check=True):
        environment = os.environ.copy()
        environment.update(MIO_STATUS_FILE=str(status), MIO_DEP_ID='fixture', MIO_INSTALL_ID='fixture-id')
        command = "$ErrorActionPreference='Stop'; . " + literal(COMMON) + '; ' + body
        process = subprocess.Popen(['powershell', '-NoProfile', '-Command', command],
                                   env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        stdout, stderr = process.communicate(timeout=45)
        if check:
            self.assertEqual(process.returncode, 0, (stdout + stderr).decode(errors='replace'))
        return process

    def test_progress_keeps_installer_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            status = Path(temporary) / 'status.json'
            process = self.run_ps("Write-DepsStatus -Stage download -Percent 40 -Message progress; "
                                  "Write-DepsStatus -Stage done -Percent 100 -Message finished -Done $true", status)
            payload = json.loads(status.read_text(encoding='utf-8-sig'))
            self.assertEqual(payload['console_pid'], process.pid)
            self.assertEqual(payload['install_id'], 'fixture-id')
            self.assertGreater(int(payload['console_started_ticks']), 0)
            self.assertTrue(payload['started_at'])
            self.assertTrue(payload['done'])

    def test_zip_structure_and_paths_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = root / 'good.zip'
            unsafe = root / 'unsafe.zip'
            with zipfile.ZipFile(good, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('bin/ffmpeg.exe', b'fixture')
            with zipfile.ZipFile(unsafe, 'w') as archive:
                archive.writestr('../escape.exe', b'fixture')
            self.run_ps('Assert-DepsZipArchive -Path ' + literal(good) + " -RequiredFiles @('ffmpeg.exe')", root/'status.json')
            missing = self.run_ps('Assert-DepsZipArchive -Path ' + literal(good) + " -RequiredFiles @('ffprobe.exe')", root/'status.json', check=False)
            self.assertNotEqual(missing.returncode, 0)
            bad = self.run_ps('Assert-DepsZipArchive -Path ' + literal(unsafe), root/'status.json', check=False)
            self.assertNotEqual(bad.returncode, 0)

    def test_source_switch_does_not_append_another_servers_partial(self):
        expected = b'new-download'
        ranges = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/first':
                    if self.headers.get('Range'):
                        self.send_response(416)
                        self.end_headers()
                    else:
                        self.send_response(200)
                        self.send_header('Content-Length', str(len(expected)))
                        self.end_headers()
                        self.wfile.write(b'OLD-')
                        self.wfile.flush()
                        self.close_connection = True
                else:
                    ranges.append(self.headers.get('Range'))
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(expected)))
                    self.end_headers()
                    self.wfile.write(expected)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / 'file.zip'
                base = f'http://127.0.0.1:{server.server_port}'
                body = ("Invoke-Download -Urls @(" + literal(base+'/first') + ',' + literal(base+'/second') +
                        ') -Output ' + literal(output) + ' -ExpectedSize ' + str(len(expected)) +
                        ' -ExpectedSha256 ' + literal(hashlib.sha256(expected).hexdigest()))
                self.run_ps(body, Path(temporary)/'status.json')
                self.assertEqual(output.read_bytes(), expected)
                self.assertEqual(ranges, [None])
                self.assertTrue(list(Path(temporary).glob('*.source-*.part')))
        finally:
            server.shutdown()
            server.server_close()

    def test_untrusted_download_metadata_stops_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = self.run_ps("Invoke-Download -Urls @('http://127.0.0.1:1/unused') -Output " + literal(root/'out'), root/'status.json', check=False)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((root/'out').exists())


class ChildStatusTests(unittest.TestCase):
    def test_python_model_downloader_keeps_parent_installer_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            status = Path(temporary) / 'status.json'
            with patch.dict(os.environ, {'MIO_STATUS_FILE': str(status), 'MIO_INSTALL_PID': '1234',
                                         'MIO_INSTALL_ID': 'launch-id', 'MIO_INSTALL_STARTED_TICKS': '98765',
                                         'MIO_INSTALL_STARTED_AT': '2026-10-11T12:00:00+08:00'}):
                spec = importlib.util.spec_from_file_location('download_status_fixture', COMMON.with_name('download-from-modelscope.py'))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.write_status(percent=30, message='downloading')
            payload = json.loads(status.read_text(encoding='utf-8'))
            self.assertEqual(payload['console_pid'], 1234)
            self.assertEqual(payload['install_id'], 'launch-id')
            self.assertEqual(payload['console_started_ticks'], '98765')


if __name__ == '__main__':
    unittest.main()
