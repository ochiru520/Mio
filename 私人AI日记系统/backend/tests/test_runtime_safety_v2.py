"""Safety invariants for the unified runtime, using isolated data and fake services."""
import asyncio
import gc
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import db, model_runtime as runtime, privacy_service as privacy
from app import agent_task_service as tasks, agent_file_service as files, creation_service as creation
from app import weekly_review_service as weekly, monthly_review_service as monthly
from app import review_service as review, autonomy_service as autonomy, maintenance_service as maintenance
from app.config import settings, save_runtime_settings, runtime_settings_snapshot, update_runtime_settings_snapshot, SettingsConflictError
from app.creation_models import CreationJobRequest
from app.routes.conversations import delete_conversation


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    previous = dict(vars(settings))
    values = dict(data_dir=tmp_path/'private', db_path=tmp_path/'private'/'test.db',
                  diary_dir=tmp_path/'private'/'diaries', runtime_config_path=tmp_path/'private'/'runtime.json',
                  model_profiles_path=tmp_path/'private'/'models.json', companion_config_path=tmp_path/'private'/'companion.json',
                  mio_profile_path=tmp_path/'private'/'profile.json', creation_output_dir=tmp_path/'private'/'outputs',
                  agent_attachment_dir=tmp_path/'private'/'attachments',
                  qq_allowed_user_ids=('audit',), qq_proactive_enabled=True, qq_bot_enabled=False,
                  qq_proactive_day_start_hour=0, qq_proactive_day_end_hour=0,
                  qq_proactive_min_idle_minutes=120, qq_proactive_max_idle_minutes=120)
    for key, value in values.items():
        object.__setattr__(settings, key, value)
    for key in ('_active', '_background', '_approvals'):
        monkeypatch.setattr(tasks, key, {})
    monkeypatch.setattr(runtime, '_active', {})
    monkeypatch.setattr(creation, '_tasks', {})
    monkeypatch.setattr(monthly, '_active_generations', set())
    maintenance.reset_runtime_state()
    db.init_db(); tasks.initialize(); runtime.initialize()
    yield
    maintenance.reset_runtime_state()
    for key, value in previous.items():
        object.__setattr__(settings, key, value)
    gc.collect()


def test_private_ancestors_descendants_and_old_grants_rejected(tmp_path):
    nested = settings.data_dir/'records'
    nested.mkdir()
    secret = nested/'synthetic.json'
    secret.write_text('{}')
    for root in (tmp_path, settings.data_dir, nested):
        with pytest.raises(ValueError): files.roots([str(root)])
    files.roots([])
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT INTO agent_file_roots VALUES(?)', (str(tmp_path),))
    with pytest.raises(ValueError): files.read_document(str(secret))
    assert str(tmp_path) not in files.roots()
    output = files.write_document('safe.md', 'synthetic', 'task_audit')
    assert files.read_document(output['path'])['text'] == 'synthetic'


def test_settings_revision_prevents_lost_update_and_has_api_conflict():
    from app.routes.settings import router
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        before = client.get('/api/settings/runtime').json()
        save_runtime_settings({'qq_proactive_enabled': False})
        result = client.patch('/api/settings/runtime', headers={'If-Match': before['revision']},
                              json={'qq_proactive_enabled': True, 'daily_diary_check_seconds': 90})
        assert result.status_code == 409
        assert not settings.qq_proactive_enabled
        fresh = client.get('/api/settings/runtime').json()
        result = client.patch('/api/settings/runtime', headers={'If-Match': fresh['revision']}, json={'daily_diary_check_seconds': 90})
        assert result.status_code == 200
        assert result.json()['application']['applied']
        assert not result.json()['settings']['qq_proactive_enabled']


def test_concurrent_settings_save_accepts_only_one_revision():
    revision = runtime_settings_snapshot()['revision']
    def save(value):
        try:
            update_runtime_settings_snapshot({'daily_diary_check_seconds': value}, revision)
            return 'saved'
        except SettingsConflictError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, (90, 120))) == ['conflict', 'saved']


@pytest.mark.parametrize('kind', ['weekly', 'monthly', 'review'])
def test_privacy_stops_inflight_record_and_no_late_save(kind):
    db.upsert_diary('2026-08-03', 'Test', 'synthetic', '', 'done')
    module = {'weekly': weekly, 'monthly': monthly, 'review': review}[kind]
    async def run():
        started = asyncio.Event()
        async def blocked(*args, **kwargs):
            started.set(); await asyncio.Event().wait()
        with patch.object(module, 'call_chat_completion', new=AsyncMock(side_effect=blocked)), patch('app.routes.onebot.disconnect_all_connections', new=AsyncMock(return_value=0)):
            callback = {'weekly': lambda: weekly.generate_weekly_review('2026-08-03'),
                        'monthly': lambda: monthly.generate_monthly_review('2026-08'),
                        'review': lambda: review.generate_review_for_date('2026-08-03')}[kind]
            job = asyncio.create_task(callback())
            await asyncio.wait_for(started.wait(), 3)
            assert runtime.live_operations(automatic_only=True)
            assert (await privacy.pause_sensitive_capabilities())['paused']
            with pytest.raises(asyncio.CancelledError): await job
            assert not runtime.live_operations()
            assert db.get_weekly_review('2026-08-03') is None
            assert db.get_monthly_review('2026-08') is None
            assert db.get_daily_review('2026-08-03') is None
    asyncio.run(run())


def test_privacy_stops_proactive_before_delivery_and_keeps_event_resumable():
    now = datetime.fromisoformat('2026-09-09T12:00:00+08:00')
    conversation = 'qq_private_audit'
    autonomy.update_policy({'quiet_start_hour': 0, 'quiet_end_hour': 0})
    mid = db.save_message('user', 'synthetic', conversation_id=conversation)
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE messages SET created_at=? WHERE id=?', ((now-timedelta(hours=3)).isoformat(), mid))
    assert autonomy.collect_scheduled_proactive_events(now) == 1
    async def run():
        started = asyncio.Event()
        async def blocked(*args, **kwargs):
            started.set(); await asyncio.Event().wait()
        with patch('app.chat_service.generate_qq_proactive_replies', new=AsyncMock(side_effect=blocked)), patch('app.routes.onebot.disconnect_all_connections', new=AsyncMock(return_value=0)):
            job = asyncio.create_task(autonomy.process_once(now))
            await asyncio.wait_for(started.wait(), 3)
            await privacy.pause_sensitive_capabilities()
            await job
            assert db.get_last_message(conversation_id=conversation, role='assistant') is None
            assert db.list_agent_events()[0]['status'] == 'pending'
    asyncio.run(run())


def test_corrupt_privacy_state_fails_closed():
    (settings.data_dir/'隐私暂停.json').write_text('{')
    assert runtime.privacy_blocked()
    assert privacy.privacy_status()['transition'] == 'state_uncertain'
    with pytest.raises(runtime.OperationBlocked):
        asyncio.run(weekly.generate_weekly_review('2026-08-03'))


@pytest.mark.parametrize('kind', ['weekly', 'monthly', 'review'])
def test_record_background_does_not_replace_new_manual_result(kind):
    db.upsert_diary('2026-08-03', 'Test', 'synthetic', '', 'done')
    module = {'weekly': weekly, 'monthly': monthly, 'review': review}[kind]
    async def model(*args, **kwargs):
        {'weekly': lambda: db.upsert_weekly_review('2026-08-03', 'manual'),
         'monthly': lambda: db.upsert_monthly_review('2026-08', 'manual'),
         'review': lambda: db.upsert_daily_review('2026-08-03', 'manual')}[kind]()
        return 'late automatic'
    async def run():
        with patch.object(module, 'call_chat_completion', new=AsyncMock(side_effect=model)):
            result = await {'weekly': lambda: weekly.generate_weekly_review('2026-08-03', False),
                            'monthly': lambda: monthly.generate_monthly_review('2026-08', False),
                            'review': lambda: review.generate_review_for_date('2026-08-03', False)}[kind]()
            assert not result.created and result.markdown_content == 'manual'
    asyncio.run(run())


def test_delete_failure_restores_writable_conversation():
    db.create_agent_conversation('desktop_audit', 'Test')
    with patch.object(creation, 'schedule_job'):
        job, _ = creation.create_job(CreationJobRequest(prompt='synthetic'), conversation_id='desktop_audit')
    creation._update_job(job['id'], status='running')
    async def run():
        with patch.object(creation, 'cancel_job', new=AsyncMock(side_effect=RuntimeError('offline'))):
            with pytest.raises(HTTPException) as error: await delete_conversation('desktop_audit')
            assert error.value.status_code == 409
    asyncio.run(run())
    assert not db.conversation_deleted('desktop_audit')
    db.assert_conversation_writable('desktop_audit')


def remote_job():
    connection = {'api_key': 'fake', 'base_url': 'https://invalid.example/v1', 'auth_scheme': 'bearer'}
    with patch.object(creation, '_provider_connection', return_value=connection):
        job, _ = creation.create_job(CreationJobRequest(backend='remote_api', provider_id='mock', model_id='mock', prompt='synthetic'))
    return job, connection


def test_remote_restart_requires_reconciliation_and_explicit_retry():
    job, connection = remote_job()
    creation._update_job(job['id'], status='running')
    async def run():
        with patch.object(creation, '_request_remote_image', new_callable=AsyncMock) as model:
            await creation.resume_active_jobs()
            model.assert_not_awaited()
            assert creation.get_job(job['id'])['status'] == 'unknown'
    asyncio.run(run())
    with patch.object(creation, '_provider_connection', return_value=connection):
        retry = creation.retry_job(job['id'])
        assert retry['status'] == 'needs_confirmation'
        assert retry['parent_job_id'] == job['id']
        assert '扣费' in retry['confirmation_reason']


@pytest.mark.parametrize('status', [200, 408, 500])
def test_ambiguous_remote_response_does_not_try_another_generation(status):
    client = AsyncMock(); client.__aenter__.return_value = client
    client.post.return_value = httpx.Response(status, text='unexpected response')
    async def run():
        with patch.object(creation.httpx, 'AsyncClient', return_value=client):
            with pytest.raises(creation.RemoteSubmissionUnknown):
                await creation._request_remote_image('test', {'base_url':'https://invalid.example/v1','api_key':'fake','auth_scheme':'bearer'}, ['images','responses'], [], {'prompt':'synthetic'}, [])
        assert client.post.await_count == 1
        assert client.post.await_args.kwargs['headers']['Idempotency-Key'] == 'mio-creation-test'
    asyncio.run(run())


def test_backup_maintenance_cancels_actual_creation_worker_before_drain():
    from app import main
    job, connection = remote_job()
    async def run():
        started = asyncio.Event()
        async def blocked(*args, **kwargs):
            started.set(); await asyncio.Event().wait()
        app = main.create_app()
        with patch.object(main, 'initialize_runtime'), patch.object(main, '_start_background_tasks', new_callable=AsyncMock), patch.object(main.onebot, 'disconnect_all_connections', new=AsyncMock(return_value=0)), patch.object(main.companion_service, 'shutdown'), patch.object(creation, '_provider_connection', return_value=connection), patch.object(creation, '_request_remote_image', new=AsyncMock(side_effect=blocked)):
            async with main.app_lifespan(app):
                creation.confirm_job(job['id'])
                await asyncio.wait_for(started.wait(), 3)
                result = await app.state.enter_maintenance('test')
                assert result['blocked'] and not creation._tasks
                assert creation.get_job(job['id'])['status'] == 'unknown'
                await app.state.finish_maintenance('restart_required', resume=False)
    asyncio.run(run())


def test_bad_older_diary_does_not_block_fresh_dates_even_after_restart():
    from app import daily_diary_service as daily
    for date in ['2026-08-01', '2026-09-06']:
        db.add_diary_material('synthetic', date=date)
    object.__setattr__(settings, 'daily_diary_auto_enabled', True)
    now = datetime.fromisoformat('2026-09-09T12:00:00+08:00')
    async def run():
        async def generate(date, **kwargs):
            if date == '2026-08-01':
                raise ValueError('old date failed')
            db.upsert_diary(date, 'new', 'synthetic', '', 'done')
            return {'skipped': False}
        with patch.object(daily, 'generate_diary_for_date_payload', new=AsyncMock(side_effect=generate)) as model:
            with pytest.raises(ValueError): await daily.run_daily_diary_once(now)
            # Discard transient backoff just as a restarted process does.
            with patch.object(daily, '_retry_at', 0), patch.object(daily, '_retry_signature', ()):
                assert await daily.run_daily_diary_once(now) == 1
                assert await daily.run_daily_diary_once(now) == 0
            assert [call.args[0] for call in model.await_args_list] == ['2026-08-01', '2026-09-06']
    asyncio.run(run())
