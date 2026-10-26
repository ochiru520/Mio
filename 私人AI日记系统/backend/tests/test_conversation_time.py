from __future__ import annotations

import unittest
from datetime import datetime

from app.config import settings
from app.chat_service import (
    _RUNTIME_CONTEXT_RULES,
    _annotate_history_content,
    _build_conversation_orientation_context,
    _build_current_time_context,
    _build_user_message_gap_context,
    _relative_message_time,
    replies_for_source,
)


class ConversationTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = datetime.fromisoformat("2026-07-14T08:10:00+08:00")
        for name, value in (("timezone", "Asia/Shanghai"), ("day_boundary_hour", 4)):
            original = getattr(settings, name)
            self.addCleanup(object.__setattr__, settings, name, original)
            object.__setattr__(settings, name, value)

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

    def test_message_annotations_use_stable_absolute_timestamps(self) -> None:
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
        self.assertEqual(old_content, "[内部消息时间：2026-07-13T23:30:00+08:00]\n今天很累。")
        self.assertEqual(current_content, "[内部消息时间：2026-07-14T08:10:00+08:00]\n醒了。")

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

    def test_time_context_includes_actual_offset_timezone_and_weekday(self) -> None:
        context = _build_current_time_context(self.current)
        self.assertIn("2026-07-14 08:10+08:00", context)
        self.assertIn("时区：Asia/Shanghai", context)
        self.assertIn("星期二", context)
        self.assertIn("上午", context)

    def test_record_day_boundary_is_configurable_in_facts_and_orientation(self) -> None:
        object.__setattr__(settings, "day_boundary_hour", 6)
        current = datetime.fromisoformat("2026-07-18T05:59:00+08:00")
        rows = [{"id": 1, "role": "user", "content": "还在学习。",
                 "created_at": "2026-07-17T23:59:00+08:00"}]
        context = _build_current_time_context(current)
        self.assertIn("当前记录日仍是 2026-07-17", context)
        self.assertIn("记录日边界：06:00", context)
        orientation = _build_conversation_orientation_context(rows, current=current)
        self.assertIn("仍属于同一个记录日", orientation)
        self.assertNotIn("四点", orientation)
        at_boundary = datetime.fromisoformat("2026-07-18T06:00:00+08:00")
        self.assertIn("当前记录日：2026-07-18", _build_current_time_context(at_boundary))
        self.assertIn("跨天后的新一轮对话",
                      _build_conversation_orientation_context(rows, current=at_boundary))

    def test_topic_change_does_not_claim_activity_duration(self) -> None:
        rows = [
            {"id": 1, "role": "user", "content": "我在学习。",
             "created_at": "2026-07-14T06:00:00+08:00"},
            {"id": 2, "role": "user", "content": "午饭吃什么？",
             "created_at": "2026-07-14T08:10:00+08:00"},
        ]
        context = _build_user_message_gap_context(rows, 2)
        self.assertIn("2 小时 10 分钟", context)
        self.assertNotIn("活动起点", context)
        self.assertIn("换话题或无连续性时不得认定全程在做", _RUNTIME_CONTEXT_RULES)
        self.assertIn("同一记录日内明确持续自我提升至少30分钟", _RUNTIME_CONTEXT_RULES)

    def test_internal_time_rules_remain_private_in_public_replies(self) -> None:
        self.assertIn("公开回复禁止", _RUNTIME_CONTEXT_RULES)
        reply = "[内部消息时间：2026-07-14T08:10:00+08:00]\n[今天 08:10]慢慢说。"
        self.assertEqual(replies_for_source(reply, "qq"), ["慢慢说"])
        self.assertEqual(replies_for_source(reply, "desktop"), ["慢慢说"])


if __name__ == "__main__":
    unittest.main()
