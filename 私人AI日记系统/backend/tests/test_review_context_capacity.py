import asyncio
from contextlib import closing
from unittest.mock import AsyncMock, patch

import pytest

from app import db
from app import review_context_service as context
from app.weekly_review_service import _build_day_sections as week_sections
from app.monthly_review_service import _build_day_sections as month_sections
from test_runtime_safety_v2 import isolated


def test_full_review_and_state_tail_are_not_silently_truncated(isolated):
    date='2026-08-31'
    db.upsert_diary(date,'合成日记','合成正文')
    db.upsert_daily_review(date,'前序记录'*1000+'末尾回顾证据')
    week,_=week_sections(date)
    month,_=month_sections('2026-08')
    assert '末尾回顾证据' in week and '末尾回顾证据' in month


def test_unchanged_chunks_are_cached_and_changed_sources_are_reextracted(isolated):
    callback=AsyncMock(return_value='- 2026-08-31：合成事实')
    text='### 2026-08-31\n'+'合成原文'*7000+'末尾来源'
    async def run():
        first=await context.bounded('2026-08',text,callback)
        calls=callback.await_count
        assert calls>=2 and len(first)<=18000
        assert any('末尾来源' in call.args[0][1]['content'] for call in callback.await_args_list)
        assert await context.bounded('2026-08',text,callback)==first
        assert callback.await_count==calls
        await context.bounded('2026-08',text+'来源更正',callback)
        assert callback.await_count==calls+1
        with patch.object(context,'fingerprint',return_value='changed-model'):
            await context.bounded('2026-08',text,callback)
        assert callback.await_count>calls+1
    asyncio.run(run())


def test_hierarchical_merge_bounds_all_extracted_facts_without_dropping_last_chunk(isolated):
    originals=[]; merges=[]
    async def callback(messages, **kwargs):
        text=messages[1]['content']
        if '合并以下' in messages[0]['content']:
            merges.append(text)
            return '- 2026-08-31：分层保留来源末尾证据'
        originals.append(text)
        return '- 2026-08-31：合成来源事实\n'*120
    text='### 2026-08-31\n'+'合成原文'*40000+'最后一段证据'
    result=asyncio.run(context.bounded('2026-08',text,callback))
    assert len(result)<=18000 and merges and '最后一段证据' in originals[-1]
    assert '分层合并' in result and '来源末尾证据' in result


def test_noncompressing_model_or_cancel_does_not_publish_partial_cache_as_complete(isolated):
    text='### 2026-08-31\n'+'合成原文'*6000
    async def echo(messages, **kwargs): return messages[1]['content']
    with pytest.raises(ValueError,match='有效压缩'):
        asyncio.run(context.bounded('2026-08',text,echo))
    async def cancel(messages, **kwargs): raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(context.bounded('2026-08',text,cancel))
    with closing(db.get_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM review_fact_cache').fetchone()[0]==0
