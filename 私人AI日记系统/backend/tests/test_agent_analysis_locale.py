"""Public analysis language gating without translation calls or reply edits."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app import db, agent_loop_service as loop
from app.agent_analysis_locale import ChineseAnalysisRecorder, is_chinese_analysis
from test_agent_live_progress import make_task, agent, completion
from test_runtime_safety_v2 import isolated


@pytest.mark.parametrize('text,expected', [
    ('先读取 source.docx，再检查 API 回执。', True),
    ('使用 agent_listfiles 查看 D:\\Mio\\Data\\项目。', True),
    ('我会先核对 PDF 文档，保持原来的内容结构。', True),
    ('先检查来源。', True),
    ('检查 Python 脚本并运行 pytest。', True),
    ('使用 npm 和 TypeScript 检查。', True),
    ('先运行测试。\n```python\nprint("hello world")\n```', True),
    ('```python\nprint("hello world")\n```', True),
    ('C:\\Mio\\Data\\source.docx', True),
    ('Let me understand what is happening.', False),
    ('The engine is 搜狗 and returned few results with empty snippets.', False),
    ('July-October 2026 events... 国庆节，二十届四中全会. Need verification.', False),
    ('Let', False),
    ('Let me run Python and pytest now.', False),
])
def test_language_gate_keeps_chinese_with_technical_identifiers(text, expected):
    assert is_chinese_analysis(text) is expected


def recorded(run):
    return [(row['event_type'], json.loads(row['payload_json']))
            for row in db.list_agent_run_events(run)]


def test_fragmented_english_never_leaks_and_notice_only_emits_once():
    make_task()
    recorder = ChineseAnalysisRecorder('live-run', round_index=2)
    asyncio.run(recorder.delta('Let', 'Let'))
    assert not any(kind == 'analysis_text_delta' for kind, _ in recorded('live-run'))
    asyncio.run(recorder.delta(' me find the source docx file.\nAnother English sentence.\n', ''))
    asyncio.run(recorder.delta('先读取 source.docx，', ''))
    asyncio.run(recorder.delta('再验证修改结果。', ''))
    recorder.finish('generated')
    events = recorded('live-run')
    notices = [payload for kind, payload in events if kind == 'analysis_language_notice']
    assert len(notices) == 1
    assert notices[0]['reason'] == 'non_chinese_analysis' and notices[0]['round'] == 2
    assert '不是中文' in notices[0]['text']
    chunks = ''.join(payload['text'] for kind, payload in events if kind == 'analysis_text_delta')
    assert chunks == '先读取 source.docx，再验证修改结果。'
    assert events[-1][1]['suppressed_chars'] > 0 and events[-1][1]['language'] == 'zh-CN'


def test_interrupted_chinese_span_is_preserved_without_english_partial():
    make_task()
    recorder = ChineseAnalysisRecorder('live-run')
    asyncio.run(recorder.delta('已经核对文件\nLet me', ''))
    recorder.finish('cancelled')
    events = recorded('live-run')
    assert any(kind == 'analysis_text_delta' and '已经核对文件' in payload['text'] for kind, payload in events)
    assert not any(kind == 'analysis_text_delta' and 'Let' in payload['text'] for kind, payload in events)
    assert events[-1][1]['status'] == 'cancelled'


@pytest.mark.parametrize('marker', ['```', '~~~'])
def test_fenced_code_is_not_split_by_stream_chunks_or_language_gate(marker):
    make_task()
    recorder = ChineseAnalysisRecorder('live-run')
    chunks = ['检查 Python 脚本并运行 pytest。\n', marker[:2], marker[2:] + 'python\n',
              'print("hello ', 'world")\n', '# 中文注释。\n', marker + '\n',
              'Let me check results.\n', '再检查运行结果。']
    for chunk in chunks[:6]:
        asyncio.run(recorder.delta(chunk, ''))
    before = ''.join(payload['text'] for kind, payload in recorded('live-run') if kind == 'analysis_text_delta')
    assert before == chunks[0]
    for chunk in chunks[6:]:
        asyncio.run(recorder.delta(chunk, ''))
    recorder.finish('generated')
    events = recorded('live-run')
    shown = ''.join(payload['text'] for kind, payload in events if kind == 'analysis_text_delta')
    assert shown == ''.join(chunks[:7]) + chunks[-1]
    assert any(kind == 'analysis_language_notice' for kind, _ in events)


def test_interrupted_code_fence_keeps_original_source_without_inventing_closure():
    make_task()
    recorder = ChineseAnalysisRecorder('live-run')
    original = '```python\nprint("hello world")\n'
    asyncio.run(recorder.delta(original, original))
    recorder.finish('cancelled')
    shown = ''.join(payload['text'] for kind, payload in recorded('live-run') if kind == 'analysis_text_delta')
    assert shown == original


def test_closing_fence_chunk_keeps_following_newline_before_chinese_text():
    make_task()
    recorder = ChineseAnalysisRecorder('live-run')
    first = '```python\nprint("hello world")\n```'
    asyncio.run(recorder.delta(first, first))
    asyncio.run(recorder.delta('\n再核对结果。', ''))
    recorder.finish('generated')
    shown = ''.join(payload['text'] for kind, payload in recorded('live-run') if kind == 'analysis_text_delta')
    assert shown == first + '\n再核对结果。'


def test_final_response_body_and_provider_result_are_not_translated():
    task = make_task()
    result = completion('用户引用原文: Keep this exact code')
    object.__setattr__(result, 'reasoning_text', 'Let me inspect the file.\n先确认文件内容。')
    callback = AsyncMock(return_value=result)
    returned = asyncio.run(loop.final_model_call(agent(task), callback, [], model_id='synthetic'))
    callback.assert_awaited_once()
    assert returned.reasoning_text == result.reasoning_text
    events = recorded('live-run')
    response = ''.join(payload['text'] for kind, payload in events if kind == 'response_text_delta')
    analysis = ''.join(payload['text'] for kind, payload in events if kind == 'analysis_text_delta')
    assert response == result.content
    assert analysis == '先确认文件内容。'


def test_planner_stream_uses_language_gate_without_extra_model_calls():
    make_task()
    async def stream(messages, **kwargs):
        await kwargs['on_reasoning']('Let', 'Let')
        await kwargs['on_reasoning'](' me inspect the file.\n先检查当前权限。', '')
        return completion()
    callback = AsyncMock(side_effect=stream)
    with patch('app.llm.call_chat_completion_stream_result', callback):
        asyncio.run(loop._plan([], model_id='synthetic', reasoning_level='low',
            request_id='language', allow_native=True, run_id='live-run'))
    callback.assert_awaited_once()
    events = recorded('live-run')
    assert any(kind == 'analysis_language_notice' for kind, _ in events)
    assert ''.join(payload['text'] for kind, payload in events if kind == 'analysis_text_delta') == '先检查当前权限。'
