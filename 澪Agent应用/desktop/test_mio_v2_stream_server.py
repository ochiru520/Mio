"""CPU HTTP checks for native V2 streaming, cancellation and model identity."""
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location("mio_native_bridge", Path(__file__).resolve().parents[1] / "scripts/mio_v2_stream_server.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.closed = [], threading.Event()
        def generate(segments, language, cancelled):
            self.calls.append((segments, language))
            try:
                yield b"\x01\x00" * 32
                yield b"\x02\x00" * 16
            finally:
                self.closed.set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.handler_for(generate, metadata={"active_weights": {"gpt": "original.ckpt", "sovits": "original.pth"}}))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method, path, payload=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        conn.request(method, path, data, headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def session(self):
        status, _, data = self.request("POST", "/v1/sessions", {"model": bridge.MODEL, "voice_profile": bridge.VOICE_PROFILE})
        self.assertEqual(status, 200)
        return json.loads(data)["id"]

    def ready_session(self, text="こんにちは。", language="ja"):
        key = self.session()
        self.assertEqual(self.request("POST", f"/v1/sessions/{key}/segments", {"segment_id": "one", "text": text, "language": language})[0], 200)
        self.assertEqual(self.request("POST", f"/v1/sessions/{key}/flush", {})[0], 200)
        return key

    def test_health_identity_and_recognized_stream_settings(self):
        status, _, data = self.request("GET", "/health")
        health = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(health["engine"], "mio_v2")
        self.assertEqual(health["version"], "v2")
        self.assertEqual(health["generation"]["min_chunk_length"], 32)
        self.assertTrue(health["generation"]["streaming_mode"])
        self.assertEqual(health["active_weights"]["gpt"], "original.ckpt")

    def test_japanese_stream_is_pcm_and_session_single_use(self):
        key = self.ready_session()
        status, headers, data = self.request("GET", f"/v1/sessions/{key}/audio")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-TTS-Sample-Rate"], "32000")
        self.assertEqual(headers["X-TTS-Sample-Format"], "s16le")
        self.assertEqual(data, b"\x01\x00" * 32 + b"\x02\x00" * 16)
        self.assertEqual(self.calls, [(("こんにちは。",), "ja")])
        self.assertTrue(self.closed.wait(1))
        self.assertEqual(self.request("GET", f"/v1/sessions/{key}/audio")[0], 404)

    def test_cancel_before_generation_and_identity_mismatch(self):
        key = self.ready_session()
        self.assertEqual(self.request("POST", f"/v1/sessions/{key}/cancel", {})[0], 200)
        self.assertEqual(self.request("GET", f"/v1/sessions/{key}/audio")[0], 404)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.request("POST", "/v1/sessions", {"model": "v2proplus"})[0], 400)

    def test_content_bound_and_diagnostics_do_not_echo_input(self):
        key = self.session()
        secret = "private-request-text"
        status, _, data = self.request("POST", f"/v1/sessions/{key}/segments", {"segment_id": "one", "text": secret * 601, "language": "ja"})
        self.assertEqual(status, 400)
        self.assertNotIn(secret.encode(), data)
        self.assertEqual(self.calls, [])
        for _ in range(bridge.Sessions.MAX_SESSIONS - 1):
            self.session()
        self.assertEqual(self.request("POST", "/v1/sessions", {})[0], 400)

    def test_direct_tts_uses_same_stream(self):
        status, _, data = self.request("POST", "/tts", {"text": "你好。", "text_lang": "zh"})
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 96)
        self.assertEqual(self.calls[0], (("你好。",), "zh"))

    def test_cancel_during_generation_closes_generator(self):
        first_started, release = threading.Event(), threading.Event()
        cancelled_seen = threading.Event()
        def generate(segments, language, cancelled):
            try:
                first_started.set()
                release.wait(2)
                if cancelled.is_set():
                    cancelled_seen.set()
                    return
                yield b"\x01\x00" * 10
            finally:
                self.closed.set()
        self.server.RequestHandlerClass = bridge.handler_for(generate)
        key = self.ready_session()
        result = []
        reader = threading.Thread(target=lambda: result.append(self.request("GET", f"/v1/sessions/{key}/audio")))
        reader.start()
        self.assertTrue(first_started.wait(1))
        self.request("POST", f"/v1/sessions/{key}/cancel", {})
        release.set()
        reader.join(2)
        self.assertTrue(cancelled_seen.is_set())
        self.assertTrue(self.closed.is_set())
        self.assertNotEqual(result[0][0], 200)

    def test_payload_uses_approved_parameters_without_mutating_defaults(self):
        payload = bridge.synthesis_payload("你好。", "zh", "mio.wav", "参考", "ja")
        self.assertEqual(payload["top_k"], 10)
        self.assertEqual(payload["temperature"], .68)
        self.assertFalse(payload["parallel_infer"])
        self.assertEqual(payload["seed"], 6101)
        self.assertEqual(payload["prompt_lang"], "ja")
        self.assertNotIn("text", bridge.SETTINGS)

    def test_native_inference_is_serial_even_with_two_http_clients(self):
        entered, release = threading.Event(), threading.Event()
        active, peak = [0], [0]
        calls = []
        def generate(segments, language, cancelled):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            calls.append(segments)
            try:
                if len(calls) == 1:
                    entered.set()
                    release.wait(2)
                yield b"\x01\x00" * 10
            finally:
                active[0] -= 1
        self.server.RequestHandlerClass = bridge.handler_for(generate)
        first, second = self.ready_session("一。", "zh"), self.ready_session("二。", "zh")
        results = []
        readers = [threading.Thread(target=lambda key=key: results.append(self.request("GET", f"/v1/sessions/{key}/audio"))) for key in (first, second)]
        readers[0].start()
        self.assertTrue(entered.wait(1))
        readers[1].start()
        # Request two is already allowed to run; the native call remains locked.
        self.assertEqual(self.request("GET", "/health")[0], 200)
        release.set()
        for reader in readers:
            reader.join(2)
        self.assertEqual(peak[0], 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item[0] == 200 for item in results))

    def test_warmup_consumes_both_generators_and_does_not_retain_audio(self):
        entered, closed = [], []
        def generate(segments, language, cancelled):
            entered.append((segments, language, cancelled.is_set()))
            try:
                yield b"\x01\x00" * 10
                yield b"\x02\x00" * 5
            finally:
                closed.append(language)
        result = bridge.warm_voice(SimpleNamespace(generate=generate))
        self.assertEqual(entered, [(("你好。",), "zh", False), (("こんにちは。",), "ja", False)])
        self.assertEqual(closed, ["zh", "ja"])
        self.assertEqual(result["warmup_languages"], ["zh", "ja"])
        self.assertGreaterEqual(result["warmup_seconds"], 0)
        self.assertEqual(set(result), {"warmup_seconds", "warmup_languages"})

    def test_warmup_failure_closes_generator_and_prevents_readiness(self):
        closed = []
        def generate(*_args):
            try:
                yield b"odd"
            finally:
                closed.append(True)
        with self.assertRaises(RuntimeError):
            bridge.warm_voice(SimpleNamespace(generate=generate))
        self.assertEqual(closed, [True])


class VersionMetadataTests(unittest.TestCase):
    def valid(self):
        return ({'config': {'version': 'v2', 'model': {'version': 'v2'}}, 'weight': {'enc_p.text_embedding.weight': SimpleNamespace(shape=(732,192))}}, {'weight': {'model.ar_text_embedding.word_embeddings.weight': SimpleNamespace(shape=(732,512))}, 'config': {}})

    def test_v2_shape_and_version_accepted_without_model_loading(self):
        sovits,gpt=self.valid()
        bridge.validate_v2_metadata(sovits,gpt)

    def test_other_model_generations_and_structures_rejected(self):
        for version in ('v1','v2Pro','v2ProPlus','v3','v4','v5','v5turbo'):
            with self.subTest(version=version):
                sovits,gpt=self.valid();sovits['config']['model']['version']=version
                with self.assertRaises(ValueError):bridge.validate_v2_metadata(sovits,gpt)
        sovits,gpt=self.valid();sovits['weight']['enc_p.text_embedding.weight'].shape=(322,192)
        with self.assertRaises(ValueError):bridge.validate_v2_metadata(sovits,gpt)
        sovits,gpt=self.valid();sovits['weight']['sv_emb.weight']=SimpleNamespace(shape=(1,1))
        with self.assertRaises(ValueError):bridge.validate_v2_metadata(sovits,gpt)

    def test_invalid_semantic_weight_rejected(self):
        sovits,gpt=self.valid();gpt['weight']={}
        with self.assertRaises(ValueError):bridge.validate_v2_metadata(sovits,gpt)


if __name__ == "__main__":
    unittest.main()
