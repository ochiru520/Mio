from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import autonomy_service, db
from app.companion_action_service import execute_companion_action_primitive
from app.config import settings
from app.context_service import build_periodic_memory_context
from app.daily_review_service import run_daily_review_once
from app.life_loop_service import (
    build_follow_up_result_context,
    diary_lifecycle,
    record_follow_up_result,
)
from app.memory_service import build_structured_memory_context, save_memory_candidate, save_memory_item
from app.review_service import ReviewResult
from app.routes.diary import api_get_diary
from app.routes.memory import (
    FollowUpResultRequest,
    api_record_follow_up_result,
    api_restore_memory_item,
)
from app.self_snapshot_service import build_self_snapshot


ZONE = timezone(timedelta(hours=8))


class StageNineLifeLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.originals = {
            "db_path": settings.db_path,
            "diary_dir": settings.diary_dir,
            "daily_review_auto_enabled": settings.daily_review_auto_enabled,
            "daily_review_auto_hour": settings.daily_review_auto_hour,
            "daily_review_auto_minute": settings.daily_review_auto_minute,
        }
        root = Path(self.temp_dir.name)
        object.__setattr__(settings, "db_path", root / "stage-nine.db")
        object.__setattr__(settings, "diary_dir", root / "日记")
        object.__setattr__(settings, "daily_review_auto_enabled", True)
        object.__setattr__(settings, "daily_review_auto_hour", 9)
        object.__setattr__(settings, "daily_review_auto_minute", 0)
        settings.diary_dir.mkdir(parents=True, exist_ok=True)
        db.init_db()

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            object.__setattr__(settings, name, value)
        self.temp_dir.cleanup()

    # 对话 -> 记忆：五个隔离案例。

    def test_dialogue_memory_keeps_source_and_enters_natural_context(self) -> None:
        message_id = db.save_message("user", "我更喜欢简短直接的回答", conversation_id="desktop_memory")
        saved = save_memory_item(
            layer="L0",
            category="preference",
            memory_key="preferred_reply_style",
            content="用户更喜欢简短直接的回答",
            source_conversation_id="desktop_memory",
            source_message_id=message_id,
            confidence=0.98,
        )

        row = db.get_structured_memory(int(saved["id"]))
        context = build_structured_memory_context("desktop_memory", "回答风格")

        self.assertEqual(int(row["source_message_id"]), message_id)
        self.assertIn("简短直接", context)

    def test_memory_revision_preserves_superseded_history(self) -> None:
        first = save_memory_item(
            layer="L0", category="preference", memory_key="drink", content="喜欢热茶",
            source_conversation_id="default", confidence=1,
        )
        second = save_memory_item(
            layer="L0", category="preference", memory_key="drink", content="现在更喜欢温水",
            source_conversation_id="default", confidence=1,
        )

        self.assertEqual(db.get_structured_memory(int(first["id"]))["status"], "superseded")
        self.assertEqual(db.get_structured_memory(int(second["id"]))["status"], "active")

    def test_superseded_memory_can_be_restored_as_only_active_version(self) -> None:
        first = save_memory_item(
            layer="L0", category="preference", memory_key="reply_tone", content="喜欢自然口吻",
            source_conversation_id="default", confidence=1,
        )
        second = save_memory_item(
            layer="L0", category="preference", memory_key="reply_tone", content="喜欢正式口吻",
            source_conversation_id="default", confidence=1,
        )

        self.assertTrue(db.restore_structured_memory(int(first["id"])))
        self.assertEqual(db.get_structured_memory(int(first["id"]))["status"], "active")
        self.assertEqual(db.get_structured_memory(int(second["id"]))["status"], "superseded")
        context = build_structured_memory_context("default", "口吻")
        self.assertIn("自然口吻", context)
        self.assertNotIn("正式口吻", context)

    def test_archived_memory_can_be_restored(self) -> None:
        saved = save_memory_item(
            layer="L2", category="project", memory_key="mio_project", content="正在完善澪 Agent",
            source_conversation_id="default", confidence=1,
        )
        memory_id = int(saved["id"])
        self.assertTrue(db.archive_structured_memory(memory_id))
        self.assertTrue(db.restore_structured_memory(memory_id))
        self.assertEqual(db.get_structured_memory(memory_id)["status"], "active")

    def test_active_and_candidate_memory_cannot_bypass_restore_rules(self) -> None:
        active = save_memory_item(
            layer="L0", category="identity", memory_key="city", content="住在重庆",
            source_conversation_id="default", confidence=1,
        )
        candidate = save_memory_candidate(
            layer="L1", category="current_state", memory_key="sleep", content="最近睡得较晚",
            source_conversation_id="default", confidence=0.6,
        )
        self.assertFalse(db.restore_structured_memory(int(active["id"])))
        self.assertFalse(db.restore_structured_memory(int(candidate["id"])))

    async def test_memory_restore_api_returns_the_reactivated_version(self) -> None:
        first = save_memory_item(
            layer="L0", category="preference", memory_key="language", content="偏好中文",
            source_conversation_id="default", confidence=1,
        )
        save_memory_item(
            layer="L0", category="preference", memory_key="language", content="偏好日语",
            source_conversation_id="default", confidence=1,
        )

        response = await api_restore_memory_item(int(first["id"]))

        self.assertTrue(response["restored"])
        self.assertEqual(response["memory"]["content"], "偏好中文")
        self.assertEqual(response["memory"]["status"], "active")

    # 今日状态 -> 日记 -> 确认 -> 次日回顾：五个隔离案例。

    def test_diary_lifecycle_exposes_state_and_material_evidence(self) -> None:
        date = "2026-08-14"
        db.update_daily_state_summary(date=date, key_events="完成阶段九设计", mood="平静", mood_score=4)
        db.add_diary_material("完成了阶段九设计。", date=date, source="test")

        lifecycle = diary_lifecycle(date)

        self.assertTrue(lifecycle["state_ready"])
        self.assertEqual(lifecycle["material_count"], 1)
        self.assertEqual([item["status"] for item in lifecycle["steps"][:2]], ["complete", "complete"])

    def test_generated_but_unconfirmed_diary_is_visible_as_waiting(self) -> None:
        date = "2026-08-14"
        db.upsert_diary(date, "测试日记", "# 测试日记", "", "done")

        lifecycle = diary_lifecycle(date)

        self.assertTrue(lifecycle["diary_ready"])
        self.assertFalse(lifecycle["confirmed"])
        self.assertEqual(lifecycle["steps"][3]["status"], "ready")
        self.assertEqual(lifecycle["steps"][4]["status"], "blocked")

    async def test_unconfirmed_diary_never_enters_automatic_next_day_review(self) -> None:
        date = "2026-08-14"
        db.upsert_diary(date, "未确认日记", "# 未确认日记", "", "partial")
        now = datetime(2026, 8, 15, 10, 0, tzinfo=ZONE)

        with patch("app.daily_review_service.generate_review_for_date", new_callable=AsyncMock) as generate:
            count = await run_daily_review_once(now)

        self.assertEqual(count, 0)
        generate.assert_not_awaited()

    async def test_confirmed_diary_can_enter_automatic_next_day_review(self) -> None:
        date = "2026-08-14"
        db.upsert_diary(date, "已确认日记", "# 已确认日记", "", "done")
        db.set_diary_confirmed(date, True)
        now = datetime(2026, 8, 15, 10, 0, tzinfo=ZONE)
        generated = ReviewResult(date=date, markdown_content="# 次日回顾", created=True)

        with (
            patch("app.daily_review_service.generate_review_for_date", new=AsyncMock(return_value=generated)) as generate,
            patch("app.daily_review_service._notify_review_ready", new=AsyncMock()),
        ):
            count = await run_daily_review_once(now)

        self.assertEqual(count, 1)
        generate.assert_awaited_once_with(date, overwrite=False)

    def test_completed_review_closes_all_five_diary_steps(self) -> None:
        date = "2026-08-14"
        db.update_daily_state_summary(date=date, key_events="完成语音复测")
        db.add_diary_material("语音复测通过。", date=date, source="test")
        db.upsert_diary(date, "复测日记", "# 复测日记", "", "done")
        db.set_diary_confirmed(date, True)
        db.upsert_daily_review(date, "# 次日回顾")

        lifecycle = diary_lifecycle(date)

        self.assertTrue(lifecycle["review_ready"])
        self.assertEqual([item["status"] for item in lifecycle["steps"]], ["complete"] * 5)

    async def test_diary_detail_api_returns_visible_lifecycle(self) -> None:
        date = "2026-08-14"
        db.upsert_diary(date, "API 日记", "# API 日记", "", "done")

        response = await api_get_diary(date)

        self.assertEqual(response["life_loop"]["date"], date)
        self.assertEqual(len(response["life_loop"]["steps"]), 5)

    # 提醒 -> 现实结果 -> 后续调整：五个隔离案例。

    def test_completed_follow_up_is_closed_and_kept_as_result(self) -> None:
        thread_id = db.remember_pending_thread("desktop_follow", "今晚复测日语语音", "2026-08-15T20:00:00+08:00")

        result = record_follow_up_result(thread_id, outcome="completed", summary="五条日语都播放完成")

        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(db.get_pending_thread(thread_id)["status"], "resolved")
        self.assertIn("五条日语都播放完成", build_follow_up_result_context("desktop_follow"))

    def test_partial_follow_up_stays_open_with_new_time(self) -> None:
        thread_id = db.remember_pending_thread("desktop_follow", "测试真人电话", "2026-08-15T20:00:00+08:00")

        record_follow_up_result(
            thread_id,
            outcome="partial",
            summary="中文测试完成，日语还没测",
            adjustment="下次只测日语五句",
            next_follow_up_after="2026-08-16T20:00:00+08:00",
        )

        thread = db.get_pending_thread(thread_id)
        self.assertEqual(thread["status"], "open")
        self.assertEqual(thread["follow_up_after"], "2026-08-16T20:00:00+08:00")

    def test_not_completed_result_changes_later_advice_context(self) -> None:
        thread_id = db.remember_pending_thread("desktop_follow", "完成三十分钟跑步", "2026-08-15T20:00:00+08:00")
        record_follow_up_result(
            thread_id,
            outcome="not_completed",
            summary="今天膝盖不舒服，没有跑",
            adjustment="先改为十分钟散步，仍不舒服就休息",
        )

        context = build_periodic_memory_context("desktop_follow", "运动")

        self.assertIn("未完成", context)
        self.assertIn("十分钟散步", context)
        self.assertIn("不要机械重复原建议", context)

    async def test_legacy_resolve_tool_also_records_completed_result(self) -> None:
        thread_id = db.remember_pending_thread("desktop_follow", "整理作品集", "2026-08-15T20:00:00+08:00")
        message_id = db.save_message("user", "作品集已经整理完了", conversation_id="desktop_follow")

        output = await execute_companion_action_primitive(
            {"type": "resolve_thread", "content": "整理作品集", "confidence": 1},
            "desktop_follow",
            "作品集已经整理完了",
            message_id,
        )

        self.assertEqual(output, f"thread_resolved:{thread_id}")
        results = db.list_follow_up_results(thread_id=thread_id)
        self.assertEqual(results[0]["outcome"], "completed")
        self.assertEqual(int(results[0]["source_message_id"]), message_id)

    async def test_voice_retest_vertical_sample_reaches_real_feedback(self) -> None:
        snapshot = build_self_snapshot(("capabilities",))
        capability_ids = {str(item.get("capability_id") or "") for item in snapshot["capabilities"]}
        self.assertTrue({"tts", "phone"}.issubset(capability_ids))

        now = datetime(2026, 8, 15, 20, 1, tzinfo=ZONE)
        thread_id = db.remember_pending_thread(
            "desktop_voice_retest",
            "今晚复测澪的中文、日语语音和电话识别",
            (now - timedelta(minutes=1)).isoformat(timespec="seconds"),
        )
        self.assertEqual(autonomy_service.collect_pending_thread_events(now), 1)

        delivered = await autonomy_service.process_once(now)

        self.assertEqual(delivered[0]["decision"], "deliver")
        reminder = db.get_recent_messages(5, "desktop_voice_retest")[-1]
        self.assertIn("语音", reminder["content"])

        result = record_follow_up_result(
            thread_id,
            outcome="partial",
            summary="中文和日语播放正常，电话识别仍需继续测",
            adjustment="下一轮只记录固定中文短句识别率",
            next_follow_up_after="2026-08-16T20:00:00+08:00",
        )
        self.assertEqual(result["outcome"], "partial")
        self.assertEqual(db.get_pending_thread(thread_id)["status"], "open")
        self.assertIn("固定中文短句识别率", build_follow_up_result_context("desktop_voice_retest"))

    async def test_follow_up_result_api_normalizes_next_time(self) -> None:
        thread_id = db.remember_pending_thread("desktop_follow", "继续检查电话识别", "2026-08-15T20:00:00+08:00")
        response = await api_record_follow_up_result(
            thread_id,
            FollowUpResultRequest(
                outcome="partial",
                summary="只完成了一半",
                adjustment="明晚继续",
                next_follow_up_after="2026-08-16T20:30:00",
            ),
        )

        self.assertTrue(response["saved"])
        self.assertEqual(response["result"]["next_follow_up_after"], "2026-08-16T20:30:00+08:00")


if __name__ == "__main__":
    unittest.main()
