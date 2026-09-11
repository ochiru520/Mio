"""Regression coverage for the 12 audited lifecycle defects (13 original cases)."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, agent_task_service as tasks, agent_file_service as files
from app import agent_loop_service as loop, agent_tool_service as tools
from app import creation_service as creation, privacy_service as privacy
from app.llm import CompletionResult, ToolCall
from app.routes.agent_tasks import router as task_router, approve_action
from app.routes.conversations import delete_conversation


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    originals = {}
    for key, value in {
        'data_dir': tmp_path, 'db_path': tmp_path / 'test.db',
        'agent_attachment_dir': tmp_path / 'attachments',
        'runtime_config_path': tmp_path / 'runtime.json',
    }.items():
        originals[key] = getattr(db.settings, key)
        object.__setattr__(db.settings, key, value)
    monkeypatch.setattr(tasks, '_active', {})
    monkeypatch.setattr(tasks, '_background', {})
    db.init_db()
    tasks.initialize()
    yield
    for key, value in originals.items():
        object.__setattr__(db.settings, key, value)


def prepared(suffix='a', conversation='desktop_agent_a', goal='old goal'):
    return tasks.prepare(run_id='run-' + suffix, request_id='request-' + suffix,
        conversation_id=conversation, source='desktop', user_message=goal,
        model_id='test-model', reasoning_level='low', allowed_tools=['get_today_state'], context=[])


def completion(*calls, content=''):
    return CompletionResult(content=content, model='test', prompt_tokens=10,
        cached_prompt_tokens=0, completion_tokens=5, reasoning_tokens=0,
        cost_yuan=.001, cost_source='test', tool_calls=calls)


def test_new_request_revives_paused_task_and_keeps_old_goal():
    old = prepared()
    tasks.update(old['id'], status='paused', snapshot={'constraints': ['old constraint']})
    new = prepared('b', goal='unrelated new goal')
    assert new['id'] != old['id']
    assert new['status'] == 'running'
    assert new['original_goal'] == 'unrelated new goal'
    assert tasks.get(old['id'])['status'] == 'paused'
    assert len(tasks.list_tasks()) == 2


def test_deleted_conversation_leaves_ready_task_and_private_context():
    db.create_agent_conversation('desktop_agent_a', 'Synthetic conversation')
    task = prepared()
    tasks.update(task['id'], status='ready', snapshot={'constraints': ['synthetic private context']})
    asyncio.run(delete_conversation('desktop_agent_a'))
    survivor = tasks.get(task['id'])
    assert survivor is None
    with pytest.raises(ValueError):
        db.save_message('assistant', 'late reply', conversation_id='desktop_agent_a')


def test_cancelled_parent_still_allows_pending_high_risk_approval():
    async def scenario():
        call = ToolCall('edit', 'update_profile', json.dumps({'instruction': 'synthetic instruction'}))
        with patch.object(loop, 'call_chat_completion_result', AsyncMock(return_value=completion(call))), patch.object(tools, 'tool_availability', return_value=(True, '')):
            result = await loop.run_agent_loop(conversation_id='desktop_agent_a', source='desktop',
                user_message='synthetic request', source_message_id=0, request_id='approval',
                trace_id='approval', model_id='test', reasoning_level='low', allowed_tool_names={'update_profile'})
        assert result.status == 'waiting_confirmation'
        action_id = result.observations[0].action_id
        await tasks.pause(result.task_id, cancel_jobs=True)
        assert db.get_companion_action(action_id)['status'] == 'cancelled'
        with patch('app.companion_action_service.execute_companion_action_primitive', AsyncMock(return_value='simulated write')) as execute:
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await approve_action(result.task_id, action_id)
            execute.assert_not_awaited()
        assert tasks.get(result.task_id)['status'] == 'cancelled'
        assert db.get_companion_action(action_id)['status'] == 'cancelled'
    asyncio.run(scenario())


def test_privacy_pause_does_not_stop_background_agent_dispatch():
    async def scenario():
        task = prepared()
        tasks.update(task['id'], status='ready')
        with patch.object(privacy, 'load_runtime_settings', return_value={}), patch.object(privacy, 'save_runtime_settings'), patch.object(privacy.companion_service, 'load_config', return_value={}), patch.object(privacy.companion_service, 'save_config'), patch.object(privacy.companion_service.window_observer, 'stop'), patch.object(privacy.screen_observation_service, 'end_session'), patch.object(privacy.system_audio_service, 'stop'), patch.object(privacy.autonomy_service, 'public_policy', return_value={'paused': False}), patch.object(privacy.autonomy_service, 'update_policy'), patch('app.routes.onebot.disconnect_all_connections', AsyncMock(return_value=0)), patch.object(privacy, '_capabilities', return_value=[]):
            await privacy.pause_sensitive_capabilities()
        assert privacy._load_state()['paused'] is True
        with patch.object(tasks, 'continue_task', AsyncMock()) as continuation:
            assert await tasks.process_ready() == 0
            await asyncio.gather(*list(tasks._background.values()))
            continuation.assert_not_awaited()
    asyncio.run(scenario())


def test_pause_during_final_reply_does_not_cancel_background_request():
    async def scenario():
        task = prepared()
        tasks.update(task['id'], status='ready')
        entered, release = asyncio.Event(), asyncio.Event()
        async def reply(*args, **kwargs):
            entered.set()
            await release.wait()
            return completion(content='synthetic final result')
        with patch.object(loop, 'call_chat_completion_result', AsyncMock(return_value=completion())), patch('app.llm.call_chat_completion_result', new=reply):
            await tasks.process_ready()
            await asyncio.wait_for(entered.wait(), 2)
            process = tasks._background[task['id']]
            assert task['id'] in tasks._active
            await tasks.pause(task['id'], cancel_jobs=True)
            assert process.done()
            release.set()
            await asyncio.gather(process, return_exceptions=True)
        with db.get_conn() as conn:
            saved = conn.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND source='agent_background'", ('desktop_agent_a',)).fetchone()[0]
        assert saved == 0
        assert tasks.get(task['id'])['status'] == 'cancelled'
    asyncio.run(scenario())


def test_task_over_200_recent_completed_items_is_never_dispatched():
    old = prepared()
    tasks.update(old['id'], status='ready')
    with db.get_conn() as conn:
        conn.execute("UPDATE agent_tasks SET updated_at='2000-01-01T00:00:00'")
        conn.executemany("INSERT INTO agent_tasks SELECT ?,conversation_id,source,'completed',original_goal,snapshot_json,model_id,reasoning_level,allowed_tools_json,context_json,waiting_jobs_json,spent_yuan,budget_yuan,revision,created_at,'2099-01-01T00:00:00' FROM agent_tasks WHERE id=?", [(f'task_new_{i}', old['id']) for i in range(200)])
    assert tasks.ready_to_continue(tasks.get(old['id']))
    assert asyncio.run(tasks.process_ready()) == 1


def test_hash_in_document_name_returns_broken_download_link():
    task = prepared()
    result = files.write_document('report#1.md', 'synthetic report', task['id'])
    app = FastAPI()
    app.include_router(task_router, prefix='/api/agent')
    with TestClient(app) as client:
        assert client.get(result['url']).status_code == 200
        correct = f"/api/agent/work/files/{task['id']}/{quote(result['name'], safe='')}"
        assert client.get(correct).status_code == 200


def test_long_document_observation_is_truncated_to_invalid_json(tmp_path):
    task = prepared()
    source = files.workspace() / 'long.md'
    source.write_text('A' * 16000, encoding='utf-8')
    context = tools.ToolExecutionContext(run_id='run-a', request_id='request-a', trace_id='test',
        conversation_id='desktop_agent_a', source_message_id=0, user_message='read document',
        step_index=1, task_id=task['id'])
    result = asyncio.run(tools.execute_tool_call('agent_read_document', {'path': str(source)}, context))
    assert result.status == 'completed' and len(result.result['text']) == 16000
    stored = db.get_agent_run_step(result.step_id)['result_json']
    assert len(json.loads(stored)['text']) == 16000
    assert len(tasks.observations(task['id'])[0]['result']['text']) == 16000


def test_comfy_restart_submits_again_despite_existing_prompt_receipt(tmp_path):
    async def scenario():
        submitted = []
        def handle(request):
            submitted.append(request.url.path)
            return httpx.Response(200, json={'prompt_id':'new-prompt'})
        with patch.object(creation, '_client_kwargs', return_value={'transport':httpx.MockTransport(handle)}), patch.object(creation, '_object_info', AsyncMock(return_value={})), patch.object(creation, 'build_workflow_prompt', return_value=({},{})), patch.object(creation, '_update_job'), patch.object(creation, '_monitor_comfy_job', AsyncMock(return_value={})) as monitor, patch.object(creation, '_validated_comfy_outputs', return_value=[]), patch.object(creation, '_notify_chat_job_completion'):
            await creation._run_comfy_job_inner('job_test', {'prompt_id':'already-submitted'}, tmp_path, {}, SimpleNamespace(requires_reference=False,media_type='image'), 'old-client')
        assert submitted == []
        assert monitor.await_args.args[-1] == 'already-submitted'
    asyncio.run(scenario())


def test_birefnet_retry_becomes_image_generation_without_input(tmp_path):
    row = {'id':'job_cutout','status':'failed','workflow_id':'birefnet-portrait-png','media_type':'video',
        'spec_json':json.dumps({'input':'clip.mp4','input_kind':'video','frame_limit':240,'batch_frames':8,'prompt':'BiRefNet local cutout'}),
        'conversation_id':'desktop_agent_a','source':'desktop'}
    original_get = creation._get_job_row
    (tmp_path / 'input').mkdir()
    (tmp_path / 'input/clip.mp4').write_bytes(b'synthetic')
    with patch.object(creation, '_get_job_row', side_effect=lambda key: row if key == 'job_cutout' else original_get(key)), patch.object(creation, '_require_comfy_root', return_value=tmp_path):
        result = creation.retry_job('job_cutout')
    assert result['media_type'] == 'video' and result['workflow_id'] == 'birefnet-portrait-png'
    assert result['spec']['input_kind'] == 'video' and result['spec']['frame_limit'] == 240
    assert result['parent_job_id'] == 'job_cutout' and result['status'] == 'needs_confirmation'


def test_created_jobs_do_not_reserve_gpu_slot():
    from app.creation_models import CreationJobRequest
    with patch.object(creation, 'schedule_job'):
        one, _ = creation.create_job(CreationJobRequest(prompt='first image',backend='comfyui'))
        with pytest.raises(ValueError):
            creation.create_job(CreationJobRequest(prompt='second image',backend='comfyui'))
    assert one['status'] == 'created'
    assert creation._active_job_exists()


def test_privacy_switch_omits_monthly_review_and_monthly_job_still_runs():
    from app import monthly_review_service as monthly
    saved = []
    with patch.object(privacy, 'save_runtime_settings', side_effect=lambda values: saved.append(values)), patch.object(privacy.companion_service, 'save_config'), patch.object(privacy.autonomy_service, 'update_policy'):
        assert privacy._disable_sensitive_settings() == []
    assert saved[0]['monthly_review_enabled'] is False
    privacy._save_state({'paused': True})
    with patch.object(monthly, 'settings', SimpleNamespace(monthly_review_enabled=True, monthly_review_hour=0)), patch.object(db, 'get_monthly_review', return_value=None), patch.object(monthly, '_build_day_sections', return_value=('synthetic diary',1)), patch.object(monthly, 'generate_monthly_review', AsyncMock(return_value=SimpleNamespace(created=True))) as generate, patch.object(monthly, '_notify_monthly_review_ready', AsyncMock()):
        assert asyncio.run(monthly.run_monthly_review_once(datetime(2026,9,6,12))) == 0
        generate.assert_not_awaited()


def test_final_reply_exceeds_configured_model_call_limit():
    async def scenario():
        tasks.limits({'enabled':True, 'model_calls':2})
        one = completion(ToolCall('one','get_today_state','{}'))
        two = completion(ToolCall('two','get_diary','{}'))
        with patch.object(loop, 'call_chat_completion_result', AsyncMock(side_effect=[one,two])):
            result = await loop.run_agent_loop(conversation_id='desktop_agent_a',source='desktop',
                user_message='read and verify',source_message_id=0,request_id='budget',trace_id='test',
                model_id='test',reasoning_level='low',allowed_tool_names={'get_today_state','get_diary'})
        assert result.status == 'budget_exhausted'
        step = loop.begin_final_response(result)
        with patch('app.llm.call_chat_completion_result', AsyncMock(return_value=completion(content='final'))) as final:
            await loop.final_model_call(result, final, [])
            await loop.final_model_call(result, final, [])
            final.assert_awaited_once()
        loop.finish_final_response(result,step,reply='synthetic final reply')
        run = db.get_agent_run(result.run_id)
        assert run['max_model_calls'] == 2 and run['model_calls'] == 2
    asyncio.run(scenario())


def test_budget_off_by_default_and_can_exceed_old_call_cap():
    assert tasks.limits()['enabled'] is False
    async def scenario():
        responses = [completion(ToolCall(str(i), 'agent_task_update', json.dumps({'plan':[str(i)]}))) for i in range(10)] + [completion()]
        with patch.object(loop, 'call_chat_completion_result', AsyncMock(side_effect=responses)) as model:
            # Successful work is needed to keep progressing; pure metadata loops must stop.
            result = await loop.run_agent_loop(conversation_id='desktop_agent_a', source='desktop', user_message='test', source_message_id=0, request_id='unlimited', trace_id='test', model_id='test', reasoning_level='low', allowed_tool_names={'agent_task_update'})
        assert result.status == 'waiting_user'
        assert model.await_count == 3
    asyncio.run(scenario())


def test_native_failure_reserves_final_call_and_accounts_failure():
    async def scenario():
        tasks.limits({'enabled': True, 'model_calls': 2})
        from app.llm import ModelRequestError
        with patch.object(loop, 'call_chat_completion_result', AsyncMock(side_effect=ModelRequestError('synthetic', profile=SimpleNamespace(id='test', model='test', provider_id='test', provider_name='test')))) as model:
            result = await loop.run_agent_loop(conversation_id='desktop_agent_a', source='desktop', user_message='test', source_message_id=0, request_id='nativefail', trace_id='test', model_id='test', reasoning_level='low')
        assert model.await_count == 1
        assert db.get_agent_run(result.run_id)['model_calls'] == 1
        loop.begin_final_response(result)
        final = AsyncMock(return_value=completion(content='final'))
        await loop.final_model_call(result, final, [])
        await loop.final_model_call(result, final, [])
        assert final.await_count == 1
        assert tasks.get(result.task_id)['spent_yuan'] == .001
    asyncio.run(scenario())


def test_unknown_comfy_submission_reconciles_without_resubmission(tmp_path):
    async def scenario():
        requests = []
        def handle(request):
            requests.append((request.method, request.url.path))
            return httpx.Response(200, json={'queue_running': [[1,'original',{}, {'client_id':'stable'}]], 'queue_pending': []})
        with patch.object(creation, '_client_kwargs', return_value={'transport':httpx.MockTransport(handle)}), patch.object(creation, '_update_job'), patch.object(creation, '_monitor_comfy_job', AsyncMock(return_value={})) as monitor, patch.object(creation, '_validated_comfy_outputs', return_value=[]), patch.object(creation, '_notify_chat_job_completion'):
            await creation._run_comfy_job_inner('job_test', {}, tmp_path, {'submission_pending':True}, SimpleNamespace(media_type='image'), 'stable')
        assert monitor.await_args.args[-1] == 'original'
        assert all(method == 'GET' for method, _ in requests)
    asyncio.run(scenario())


def test_concurrent_creation_reserves_single_slot():
    from concurrent.futures import ThreadPoolExecutor
    from app.creation_models import CreationJobRequest
    import threading
    barrier = threading.Barrier(2)
    def submit(index):
        barrier.wait()
        try:
            return creation.create_job(CreationJobRequest(prompt=f'image {index}', backend='comfyui'))[0]['id']
        except ValueError:
            return None
    with patch.object(creation, 'schedule_job'), ThreadPoolExecutor(2) as executor:
        results = list(executor.map(submit, range(2)))
    assert sum(value is not None for value in results) == 1


@pytest.mark.parametrize('name', ['report#1.md', 'report%20.md', '中文 空格.md'])
def test_output_filename_url_roundtrip(name):
    task = prepared()
    result = files.write_document(name, 'synthetic report', task['id'])
    app = FastAPI()
    app.include_router(task_router, prefix='/api/agent')
    with TestClient(app) as client:
        assert client.get(result['url']).text == 'synthetic report'


def test_monthly_pause_cancels_inflight_generation_before_save():
    from app import monthly_review_service as monthly
    async def scenario():
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(monthly, '_build_day_sections', return_value=('synthetic diary', 1)), patch.object(monthly, 'call_chat_completion', blocked), patch.object(db, 'upsert_monthly_review') as save:
            worker = asyncio.create_task(monthly.generate_monthly_review('2026-08'))
            await asyncio.wait_for(entered.wait(), 2)
            privacy._save_state({'paused':True})
            await monthly.cancel_active()
            await asyncio.gather(worker, return_exceptions=True)
            assert worker.cancelled()
            save.assert_not_called()
    asyncio.run(scenario())


def test_canvas_named_video_widgets_preserve_values_and_ignore_editor_fields():
    from app.creation_custom import prepare_import
    graph = {'nodes':[
        {'id':1,'type':'VHS_LoadVideo','widgets_values':{'video':'source.mp4','frame_load_cap':8,'videopreview':{'hidden':False}}},
        {'id':2,'type':'PreviewImage','inputs':[{'name':'images','link':1}]},
    ],'links':[[1,1,0,2,0,'IMAGE']]}
    info = {'VHS_LoadVideo':{'input':{'required':{'video':[['source.mp4']], 'frame_load_cap':['INT',{'default':0}]}}},'PreviewImage':{'input':{'required':{'images':['IMAGE']}},'output_node':True}}
    result = prepare_import(graph, info)
    assert result['prompt']['1']['inputs'] == {'video':'source.mp4','frame_load_cap':8}
    assert result['prompt']['2']['class_type'] == 'SaveImage'
    assert result['bindings'] == {}
    assert result['source_inputs'][0]['options'] == ['source.mp4']
    assert graph['nodes'][1]['type'] == 'PreviewImage'


def test_staged_reply_commit_completes_task_and_cancel_blocks_commit():
    async def scenario():
        with patch.object(loop, 'call_chat_completion_result', AsyncMock(return_value=completion())):
            result = await loop.run_agent_loop(conversation_id='desktop_agent_a', source='desktop', user_message='test', source_message_id=0, request_id='staged', trace_id='test', model_id='test', reasoning_level='low')
        step = loop.begin_final_response(result)
        assert tasks.get(result.task_id)['status'] == 'responding'
        loop.defer_final_response(result, step, reply='staged')
        source_id = db.save_message('user', 'synthetic request', conversation_id='desktop_agent_a')
        loop.commit_deferred_final_response(result.run_id, source_id)
        assert tasks.get(result.task_id)['status'] == 'completed'
        await tasks.pause(result.task_id, cancel_jobs=True)
        with pytest.raises(ValueError):
            loop.commit_deferred_final_response(result.run_id, 0)
        with pytest.raises(ValueError):
            db.save_message('assistant', 'late staged reply', conversation_id='desktop_agent_a', request_id='staged')
    asyncio.run(scenario())
