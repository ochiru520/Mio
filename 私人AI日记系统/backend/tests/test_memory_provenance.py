from unittest.mock import patch

from app import db
from app.life_loop_service import build_follow_up_result_context
from test_runtime_safety_v2 import isolated


def test_follow_up_context_preserves_recorded_time_without_claiming_event_time():
    row = {
        'id': 1, 'created_at': '2025-01-01T12:00:00+08:00',
        'thread_content': 'tomorrow release', 'summary': 'historical result',
        'outcome': 'completed', 'adjustment': '', 'next_follow_up_after': '',
        'source_message_id': 0,
    }
    with patch.object(db, 'list_follow_up_results', return_value=[row]):
        text = build_follow_up_result_context('audit')
    assert '记录于=2025-01-01T12:00:00+08:00' in text
    assert '发生于' not in text
