"""Real PS5.1/curl pause, resume and cancellation against an owned Range server."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell required')
class DownloadControlTests(unittest.TestCase):
    def test_pause_cancel_and_resume_preserves_pinned_partial(self):
        payload = bytes(range(256)) * (24 * 1024)
        requested_offsets = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                offset = int(self.headers.get('Range', 'bytes=0-').split('=')[1].split('-')[0])
                requested_offsets.append(offset)
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length', str(len(payload) - offset))
                self.send_header('Accept-Ranges', 'bytes')
                if offset:
                    self.send_header('Content-Range', f'bytes {offset}-{len(payload)-1}/{len(payload)}')
                self.end_headers()
                try:
                    for pos in range(offset, len(payload), 16384):
                        self.wfile.write(payload[pos:pos+16384]); self.wfile.flush()
                        time.sleep(.025)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)

        with tempfile.TemporaryDirectory(prefix='mio-download-') as temporary:
            root = Path(temporary)
            common = Path(__file__).resolve().parents[1] / 'scripts/deps/common.ps1'
            script = root/'download.ps1'
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            script.write_text(f". {quote(common)}\ntry {{ Invoke-Download -Urls @('http://127.0.0.1:{server.server_port}/file') -Output {quote(root/'model.zip')} -ExpectedSize {len(payload)} -ExpectedSha256 '{hashlib.sha256(payload).hexdigest()}' }} catch {{ Write-DepsFail -Message $_.Exception.Message; exit 1 }}\n", encoding='utf-8-sig')
            status = root/'status.json'; control = root/'control.json'
            env = dict(os.environ, MIO_STATUS_FILE=str(status), MIO_DOWNLOAD_CONTROL_FILE=str(control), MIO_DEP_ID='synthetic')
            children = []
            with (root/'download.log').open('wb') as log:
                def start(identity):
                    process = subprocess.Popen(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                        env=dict(env, MIO_INSTALL_ID=identity), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    children.append(process)
                    return process

                def state():
                    try:
                        return json.loads(status.read_text(encoding='utf-8-sig'))
                    except (OSError, ValueError):
                        return {}

                def wait(predicate, timeout=25):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        if predicate():
                            return
                        time.sleep(.05)
                    raise AssertionError(f'download did not reach expected state: {state()}')

                def command(identity, action):
                    temp = control.with_suffix('.tmp')
                    temp.write_text(json.dumps({'install_id': identity, 'action': action}), encoding='utf-8')
                    temp.replace(control)

                try:
                    process = start('first')
                    wait(lambda: state().get('can_pause') and state().get('downloaded_bytes', 0) > 0)
                    command('stale', 'cancel')
                    before = state()['downloaded_bytes']
                    wait(lambda: state().get('downloaded_bytes', 0) > before)
                    command('first', 'pause')
                    wait(lambda: state().get('stage') == 'paused')
                    partial = next(root.glob('*.part'))
                    paused = partial.stat().st_size
                    time.sleep(.7)
                    self.assertEqual(partial.stat().st_size, paused)
                    self.assertIsNone(process.poll())
                    self.assertTrue(state()['can_resume'])
                    command('first', 'resume')
                    wait(lambda: state().get('stage') == 'downloading' and state().get('downloaded_bytes', 0) > paused)
                    command('first', 'cancel')
                    self.assertNotEqual(process.wait(timeout=10), 0)
                    self.assertEqual(state()['stage'], 'cancelled')
                    self.assertTrue(partial.exists())
                    self.assertFalse((root/'model.zip').exists())
                    # The stale cancel must not cancel the next install, which resumes the same source.
                    process = start('second')
                    self.assertEqual(process.wait(timeout=30), 0)
                    self.assertEqual(hashlib.sha256((root/'model.zip').read_bytes()).digest(), hashlib.sha256(payload).digest())
                    self.assertGreaterEqual(sum(value > 0 for value in requested_offsets), 2)
                    self.assertFalse(state()['can_pause'])
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.terminate(); child.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
