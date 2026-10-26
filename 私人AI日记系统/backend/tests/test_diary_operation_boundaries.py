"""Shared day operations preserve HTTP, QQ and persistence boundaries."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, day_end_service
from app import daily_state_analysis_service as analysis
from app import diary_operations_service as diaries
from app import agent_task_service as tasks
from app.routes import chat, day, diary, onebot, review
from app.service_errors import ServiceOperationError
from test_runtime_safety_v2 import isolated


def test_day_closing_uses_saved_diary_date_and_preserves_order(monkeypatch):
    order = []

    async def state():
        order.append('state')
        return {'mood': '平稳'}

    async def generate():
        assert order == ['state']
        order.append('diary')
        return {'date': '2026-10-02', 'markdown': '合成日记'}

    monkeypatch.setattr(analysis, 'analyze_today_state', state)
    monkeypatch.setattr(diaries, 'generate_today_diary_payload', generate)
    app = FastAPI()
    app.include_router(day.router)
    with TestClient(app) as client:
        response = client.post('/api/day/end-today')
    assert response.status_code == 200
    assert response.json() == {
        'date': '2026-10-02', 'state': {'mood': '平稳'},
        'diary': {'date': '2026-10-02', 'markdown': '合成日记'},
        'diary_url': '/diaries/2026-10-02',
    }
    assert order == ['state', 'diary']


@pytest.mark.parametrize('status', [400, 409, 502])
def test_http_analysis_and_day_closing_preserve_failure_metadata(monkeypatch, status):
    monkeypatch.setattr(analysis, 'analyze_today_state', AsyncMock(
        side_effect=ServiceOperationError(status, '合成分析错误')))
    generate = AsyncMock()
    monkeypatch.setattr(diaries, 'generate_today_diary_payload', generate)
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(day.router)
    with TestClient(app) as client:
        for url in ('/api/state/analyze-today', '/api/day/end-today'):
            response = client.post(url)
            assert response.status_code == status
            assert response.json() == {'detail': '合成分析错误'}
    generate.assert_not_awaited()


def test_day_closing_cancellation_never_starts_diary(monkeypatch):
    monkeypatch.setattr(analysis, 'analyze_today_state', AsyncMock(side_effect=asyncio.CancelledError))
    generate = AsyncMock()
    monkeypatch.setattr(diaries, 'generate_today_diary_payload', generate)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(day_end_service.end_today())
    generate.assert_not_awaited()


def test_qq_analysis_failure_is_best_effort_but_cancellation_propagates(monkeypatch):
    analyze = AsyncMock(side_effect=ServiceOperationError(502, '合成分析失败'))
    generate = AsyncMock(return_value={'date': '2026-10-02'})
    monkeypatch.setattr(analysis, 'analyze_today_state', analyze)
    monkeypatch.setattr(diaries, 'generate_today_diary_payload', generate)
    assert asyncio.run(onebot._generate_today_diary_replies()) == ['生成完毕。']
    generate.assert_awaited_once()
    generate.reset_mock()
    analyze.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(onebot._generate_today_diary_replies())
    generate.assert_not_awaited()


def test_qq_diary_failures_keep_readable_detail(monkeypatch):
    monkeypatch.setattr(analysis, 'analyze_today_state', AsyncMock(return_value={}))
    monkeypatch.setattr(diaries, 'generate_today_diary_payload', AsyncMock(
        side_effect=ServiceOperationError(400, '今天还没有素材。')))
    monkeypatch.setattr(diaries, 'edit_diary_with_instruction', AsyncMock(
        side_effect=ServiceOperationError(404, '没有找到这一天的日记。')))
    assert asyncio.run(onebot._generate_today_diary_replies()) == ['日记没生成成功：今天还没有素材。']
    assert asyncio.run(onebot._edit_diary_replies('2026-10-02', '合成指令')) == ['日记没改成功：没有找到这一天的日记。']


def test_diary_and_review_reject_nonexistent_calendar_dates_without_model_calls(isolated, monkeypatch):
    generate = AsyncMock()
    monkeypatch.setattr(review, 'generate_review_for_date', generate)
    app = FastAPI()
    app.include_router(diary.router)
    app.include_router(review.router)
    with TestClient(app) as client:
        for method, url in (
            ('get', '/api/diaries/2026-02-30'),
            ('post', '/api/reviews/2026-02-30/generate'),
        ):
            response = getattr(client, method)(url)
            assert response.status_code == 400
            assert response.json() == {'detail': '日记日期无效。'}
    generate.assert_not_awaited()


def test_http_diary_generate_maps_domain_failure_and_keeps_request_body_schema(isolated, monkeypatch):
    monkeypatch.setattr(diary, 'generate_today_diary_payload', AsyncMock(
        side_effect=ServiceOperationError(409, '隐私暂停中，日记生成已停止。')))
    app = FastAPI()
    app.include_router(diary.router)
    with TestClient(app) as client:
        response = client.post('/api/diary/generate-today')
        assert response.status_code == 409
        assert response.json()['detail'] == '隐私暂停中，日记生成已停止。'
        db.upsert_diary('2026-10-02', '合成标题', '# 原内容')
        assert client.put('/api/diaries/2026-10-02', json={}).status_code == 422
        changed = client.put('/api/diaries/2026-10-02', json={
            'markdown_content': '# 更新内容', 'expected_revision': 1,
        })
        assert changed.status_code == 200
        assert changed.json()['revision'] == 2
        stale = client.put('/api/diaries/2026-10-02', json={
            'markdown_content': '# 过期修改', 'expected_revision': 1,
        })
        assert stale.status_code == 409
        assert db.get_diary('2026-10-02')['markdown_content'] == '# 更新内容'


@pytest.mark.parametrize('failure', ['paused', 'cancelled', 'provider'])
def test_generation_never_saves_after_pause_cancellation_or_provider_failure(isolated, monkeypatch, failure):
    date = '2026-10-02'
    db.add_diary_material('合成素材', date=date)
    paused = False

    async def complete(*args, **kwargs):
        nonlocal paused
        if failure == 'paused':
            paused = True
            return '# 迟到内容'
        if failure == 'cancelled':
            raise asyncio.CancelledError()
        raise RuntimeError('合成供应商失败')

    monkeypatch.setattr(tasks, 'automatic_work_paused', lambda: paused)
    monkeypatch.setattr(diaries, '_diary_model_id', lambda: 'synthetic')
    monkeypatch.setattr(diaries, 'call_chat_completion', complete)
    expected = asyncio.CancelledError if failure == 'cancelled' else ServiceOperationError
    with pytest.raises(expected) as error:
        asyncio.run(diaries.generate_diary_for_date_payload(date))
    if failure != 'cancelled':
        assert error.value.status_code == (409 if failure == 'paused' else 502)
    assert db.get_diary(date) is None
    assert not db.settings.diary_dir.joinpath(date + '.md').exists()
    assert db.list_diary_materials(date)[0]['used_in_diary'] == 0
