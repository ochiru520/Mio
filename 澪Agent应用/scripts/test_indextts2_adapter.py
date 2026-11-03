from __future__ import annotations

import base64
from contextlib import nullcontext
from io import BytesIO
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave

import indextts2_adapter as adapter


def wav_bytes():
    memory = BytesIO()
    with wave.open(memory, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(b'\x01\x00' * 2205)
    return memory.getvalue()


class Model:
    def __init__(self): self.calls = []
    def infer(self, **options):
        self.calls.append(options)
        Path(options['output_path']).write_bytes(wav_bytes())


class AdapterVersionTests(unittest.TestCase):
    def payload(self, **options):
        return {'protocol': adapter.PROTOCOL, 'text':'公开测试文本。', 'reference_name':'synthetic.wav',
                'reference_audio_base64':base64.b64encode(wav_bytes()).decode(), **options}

    def test_25_language_is_passed_and_version_mismatch_does_not_infer(self):
        model = Model()
        self.assertEqual(adapter.synthesize(model, self.payload(language='ja',model_version='2.5'),model_version='2.5'),wav_bytes())
        self.assertEqual(model.calls[0]['lang'],'JA')
        self.assertFalse(Path(model.calls[0]['spk_audio_prompt']).exists())
        with self.assertRaisesRegex(ValueError,'当前服务加载'):
            adapter.synthesize(model,self.payload(model_version='2'),model_version='2.5')
        with self.assertRaisesRegex(ValueError,'支持中文'):
            adapter.synthesize(model,self.payload(language='xx'),model_version='2.5')
        self.assertEqual(len(model.calls),1)

    def test_legacy_2_does_not_get_incompatible_language_argument(self):
        model = Model()
        adapter.synthesize(model,self.payload())
        self.assertNotIn('lang',model.calls[0])

    def test_http_health_reports_actual_family_and_mismatch_is_400(self):
        model = Model()
        server = adapter.HTTPServer(('127.0.0.1',0),adapter.handler_for(model,model_version='2.5'))
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        address=f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(address+'/health',timeout=2) as response:
                status=json.load(response)
            self.assertEqual(status['model_version'],'2.5')
            self.assertEqual(status['streaming'],'segment')
            request=urllib.request.Request(address+'/synthesize',data=json.dumps(self.payload(version='2')).encode(),headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request,timeout=2)
            self.assertEqual(error.exception.code,400)
            self.assertEqual(model.calls,[])
        finally:
            server.shutdown();server.server_close();thread.join(2)

    def test_low_memory_startup_failure_restores_global_hooks_and_dtype(self):
        state = {'dtype':'float32'}
        def allocation_failure(*_args, **_kwargs):
            self.assertEqual(state['dtype'],'bfloat16')
            raise RuntimeError('allocation failed')
        original_load = lambda *_args, **_kwargs: None
        fake_torch = SimpleNamespace(load=original_load, cuda=SimpleNamespace(is_available=lambda:True),
            bfloat16='bfloat16', get_default_dtype=lambda:state['dtype'],
            set_default_dtype=lambda value:state.update(dtype=value), device=lambda _name:nullcontext())
        engine = SimpleNamespace(UnifiedVoice=allocation_failure)
        engine.IndexTTS2 = lambda **_options: engine.UnifiedVoice()
        with patch.dict('sys.modules',{'torch':fake_torch,'indextts':SimpleNamespace(infer_v2_5=engine)}):
            with self.assertRaisesRegex(RuntimeError,'allocation failed'):
                adapter.load_index25(Path('unused'),bf16=True,low_memory=True)
        self.assertIs(fake_torch.load,original_load)
        self.assertIs(engine.UnifiedVoice,allocation_failure)
        self.assertEqual(state['dtype'],'float32')


if __name__ == '__main__': unittest.main()
