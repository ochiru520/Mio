from __future__ import annotations

from io import BytesIO
import contextlib
import hashlib
import json
import tempfile
import threading
import time
import sys
import unittest
from pathlib import Path
from unittest import mock
import wave

from app import genie_tts_service


def _wav_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(32000)
        stream.writeframes(b"\x00\x00" * 3200)
    return output.getvalue()


class GenieTtsServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        genie_tts_service.stop_worker()
        genie_tts_service._file_digest.cache_clear()

    def test_existing_character_is_reused_by_weight_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            models = root / "models" / "genie"
            character = models / "named-character"
            character.mkdir(parents=True)
            for name in genie_tts_service.REQUIRED_CHARACTER_FILES:
                (character / name).write_bytes(b"model")
            gpt = root / "voice.ckpt"
            sovits = root / "voice.pth"
            gpt.write_bytes(b"gpt")
            sovits.write_bytes(b"sovits")
            (character / "mio-genie-v2.json").write_text(json.dumps({
                "gpt_sha256": hashlib.sha256(b"gpt").hexdigest(),
                "sovits_sha256": hashlib.sha256(b"sovits").hexdigest(),
            }), encoding="utf-8")
            paths = {
                "root": root,
                "models": models,
                "default_model": models / "default-v2",
            }

            with mock.patch("app.genie_tts_service._paths", return_value=paths):
                selected = genie_tts_service._resolve_character_model({
                    "gpt_sovits_gpt_weights": str(gpt),
                    "gpt_sovits_sovits_weights": str(sovits),
                })

            self.assertEqual(selected, character)

    def test_runtime_status_reports_the_actual_mio_onnx_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "models" / "genie" / "mio-v1"
            model.mkdir(parents=True)
            for name in genie_tts_service.REQUIRED_CHARACTER_FILES:
                (model / name).write_bytes(b"model")
            paths = {
                "python": root / ".genie-env" / "Scripts" / "python.exe",
                "site_packages": root / ".genie-env" / "Lib" / "site-packages",
                "data": root / "GenieData",
                "models": root / "models" / "genie",
                "mio_model": model,
                "worker": root / "genie_tts_worker.py",
            }
            with mock.patch.object(genie_tts_service, "_paths", return_value=paths):
                status = genie_tts_service.runtime_status()

        self.assertTrue(status["model_ready"])
        self.assertEqual(status["model_dir"], str(model))
        self.assertIn("Mio", status["model_source"])

    def test_synthesize_writes_and_validates_standard_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(_wav_bytes())
            character = root / "character"
            character.mkdir()
            for name in genie_tts_service.REQUIRED_CHARACTER_FILES:
                (character / name).write_bytes(b"model")

            def fake_request(payload, *, timeout, idle_seconds):
                del timeout, idle_seconds
                Path(payload["output_path"]).write_bytes(_wav_bytes())
                return {"ok": True, "first_audio_ms": 120.0, "total_ms": 300.0, "duration_seconds": 0.1}

            with (
                mock.patch("app.genie_tts_service._resolve_character_model", return_value=character),
                mock.patch("app.genie_tts_service._request", side_effect=fake_request),
            ):
                content = genie_tts_service.synthesize_wav("你好", {
                    "gpt_sovits_ref_audio": str(reference),
                    "gpt_sovits_prompt_text": "参考文本",
                    "gpt_sovits_prompt_language": "zh",
                    "gpt_sovits_text_language": "zh",
                })

            self.assertTrue(content.startswith(b"RIFF"))
            self.assertEqual(genie_tts_service.runtime_status()["last_metrics"]["first_audio_ms"], 120.0)

    def test_idle_worker_schedules_memory_reclamation(self) -> None:
        timer = mock.Mock()
        with mock.patch("app.genie_tts_service.threading.Timer", return_value=timer) as timer_type:
            genie_tts_service._schedule_idle_shutdown()

        timer_type.assert_called_once_with(
            genie_tts_service.GENIE_IDLE_SECONDS,
            genie_tts_service._stop_if_idle,
        )
        timer.start.assert_called_once_with()


FAKE_STREAM_WORKER = r'''
import base64, json, sys, time
from pathlib import Path
for line in sys.stdin:
    request=json.loads(line)
    request_id=request.get('request_id')
    def emit(event):
        print(json.dumps(dict(event, request_id=request_id)),flush=True)
    action=request['action']
    if action=='shutdown':
        break
    if action=='probe':
        emit({'ok':True})
        continue
    text=request.get('text','')
    print(json.dumps({'ok':True,'event':'audio','request_id':'stale-request','pcm_base64':'////'}),flush=True)
    emit({'ok':True,'event':'start','sample_rate':44100 if text=='bad-format' else 32000,'channels':1,'sample_width':2})
    Path('started').write_text('started')
    if text=='hang':
        time.sleep(60)
    if text=='no-audio':
        emit({'ok':True,'event':'done','frames':0})
        continue
    first = b'\x01\x00' * 800
    if text=='bad-pcm':
        first=b'\x01'
    emit({'ok':True,'event':'audio','pcm_base64':base64.b64encode(first).decode()})
    if text=='blocked':
        while not Path('release').exists(): time.sleep(.01)
    if text=='error-after-audio':
        emit({'ok':False,'error':'synthetic generation error'})
        continue
    second=b'\x02\x00' * 800
    emit({'ok':True,'event':'audio','pcm_base64':base64.b64encode(second).decode()})
    emit({'ok':True,'event':'done','frames':999 if text=='bad-count' else 1600,'first_audio_ms':2,'total_ms':5})
'''


class GeniePcmStreamProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.worker=self.root/'worker.py'
        self.worker.write_text(FAKE_STREAM_WORKER,encoding='utf-8')
        reference=self.root/'reference.wav';reference.write_bytes(_wav_bytes())
        self.config={'gpt_sovits_ref_audio':str(reference),'gpt_sovits_prompt_text':'合成测试参考文本','voice_idle_timeout_seconds':0}
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(mock.patch('app.local_voice_engines.manager.selection_reader', return_value={'voice_engine': 'gpt_sovits'}))
        self.stack.enter_context(mock.patch.object(genie_tts_service,'_paths',return_value={'python':Path(sys.executable),'worker':self.worker,'root':self.root,'data':self.root}))
        self.stack.enter_context(mock.patch.object(genie_tts_service,'runtime_status',return_value={'ready':True}))
        self.stack.enter_context(mock.patch.object(genie_tts_service,'_resolve_character_model',return_value=self.root))
        self.processes=[]
        self.real_start=genie_tts_service._start_worker
        def start():
            pair=self.real_start()
            if pair[0] not in self.processes: self.processes.append(pair[0])
            return pair
        self.stack.enter_context(mock.patch.object(genie_tts_service,'_start_worker',side_effect=start))

    def tearDown(self):
        genie_tts_service.stop_worker()
        self.stack.close()
        for process in self.processes:
            if process.poll() is None:
                process.kill();process.wait(timeout=2)
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream is not None:stream.close()
        self.temporary.cleanup()

    def stream(self,text='normal',**kwargs):
        return genie_tts_service.iter_speech_pcm(text,self.config,**kwargs)

    def test_first_pcm_arrives_before_generation_finishes_and_stale_chunks_are_ignored(self):
        stream=self.stream('blocked')
        first=next(stream)
        self.assertEqual(first,b'\x01\x00'*800)
        self.assertFalse((self.root/'release').exists())
        self.assertFalse(genie_tts_service._last_metrics['complete'])
        (self.root/'release').write_text('release')
        self.assertEqual(list(stream),[b'\x02\x00'*800])
        self.assertTrue(genie_tts_service._last_metrics['complete'])
        self.assertEqual(genie_tts_service._last_metrics['received_bytes'],3200)

    def test_close_from_a_different_thread_kills_worker_and_next_request_is_clean(self):
        stream=self.stream('blocked')
        self.assertEqual(next(stream),b'\x01\x00'*800)
        old=genie_tts_service._worker
        thread=threading.Thread(target=stream.close);thread.start();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertIsNotNone(old.poll())
        self.assertIsNone(genie_tts_service._worker)
        self.assertEqual(list(self.stream()),[b'\x01\x00'*800,b'\x02\x00'*800])
        self.assertIsNot(genie_tts_service._worker,old)

    def test_cancel_event_interrupts_wait_before_first_audio(self):
        cancel=threading.Event();stream=self.stream('hang',cancel_event=cancel)
        errors=[]
        def consume():
            try:next(stream)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=consume);thread.start()
        deadline=time.monotonic()+3
        while not (self.root/'started').exists() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue((self.root/'started').exists())
        cancel.set();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0],InterruptedError)
        self.assertIsNone(genie_tts_service._worker)

    def test_timeout_discards_the_worker(self):
        with self.assertRaises(TimeoutError):list(self.stream('hang',timeout=.2))
        self.assertIsNone(genie_tts_service._worker)

    def test_stream_rejects_wrong_format_odd_pcm_empty_audio_and_incomplete_counts(self):
        for text in ('bad-format','bad-pcm','no-audio','bad-count'):
            with self.subTest(text=text):
                with self.assertRaises(OSError):list(self.stream(text))

    def test_generation_error_after_first_pcm_is_not_reported_as_completion(self):
        stream=self.stream('error-after-audio')
        self.assertEqual(next(stream),b'\x01\x00'*800)
        with self.assertRaisesRegex(OSError,'synthetic generation error'):next(stream)
        self.assertIsNone(genie_tts_service._worker)
        self.assertFalse(genie_tts_service._last_metrics['complete'])


if __name__ == "__main__":
    unittest.main()
