"""Automatic diaries: offline catch-up, non-destructive saves and useful failures."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app import db, daily_diary_service as daily, llm
from app.config import settings
from app.model_registry import ModelProfile
from app.routes import diary


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    values = dict(data_dir=tmp_path, db_path=tmp_path / 'db.sqlite',
                  diary_dir=tmp_path / 'diaries', model_profiles_path=tmp_path / 'models.json',
                  daily_diary_auto_enabled=True, daily_diary_model_id='',
                  day_boundary_hour=4, timezone='Asia/Shanghai')
    originals = {key: getattr(settings, key) for key in values}
    for key, value in values.items():
        object.__setattr__(settings, key, value)
    monkeypatch.setattr(daily, '_active', None)
    monkeypatch.setattr(daily, '_manual_check', None)
    monkeypatch.setattr(daily, '_retry_at', 0.0)
    monkeypatch.setattr(daily, '_failures', 0)
    monkeypatch.setattr(daily, '_retry_signature', ())
    monkeypatch.setattr(daily, '_last_check', {})
    db.init_db()
    settings.diary_dir.mkdir(exist_ok=True)
    yield
    for key, value in originals.items():
        object.__setattr__(settings, key, value)


NOW = datetime.fromisoformat('2026-09-07T01:00:00+08:00')


def test_restart_catches_multiple_old_days_one_per_tick_without_overwriting():
    for date in ('2026-09-03', '2026-09-04', '2026-09-05', '2026-09-06'):
        db.add_diary_material('素材', date=date)
    db.upsert_diary('2026-09-04', '已有', '保持原样', '', 'done')

    async def generate(date, **kwargs):
        assert kwargs == {'overwrite': False}
        db.upsert_diary(date, '自动', '补写', '', 'unknown')
        return {'skipped': False}

    async def run():
        with patch.object(daily, 'generate_diary_for_date_payload', new=AsyncMock(side_effect=generate)) as mock:
            assert await daily.run_daily_diary_once(NOW) == 1
            assert [call.args[0] for call in mock.await_args_list] == ['2026-09-03']
            assert await daily.run_daily_diary_once(NOW) == 1
            assert await daily.run_daily_diary_once(NOW) == 0
            assert [call.args[0] for call in mock.await_args_list] == ['2026-09-03', '2026-09-05']
    asyncio.run(run())
    assert db.get_diary('2026-09-04')['markdown_content'] == '保持原样'
    assert db.get_diary('2026-09-06') is None


def test_message_dates_follow_boundary_and_group_chats_are_excluded():
    with db.get_conn() as conn:
        for timestamp, conversation in [('2026-09-05T02:00:00+08:00', 'desktop_a'),
                                        ('2026-09-03T14:00:00+08:00', 'qq_group_1'),
                                        ('2026-09-07T01:00:00+08:00', 'desktop_a')]:
            conn.execute("INSERT INTO messages(role,content,source,conversation_id,created_at) VALUES('user','test','desktop',?,?)", (conversation, timestamp))
    assert daily._missing_dates(NOW) == ['2026-09-04']


def test_auth_error_is_visible_backed_off_and_model_change_releases_it():
    db.add_diary_material('素材', date='2026-09-05')
    async def run():
        with patch.object(daily, 'generate_diary_for_date_payload', new=AsyncMock(side_effect=HTTPException(502, '模型鉴权失败 HTTP 401'))) as generate:
            with pytest.raises(HTTPException):
                await daily.run_daily_diary_once(NOW)
            assert daily.get_daily_diary_status()['error'] == '模型鉴权失败 HTTP 401'
            assert daily.get_daily_diary_status()['retry_after_seconds'] > 250
            assert await daily.run_daily_diary_once(NOW) == 0
            assert generate.await_count == 1
            settings.model_profiles_path.write_text('{}')
            with pytest.raises(HTTPException):
                await daily.run_daily_diary_once(NOW)
            assert generate.await_count == 2
    asyncio.run(run())


def test_manual_save_during_generation_is_preserved():
    date = '2026-09-05'
    db.add_diary_material('素材', date=date)
    async def model(*args, **kwargs):
        diary.save_diary_markdown(date, '手写内容', '', 'done', '2026-09-07')
        return 'AI 迟到结果'
    async def run():
        with patch.object(diary, 'call_chat_completion', new=AsyncMock(side_effect=model)), patch.object(diary, '_diary_model_id', return_value='test'):
            assert (await diary.generate_diary_for_date_payload(date, overwrite=False))['skipped']
    asyncio.run(run())
    assert db.get_diary(date)['markdown_content'] == '手写内容'
    assert (settings.diary_dir / f'{date}.md').read_text(encoding='utf-8') == '手写内容'


def test_parallel_checks_do_not_duplicate_and_privacy_cancels_inflight():
    db.add_diary_material('素材', date='2026-09-05')
    async def run():
        started = asyncio.Event()
        async def generate(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        with patch.object(daily, 'generate_diary_for_date_payload', new=AsyncMock(side_effect=generate)) as model:
            task = asyncio.create_task(daily.run_daily_diary_once(NOW))
            await started.wait()
            assert await daily.run_daily_diary_once(NOW, force=True) == 0
            from app.privacy_service import _save_state
            _save_state({'paused': True})
            await daily.cancel_active()
            assert await task == 0
            assert await daily.run_daily_diary_once(NOW) == 0
            assert model.await_count == 1
            assert daily.get_daily_diary_status()['result'] == 'paused'
    asyncio.run(run())


def test_explicit_diary_model_does_not_silently_fallback_when_removed():
    object.__setattr__(settings, 'daily_diary_model_id', 'chosen')
    profiles = [SimpleNamespace(id='first', base_urls=('test',), api_key='test'),
                SimpleNamespace(id='chosen', base_urls=('test',), api_key='test')]
    with patch.object(diary, 'list_model_profiles', return_value=profiles):
        assert diary._diary_model_id() == 'chosen'
    with patch.object(diary, 'list_model_profiles', return_value=profiles[:1]):
        with pytest.raises(llm.LLMConfigError, match='日记专用模型不可用'):
            diary._diary_model_id()


def test_finished_child_keeps_slot_until_owner_records_result():
    db.add_diary_material('素材', date='2026-09-05')
    async def run():
        completed = asyncio.create_task(asyncio.sleep(0))
        await completed
        with patch.object(daily, '_active', completed), patch.object(daily, 'generate_diary_for_date_payload', new_callable=AsyncMock) as model:
            assert await daily.run_daily_diary_once(NOW, force=True) == 0
            model.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('fallback_success', [False, True])
def test_html_compatibility_route_does_not_hide_401_or_valid_fallback(fallback_success):
    profile = ModelProfile(id='test', provider_name='Test', display_name='Test', model='test',
                           base_urls=('https://example.test/v1',), api_key='test')
    routes = [llm.CompletionRoute('https://example.test/v1', '', 'direct'),
              llm.CompletionRoute('https://example.test', '', 'direct')]
    responses = [httpx.Response(401, json={'error': 'Invalid key'}), httpx.Response(200, text='<html>Login</html>')]
    if fallback_success:
        routes.append(llm.CompletionRoute('https://backup.test/v1', '', 'direct'))
        responses.append(httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]}))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = responses
    async def run():
        with patch.object(llm, 'require_configured'), patch.object(llm, 'resolve_model_id', return_value='test'), patch.object(llm, 'get_model_profile', return_value=profile), patch.object(llm, '_completion_routes', return_value=routes), patch.object(llm.httpx, 'AsyncClient', return_value=client):
            if fallback_success:
                assert (await llm.call_chat_completion_result([], model_id='test')).content == 'ok'
            else:
                with pytest.raises(llm.ModelRequestError, match='鉴权失败') as raised:
                    await llm.call_chat_completion_result([], model_id='test')
                assert raised.value.http_status == 401
    asyncio.run(run())
