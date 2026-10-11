from datetime import datetime

from app import db, memory_service as memory, memory_revision_service as revisions
from app.context_service import build_temporal_evidence_context, build_recent_user_timeline_context
from app.chat_service import _annotate_history_content
from test_runtime_safety_v2 import isolated


def source_memory():
    cid = 'desktop_memory_control'
    original = '我把星桥项目的私人归档标签设为 ORBIT-7391。'
    source_id = db.save_message('user', original, conversation_id=cid)
    item = memory.save_memory_item(layer='L0', category='preference', memory_key='archive-label',
        content='星桥项目的私人归档标签是 ORBIT-7391。', source_conversation_id=cid,
        source_message_id=source_id, confidence=0.99)
    return cid, original, source_id, item['id']


def test_sleep_controls_original_source_even_when_extracted_fact_is_paraphrased(isolated):
    cid, original, source_id, memory_id = source_memory()
    query = '我之前说的星桥项目私人归档标签是什么？'
    assert 'ORBIT-7391' in build_temporal_evidence_context('desktop_new', query)
    revisions.revise(memory_id, 'sleep')
    assert 'ORBIT-7391' not in build_temporal_evidence_context('desktop_new', query)
    assert 'ORBIT-7391' not in build_recent_user_timeline_context(cid)
    row = db.get_message_by_id(source_id)
    assert 'ORBIT-7391' not in _annotate_history_content(row, original, datetime.now().astimezone())
    assert db.get_message_by_id(source_id)['content'] == original
    # The current user's new message remains authoritative even if it mentions
    # the same original statement; only stored historical prompt copies change.
    assert 'ORBIT-7391' in _annotate_history_content(row, original, datetime.now().astimezone(), current_message_id=source_id)
    revisions.revise(memory_id, 'restore')
    assert 'ORBIT-7391' in build_temporal_evidence_context('desktop_new', query)
    assert 'ORBIT-7391' in build_recent_user_timeline_context(cid)


def test_correction_blocks_paraphrased_source_without_hiding_unrelated_message(isolated):
    cid, original, source_id, memory_id = source_memory()
    other_id = db.save_message('user', '另外我喜欢橙色。', conversation_id=cid)
    revisions.revise(memory_id, 'correct', content='星桥项目的私人归档标签是 ORBIT-NEW。')
    assert 'ORBIT-7391' not in revisions.sanitize_history(original, source_message_id=source_id)
    assert revisions.sanitize_history('另外我喜欢橙色。', source_message_id=other_id) == '另外我喜欢橙色。'
    assert 'ORBIT-NEW' in memory.build_structured_memory_context('desktop_new', '星桥归档标签')
