"""CPU-only native voice selection using owned synthetic worker processes."""
import json
import os
from pathlib import Path
import socket
import sys
import threading
import tempfile
import time
from unittest.mock import patch
import pytest
os.environ.setdefault('MIO_DISABLE_DOTENV', '1')
os.environ.setdefault('MIO_RUNTIME_ROOT', tempfile.mkdtemp(prefix='mio-native-test-'))
from app import local_voice_engines as engines, companion_service as companion, mio_v2_tts_service as native

def wait(predicate):
 deadline=time.monotonic()+8
 while time.monotonic()<deadline:
  if predicate():return
  time.sleep(.03)
 raise AssertionError('Expected owned lifecycle transition')

@pytest.fixture
def installed(tmp_path,monkeypatch):
 script=tmp_path/'synthetic.py'
 script.write_text("import sys,json,argparse\nfrom http.server import BaseHTTPRequestHandler,HTTPServer\np=argparse.ArgumentParser();p.add_argument('--port',type=int);p.add_argument('--voice-profile');a,_=p.parse_known_args()\nclass H(BaseHTTPRequestHandler):\n def log_message(self,*args):pass\n def do_GET(self):\n  b=json.dumps({'ready':True,'service':'mio-v2-native','engine':'mio_v2','version':'v2','voice_profile':a.voice_profile}).encode();self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)\nHTTPServer(('127.0.0.1',a.port),H).serve_forever()\n",'utf-8')
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 profiles={}
 for ident in ('first','second'):
  values={}
  for key,suffix in [('gpt_sovits_gpt_weights','.ckpt'),('gpt_sovits_sovits_weights','.pth'),('gpt_sovits_ref_audio','.wav')]:
   path=tmp_path/(ident+suffix);path.write_bytes(b'not-loaded-by-synthetic-worker');values[key]=str(path)
  profiles[ident]={'engine':'gpt_sovits','name':ident,'gpt_sovits_prompt_text':'你好。','gpt_sovits_prompt_language':'zh',**values}
 config={'voice_engine':'gpt_sovits','local_voice_runtime':'mio_v2','default_voice_profile_id':'first','voice_profiles':profiles}
 entry={'python':sys.executable,'script':str(script),'cwd':str(tmp_path),'url':f'http://127.0.0.1:{port}','args':['--port',str(port),'--reference-text','old reference']}
 manifest=tmp_path/'engines.json';manifest.write_text(json.dumps({'schema':1,'engines':{'mio_v2':entry}}),'utf-8')
 monkeypatch.setenv('MIO_VOICE_ENGINES_MANIFEST',str(manifest));monkeypatch.setattr(engines,'resource_check',lambda _:None)
 return config,manifest

def test_stopping_voice_invalidates_queued_utterances_before_they_can_restart_models():
 from unittest.mock import Mock
 old=companion._speech_generation
 worker=Mock()
 with patch.object(companion,'_speak_wav_worker_impl',worker):
  companion.cancel_speech_playback()
  result={}
  companion._speak_wav_worker('排队中的旧回复',old,'synthetic',result)
  worker.assert_not_called()
  assert result['canceled']


def test_launch_uses_selected_profile_without_rewriting_manifest(installed):
 config,manifest=installed;original=manifest.read_bytes()
 entry=engines.load_entry('mio_v2',config)
 args=entry['args'];p=config['voice_profiles']['first']
 assert args[args.index('--gpt')+1]==str(Path(p['gpt_sovits_gpt_weights']).resolve())
 assert args[args.index('--reference-text')+1]=='你好。'
 assert args[args.index('--voice-profile')+1]=='first'
 assert manifest.read_bytes()==original

def test_same_runtime_changes_actual_owned_model_and_can_return_to_previous(installed):
 config,_=installed;manager=engines.LocalVoiceManager();manager.selection_reader=lambda:config
 try:
  manager.start('mio_v2',config);first=manager.child
  wait(lambda:not manager.status('mio_v2',config)['service_loading'])
  assert manager.last_successful_profile_id=='first'
  manager.start('mio_v2',config);assert manager.child is first
  config['default_voice_profile_id']='second';manager.start('mio_v2',config)
  assert manager.child is not first and first.poll() is not None
  wait(lambda:not manager.status('mio_v2',config)['service_loading'])
  assert manager.last_successful_profile_id=='second'
  second=manager.child;config['default_voice_profile_id']='first';manager.start('mio_v2',config)
  assert second.poll() is not None
 finally:manager.stop_all()

def test_invalid_new_files_leave_old_model_and_all_profile_configs_intact(installed):
 config,_=installed;manager=engines.LocalVoiceManager();manager.selection_reader=lambda:config
 try:
  manager.start('mio_v2',config);first=manager.child
  config['default_voice_profile_id']='second';config['voice_profiles']['second']['gpt_sovits_gpt_weights']='Z:/missing.ckpt'
  before=json.dumps(config,sort_keys=True)
  with pytest.raises(OSError,match='文件不可用'):manager.start('mio_v2',config)
  assert manager.child is first and first.poll() is None
  assert json.dumps(config,sort_keys=True)==before
 finally:manager.stop_all()

def test_late_snapshot_cannot_displace_new_profile_or_running_voice(installed):
 config,_=installed;manager=engines.LocalVoiceManager();manager.selection_reader=lambda:config
 stale=engines.selected_native_profile(config)
 config['default_voice_profile_id']='second'
 with pytest.raises(OSError,match='已切换'):manager.ensure_mio_v2(stale)
 assert manager.child is None
 try:
  manager.start('mio_v2',config);child=manager.child
  config['default_voice_profile_id']='first'
  with pytest.raises(OSError,match='显式启动'):manager.ensure_mio_v2(config)
  assert manager.child is child
 finally:manager.stop_all()

def test_midstream_profile_change_closes_old_iterator():
 config=companion.load_normalized_config({'local_voice_runtime':'mio_v2','voice_engine':'gpt_sovits'})
 selected=engines.selected_native_profile(config);closed=threading.Event()
 def chunks(*args,**kwargs):
  try:
   yield b'\x20\x00'*100
   config['gpt_sovits_url']='http://127.0.0.1:11111'
   yield b'\x20\x00'*100
  finally:closed.set()
 with patch.object(companion,'load_config',side_effect=lambda:dict(config)),patch.object(companion,'_prepare_mio_v2_input',return_value=('你好。',selected,'zh')),patch.object(native,'iter_speech_pcm',side_effect=chunks):
  stream=companion.iter_speech_wav_stream('你好。',config_snapshot=config,cancel_event=threading.Event())
  next(stream);assert next(stream)
  with pytest.raises(OSError,match='已切换'):next(stream)
 assert closed.is_set()

@pytest.mark.parametrize('runtime', ['mio_v2', 'gpt_sovits'])
def test_windows_streaming_uses_selected_output_device_without_loading_models(runtime):
 from unittest.mock import Mock
 from app import audio_output_devices, tts_http_audio
 config={'local_voice_runtime':runtime,'voice_volume':85}
 winmm=Mock();winmm.waveOutOpen.return_value=0
 with patch.object(companion.ctypes,'WinDLL',return_value=winmm), \
      patch.object(audio_output_devices,'resolve_output_device',return_value=7), \
      patch.object(companion,'_wave_out_play_chunk',return_value=True), \
      patch.object(companion,'_prepare_mio_v2_input',return_value=('你好。',config,'zh')), \
      patch.object(native,'iter_speech_pcm',side_effect=lambda *a,**k:(item for item in [b'\x10\x00'*100])), \
      patch.object(companion,'_gpt_sovits_request_payload',return_value=('http://127.0.0.1:1',{})), \
      patch.object(companion,'_apply_gpt_sovits_weights'), \
      patch.object(companion.httpx,'Client') as client:
  response=client.return_value.__enter__.return_value.stream.return_value.__enter__.return_value
  response.is_success=True
  response.iter_bytes.return_value=[tts_http_audio.streaming_header()+b'\x10\x00'*100]
  assert companion._play_gpt_sovits_stream('你好。',config,companion._speech_generation)
 assert winmm.waveOutOpen.call_args.args[1]==7
