"""Isolated model resource transactions; no private config, GPU or real models."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('MIO_DISABLE_DOTENV', '1')
os.environ.setdefault('MIO_RUNTIME_ROOT', tempfile.mkdtemp(prefix='mio-model-tests-'))

from app import model_management_service as service
from app import companion_service, config, dependency_installer, dependency_probe, dependency_removal, anif_setup_service
from app import environment_check_service, genie_tts_service, local_voice_engines, local_vision_service, system_audio_service
from app.call_session_service import manager as call_manager


class ModelManagementTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='mio-resource-')))
        self.settings = SimpleNamespace(voice_training_dir=self.root/'voice', local_vision_dir=self.root/'vision',
            companion_dir=self.root/'data/companion', data_dir=self.root/'data', db_path=self.root/'data/test.sqlite',
            diary_dir=self.root/'data/diary', workspace_root=self.root/'project',
            companion_config_path=self.root/'data/companion/settings.json', runtime_config_path=self.root/'data/runtime.json')
        for module in (service, dependency_removal):
            self.stack.enter_context(patch.object(module, 'settings', self.settings))
        self.state = {'voice_profiles': {'a': {'name':'声音 A'}, 'b': {'name':'声音 B'}},
                      'default_voice_profile_id':'a', 'voice_engine':'gpt_sovits','local_voice_runtime':'mio_v2'}
        self.stack.enter_context(patch.object(companion_service, 'load_config', side_effect=lambda: copy.deepcopy(self.state)))
        self.saved = self.stack.enter_context(patch.object(companion_service, 'save_config', side_effect=self.save_config))
        self.stack.enter_context(patch.object(call_manager, 'status', return_value={'active':False}))
        self.stack.enter_context(patch.object(dependency_installer, '_install_running', return_value=False))
        self.stack.enter_context(patch.object(dependency_installer, '_package_source', return_value={}))
        self.stack.enter_context(patch.object(service.shutil, 'disk_usage', return_value=SimpleNamespace(free=100*1024**3)))
        self.runtime_save = self.stack.enter_context(patch.object(config, 'save_runtime_settings', side_effect=self.save_root))
        self.stack.enter_context(patch.object(dependency_probe, 'invalidate'))
        self.stack.enter_context(patch.object(environment_check_service, 'refresh_detection_cache'))
        self.stop_all = self.stack.enter_context(patch.object(local_voice_engines.manager, 'stop_all'))
        self.stop_one = self.stack.enter_context(patch.object(local_voice_engines.manager, 'stop'))
        self.stop_genie = self.stack.enter_context(patch.object(genie_tts_service, 'stop_worker'))
        self.stop_vision = self.stack.enter_context(patch.object(local_vision_service, 'stop_server'))
        self.stop_asr = self.stack.enter_context(patch.object(system_audio_service, 'stop'))
        self.stop_voice = self.stack.enter_context(patch.object(companion_service, 'stop_voice_service'))
        self.stack.enter_context(patch.object(local_vision_service, 'passive_status', return_value={'pulling':False}))

    def save_config(self, changes):
        self.state.update(copy.deepcopy(changes)); return copy.deepcopy(self.state)

    def save_root(self, changes):
        for key, value in changes.items():
            setattr(self.settings, key, Path(value))

    def file(self, relative, content=b'model'):
        path = self.root/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content); return path

    def preview(self, migrate=True):
        return service.storage_preview('voice', str(self.root/'new-voice'), migrate)

    def commit(self, plan):
        return service.storage_commit('voice', plan['destination'], plan['mode']=='copy_and_switch', plan['token'])

    def test_catalog_truthful_status_and_no_automatic_external_install(self):
        with patch.object(dependency_installer, 'list_dependencies', return_value=[{'id':'whisper','status':'installed'}]), \
             patch.object(companion_service, 'voice_runtime_health', return_value={}), \
             patch.object(local_voice_engines.manager, 'status', return_value={'managed_available':False,'managed_running':False,'service_loading':False}), \
             patch.object(local_vision_service,'passive_status',return_value={'owned_server':True}), \
             patch.object(system_audio_service,'status',return_value={'running':False,'latest_text':'PRIVATE'}), \
             patch.object(service,'hardware',return_value={'gpus':[]}):
            result = {"items": service._catalog_items()}
        items={row['id']:row for row in result['items']}
        self.assertTrue(items['whisper']['installed'])
        self.assertTrue(items['ollama_vision']['running'])
        self.assertEqual(items['mio_v2']['install_mode'],'external')
        self.assertIsNone(items['mio_v2']['dependency_id'])
        self.assertTrue(items['mio_v2']['selected'])
        self.assertEqual(items['aniflive']['verification'],'experimental')
        self.assertEqual(items['aniflive']['install_mode'], 'managed_build')
        self.assertIn('V2', items['aniflive']['description'])
        self.assertNotIn('V2ProPlus', items['aniflive']['description'])
        self.assertIsNone(items['indextts2']['size_bytes'])
        self.assertNotIn('PRIVATE',str(result))

    def test_native_running_loading_and_browsing_never_starts_models(self):
        def status(engine,config):
            return {'managed_available':engine=='mio_v2','managed_running':engine=='mio_v2','service_loading':engine=='mio_v2'}
        with patch.object(dependency_installer,'list_dependencies',return_value=[]), \
             patch.object(companion_service,'voice_runtime_health',return_value={}), \
             patch.object(local_voice_engines.manager,'status',side_effect=status), \
             patch.object(system_audio_service,'status',return_value={'running':False}), \
             patch.object(service,'hardware',return_value={'gpus':[]}), \
             patch.object(local_vision_service,'status') as vision_probe, \
             patch.object(local_vision_service,'probe_inference') as inference, \
             patch.object(local_vision_service,'start_server') as start, \
             patch.object(local_voice_engines.manager,'start') as load:
            rows={row['id']:row for row in service._catalog_items()}
        self.assertTrue(rows['mio_v2']['installed']); self.assertTrue(rows['mio_v2']['running'])
        self.assertTrue(rows['mio_v2']['can_release']); self.assertEqual(rows['mio_v2']['status'],'loading')
        for operation in (vision_probe,inference,start,load): operation.assert_not_called()

    def test_release_target_only(self):
        service.release('aniflive'); self.stop_one.assert_called_once_with('aniflive')
        self.stop_genie.assert_not_called(); self.stop_vision.assert_not_called()
        service.release('ollama_vision'); self.stop_vision.assert_called_once()
        service.release('whisper'); self.stop_asr.assert_called_once()

    def test_release_blocks_during_call_or_install(self):
        with patch.object(call_manager,'status',return_value={'active':True}), self.assertRaises(ValueError):
            service.release('mio_v2')
        with patch.object(dependency_installer,'_install_running',return_value=True), self.assertRaises(ValueError):
            service.release('mio_v2')
        with self.assertRaises(ValueError): service.release('unknown')
        self.stop_one.assert_not_called()

    def test_voice_operations_reject_anif_setup_while_vision_remains_independent(self):
        with patch.object(anif_setup_service, 'busy', return_value=True):
            with self.assertRaisesRegex(ValueError, 'Anif 正在安装或编译'):
                service._idle('voice')
            with self.assertRaisesRegex(ValueError, 'Anif 正在安装或编译'):
                service.release('mio_v2')
            service._idle('vision')
            service.release('ollama_vision')
        self.stop_one.assert_not_called()
        self.stop_vision.assert_called_once()

    def test_vision_release_holds_install_and_lifecycle_locks_during_idle_check(self):
        from contextlib import contextmanager
        held=set(); original_idle=service._idle
        @contextmanager
        def operation(identifier):
            held.add(identifier)
            try: yield
            finally: held.remove(identifier)
        def check(group):
            self.assertIn('ollama_vision',held)
            self.assertTrue(local_vision_service._lifecycle_lock._is_owned())
            return original_idle(group)
        with patch.object(dependency_installer,'dependency_operation',operation),patch.object(service,'_idle',side_effect=check):
            service.release('ollama_vision')
        self.stop_vision.assert_called_once()

    def test_voice_release_holds_selection_writer_and_lifecycle_locks(self):
        from app import voice_asset_service
        def stopped(*_):
            self.assertTrue(local_voice_engines.manager.lifecycle_lock._is_owned())
            with self.assertRaisesRegex(ValueError,'正在加载'):
                with voice_asset_service.stable_selection(): pass
        self.stop_one.side_effect=stopped
        service.release('mio_v2')
        self.stop_one.assert_called_once_with('mio_v2')
        self.assertFalse(voice_asset_service._CHANGING.is_set())

    def test_anif_managed_directory_blocks_voice_storage_switch_only(self):
        preserved = self.file('voice/aniflive-managed/pip-cache/keep.whl')
        for migrate in (False, True):
            with self.subTest(migrate=migrate), self.assertRaisesRegex(ValueError, '先在 Anif 安装面板卸载'):
                service.storage_preview('voice', str(self.root / 'new-voice'), migrate)
        result = service.storage_preview('vision', str(self.root / 'new-vision'), False)
        self.assertEqual(result['group'], 'vision')
        self.assertTrue(preserved.is_file())
        self.runtime_save.assert_not_called()

    def test_catalog_checking_fallback_keeps_managed_anif_install_mode(self):
        with patch.object(service, '_CATALOG_KEY', None), patch.object(service, '_CATALOG_CACHE', {}), \
             patch.object(service, '_CATALOG_RUNNING', set()), patch.object(service, '_CATALOG_REPEAT', set()), \
             patch.object(service.threading, 'Thread'):
            result = service.catalog()
        item = next(row for row in result['items'] if row['id'] == 'aniflive')
        definition = next(row for row in service.CATALOG if row['id'] == 'aniflive')
        self.assertEqual(item['status'], 'checking')
        self.assertEqual(item['install_mode'], 'managed_build')
        self.assertEqual(item['description'], definition['description'])
        self.assertEqual(item['limitations'], [definition['compatibility']])

    def test_common_cache_cleanup_does_not_enter_anif_download_or_pip_cache(self):
        original = [self.file('voice/aniflive-managed/pip-cache/voice.zip'),
                    self.file('voice/aniflive-managed/downloads/voice.zip'),
                    self.file('voice/aniflive-managed/downloads/voice.zip.part')]
        removable = self.file('voice/downloads/voice.zip')
        with patch.object(dependency_installer, '_package_source', return_value={'file_name': 'voice.zip'}):
            plan = service.cache_preview()
            self.assertEqual(plan['paths'], [str(removable)])
            service.cache_clear(plan['token'])
        self.assertFalse(removable.exists())
        self.assertTrue(all(path.is_file() for path in original))

    def test_new_empty_location_and_no_personal_file_copy(self):
        personal=self.file('voice/personal-training/keep.wav')
        result=self.commit(self.preview(migrate=False))
        self.assertTrue(result['restart_required']); self.assertTrue(result['source_retained'])
        self.assertEqual(self.settings.voice_training_dir,self.root/'new-voice')
        self.assertTrue(personal.is_file()); self.assertEqual(list((self.root/'new-voice').iterdir()),[])

    def test_migrate_verifies_preserves_source_and_rewrites_owned_refs_only(self):
        owned=self.file('voice/models/genie/mio-v1/voice.onnx')
        personal=self.file('voice/private-dataset.wav')
        self.state['voice_profiles']['a']['gpt_sovits_ref_audio']=str(owned)
        self.state['voice_profiles']['b']['gpt_sovits_ref_audio']=str(personal)
        plan=self.preview(); self.commit(plan)
        copied=self.root/'new-voice/models/genie/mio-v1/voice.onnx'
        self.assertEqual(copied.read_bytes(),owned.read_bytes()); self.assertTrue(owned.is_file())
        self.assertEqual(self.state['voice_profiles']['a']['gpt_sovits_ref_audio'],str(copied))
        self.assertEqual(self.state['voice_profiles']['b']['gpt_sovits_ref_audio'],str(personal))
        self.assertFalse((self.root/'new-voice/private-dataset.wav').exists())

    def test_populated_source_requires_migrate(self):
        self.file('voice/models/genie/mio-v1/voice.onnx')
        with self.assertRaisesRegex(ValueError,'复制并切换'): self.preview(migrate=False)

    def test_vision_relocation_copies_explicit_choice_without_private_files(self):
        selection = self.file('vision/model-selection.json', b'{"selected_model":"qwen2.5vl:3b"}')
        model = self.file('vision/models/blobs/synthetic-model')
        unrelated = self.file('vision/my-personal-notes.txt')
        destination = self.root / 'new-vision'
        with patch('app.local_vision_models.download_status', return_value={'active': False}):
            plan = service.storage_preview('vision', str(destination), True)
            result = service.storage_commit('vision', str(destination), True, plan['token'])
        self.assertTrue(result['source_retained'])
        self.assertEqual((destination / 'model-selection.json').read_bytes(), selection.read_bytes())
        self.assertEqual((destination / 'models/blobs/synthetic-model').read_bytes(), model.read_bytes())
        self.assertFalse((destination / 'my-personal-notes.txt').exists())
        self.assertTrue(unrelated.is_file())
        from app import local_vision_models
        with patch.object(local_vision_service, 'settings', self.settings):
            self.assertEqual(local_vision_models.effective_model(), 'qwen2.5vl:3b')

    def test_vision_download_blocks_relocation_and_component_uninstall(self):
        self.file('vision/models/blobs/synthetic-model')
        with patch('app.local_vision_models.download_status', return_value={'active': True}):
            with self.assertRaisesRegex(ValueError, '下载'):
                service.storage_commit('vision', str(self.root / 'new-vision'), True, 'a' * 64)
            with self.assertRaisesRegex(ValueError, '下载'):
                dependency_removal.uninstall('ollama_vision', 'a' * 64)
        self.runtime_save.assert_not_called()
        self.assertTrue((self.root / 'vision/models/blobs/synthetic-model').is_file())

    def test_plan_survives_normal_free_space_changes(self):
        plan=self.preview()
        with patch.object(service.shutil,'disk_usage',return_value=SimpleNamespace(free=99*1024**3)):
            self.assertEqual(plan['token'],self.preview()['token'])

    def test_stale_source_or_voice_config_rejected(self):
        path=self.file('voice/GenieData/model.bin'); plan=self.preview(); path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'变化'): self.commit(plan)
        plan=self.preview(); self.state['voice_profiles']['a']['name']='changed'
        with self.assertRaisesRegex(ValueError,'变化'): self.commit(plan)
        self.runtime_save.assert_not_called()

    def test_invalid_destinations_and_insufficient_disk(self):
        bad=[self.root/'voice/nested',self.root/'data/models',self.root/'data/companion/cache',
             self.root/'vision/nested',self.root/'vision',self.root]
        for destination in bad:
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                service.storage_preview('voice',str(destination))
        with self.assertRaises(ValueError): service.storage_preview('voice','relative/model')
        with patch.object(service.shutil,'disk_usage',return_value=SimpleNamespace(free=1)), self.assertRaisesRegex(ValueError,'空间'):
            self.preview()

    def test_copy_failure_preserves_source_and_config(self):
        path=self.file('voice/GenieData/model.bin'); plan=self.preview()
        with patch.object(service.shutil,'copytree',side_effect=OSError('disk failure')), self.assertRaises(OSError):
            self.commit(plan)
        self.assertTrue(path.is_file()); self.assertFalse((self.root/'new-voice').exists())
        self.assertFalse(list(self.root.glob('.mio-model-migrate-*')))
        self.assertEqual(self.settings.voice_training_dir,self.root/'voice')

    def test_failed_runtime_save_restores_profile_and_source(self):
        path=self.file('voice/GenieData/ref.wav'); self.state['voice_profiles']['a']['gpt_sovits_ref_audio']=str(path)
        plan=self.preview()
        with patch.object(config,'save_runtime_settings',side_effect=OSError('write failed')), self.assertRaises(OSError):
            self.commit(plan)
        self.assertEqual(self.state['voice_profiles']['a']['gpt_sovits_ref_audio'],str(path))
        self.assertTrue(path.is_file()); self.assertFalse((self.root/'new-voice').exists())

    def test_voice_storage_uses_selection_writer_gate(self):
        from app import voice_asset_service
        self.file('voice/GenieData/model.bin'); plan=self.preview()
        original=service.shutil.copytree
        def copy_while_busy(*args,**kwargs):
            with self.assertRaisesRegex(ValueError,'正在加载'):
                with voice_asset_service.stable_selection(): pass
            return original(*args,**kwargs)
        with patch.object(service.shutil,'copytree',side_effect=copy_while_busy): self.commit(plan)
        self.assertFalse(voice_asset_service._CHANGING.is_set())

    def test_voice_storage_write_then_notification_failure_restores_selection(self):
        owned=self.file('voice/GenieData/ref.wav')
        self.state['voice_profiles']['a']['gpt_sovits_ref_audio']=str(owned)
        plan=self.preview(); calls=0
        def write_then_fail_once(changes):
            nonlocal calls
            result=self.save_config(changes); calls+=1
            if calls==1: raise OSError('notification failed')
            return result
        self.saved.side_effect=write_then_fail_once
        with self.assertRaises(OSError): self.commit(plan)
        self.assertEqual(self.state['voice_profiles']['a']['gpt_sovits_ref_audio'],str(owned))
        self.assertTrue(owned.is_file()); self.assertFalse((self.root/'new-voice').exists())

    def test_voice_storage_root_write_then_notification_failure_restores_root(self):
        self.file('voice/GenieData/model.bin'); plan=self.preview(); calls=0
        def write_then_fail_once(changes):
            nonlocal calls
            self.save_root(changes); calls+=1
            if calls==1: raise OSError('notification failed')
        self.runtime_save.side_effect=write_then_fail_once
        with self.assertRaises(OSError): self.commit(plan)
        self.assertEqual(self.settings.voice_training_dir,self.root/'voice')
        self.assertFalse((self.root/'new-voice').exists())

    def test_voice_storage_copy_does_not_overwrite_concurrent_voice_edit(self):
        self.file('voice/GenieData/model.bin'); plan=self.preview(); original=service.shutil.copytree
        def edit_during_copy(*args,**kwargs):
            result=original(*args,**kwargs)
            self.state['voice_profiles']['a']['name']='newer edit'
            return result
        with patch.object(service.shutil,'copytree',side_effect=edit_during_copy),self.assertRaisesRegex(ValueError,'变化'):
            self.commit(plan)
        self.assertEqual(self.state['voice_profiles']['a']['name'],'newer edit')
        self.saved.assert_not_called(); self.assertFalse((self.root/'new-voice').exists())

    def test_corrupt_copy_rejected_by_hash_even_with_same_size(self):
        path=self.file('voice/GenieData/model.bin'); plan=self.preview(); original=service.shutil.copytree
        def corrupt(*args,**kwargs):
            result=original(*args,**kwargs); (Path(args[1])/'model.bin').write_bytes(b'WRONG'); return result
        with patch.object(service.shutil,'copytree',side_effect=corrupt), self.assertRaisesRegex(OSError,'校验'):
            self.commit(plan)
        self.assertEqual(path.read_bytes(),b'model'); self.runtime_save.assert_not_called()

    def test_reparse_model_target_rejected_without_following_external_files(self):
        external=self.file('external/keep.bin'); target=self.file('voice/GenieData/model.bin').parent
        original=Path.lstat
        def linked(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400) if path==target else info
        # Simulate Windows junction metadata; does not need OS link creation privileges.
        with patch.object(Path,'lstat',linked), self.assertRaisesRegex(ValueError,'链接|联接'): self.preview()
        self.assertEqual(external.read_bytes(),b'model')

    def test_source_change_during_copy_aborts(self):
        path=self.file('voice/GenieData/model.bin'); plan=self.preview(); original=service.shutil.copytree
        def change(*args,**kwargs):
            result=original(*args,**kwargs); path.write_bytes(b'new contents'); return result
        with patch.object(service.shutil,'copytree',side_effect=change), self.assertRaisesRegex(ValueError,'变化'):
            self.commit(plan)
        self.runtime_save.assert_not_called(); self.assertFalse((self.root/'new-voice').exists())

    def test_shared_and_external_voice_files_never_deleted(self):
        self.state['default_voice_profile_id']='b'  # Switch explicitly before deleting A.
        shared=self.file('data/companion/音色权重/shared.pth')
        external=self.file('external/private.ckpt')
        owned=self.file('data/companion/音色权重/a/model.ckpt')
        self.state['voice_profiles']['a'].update(gpt_sovits_sovits_weights=str(shared),gpt_sovits_gpt_weights=str(owned),gpt_sovits_ref_audio=str(external))
        self.state['voice_profiles']['b']['gpt_sovits_sovits_weights']=str(shared)
        plan=service.voice_removal_preview('a',True,True)
        self.assertEqual(plan['paths'],[str(owned)])
        result=service.voice_remove('a',plan['token'],True,True)
        self.assertTrue(result['ok']); self.assertTrue(shared.is_file()); self.assertTrue(external.is_file())
        self.assertFalse(owned.exists()); self.assertEqual(self.state['default_voice_profile_id'],'b')

    def test_profile_only_removal_keeps_files(self):
        self.state['default_voice_profile_id']='b'
        owned=self.file('data/companion/音色权重/a.pth')
        self.state['voice_profiles']['a']['gpt_sovits_sovits_weights']=str(owned)
        plan=service.voice_removal_preview('a',False,True); service.voice_remove('a',plan['token'],False,True)
        self.assertTrue(owned.is_file()); self.assertNotIn('a',self.state['voice_profiles'])

    def test_last_profile_or_stale_voice_plan_rejected(self):
        self.state['default_voice_profile_id']='b'
        plan=service.voice_removal_preview('a',False,True); self.state['voice_profiles']['a']['name']='new'
        with self.assertRaisesRegex(ValueError,'变化'): service.voice_remove('a',plan['token'],False,True)
        self.state['voice_profiles'].pop('b')
        with self.assertRaisesRegex(ValueError,'至少'): service.voice_removal_preview('a',True,True)

    def test_failed_profile_save_restores_staged_voice_file(self):
        self.state['default_voice_profile_id']='b'
        owned=self.file('data/companion/音色权重/a.pth')
        self.state['voice_profiles']['a']['gpt_sovits_sovits_weights']=str(owned)
        plan=service.voice_removal_preview('a',True,True)
        with patch.object(companion_service,'save_config',side_effect=OSError('write failed')), self.assertRaises(OSError):
            service.voice_remove('a',plan['token'],True,True)
        self.assertTrue(owned.is_file()); self.assertFalse(list(owned.parent.glob('.mio-removal-*')))

    def test_stale_voice_file_rejected(self):
        self.state['default_voice_profile_id']='b'
        owned=self.file('data/companion/音色权重/a.pth')
        self.state['voice_profiles']['a']['gpt_sovits_sovits_weights']=str(owned)
        plan=service.voice_removal_preview('a',True,True); owned.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'变化'): service.voice_remove('a',plan['token'],True,True)
        self.assertEqual(owned.read_bytes(),b'changed'); self.saved.assert_not_called()

    def test_voice_unlink_failure_reports_remaining_file(self):
        self.state['default_voice_profile_id']='b'
        owned=self.file('data/companion/音色权重/a.pth')
        self.state['voice_profiles']['a']['gpt_sovits_sovits_weights']=str(owned)
        plan=service.voice_removal_preview('a',True,True)
        with patch.object(Path,'unlink',side_effect=PermissionError('locked')):
            result=service.voice_remove('a',plan['token'],True,True)
        self.assertFalse(result['ok']); self.assertEqual(result['remaining_files'],[str(owned)])
        self.assertTrue(owned.exists()); self.assertEqual(result['removed_bytes'],0)

    def test_routes_expose_contract_and_do_not_leak_internal_exception(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routes.model_management import router
        app=FastAPI(); app.include_router(router); client=TestClient(app)
        with patch.object(service,'catalog',return_value={'items':[],'storage':[],'hardware':{}}):
            self.assertEqual(client.get('/api/model-management').json()['items'],[])
        with patch.object(service,'release',side_effect=ValueError('请结束通话')):
            response=client.post('/api/model-management/mio_v2/release')
            self.assertEqual(response.status_code,409); self.assertIn('通话',response.text)
        with patch.object(service,'release',side_effect=OSError('PRIVATE INTERNAL')):
            response=client.post('/api/model-management/mio_v2/release')
            self.assertEqual(response.status_code,500); self.assertNotIn('PRIVATE INTERNAL',response.text)
        self.assertEqual(client.post('/api/model-management/storage/voice/commit',json={'path':'x','token':'short'}).status_code,422)

    def prepare_cache(self):
        self.stack.enter_context(patch.object(dependency_installer,'_package_source',side_effect=lambda dep:{'file_name':dep+'.zip'}))

    def test_cache_only_fixed_download_packages_and_partials(self):
        self.prepare_cache()
        allowed=[self.file('voice/downloads/genie_runtime.zip'),self.file('voice/downloads/gpt_sovits.zip.part'),
                 self.file('voice/downloads/genie_runtime.zip.source-0123456789abcdef.part'),
                 self.file('vision/ollama-windows-amd64.zip'),self.file('vision/ollama-windows-amd64.zip.source-abcdef1234567890.part')]
        preserved=[self.file('voice/downloads/my-voice.pth'),self.file('voice/downloads/unknown.zip'),
                   self.file('voice/downloads/nested/genie_runtime.zip'),self.file('voice/cache/faster-whisper/weight.bin.part'),
                   self.file('vision/models/blobs/weight.part'),self.file('external/genie_runtime.zip'),
                   self.file('voice/downloads/genie_runtime.zip.source-invalid.part'),self.file('voice/.genie-env/python.exe')]
        plan=service.cache_preview()
        self.assertEqual(set(plan['paths']),{str(path) for path in allowed})
        result=service.cache_clear(plan['token'])
        self.assertTrue(result['ok']); self.assertEqual(result['removed_bytes'],sum(5 for _ in allowed))
        self.assertTrue(all(path.exists() for path in preserved)); self.assertFalse(any(path.exists() for path in allowed))

    def test_cache_active_install_and_active_pull_blocked(self):
        self.prepare_cache(); path=self.file('voice/downloads/genie_runtime.zip.part')
        with patch.object(dependency_installer,'_install_running',side_effect=lambda dep:dep=='napcat'), self.assertRaisesRegex(ValueError,'安装中'):
            service.cache_preview()
        plan=service.cache_preview()
        with patch.object(local_vision_service,'passive_status',return_value={'pulling':True}), self.assertRaisesRegex(ValueError,'下载'):
            service.cache_clear(plan['token'])
        self.assertTrue(path.exists())

    def test_cache_changed_file_or_reference_rejects_token(self):
        self.prepare_cache(); path=self.file('voice/downloads/genie_runtime.zip')
        plan=service.cache_preview(); path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'变化'): service.cache_clear(plan['token'])
        plan=service.cache_preview(); self.state['voice_profiles']['a']['gpt_sovits_ref_audio']=str(path)
        with self.assertRaisesRegex(ValueError,'变化'): service.cache_clear(plan['token'])
        self.assertTrue(path.exists()); self.assertEqual(service.cache_preview()['paths'],[])

    def test_cache_linked_directory_rejected(self):
        self.prepare_cache(); path=self.file('voice/downloads/genie_runtime.zip'); directory=path.parent
        original=Path.lstat
        def linked(candidate,*args,**kwargs):
            info=original(candidate,*args,**kwargs)
            return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400) if candidate==directory else info
        with patch.object(Path,'lstat',linked), self.assertRaisesRegex(ValueError,'链接|联接'): service.cache_preview()
        self.assertTrue(path.exists())

    def test_cache_hardlinked_package_preserved(self):
        self.prepare_cache(); path=self.file('voice/downloads/genie_runtime.zip')
        external=self.root/'external.zip'; os.link(path,external)
        self.assertEqual(service.cache_preview()['paths'],[]); self.assertTrue(path.exists()); self.assertTrue(external.exists())

    def test_cache_rename_failure_restores_all_files(self):
        self.prepare_cache(); first=self.file('voice/downloads/genie_runtime.zip'); second=self.file('voice/downloads/gpt_sovits.zip')
        plan=service.cache_preview(); original=Path.rename; calls=0
        def fail(path,destination):
            nonlocal calls
            calls+=1
            if calls==2: raise OSError('locked')
            return original(path,destination)
        with patch.object(Path,'rename',fail), self.assertRaises(OSError): service.cache_clear(plan['token'])
        self.assertTrue(first.exists()); self.assertTrue(second.exists())
        self.assertFalse(list(first.parent.glob('.mio-cache-removal-*')))

    def test_cache_unlink_failure_reports_retained_file(self):
        self.prepare_cache(); path=self.file('voice/downloads/genie_runtime.zip'); plan=service.cache_preview()
        with patch.object(Path,'unlink',side_effect=PermissionError('locked')): result=service.cache_clear(plan['token'])
        self.assertFalse(result['ok']); self.assertEqual(result['removed_bytes'],0)
        self.assertEqual(result['remaining_files'],[str(path)]); self.assertTrue(path.exists())

    def test_cache_api_contract(self):
        self.prepare_cache(); path=self.file('voice/downloads/genie_runtime.zip.part')
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routes.model_management import router
        app=FastAPI(); app.include_router(router); client=TestClient(app)
        response=client.get('/api/model-management/cache/preview'); self.assertEqual(response.status_code,200)
        result=client.post('/api/model-management/cache/clear',json={'token':response.json()['token']})
        self.assertEqual(result.status_code,200); self.assertTrue(result.json()['ok']); self.assertFalse(path.exists())


if __name__=='__main__': unittest.main()
