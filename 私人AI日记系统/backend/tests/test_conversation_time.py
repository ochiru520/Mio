from __future__ import annotations

import unittest
from datetime import datetime

from app.chat_service import (
    _annotate_history_content,
    _build_conversation_orientation_context,
    _build_current_time_context,
    _build_user_message_gap_context,
    _relative_message_time,
)


class ConversationTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = datetime.fromisoformat("2026-07-14T08:10:00+08:00")

    def test_relative_labels_distinguish_today_and_yesterday(self) -> None:
        self.assertEqual(
            _relative_message_time("2026-07-14T07:30:00+08:00", self.current),
            "今天 07:30",
        )
        self.assertEqual(
            _relative_message_time("2026-07-13T23:30:00+08:00", self.current),
            "昨天 23:30",
        )

    def test_cross_day_start_is_explicitly_oriented(self) -> None:
        rows = [
            {
                "id": 1,
                "role": "assistant",
                "content": "今天早点睡。",
                "created_at": "2026-07-13T23:30:00+08:00",
            },
            {
                "id": 2,
                "role": "user",
                "content": "醒了。",
                "created_at": "2026-07-14T08:10:00+08:00",
            },
        ]
        context = _build_conversation_orientation_context(
            rows,
            current_message_id=2,
            current=self.current,
        )
        self.assertIn("跨天后的新一轮对话", context)
        self.assertIn("昨天消息里的“今天”现在必须理解为昨天", context)
        self.assertIn("上一条可见消息：昨天 23:30", context)

    def test_same_day_long_gap_is_a_new_session_not_a_new_day(self) -> None:
        rows = [
            {
                "id": 1,
                "role": "assistant",
                "content": "一会儿见。",
                "created_at": "2026-07-14T05:00:00+08:00",
            },
            {
                "id": 2,
                "role": "user",
                "content": "我回来了。",
                "created_at": "2026-07-14T08:10:00+08:00",
            },
        ]
        context = _build_conversation_orientation_context(
            rows,
            current_message_id=2,
            current=self.current,
        )
        self.assertIn("今天间隔约 3 小时后重新开始", context)
        self.assertNotIn("跨天", context)

    def test_message_annotations_mark_current_turn_without_leaking_as_reply(self) -> None:
        old_row = {
            "id": 1,
            "created_at": "2026-07-13T23:30:00+08:00",
        }
        current_row = {
            "id": 2,
            "created_at": "2026-07-14T08:10:00+08:00",
        }
        old_content = _annotate_history_content(old_row, "今天很累。", self.current, 2)
        current_content = _annotate_history_content(current_row, "醒了。", self.current, 2)
        self.assertEqual(old_content, "[内部消息时间：昨天 23:30]\n今天很累。")
        self.assertEqual(current_content, "[内部消息时间：今天 08:10（本轮新消息）]\n醒了。")

    def test_before_four_cross_midnight_is_same_record_day(self) -> None:
        current = datetime.fromisoformat("2026-07-18T01:00:00+08:00")
        rows = [
            {
                "id": 1,
                "role": "user",
                "content": "我在做东西。",
                "created_at": "2026-07-17T23:00:00+08:00",
            },
            {
                "id": 2,
                "role": "user",
                "content": "还在做。",
                "created_at": "2026-07-18T01:00:00+08:00",
            },
        ]

        self.assertEqual(_relative_message_time(rows[0]["created_at"], current), "今天（昨晚）23:00")
        orientation = _build_conversation_orientation_context(rows, current_message_id=2, current=current)
        self.assertIn("仍属于同一个记录日", orientation)
        self.assertIn("实际间隔约 2 小时", orientation)
        self.assertNotIn("跨天后的新一轮", orientation)

        time_context = _build_current_time_context(current)
        self.assertIn("当前记录日仍是 2026-07-17", time_context)

    def test_user_gap_context_uses_previous_user_message(self) -> None:
        rows = [
            {
                "id": 1,
                "role": "user",
                "content": "我在做东西。",
                "created_at": "2026-07-17T23:00:00+08:00",
            },
            {
                "id": 2,
                "role": "assistant",
                "content": "嗯。",
                "created_at": "2026-07-17T23:00:05+08:00",
            },
            {
                "id": 3,
                "role": "user",
                "content": "还在做。",
                "created_at": "2026-07-18T01:00:00+08:00",
            },
        ]

        context = _build_user_message_gap_context(rows, current_message_id=3)
        self.assertIn("两次用户发言实际间隔：约 2 小时", context)
        self.assertIn("我在做东西", context)
        self.assertIn("还在做", context)
        self.assertIn("属于同一个记录日", context)

    def test_user_gap_context_finds_activity_anchor_before_intervening_chat(self) -> None:
        rows = [
            {
                "id": 1,
                "role": "user",
                "content": "我在搭建 AI 流水线。",
                "created_at": "2026-07-17T23:45:00+08:00",
            },
            {
                "id": 2,
                "role": "user",
                "content": "有点难度。",
                "created_at": "2026-07-18T00:47:00+08:00",
            },
            {
                "id": 3,
                "role": "user",
                "content": "还在弄流水线。",
                "created_at": "2026-07-18T00:48:00+08:00",
            },
        ]

        context = _build_user_message_gap_context(rows, current_message_id=3)
        self.assertIn("最近的活动起点线索", context)
        self.assertIn("23:45", context)
        self.assertIn("1 小时 3 分钟", context)


if __name__ == "__main__":
    unittest.main()
