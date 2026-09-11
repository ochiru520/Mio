import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import db, chat_service as chat, autonomy_service as autonomy
from app.config import settings
from app.llm import CompletionResult, LLMConfigError


@pytest.fixture(autouse=True)
def isolated(tmp_path):
    changes=dict(db_path=tmp_path/'db.sqlite', data_dir=tmp_path,
                 companion_config_path=tmp_path/'companion.json', qq_allowed_user_ids=('10001',),
                 qq_proactive_enabled=True, qq_bot_enabled=False,
                 qq_proactive_min_idle_minutes=120, qq_proactive_max_idle_minutes=120,
                 qq_proactive_day_start_hour=8, qq_proactive_day_end_hour=22)
    old={key:getattr(settings,key) for key in changes}
    for key,value in changes.items():object.__setattr__(settings,key,value)
    db.init_db()
    yield
    for key,value in old.items():object.__setattr__(settings,key,value)


@pytest.mark.parametrize('kind', ['proactive','startup','night'])
def test_background_messages_use_saved_chat_model(kind):
    completion=CompletionResult(content='你好',model='chosen',profile_id='chosen',prompt_tokens=1,
        cached_prompt_tokens=0,completion_tokens=1,reasoning_tokens=0,cost_yuan=0,cost_source='test')
    context=SimpleNamespace(system_context='',raw_messages=[])
    async def run():
        with patch('app.companion_service.load_config',return_value={'chat_model_id':'chosen','chat_reasoning_level':'high'}), patch.object(chat,'resolve_model_id',side_effect=lambda value:value), patch.object(chat,'require_configured') as configured, patch.object(chat,'load_manuals',return_value={}), patch.object(chat,'build_system_prompt',return_value='test'), patch.object(chat,'_build_qq_thinking_context',return_value=''), patch.object(chat,'build_chat_context',new=AsyncMock(return_value=context)), patch.object(chat,'build_chat_context_snapshot',return_value=context), patch.object(chat,'call_chat_completion_result',new=AsyncMock(return_value=completion)) as call:
            if kind=='proactive':await chat.generate_qq_proactive_replies('test',120)
            elif kind=='startup':await chat.generate_desktop_startup_replies('test')
            else:await chat.generate_qq_night_close_replies('test')
            configured.assert_called_once_with('chosen')
            assert call.await_args.kwargs['model_id']=='chosen'
            assert call.await_args.kwargs['reasoning_level']==('off' if kind=='startup' else 'high')
    asyncio.run(run())


def test_removed_selection_is_not_silently_sent_to_old_default():
    with patch('app.companion_service.load_config',return_value={'chat_model_id':'removed'}), patch.object(chat,'resolve_model_id',side_effect=LLMConfigError('removed')), patch.object(chat,'call_chat_completion_result',new_callable=AsyncMock) as model:
        with pytest.raises(LLMConfigError):asyncio.run(chat.generate_qq_proactive_replies('test',120))
        model.assert_not_awaited()


NOW=datetime.fromisoformat('2026-09-07T12:00:00+08:00')


def prepare():
    autonomy.update_policy({'quiet_start_hour':0,'quiet_end_hour':0})
    mid=db.save_message('user','test',conversation_id='qq_private_10001')
    with db.get_conn() as conn:
        conn.execute('UPDATE messages SET created_at=? WHERE id=?',((NOW-timedelta(hours=3)).isoformat(timespec='seconds'),mid))
    assert autonomy.collect_scheduled_proactive_events(NOW)==1


def test_generation_retries_twice_then_stops_without_fake_delivery():
    prepare()
    async def run():
        with patch.object(chat,'generate_qq_proactive_replies',new=AsyncMock(side_effect=RuntimeError('network unavailable'))) as model:
            decisions=[]
            for minutes in [0,5,15]:
                result=await autonomy.process_once(NOW+timedelta(minutes=minutes))
                decisions.append(result[0]['decision'])
            assert decisions==['retry','retry','failed']
            assert model.await_count==3
            assert not await autonomy.process_once(NOW+timedelta(minutes=30))
            assert db.list_agent_events()[0]['status']=='failed'
            assert not db.list_autonomy_behaviors()
            assert len(db.get_recent_messages(20,'qq_private_10001'))==1
    asyncio.run(run())


def test_retry_is_discarded_after_user_resumes_chat():
    prepare()
    async def run():
        with patch.object(chat,'generate_qq_proactive_replies',new=AsyncMock(side_effect=RuntimeError('network unavailable'))) as model:
            await autonomy.process_once(NOW)
            mid=db.save_message('user','new chat',conversation_id='qq_private_10001')
            with db.get_conn() as conn:
                conn.execute('UPDATE messages SET created_at=? WHERE id=?',((NOW+timedelta(minutes=1)).isoformat(timespec='seconds'),mid))
            result=await autonomy.process_once(NOW+timedelta(minutes=5))
            assert result[0]['decision']=='ignore'
            assert model.await_count==1
    asyncio.run(run())


def test_pending_retry_blocks_duplicate_scheduled_events_and_expires_at_night():
    prepare()
    assert autonomy.collect_scheduled_proactive_events(NOW+timedelta(hours=3))==0
    assert len(db.list_agent_events())==1
    async def run():
        with patch.object(chat,'generate_qq_proactive_replies',new_callable=AsyncMock) as model:
            result=await autonomy.process_once(NOW.replace(hour=23))
            assert result[0]['decision']=='ignore'
            model.assert_not_awaited()
    asyncio.run(run())
