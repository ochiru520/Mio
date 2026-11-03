"""No-model HTTP acceptance for original-V2 identity and isolated cancellation."""
from contextlib import contextmanager
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    spec = importlib.util.spec_from_file_location("anif_v2_server_cpu_tests", SCRIPTS / "aniflive_v2_server.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)


def metadata():
    voice = object.__new__(bridge.AnifV2Voice)
    voice.engine = SimpleNamespace(v2_metadata={
        "engine": "aniflive", "runtime": "aniflive_v2", "model_family": "GPT-SoVITS V2",
        "model_version": "GPT-SoVITS V2", "uses_original_finetuned_weights": True,
        "original_mio_weights_loaded": True, "uses_speaker_embedding": False,
        "neural_stage_count": 8, "weight_sha256": {"gpt": "a" * 64, "sovits": "b" * 64},
        "experimental": True, "numerical_verified": False,
    })
    voice.profile, voice.reference, voice.reference_language = "mio", Path("test-reference.wav"), "ja"
    voice.load_seconds, voice.warmup_seconds = 1., .2
    voice.streamer = SimpleNamespace(semantic_runtime=SimpleNamespace(sampling_contract="native-v2proplus-v1"))
    return voice.metadata()


@contextmanager
def serve(generate):
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.handler_for(
        generate, voice_profile="mio", reference_language="ja", reference_kind="user-provided",
        model_id=bridge.MODEL_ID, runtime_metadata=metadata()))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(address, method, path, body=None):
    connection = http.client.HTTPConnection(*address, timeout=3)
    try:
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=data, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw)
    finally:
        connection.close()


def session(address):
    status, value = request(address, "POST", "/v1/sessions",
                            {"model": bridge.MODEL_ID, "voice_profile": "mio"})
    if status != 200:
        raise AssertionError(value)
    prefix = "/v1/sessions/" + value["id"]
    status, _ = request(address, "POST", prefix + "/segments",
                        {"text": "こんにちは。", "language": "ja", "segment_id": "one"})
    if status != 200:
        raise AssertionError("segment rejected")
    status, _ = request(address, "POST", prefix + "/flush", {})
    if status != 200:
        raise AssertionError("flush rejected")
    return prefix


class IdentityTests(unittest.TestCase):
    def test_health_reports_actual_v2_runtime_and_reference(self):
        def generate(*_):
            raise AssertionError("health cannot synthesize")
        with serve(generate) as address:
            status, value = request(address, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(value["model"], "mio-v2-aniflive")
        self.assertEqual(value["model_family"], "GPT-SoVITS V2")
        self.assertEqual(value["runtime"], "aniflive_v2")
        self.assertEqual(value["neural_stage_count"], 8)
        self.assertFalse(value["uses_speaker_embedding"])
        self.assertFalse(value["numerical_verified"])
        self.assertFalse(value["public_synthetic_reference"])
        self.assertEqual(value["reference_language"], "ja")
        self.assertEqual(value["generation"]["first_chunk_tokens"], 17)
        self.assertEqual(value["generation"]["first_lookahead_tokens"], 8)
        self.assertEqual(value["generation"]["minimum_preview_context_tokens"], 25)
        self.assertEqual(value["generation"]["configured_first_context_tokens"], 17)
        self.assertTrue(value["generation"]["preview_target_dynamic"])
        self.assertEqual(value["generation"]["chunk_length"], 32)
        self.assertEqual(value["generation"]["semantic_sampling"], "native-v2proplus-v1")

    def test_pro_model_and_unloaded_profile_rejected_without_generation(self):
        calls = []
        with serve(lambda *args: calls.append(args)) as address:
            for payload in ({"model": "mio-v2proplus-experimental"},
                            {"model": bridge.MODEL_ID, "voice_profile": "another"}):
                with self.subTest(payload=payload):
                    status, _ = request(address, "POST", "/v1/sessions", payload)
                    self.assertEqual(status, 400)
        self.assertEqual(calls, [])

    def test_open_session_cancelled_before_synthesis_never_generates(self):
        calls = []
        with serve(lambda *args: calls.append(args)) as address:
            status, value = request(address, "POST", "/v1/sessions", {"model": bridge.MODEL_ID})
            self.assertEqual(status, 200)
            prefix = "/v1/sessions/" + value["id"]
            self.assertEqual(request(address, "POST", prefix + "/cancel", {})[0], 200)
            self.assertEqual(request(address, "GET", prefix + "/audio")[0], 404)
        self.assertEqual(calls, [])


class CancellationTests(unittest.TestCase):
    def test_active_cancel_drops_late_pcm_closes_generator_and_releases_lock(self):
        closed = threading.Event()
        calls = []
        def generate(_segments, _language, cancelled):
            calls.append(cancelled)
            try:
                yield b"\x01\x00"
                if len(calls) == 1:
                    if not cancelled.wait(2):
                        raise AssertionError("cancel did not reach generator")
                    yield b"\x7f\x7f"  # Must never reach the client.
            finally:
                closed.set()
        with serve(generate) as address:
            prefix = session(address)
            connection = http.client.HTTPConnection(*address, timeout=3)
            try:
                connection.request("GET", prefix + "/audio")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(2), b"\x01\x00")
                self.assertEqual(request(address, "POST", prefix + "/cancel", {})[0], 200)
                with self.assertRaises(http.client.IncompleteRead) as failure:
                    response.read()
                self.assertNotIn(b"\x7f\x7f", failure.exception.partial)
                self.assertTrue(closed.wait(2))
            finally:
                connection.close()
            # A new request gets the same inference lock after cancellation.
            prefix = session(address)
            connection = http.client.HTTPConnection(*address, timeout=3)
            try:
                connection.request("GET", prefix + "/audio")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"\x01\x00")
            finally:
                connection.close()
        self.assertEqual(len(calls), 2)
        self.assertIsNot(calls[0], calls[1])


class GenerateBoundaryTests(unittest.TestCase):
    def voice(self, chunks):
        voice = object.__new__(bridge.AnifV2Voice)
        voice.planner = object()
        voice.streamer = SimpleNamespace(iter_audio=lambda **kwargs: chunks)
        return voice

    def test_already_cancelled_skips_planning_and_streamer(self):
        cancelled = threading.Event(); cancelled.set()
        voice = self.voice(None)
        with patch.object(bridge, "speech_segments") as planning:
            self.assertEqual(list(voice.generate(("你好。",), "zh", cancelled)), [])
            planning.assert_not_called()

    def test_multiple_input_segments_preserved_and_cancel_between_pcm_chunks(self):
        cancelled = threading.Event()
        closed = threading.Event()
        def pieces():
            try:
                yield [0.5, -0.5]
                cancelled.set()
                yield [1.0, 1.0]  # Produced concurrently with stop; must be dropped.
            finally:
                closed.set()
        voice = self.voice(pieces())
        planned = ("第一句。", "第二句。", "第三句。")
        with patch.object(bridge, "speech_segments", return_value=planned) as planning:
            result = list(voice.generate(planned, "zh", cancelled))
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 4)
        self.assertEqual(planning.call_args.args[0], planned)
        self.assertTrue(closed.is_set())

    def test_invalid_direct_inputs_fail_before_planning(self):
        voice = self.voice(None)
        for segments, language in (((), "zh"), (("好" * 601,), "zh"),
                                   (("好",) * 9, "zh"), (("好",), "unknown"),
                                   ((None,), "zh")):
            with self.subTest(segments=segments, language=language):
                with patch.object(bridge, "speech_segments") as planning:
                    with self.assertRaises(ValueError):
                        list(voice.generate(segments, language, threading.Event()))
                    planning.assert_not_called()

    def test_nonfinite_audio_rejected_and_generator_closed(self):
        closed = threading.Event()
        def pieces():
            try:
                yield [float("nan")]
            finally:
                closed.set()
        voice = self.voice(pieces())
        with patch.object(bridge, "speech_segments", return_value=("好",)):
            with self.assertRaises(RuntimeError):
                list(voice.generate(("好",), "zh", threading.Event()))
        self.assertTrue(closed.is_set())

    def test_failed_port_bind_closes_loaded_voice(self):
        ready_metadata = metadata()
        voice = SimpleNamespace(generate=lambda *_: None, metadata=lambda: ready_metadata, close=lambda: None)
        args = SimpleNamespace(port=9882, voice_profile="mio", reference_language="ja")
        with patch.object(bridge, "arguments", return_value=args), \
                patch.object(bridge, "AnifV2Voice", return_value=voice), \
                patch.object(voice, "close") as close, \
                patch.object(bridge, "ThreadingHTTPServer", side_effect=OSError("address in use")):
            with self.assertRaises(OSError):
                bridge.main()
            close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
