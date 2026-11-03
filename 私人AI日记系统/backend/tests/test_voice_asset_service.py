"""Voice migration, identity and cleanup invariants using synthetic assets only."""
from __future__ import annotations

from contextlib import ExitStack
import copy
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import wave

os.environ.setdefault('MIO_DISABLE_DOTENV', '1')
os.environ.setdefault('MIO_RUNTIME_ROOT', tempfile.mkdtemp(prefix='mio-voice-assets-tests-'))

from app import voice_asset_service as assets
from app import model_management_service as resources, companion_service, local_voice_engines, genie_tts_service
from app import dependency_installer, anif_setup_service, mio_v2_tts_service, aniflive_tts_service
from app.call_session_service import manager as calls
_VERIFY_CANDIDATE = assets._verify_candidate


def audio():
    output = BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(32000)
        wav.writeframes(b'\x01\x00' * 3200)
    return output.getvalue()


class VoiceAssetTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='mio-voice-assets-')))
        self.settings = SimpleNamespace(voice_training_dir=self.root/'voice', companion_dir=self.root/'data/companion')
        self.stack.enter_context(patch.object(resources, 'settings', self.settings))
        self.registry = self.root/'engines.json'
        self.stack.enter_context(patch.object(local_voice_engines, 'manifest_path', return_value=self.registry))
        profile = {'name': '自训 A', 'engine': 'gpt_sovits', 'gpt_sovits_prompt_text': 'こんにちは。',
                   'gpt_sovits_prompt_language': 'ja', 'gpt_sovits_text_language': 'auto'}
        for field, name in (('gpt_sovits_gpt_weights','a.ckpt'), ('gpt_sovits_sovits_weights','a.pth'), ('gpt_sovits_ref_audio','a.wav')):
            profile[field] = str(self.file('data/companion/音色权重/a/'+name))
        self.state = {'voice_profiles': {'a': profile, 'b': dict(profile, name='旧音色 B')},
                      'default_voice_profile_id':'b', 'voice_engine':'gpt_sovits', 'local_voice_runtime':'mio_v2',
                      'aniflive_model':'old-model', 'aniflive_voice_profile':'b', 'aniflive_url':'http://127.0.0.1:9883',
                      'unrelated_setting':'keep'}
        self.stack.enter_context(patch.object(companion_service, 'load_config', side_effect=lambda:copy.deepcopy(self.state)))
        self.saved = self.stack.enter_context(patch.object(companion_service, 'save_config', side_effect=self.save))
        self.cancel = self.stack.enter_context(patch.object(companion_service, 'cancel_speech_playback'))
        self.stop_original = self.stack.enter_context(patch.object(companion_service, '_stop_owned_original_voice'))
        self.stop_voice = self.stack.enter_context(patch.object(companion_service, 'stop_voice_service'))
        self.stack.enter_context(patch.object(calls,'status',return_value={'active':False}))
        self.stack.enter_context(patch.object(anif_setup_service,'busy',return_value=False))
        self.stack.enter_context(patch.object(dependency_installer,'_install_running',return_value=False))
        self.stack.enter_context(patch.object(local_voice_engines, 'load_entry', return_value={'url':'http://127.0.0.1:9880'}))
        self.stop = self.stack.enter_context(patch.object(local_voice_engines.manager,'stop_all'))
        self.start = self.stack.enter_context(patch.object(local_voice_engines.manager,'start'))
        self.stack.enter_context(patch.object(local_voice_engines.manager,'child',None))
        self.stack.enter_context(patch.object(genie_tts_service, 'runtime_status', return_value={'running':False}))
        self.stack.enter_context(patch.object(genie_tts_service,'stop_worker'))
        self.verify = self.stack.enter_context(patch.object(assets,'_verify_candidate'))
        assets._CHANGING.clear()

    def file(self, relative, value=b'voice'):
        path=self.root/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(value); return path

    def save(self, changes):
        self.state.update(copy.deepcopy(changes)); return copy.deepcopy(self.state)

    def converted(self, *, registered=True, ready=True, identifier='a'):
        root=self.root/'voice/aniflive-managed'; job=root/'jobs'/('a'*32)
        profile=self.state['voice_profiles'][identifier]
        source={'id':identifier, 'gpt':profile['gpt_sovits_gpt_weights'], 'sovits':profile['gpt_sovits_sovits_weights'],
                'reference':profile['gpt_sovits_ref_audio'], 'reference_text':profile['gpt_sovits_prompt_text'],
                'reference_language':profile['gpt_sovits_prompt_language'],
                'weight_sha256':{'gpt':assets._digest(profile['gpt_sovits_gpt_weights']), 'sovits':assets._digest(profile['gpt_sovits_sovits_weights'])},
                'reference_sha256':assets._digest(profile['gpt_sovits_ref_audio'])}
        self.file('voice/aniflive-managed/.mio-anif-owner.json',json.dumps({'schema':1,'owner':'mio-aniflive-managed'}).encode())
        self.file('voice/aniflive-managed/jobs/'+('a'*32)+'/job.json',json.dumps({'profile':source}).encode())
        self.file('voice/aniflive-managed/jobs/'+('a'*32)+'/status.json',json.dumps({'stage':'ready' if ready else 'interrupted','ready':ready}).encode())
        entry={'url':'http://127.0.0.1:9883','args':['--gpt',source['gpt'],'--sovits',source['sovits'],'--reference',source['reference'],'--voice-profile',identifier,'--engines',str(job/'engines')]}
        self.file('voice/aniflive-managed/installed.json',json.dumps({'job_id':job.name,'voice_id':identifier,'registration':entry}).encode())
        self.registry.write_text(json.dumps({'schema':1,'engines':{'aniflive':entry} if registered else {}}))
        return job

    def test_passive_inventory_preserves_upgrade_choices_and_files(self):
        self.state.update(voice_engine='gpt_sovits_v5', local_voice_runtime='genie')
        before=copy.deepcopy(self.state)
        result=resources.voices(); info=result['voices'][0]
        self.assertEqual(self.state,before); self.saved.assert_not_called(); self.start.assert_not_called()
        self.assertEqual(info['model_version'],'待加载验证')
        self.assertEqual(info['languages'],['zh','ja']); self.assertEqual(info['missing_files'],[])
        self.assertEqual(result['voices'][1]['external_engine'],'gpt_sovits_v5')

    def test_missing_weight_blocks_selection_without_modification(self):
        Path(self.state['voice_profiles']['a']['gpt_sovits_gpt_weights']).unlink()
        with self.assertRaisesRegex(ValueError,'补齐'):
            assets.select('a','mio_v2')
        self.saved.assert_not_called(); self.verify.assert_not_called()
        self.assertEqual(self.state['default_voice_profile_id'],'b')

    def test_success_is_explicit_verified_and_keeps_other_profiles(self):
        before=copy.deepcopy(self.state['voice_profiles'])
        result=assets.select('a','mio_v2')
        self.assertTrue(result['verified']); self.assertEqual(self.state['default_voice_profile_id'],'a')
        self.assertEqual(self.state['voice_profiles'],before); self.assertEqual(self.state['unrelated_setting'],'keep')
        candidate=self.verify.call_args.args[1]
        self.assertEqual(candidate['voice_profile_id'],'a'); self.assertEqual(candidate['gpt_sovits_prompt_language'],'ja')
        self.assertFalse(assets._CHANGING.is_set())
        info=assets.inspect_profile('a',self.state)
        self.assertIn('本机加载发声通过',info['model_version'])
        self.assertIn('mio_v2',info['verification'])

    def test_verification_receipt_expires_when_original_changes(self):
        assets.select('a','mio_v2')
        Path(self.state['voice_profiles']['a']['gpt_sovits_gpt_weights']).write_bytes(b'changed weight')
        info=assets.inspect_profile('a',self.state)
        self.assertEqual(info['verification'],{})
        self.assertEqual(info['model_version'],'待加载验证')

    def test_copy_to_another_computer_requires_new_local_verification(self):
        assets.select('a','mio_v2')
        with patch.object(assets,'_device_identity',return_value='another-computer'):
            info=assets.inspect_profile('a',self.state)
        self.assertEqual(info['verification'],{}); self.assertEqual(info['model_version'],'待加载验证')

    def test_voice_used_as_conversion_base_cannot_be_removed(self):
        self.state['voice_profiles']['b']['so_vits_svc_base_profile_id']='a'
        with self.assertRaisesRegex(ValueError,'作为基底'):
            resources.voice_removal_preview('a',False,True)
        self.saved.assert_not_called()

    def test_edit_while_staging_deletion_preserves_new_config_and_original_file(self):
        self.state['voice_profiles']['b']={'name':'B'}
        owned=Path(self.state['voice_profiles']['a']['gpt_sovits_gpt_weights'])
        plan=resources.voice_removal_preview('a',True,True)
        original=Path.rename
        def rename_and_edit(path,destination):
            result=original(path,destination)
            if path==owned:
                self.state['voice_profiles']['a']['name']='newer name'
            return result
        with patch.object(Path,'rename',rename_and_edit),self.assertRaisesRegex(ValueError,'清理期间'):
            resources.voice_remove('a',plan['token'],True,True)
        self.assertTrue(owned.exists()); self.assertEqual(self.state['voice_profiles']['a']['name'],'newer name')
        self.saved.assert_not_called()

    def test_file_restore_runs_even_when_config_rollback_also_fails(self):
        self.state['voice_profiles']['b']={'name':'B'}
        owned=Path(self.state['voice_profiles']['a']['gpt_sovits_gpt_weights'])
        plan=resources.voice_removal_preview('a',True,True)
        def write_then_fail(changes):
            self.save(changes)
            raise OSError('notification failed')
        self.saved.side_effect=write_then_fail
        with self.assertRaises(OSError): resources.voice_remove('a',plan['token'],True,True)
        self.assertTrue(owned.is_file()); self.assertFalse(list(owned.parent.glob('.mio-removal-*')))
        self.assertIn('a',self.state['voice_profiles'])

    def test_failed_verification_restores_saved_selection(self):
        before=copy.deepcopy(self.state)
        self.verify.side_effect=OSError('bad checkpoint')
        with self.assertRaisesRegex(ValueError,'保留原选择'):
            assets.select('a','mio_v2')
        self.assertEqual(self.state,before); self.assertFalse(assets._CHANGING.is_set())

    def test_config_notification_failure_after_write_restores_previous_selection(self):
        before=copy.deepcopy(self.state); calls=0
        def save_and_fail_once(changes):
            nonlocal calls
            result=self.save(changes); calls+=1
            if calls==1: raise OSError('notification failed after atomic write')
            return result
        self.saved.side_effect=save_and_fail_once
        with self.assertRaisesRegex(ValueError,'保留原选择'):
            assets.select('a','mio_v2')
        self.assertEqual(self.state,before); self.verify.assert_not_called()
        self.assertFalse(assets._CHANGING.is_set())

    def test_failed_rollback_does_not_claim_original_selection_was_restored(self):
        def persist_first(changes):
            if changes.get('default_voice_profile_id')=='a': self.save(changes)
            raise OSError('disk refused save')
        self.saved.side_effect=persist_first
        with self.assertRaisesRegex(ValueError,'暂未确认恢复原音色'):
            assets.select('a','mio_v2')
        self.assertEqual(self.state['default_voice_profile_id'],'a')
        self.assertFalse(assets._CHANGING.is_set())

    def test_inventory_cannot_expose_candidate_as_final_selection(self):
        def verify(*_):
            with self.assertRaisesRegex(ValueError,'正在加载'):
                resources.voices()
        self.verify.side_effect=verify
        assets.select('a','mio_v2')
        self.assertEqual(next(row['id'] for row in resources.voices()['voices'] if row['selected']),'a')

    def test_catalog_worker_keeps_last_confirmed_card_during_candidate_verification(self):
        key=('synthetic-voice','synthetic-vision','synthetic-companion')
        confirmed=[{'id':'mio_v2','selected':False},{'id':'aniflive','selected':True}]
        with patch.object(resources,'_CATALOG_KEY',key), \
             patch.object(resources,'_CATALOG_CACHE',{'items':{'value':confirmed,'at':1,'error':''}}), \
             patch.object(resources,'_CATALOG_RUNNING',{'items'}), \
             patch.object(resources,'_CATALOG_REPEAT',set()), \
             patch.object(resources,'_catalog_items_snapshot') as snapshot:
            def verify(*_):
                resources._catalog_update('items',key,resources._catalog_items)
                self.assertEqual(resources._CATALOG_CACHE['items']['value'],confirmed)
                self.assertTrue(resources._CATALOG_CACHE['items']['error'])
                snapshot.assert_not_called()
            self.verify.side_effect=verify
            assets.select('a','mio_v2')

    def test_source_changes_during_loading_roll_back_and_do_not_record_verification(self):
        before=copy.deepcopy(self.state)
        self.verify.side_effect=lambda *_: Path(self.state['voice_profiles']['a']['gpt_sovits_gpt_weights']).write_bytes(b'new')
        with self.assertRaisesRegex(ValueError,'原件已变化'):
            assets.select('a','mio_v2')
        self.assertEqual(self.state,before); self.assertNotIn('verified',assets.profile_record('a'))

    def test_gate_blocks_facade_synthesis_during_candidate_validation(self):
        def verify(*_):
            with patch.object(companion_service,'_iter_speech_wav_stream_impl') as stream_impl, patch.object(companion_service,'_synthesize_speech_wav_impl') as synth_impl:
                with self.assertRaisesRegex(ValueError,'正在加载'):
                    list(companion_service.iter_speech_wav_stream('test'))
                with self.assertRaisesRegex(ValueError,'正在加载'):
                    companion_service.synthesize_speech_wav('test')
                stream_impl.assert_not_called(); synth_impl.assert_not_called()
        self.verify.side_effect=verify
        assets.select('a','mio_v2')

    def test_writer_waits_for_existing_reader_before_saving_candidate(self):
        entered=threading.Event(); release=threading.Event(); completed=threading.Event(); errors=[]
        def reader():
            with assets.stable_selection():
                entered.set(); release.wait(3)
        def writer():
            try: assets.select('a','mio_v2')
            except BaseException as exc: errors.append(exc)
            finally: completed.set()
        first=threading.Thread(target=reader); first.start(); self.assertTrue(entered.wait(1))
        second=threading.Thread(target=writer); second.start()
        self.assertFalse(completed.wait(.1)); self.saved.assert_not_called()
        release.set(); first.join(2); second.join(2)
        self.assertTrue(completed.is_set()); self.assertEqual(errors,[])

    def test_stream_can_advance_and_close_on_different_pool_threads(self):
        release=threading.Event(); first_done=threading.Event(); second_done=threading.Event()
        errors=[]; identities=[]; chunks=[]
        baseline=assets._SELECTION_READERS
        def implementation(*args,**kwargs):
            yield b'first'
            yield b'second'
        with patch.object(companion_service,'_iter_speech_wav_stream_impl',side_effect=implementation):
            stream=companion_service.iter_speech_wav_stream('test')
            def advance(done):
                try:
                    identities.append(threading.get_ident()); chunks.append(next(stream))
                except BaseException as exc: errors.append(exc)
                finally:
                    done.set(); release.wait(3)
            first=threading.Thread(target=advance,args=(first_done,)); first.start()
            self.assertTrue(first_done.wait(1))
            second=threading.Thread(target=advance,args=(second_done,)); second.start()
            self.assertTrue(second_done.wait(1))
            def close():
                try:
                    identities.append(threading.get_ident()); stream.close()
                except BaseException as exc: errors.append(exc)
            third=threading.Thread(target=close); third.start(); third.join(1)
            release.set(); first.join(1); second.join(1)
        self.assertFalse(third.is_alive()); self.assertEqual(errors,[])
        self.assertEqual(chunks,[b'first',b'second']); self.assertEqual(len(set(identities)),3)
        self.assertEqual(assets._SELECTION_READERS,baseline)

    def test_writer_waits_for_cross_thread_stream_close_and_blocks_new_readers(self):
        def implementation(*args,**kwargs):
            yield b'first'
            yield b'second'
        writer_done=threading.Event(); errors=[]
        with patch.object(companion_service,'_iter_speech_wav_stream_impl',side_effect=implementation):
            stream=companion_service.iter_speech_wav_stream('test')
            self.assertEqual(next(stream),b'first')
            def write():
                try: assets.select('a','mio_v2')
                except BaseException as exc: errors.append(exc)
                finally: writer_done.set()
            writer=threading.Thread(target=write); writer.start()
            self.assertTrue(assets._CHANGING.wait(1))
            self.assertFalse(writer_done.wait(.1)); self.saved.assert_not_called()
            with self.assertRaisesRegex(ValueError,'正在加载'):
                with assets.stable_selection(): pass
            closer=threading.Thread(target=stream.close); closer.start(); closer.join(1)
            writer.join(2)
        self.assertTrue(writer_done.is_set()); self.assertEqual(errors,[])
        self.assertEqual(self.state['default_voice_profile_id'],'a')
        self.assertEqual(assets._SELECTION_READERS,0)

    def test_second_writer_is_rejected_without_queuing_a_late_voice_change(self):
        with assets._selection_transaction():
            with self.assertRaisesRegex(ValueError,'不会排队'):
                with assets._selection_transaction(): pass
        self.saved.assert_not_called(); self.assertFalse(assets._CHANGING.is_set())

    def test_rejected_active_call_operation_does_not_cancel_its_speech(self):
        with patch.object(calls,'status',return_value={'active':True}),self.assertRaisesRegex(ValueError,'结束语音通话'):
            assets.select('a','mio_v2')
        self.cancel.assert_not_called(); self.saved.assert_not_called()

    def test_unknown_engine_never_reinterprets_voice_or_changes_selection(self):
        with self.assertRaisesRegex(ValueError,'不支持'):
            assets.select('a','gpt_sovits_v5')
        self.saved.assert_not_called()

    def test_anif_source_hash_change_refuses_reuse(self):
        self.converted()
        Path(self.state['voice_profiles']['a']['gpt_sovits_gpt_weights']).write_bytes(b'new checkpoint')
        with self.assertRaisesRegex(ValueError,'重新编译'):
            assets.select('a','aniflive')
        self.saved.assert_not_called(); self.verify.assert_not_called()

    def test_anif_reference_change_refuses_reuse(self):
        self.converted(); self.state['voice_profiles']['a']['gpt_sovits_prompt_text']='changed'
        with self.assertRaisesRegex(ValueError,'重新编译'):
            assets.select('a','aniflive')
        self.saved.assert_not_called()

    def test_compatible_registered_anif_selects_exact_profile_and_keeps_native(self):
        self.converted()
        result=assets.select('a','aniflive')
        self.assertTrue(result['verified']); self.assertEqual(self.state['voice_engine'],'aniflive')
        self.assertEqual(self.state['aniflive_voice_profile'],'a'); self.assertEqual(self.state['local_voice_runtime'],'mio_v2')

    def test_incomplete_conversion_protects_original_files_from_cleanup(self):
        self.converted(registered=False,ready=False)
        self.state['voice_profiles']['b']={'name':'B'}
        plan=resources.voice_removal_preview('a',True,False)
        self.assertEqual(plan['paths'],[]); self.assertEqual(len(plan['conversion_references']),3)
        self.assertEqual(len(plan['shared_files']),3)

    def test_registered_conversion_blocks_profile_removal(self):
        self.converted()
        with self.assertRaisesRegex(ValueError,'Anif 加速副本'):
            resources.voice_removal_preview('a',False,True)
        self.saved.assert_not_called()

    def test_corrupt_conversion_reference_never_means_original_is_unused(self):
        job=self.converted(registered=False)
        (job/'job.json').write_text('broken')
        with self.assertRaisesRegex(ValueError,'引用记录无法读取'):
            resources.voice_removal_preview('a',True,False)
        self.assertTrue(Path(self.state['voice_profiles']['a']['gpt_sovits_gpt_weights']).is_file())

    def test_wrong_shape_conversion_reference_blocks_cleanup_instead_of_crashing(self):
        job=self.converted(registered=False)
        (job/'job.json').write_text(json.dumps({'profile':{'id':'a','gpt':{'path':'wrong'}}}))
        with self.assertRaisesRegex(ValueError,'引用记录无法读取'):
            resources.voice_removal_preview('a',True,False)

    def test_wrong_shape_registry_is_passive_but_blocks_destructive_cleanup(self):
        self.registry.write_text(json.dumps({'schema':1,'engines':[]}))
        self.assertEqual(len(resources.voices()['voices']),2)
        with self.assertRaisesRegex(ValueError,'引擎引用记录无法读取'):
            resources.voice_removal_preview('a',True,False)

    def test_corrupt_identity_record_is_not_overwritten_on_switch(self):
        path=assets._asset_path(); path.parent.mkdir(parents=True,exist_ok=True); path.write_text('broken')
        before=copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError,'身份记录无法读取'):
            assets.select('a','mio_v2')
        self.assertEqual(self.state,before); self.assertEqual(path.read_text(),'broken')

    def test_wrong_shape_voice_record_has_a_clear_error_without_overwrite(self):
        path=assets._asset_path(); path.parent.mkdir(parents=True,exist_ok=True)
        content=json.dumps({'schema':1,'voices':{'a':'not an object'}}); path.write_text(content)
        with self.assertRaisesRegex(ValueError,'身份记录无法读取'):
            assets.select('a','mio_v2')
        self.saved.assert_not_called(); self.assertEqual(path.read_text(),content)

    def test_current_default_cannot_be_implicitly_replaced(self):
        for delete,remove in ((False,True),(True,False),(True,True)):
            with self.subTest(delete=delete,remove=remove),self.assertRaisesRegex(ValueError,'先切换'):
                resources.voice_removal_preview('b',delete,remove)
        self.saved.assert_not_called()

    def test_hardlinked_original_is_preserved(self):
        owned=self.file('data/companion/音色权重/unique.pth')
        os.link(owned,self.root/'shared.pth')
        self.state['voice_profiles']['a']={'name':'A','gpt_sovits_sovits_weights':str(owned)}
        plan=resources.voice_removal_preview('a',True,True)
        self.assertEqual(plan['paths'],[]); self.assertIn(str(owned),plan['shared_files'])

    def test_genie_no_matching_marker_never_falls_back_to_builtin_voice(self):
        self.file('voice/models/genie/other/mio-genie-v2.json',json.dumps({'gpt_sha256':'wrong','sovits_sha256':'wrong'}).encode())
        option=next(row for row in assets.inspect_profile('a',self.state)['variants'] if row['engine']=='genie')
        self.assertFalse(option['can_select']); self.assertEqual(option['state'],'needs_conversion')
        with self.assertRaisesRegex(ValueError,'不会替换为默认声音'):
            assets.select('a','genie')
        self.saved.assert_not_called(); self.verify.assert_not_called()

    def test_verifier_requires_valid_nonempty_audio(self):
        with self.assertRaisesRegex(ValueError,'空白音频|有效音频'):
            assets._verify_audio(b'not a wav')
        assets._verify_audio(audio())

    def test_busy_reader_has_bounded_failure_without_leaving_writer_gate_closed(self):
        before = copy.deepcopy(self.state)
        with assets.stable_selection(), patch.object(assets, 'SELECTION_WAIT_SECONDS', .01):
            with self.assertRaisesRegex(ValueError, '先停止语音'):
                with assets._selection_transaction():
                    self.fail('cannot change selection while a reader remains active')
            self.assertFalse(assets._CHANGING.is_set())
        self.assertEqual(self.state, before)
        with assets.stable_selection():
            pass

    def test_native_verifier_requires_exact_reported_weight_hashes(self):
        profile=self.state['voice_profiles']['a']; candidate=dict(self.state,**profile,voice_profile_id='a')
        with patch.object(mio_v2_tts_service,'runtime_status',return_value={'service_ready':True}), \
             patch.object(mio_v2_tts_service,'_health',return_value={'weight_sha256':{'gpt':'wrong','sovits':'wrong'}}), \
             patch.object(mio_v2_tts_service,'_validate_profile'), \
             patch.object(mio_v2_tts_service,'synthesize_wav',return_value=audio()) as synth:
            with self.assertRaisesRegex(ValueError,'双权重身份'):
                _VERIFY_CANDIDATE('mio_v2',candidate)
            synth.assert_not_called()

    def test_anif_verifier_rejects_same_profile_name_with_wrong_actual_weights(self):
        profile=self.state['voice_profiles']['a']; candidate=dict(self.state,**profile,voice_profile_id='a')
        with patch.object(aniflive_tts_service,'runtime_status',return_value={'selected_identity_ready':True,'weight_sha256':{'gpt':'wrong','sovits':'wrong'}}), \
             patch.object(aniflive_tts_service,'synthesize_wav',return_value=audio()) as synth:
            with self.assertRaisesRegex(ValueError,'实际权重身份'):
                _VERIFY_CANDIDATE('aniflive',candidate)
            synth.assert_not_called()

    def legacy_genie(self):
        profile=self.state['voice_profiles']['b']
        profile['gpt_sovits_gpt_weights']=''
        profile['gpt_sovits_sovits_weights']=''
        self.state['local_voice_runtime']='genie'
        for name in genie_tts_service.REQUIRED_CHARACTER_FILES:
            self.file('voice/models/genie/mio-v1/'+name)
        return profile

    def test_legacy_genie_without_originals_is_kept_and_can_switch_back(self):
        profile=self.legacy_genie()
        before=copy.deepcopy(self.state)
        info=assets.inspect_profile('b',self.state)
        option=next(row for row in info['variants'] if row['engine']=='genie')
        self.assertTrue(option['can_select']); self.assertIn('原始权重未导入',info['model_version'])
        self.assertFalse(assets._asset_path().exists())  # inventory is passive
        assets.select('a','mio_v2')  # capture the old known voice before switching away
        self.assertTrue(assets.profile_record('b')['genie'])
        self.assertEqual(assets._stored_genie('b',profile),self.root/'voice/models/genie/mio-v1')
        with patch.object(genie_tts_service, '_resolve_character_model', return_value=self.root/'voice/models/genie/mio-v1'):
            result=assets.select('b','genie')
        self.assertTrue(result['verified']); self.assertEqual(self.state['local_voice_runtime'],'genie')
        self.assertEqual(self.state['voice_profiles'],before['voice_profiles'])

    def test_legacy_genie_new_originals_invalidate_old_mapping(self):
        profile=self.legacy_genie()
        assets.select('a','mio_v2')
        profile.update(gpt_sovits_gpt_weights=str(self.file('data/companion/音色权重/b/new.ckpt')),
                       gpt_sovits_sovits_weights=str(self.file('data/companion/音色权重/b/new.pth')))
        before=copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError,'配套|需要|变化'):
            assets.select('b','genie')
        self.assertEqual(self.state,before)
        with self.assertRaisesRegex(ValueError,'原件或参考内容已变化'):
            assets.genie_model_override(dict(self.state, voice_profile_id='b'))

    def test_unknown_nonselected_legacy_profile_is_not_default_mio(self):
        self.legacy_genie()
        self.state['voice_profiles']['a']={'name':'Unknown','engine':'gpt_sovits'}
        info=assets.inspect_profile('a',self.state)
        self.assertFalse(next(row for row in info['variants'] if row['engine']=='genie')['can_select'])
        with self.assertRaises(ValueError): assets.select('a','genie')

    def test_removing_profile_forgets_its_genie_mapping_only(self):
        profile=self.legacy_genie()
        assets.select('a','mio_v2')
        plan=resources.voice_removal_preview('b',False,True)
        resources.voice_remove('b',plan['token'],False,True)
        self.assertEqual(assets.profile_record('b'),{})
        self.assertTrue((self.root/'voice/models/genie/mio-v1').is_dir())

    def test_storage_migration_carries_fixed_genie_identity_and_invalidates_receipt(self):
        profile=self.legacy_genie(); original=self.root/'voice/models/genie/mio-v1'
        assets._remember_genie('b',profile,original)
        assets._remember_verification('b','genie',assets._source_identity(profile))
        destination=self.root/'new-voice'; copied=destination/'models/genie/mio-v1'
        import shutil
        shutil.copytree(original,copied)
        records=assets.relocated_records(assets._asset_records(),self.root/'voice',destination,[self.root/'voice/models/genie/mio-v1'],self.state)
        assets._write_assets(records)
        self.settings.voice_training_dir=destination
        self.assertEqual(assets._stored_genie('b',profile),copied)
        self.assertNotIn('verified',assets.profile_record('b'))
        self.assertTrue(original.is_dir())


if __name__=='__main__': unittest.main()
