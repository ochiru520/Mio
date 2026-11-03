"""Exercise cancellation over real localhost sockets without a model or microphone."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from app import aniflive_tts_service


def test_cancel_before_first_pcm_releases_only_owned_session():
    waiting_for_audio = threading.Event()
    release_audio = threading.Event()
    cancelled = threading.Event()
    paths = []
    errors = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            paths.append(self.path)
            body = json.dumps({'id': 'mio-owned'} if self.path == '/v1/sessions' else {}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if self.path == '/v1/sessions/mio-owned/cancel':
                release_audio.set()

        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            for key, value in {
                'Content-Type': 'application/octet-stream',
                'Transfer-Encoding': 'chunked',
                'X-TTS-Sample-Format': 's16le',
                'X-TTS-Sample-Rate': '32000',
                'X-TTS-Channels': '1',
            }.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.flush()
            waiting_for_audio.set()
            release_audio.wait(6)
            try:
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
            except OSError:
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()

    def consume():
        try:
            list(aniflive_tts_service.iter_speech_pcm(
                '这是一条合成协议测试。',
                {'aniflive_url': f'http://127.0.0.1:{server.server_port}'},
                cancel_event=cancelled,
            ))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=consume, daemon=True)
    worker.start()
    try:
        assert waiting_for_audio.wait(4), 'request must reach audio headers'
        cancelled.set()
        worker.join(5)
        assert not worker.is_alive(), 'cancel must release a request before its first PCM'
        assert errors and isinstance(errors[0], InterruptedError)
        assert '/v1/sessions/mio-owned/cancel' in paths
        assert all(path.startswith('/v1/sessions') for path in paths)
    finally:
        cancelled.set()
        release_audio.set()
        worker.join(6)
        server.shutdown()
        server.server_close()
        serving.join(2)
