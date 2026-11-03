from __future__ import annotations

import importlib.util
import asyncio
import base64
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import unittest
from unittest import mock


WORKER = Path(__file__).with_name("genie_tts_worker.py")
SPEC = importlib.util.spec_from_file_location("mio_genie_tts_worker", WORKER)
assert SPEC is not None and SPEC.loader is not None
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class GenieWorkerTextTests(unittest.TestCase):
    def test_chinese_g2p_text_removes_unsupported_symbols(self) -> None:
        cleaned = worker._safe_g2p_text("你好\u200b🙂〔测试〕", "Chinese", fallback="你好。")
        self.assertEqual(cleaned, "你好测试")

    def test_punctuation_only_text_uses_language_fallback(self) -> None:
        self.assertEqual(worker._safe_g2p_text("……！！！", "Chinese", fallback="你好。"), "你好。")
        self.assertEqual(worker._safe_g2p_text("……", "Japanese", fallback="こんにちは。"), "こんにちは。")

    def test_japanese_text_is_preserved(self) -> None:
        text = "つまらないものですが、ありがとうございます。"
        self.assertEqual(worker._safe_g2p_text(text, "Japanese", fallback="こんにちは。"), text)

    def test_reference_prompt_g2p_failure_retries_with_safe_text(self) -> None:
        genie = mock.Mock()
        genie.set_reference_audio.side_effect = [IndexError("tone"), None]

        retried = worker._set_reference_audio_safely(
            genie,
            "mio",
            Path("reference.wav"),
            "异常提示文本",
            "Chinese",
            "你好。",
        )

        self.assertTrue(retried)
        self.assertEqual(genie.set_reference_audio.call_count, 2)
        self.assertEqual(genie.set_reference_audio.call_args_list[-1].args[-2:], ("你好。", "Chinese"))


class GenieWorkerPcmStreamTests(unittest.TestCase):
    def test_sentence_pcm_is_emitted_before_next_sentence_is_generated(self):
        events=[]
        class Genie:
            async def tts_async(self,character,text,**kwargs):
                self.options=kwargs
                yield b'\x01\x00'*10000
                self.first_published=any(event['event']=='audio' for event in events)
                yield b'\x02\x00'*100
        genie=Genie()
        final=asyncio.run(worker._stream_pcm(genie,'mio','第一句。第二句。',events.append))
        self.assertTrue(genie.first_published)
        self.assertEqual(genie.options,{'play':False,'split_sentence':True})
        self.assertEqual(events[0],{'ok':True,'event':'start','sample_rate':32000,'channels':1,'sample_width':2})
        pcm=b''.join(base64.b64decode(e['pcm_base64']) for e in events if e['event']=='audio')
        self.assertEqual(pcm,b'\x01\x00'*10000+b'\x02\x00'*100)
        self.assertTrue(all(len(base64.b64decode(e['pcm_base64']))<=16384 for e in events if e['event']=='audio'))
        self.assertEqual(final['frames'],10100)
        self.assertEqual(final['event'],'done')
        self.assertIsNotNone(final['first_audio_ms'])

    def test_empty_chunks_do_not_count_as_first_audio(self):
        events=[]
        class Genie:
            async def tts_async(self,*args,**kwargs):
                yield b''
                yield b'\x01\x00'
        final=asyncio.run(worker._stream_pcm(Genie(),'mio','文本',events.append))
        self.assertEqual([e['event'] for e in events],['start','audio'])
        self.assertEqual(final['frames'],1)

    def test_empty_or_odd_pcm_fails_instead_of_emitting_a_successful_done(self):
        for pcm in (b'',b'\x01'):
            with self.subTest(pcm=pcm):
                class Genie:
                    async def tts_async(self,*args,**kwargs):yield pcm
                with self.assertRaises(OSError):asyncio.run(worker._stream_pcm(Genie(),'mio','文本',lambda event:None))

    def test_real_worker_process_streams_protocol_stdout_before_synthesis_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);package=root/'genie_tts';package.mkdir()
            (package/'__init__.py').write_text('''
import asyncio
from pathlib import Path
def load_character(*args): print('library diagnostic output')
def set_reference_audio(*args): pass
def stop(): pass
async def tts_async(*args,**kwargs):
    print('synthesis diagnostic output')
    yield b'\\x01\\x00'*2
    while not Path('release').exists(): await asyncio.sleep(.01)
    yield b'\\x02\\x00'*2
''',encoding='utf-8')
            (package/'ModelManager.py').write_text('from types import SimpleNamespace\nmodel_manager=SimpleNamespace(character_to_language={})\n',encoding='utf-8')
            reference=root/'reference.wav';reference.write_bytes(b'test reference')
            env=dict(os.environ,PYTHONPATH=str(root),MIO_GENIE_DATA_DIR=str(root),PYTHONUTF8='1')
            process=subprocess.Popen([sys.executable,'-u',str(WORKER)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',env=env,cwd=root,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            events=queue.Queue()
            def read():
                for line in process.stdout:events.put(json.loads(line))
            reader=threading.Thread(target=read,daemon=True);reader.start()
            try:
                request={'action':'stream','request_id':'wire-test','model_dir':str(root),'reference_audio':str(reference),'prompt_text':'参考文本','text':'第一句。第二句。'}
                process.stdin.write(json.dumps(request)+'\n');process.stdin.flush()
                start=events.get(timeout=3);first=events.get(timeout=3)
                self.assertEqual(start['event'],'start')
                self.assertEqual(first['event'],'audio')
                self.assertEqual(first['request_id'],'wire-test')
                self.assertFalse((root/'release').exists())
                self.assertEqual(base64.b64decode(first['pcm_base64']),b'\x01\x00'*2)
                (root/'release').write_text('release')
                self.assertEqual(events.get(timeout=3)['event'],'audio')
                final=events.get(timeout=3)
                self.assertEqual(final['event'],'done');self.assertEqual(final['frames'],4)
                process.stdin.write('{"action":"shutdown"}\n');process.stdin.flush()
                process.wait(timeout=3)
                self.assertEqual(process.returncode,0)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=3)
                reader.join(timeout=1)
                for stream in (process.stdin,process.stdout,process.stderr):stream.close()


if __name__ == "__main__":
    unittest.main()
