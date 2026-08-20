from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import autonomy_service, db
from app.agent_loop_service import run_agent_loop
from app.chat_service import ChatResult
from app.llm import CompletionResult, ToolCall
from app.web_search_service import WebLookup, WebSource


ZONE = datetime.now().astimezone().tzinfo


def generated_proactive_result(content: str = "刚想起你，来看看你在忙什么。") -> ChatResult:
    return ChatResult(
        reply=content,
        replies=[content],
        request_id="proactive-model-request",
        model_id="test-profile",
        provider_id="test-provider",
        provider_name="测试供应商",
        provider_model="test-model",
        provider_request_id="provider-request",
        reasoning_level="standard",
        prompt_tokens=21,
        cached_prompt_tokens=3,
        completion_tokens=8,
        reasoning_tokens=2,
        request_cost_yuan=0.012,
        request_cost_source="configured_estimate",
        first_token_latency_ms=321.0,
        total_latency_ms=654.0,
    )


def completion_with_search(query: str) -> CompletionResult:
    return CompletionResult(
        content="",
        model="test-model",
        prompt_tokens=10,
        cached_prompt_tokens=0,
        completion_tokens=5,
        reasoning_tokens=0,
        cost_yuan=0.0,
        cost_source="test",
        tool_calls=(ToolCall("search-retry", "search_web", '{"query":"' + query + '"}'),),
    )


class AutonomyServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "autonomy.db")
        db.init_db()
        self.now = datetime(2026, 8, 15, 12, 0, tzinfo=ZONE)

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def add_due_thread(self, conversation_id: str = "desktop_agent_test") -> int:
        return db.remember_pending_thread(
            conversation_id,
            "复测澪的日语语音",
            (self.now - timedelta(minutes=1)).isoformat(timespec="seconds"),
            source_message_id=42,
        )

    async def test_unowned_event_is_observed_but_never_acts(self) -> None:
        db.record_agent_event(
            "unowned:one",
            "service_health_changed",
            capability="service_health",
            relevance=1.0,
            confidence=1.0,
            urgency=1.0,
            occurred_at=self.now.isoformat(timespec="seconds"),
        )

        results = await autonomy_service.process_once(self.now)

        self.assertEqual(results[0]["decision"], "ignore")
        self.assertEqual(db.list_agent_events(limit=1)[0]["status"], "ignored")
        self.assertEqual(db.list_autonomy_behaviors(), [])
        self.assertEqual(db.get_recent_messages(10, "default"), [])

    async def test_due_thread_creates_authorized_goal_and_delivers_once(self) -> None:
        thread_id = self.add_due_thread()
        self.assertEqual(autonomy_service.collect_pending_thread_events(self.now), 1)

        first = await autonomy_service.process_once(self.now)

        self.assertEqual(first[0]["decision"], "deliver")
        goals = db.list_agent_goals(status="active")
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["source_ref"], str(thread_id))
        messages = db.get_recent_messages(10, "desktop_agent_test")
        self.assertEqual(len(messages), 1)
        self.assertIn("复测澪的日语语音", messages[0]["content"])
        self.assertEqual(db.list_due_pending_threads("desktop_agent_test", self.now.isoformat()), [])

        event = db.list_agent_events(limit=1)[0]
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE agent_events SET status = 'claimed', claimed_at = ? WHERE id = ?",
                ((self.now - timedelta(minutes=10)).isoformat(timespec="seconds"), int(event["id"])),
            )
        second = await autonomy_service.process_once(self.now)

        self.assertEqual(second[0]["decision"], "recovered")
        self.assertEqual(len(db.get_recent_messages(10, "desktop_agent_test")), 1)
        self.assertEqual(db.list_agent_events(limit=1)[0]["status"], "processed")

    async def test_pause_keeps_due_event_pending_without_behavior(self) -> None:
        self.add_due_thread()
        autonomy_service.collect_pending_thread_events(self.now)
        autonomy_service.update_policy({"paused": True})

        result = await autonomy_service.process_once(self.now)

        self.assertEqual(result[0]["decision"], "wait")
        event = db.list_agent_events(limit=1)[0]
        self.assertEqual(event["status"], "pending")
        self.assertGreater(_time(event["available_at"]), self.now)
        self.assertEqual(db.list_autonomy_behaviors(), [])

    async def test_quiet_hours_reschedule_to_morning(self) -> None:
        night = self.now.replace(hour=23)
        db.remember_pending_thread(
            "desktop_agent_test",
            "明早提醒我查看结果",
            (night - timedelta(minutes=1)).isoformat(timespec="seconds"),
        )
        autonomy_service.collect_pending_thread_events(night)

        result = await autonomy_service.process_once(night)

        self.assertEqual(result[0]["decision"], "wait")
        event = db.list_agent_events(limit=1)[0]
        available = _time(event["available_at"])
        self.assertEqual((available.hour, available.day), (8, 16))
        self.assertEqual(db.list_autonomy_behaviors(), [])

    async def test_daily_limit_zero_blocks_even_authorized_event(self) -> None:
        self.add_due_thread()
        autonomy_service.collect_pending_thread_events(self.now)
        autonomy_service.update_policy({"daily_behavior_limit": 0})

        result = await autonomy_service.process_once(self.now)

        self.assertEqual(result[0]["decision"], "wait")
        self.assertIn("次数上限", result[0]["reason"])
        self.assertEqual(db.list_autonomy_behaviors(), [])

    async def test_capability_override_can_disable_one_authorized_event(self) -> None:
        goal = autonomy_service.create_goal(
            "关注服务健康",
            capabilities=["service_health"],
        )
        db.record_agent_event(
            "service-health:disabled-override",
            "service_health_changed",
            goal_id=int(goal["id"]),
            capability="service_health",
            relevance=1.0,
            confidence=1.0,
            urgency=0.8,
            occurred_at=self.now.isoformat(timespec="seconds"),
        )
        autonomy_service.update_policy(
            {"autonomy_level": "auto_low_risk", "capability_overrides": {"service_health": "disabled"}}
        )

        result = await autonomy_service.process_once(self.now)

        self.assertEqual(result[0]["decision"], "ignore")
        self.assertIn("单独关闭", result[0]["reason"])
        self.assertEqual(db.list_autonomy_behaviors(), [])

    async def test_estimated_cost_is_blocked_before_daily_budget_is_exceeded(self) -> None:
        existing_event = db.record_agent_event(
            "budget:existing",
            "existing_behavior",
            capability="test",
            relevance=1.0,
            confidence=1.0,
            occurred_at=(self.now - timedelta(minutes=10)).isoformat(timespec="seconds"),
        )
        existing_goal = autonomy_service.create_goal("已有行为", capabilities=["test"])
        existing = db.create_autonomy_behavior(
            "budget:existing-behavior",
            event_id=int(existing_event["id"]),
            goal_id=int(existing_goal["id"]),
            conversation_id="default",
            behavior_type="notification",
            capability="test",
            risk_level="low",
            permission_mode="auto_low_risk",
            status="delivered",
            reason="预算样本",
            evidence={},
            content="预算样本",
        )
        db.update_autonomy_behavior(
            int(existing["id"]),
            {"cost_yuan": 0.04, "completed_at": (self.now - timedelta(minutes=10)).isoformat(timespec="seconds")},
        )
        # created_at 由 create 写入真实时间，需拨回逻辑日，避免跨日后 usage 统计漏算。
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE autonomy_behaviors SET created_at = ? WHERE behavior_key = ?",
                (
                    (self.now - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "budget:existing-behavior",
                ),
            )
        db.finish_agent_event(int(existing_event["id"]), "processed", reason="预算样本已完成。")
        target_goal = autonomy_service.create_goal("预算门禁", capabilities=["paid_suggestion"])
        db.record_agent_event(
            "budget:target",
            "paid_suggestion_ready",
            goal_id=int(target_goal["id"]),
            capability="paid_suggestion",
            risk_level="low",
            payload={"estimated_cost_yuan": 0.02},
            relevance=1.0,
            confidence=1.0,
            urgency=0.8,
            occurred_at=self.now.isoformat(timespec="seconds"),
        )
        autonomy_service.update_policy(
            {
                "autonomy_level": "auto_low_risk",
                "daily_budget_yuan": 0.05,
                "minimum_interval_minutes": 1,
                "quiet_start_hour": 0,
                "quiet_end_hour": 0,
            }
        )

        result = await autonomy_service.process_once(self.now)

        self.assertEqual(result[0]["decision"], "wait")
        self.assertIn("主动预算", result[0]["reason"])

    async def test_high_risk_event_waits_for_confirmation(self) -> None:
        goal = autonomy_service.create_goal(
            "确认后发送",
            conversation_id="desktop_agent_test",
            autonomy_level="confirm_high_risk",
            capabilities=["external_action"],
        )
        db.record_agent_event(
            "high-risk:one",
            "external_action_ready",
            conversation_id="desktop_agent_test",
            goal_id=int(goal["id"]),
            capability="external_action",
            risk_level="high",
            payload={"summary": "等待确认"},
            relevance=1.0,
            confidence=1.0,
            urgency=0.8,
            interruption_cost=0.2,
            occurred_at=self.now.isoformat(timespec="seconds"),
        )

        result = await autonomy_service.process_once(self.now)

        self.assertEqual(result[0]["decision"], "confirm")
        behavior = db.list_autonomy_behaviors(limit=1)[0]
        self.assertEqual(behavior["status"], "awaiting_confirmation")
        self.assertEqual(db.get_recent_messages(10, "desktop_agent_test"), [])

        approved = await autonomy_service.approve_behavior(int(behavior["id"]))
        self.assertEqual(approved["status"], "delivered")
        self.assertEqual(len(db.get_recent_messages(10, "desktop_agent_test")), 1)

    async def test_qq_sending_state_becomes_unknown_without_blind_retry(self) -> None:
        goal = autonomy_service.create_goal(
            "QQ 消息",
            conversation_id="qq_private_10001",
            autonomy_level="auto_low_risk",
            capabilities=["follow_up_reminder"],
        )
        event = db.record_agent_event(
            "qq-ambiguous:one",
            "pending_thread_due",
            conversation_id="qq_private_10001",
            goal_id=int(goal["id"]),
            capability="follow_up_reminder",
            risk_level="low",
            payload={"content": "确认 QQ 送达", "thread_id": 0},
            relevance=1.0,
            confidence=1.0,
            urgency=0.8,
            interruption_cost=0.2,
            occurred_at=self.now.isoformat(timespec="seconds"),
        )
        behavior = db.create_autonomy_behavior(
            f"event:{int(event['id'])}:primary",
            event_id=int(event["id"]),
            goal_id=int(goal["id"]),
            conversation_id="qq_private_10001",
            behavior_type="notification",
            capability="follow_up_reminder",
            risk_level="low",
            permission_mode="auto_low_risk",
            status="planned",
            reason="测试崩溃边界",
            evidence={},
            content="确认 QQ 送达",
            destination="app+qq",
        )
        db.update_autonomy_behavior(int(behavior["id"]), {"qq_delivery_status": "sending"})

        originals = {
            "qq_bot_enabled": autonomy_service.settings.qq_bot_enabled,
            "qq_allowed_user_ids": autonomy_service.settings.qq_allowed_user_ids,
        }
        try:
            object.__setattr__(autonomy_service.settings, "qq_bot_enabled", True)
            object.__setattr__(autonomy_service.settings, "qq_allowed_user_ids", ("10001",))
            with patch("app.routes.onebot.send_private_message", new=AsyncMock()) as send:
                result = await autonomy_service.process_once(self.now)
        finally:
            for name, value in originals.items():
                object.__setattr__(autonomy_service.settings, name, value)

        send.assert_not_awaited()
        self.assertEqual(result[0]["behavior"]["qq_delivery_status"], "delivery_unknown")
        self.assertEqual(result[0]["behavior"]["status"], "delivery_unknown")
        self.assertEqual(len(db.get_recent_messages(10, "qq_private_10001")), 1)

    async def test_two_hour_schedule_catches_up_after_restart_and_delivers_once(self) -> None:
        conversation_id = "qq_private_10001"
        message_id = db.save_message("user", "忙完再聊", conversation_id=conversation_id)
        last_user_at = self.now - timedelta(hours=3)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                (last_user_at.isoformat(timespec="seconds"), message_id),
            )
        overrides = {
            "qq_proactive_enabled": True,
            "qq_allowed_user_ids": ("10001",),
            "qq_bot_enabled": False,
            "qq_proactive_min_idle_minutes": 120,
            "qq_proactive_max_idle_minutes": 120,
            "qq_proactive_day_start_hour": 0,
            "qq_proactive_day_end_hour": 0,
        }
        originals = {name: getattr(autonomy_service.settings, name) for name in overrides}
        try:
            for name, value in overrides.items():
                object.__setattr__(autonomy_service.settings, name, value)
            autonomy_service.update_policy({"quiet_start_hour": 0, "quiet_end_hour": 0})

            self.assertEqual(autonomy_service.collect_scheduled_proactive_events(self.now), 1)
            with patch(
                "app.chat_service.generate_qq_proactive_replies",
                new=AsyncMock(return_value=generated_proactive_result()),
            ) as generate:
                first = await autonomy_service.process_once(self.now)
            self.assertEqual(first[0]["decision"], "deliver")
            self.assertEqual(first[0]["behavior"]["delivery_status"], "app_only")
            self.assertEqual(first[0]["behavior"]["qq_delivery_status"], "disabled")
            self.assertEqual(len(db.get_recent_messages(10, conversation_id)), 2)
            generate.assert_awaited_once_with(conversation_id, 180)
            behavior = first[0]["behavior"]
            self.assertEqual(behavior["model_id"], "test-profile")
            self.assertEqual(behavior["prompt_tokens"], 21)
            self.assertEqual(behavior["completion_tokens"], 8)
            self.assertEqual(behavior["cost_yuan"], 0.012)
            self.assertNotIn("安静约", behavior["content"])
            state = db.get_qq_proactive_state("10001")
            self.assertIsNotNone(state)
            self.assertTrue(str(state["last_prompt_at"]))

            self.assertEqual(autonomy_service.collect_scheduled_proactive_events(self.now), 0)
            self.assertEqual(await autonomy_service.process_once(self.now), [])
        finally:
            for name, value in originals.items():
                object.__setattr__(autonomy_service.settings, name, value)

    async def test_recurring_proactive_setting_reactivates_completed_runtime_goal(self) -> None:
        conversation_id = "qq_private_10001"
        message_id = db.save_message("user", "两个小时后找我", conversation_id=conversation_id)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                ((self.now - timedelta(hours=3)).isoformat(timespec="seconds"), message_id),
            )
        overrides = {
            "qq_proactive_enabled": True,
            "qq_allowed_user_ids": ("10001",),
            "qq_bot_enabled": False,
            "qq_proactive_min_idle_minutes": 120,
            "qq_proactive_max_idle_minutes": 120,
            "qq_proactive_day_start_hour": 0,
            "qq_proactive_day_end_hour": 0,
        }
        originals = {name: getattr(autonomy_service.settings, name) for name in overrides}
        try:
            for name, value in overrides.items():
                object.__setattr__(autonomy_service.settings, name, value)
            autonomy_service.update_policy({"quiet_start_hour": 0, "quiet_end_hour": 0})
            goal = autonomy_service.create_goal(
                "允许澪在长时间安静后联系 QQ 用户 10001",
                conversation_id=conversation_id,
                capabilities=["proactive_checkin"],
                source_kind="runtime_setting",
                source_ref="qq_proactive:10001",
            )
            db.update_agent_goal_status(int(goal["id"]), "completed")
            self.assertEqual(autonomy_service.collect_scheduled_proactive_events(self.now), 1)

            with patch(
                "app.chat_service.generate_qq_proactive_replies",
                new=AsyncMock(return_value=generated_proactive_result("我来找你说句话。")),
            ):
                result = await autonomy_service.process_once(self.now)

            self.assertEqual(result[0]["decision"], "deliver")
            refreshed = db.get_agent_goal(int(goal["id"]))
            self.assertEqual(refreshed["status"], "active")
            self.assertEqual(len(db.get_recent_messages(10, conversation_id)), 2)
        finally:
            for name, value in originals.items():
                object.__setattr__(autonomy_service.settings, name, value)

    async def test_night_close_becomes_authorized_event_and_respects_delivery_gate(self) -> None:
        conversation_id = "qq_private_10001"
        message_id = db.save_message("user", "今天先到这里", conversation_id=conversation_id)
        night = self.now.replace(hour=23)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                ((night - timedelta(hours=1)).isoformat(timespec="seconds"), message_id),
            )
        overrides = {
            "qq_bot_enabled": True,
            "qq_allowed_user_ids": ("10001",),
            "night_close_enabled": True,
            "night_close_start_hour": 23,
            "night_close_end_hour": 1,
            "night_close_min_quiet_minutes": 45,
        }
        originals = {name: getattr(autonomy_service.settings, name) for name in overrides}
        try:
            for name, value in overrides.items():
                object.__setattr__(autonomy_service.settings, name, value)
            autonomy_service.update_policy({"quiet_start_hour": 0, "quiet_end_hour": 0})

            self.assertEqual(autonomy_service.collect_night_close_events(night), 1)
            with patch(
                "app.chat_service.generate_qq_night_close_replies",
                new=AsyncMock(return_value=generated_proactive_result("要准备休息了吗？")),
            ) as generate:
                result = await autonomy_service.process_once(night)
            self.assertEqual(result[0]["decision"], "deliver")
            generate.assert_awaited_once_with(conversation_id)
            self.assertEqual(result[0]["behavior"]["content"], "要准备休息了吗？")
            self.assertEqual(result[0]["behavior"]["delivery_status"], "app_only")
            self.assertEqual(db.get_night_close_prompted_date("10001"), db.today_string(night))
            self.assertEqual(autonomy_service.collect_night_close_events(night), 0)
        finally:
            for name, value in originals.items():
                object.__setattr__(autonomy_service.settings, name, value)

    async def test_stale_night_close_event_is_not_delivered_next_morning(self) -> None:
        conversation_id = "qq_private_10001"
        night = self.now.replace(hour=23, minute=0)
        morning = (night + timedelta(days=1)).replace(hour=10, minute=0)
        message_id = db.save_message("user", "今晚跑完就收工", conversation_id=conversation_id)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                ((night - timedelta(hours=1)).isoformat(timespec="seconds"), message_id),
            )
        overrides = {
            "qq_bot_enabled": True,
            "qq_allowed_user_ids": ("10001",),
            "night_close_enabled": True,
            "night_close_start_hour": 23,
            "night_close_end_hour": 1,
            "night_close_min_quiet_minutes": 45,
        }
        originals = {name: getattr(autonomy_service.settings, name) for name in overrides}
        try:
            for name, value in overrides.items():
                object.__setattr__(autonomy_service.settings, name, value)
            autonomy_service.update_policy({"quiet_start_hour": 0, "quiet_end_hour": 0})

            self.assertEqual(autonomy_service.collect_night_close_events(night), 1)
            result = await autonomy_service.process_once(morning)

            self.assertEqual(result[0]["decision"], "ignore")
            self.assertIn("有效时段", result[0]["reason"])
            messages = db.get_recent_messages(10, conversation_id)
            self.assertEqual([row["role"] for row in messages], ["user"])
        finally:
            for name, value in originals.items():
                object.__setattr__(autonomy_service.settings, name, value)

    async def test_proactive_model_failure_never_sends_prebuilt_copy(self) -> None:
        conversation_id = "qq_private_10001"
        message_id = db.save_message("user", "两个小时后再找我", conversation_id=conversation_id)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                ((self.now - timedelta(hours=3)).isoformat(timespec="seconds"), message_id),
            )
        overrides = {
            "qq_proactive_enabled": True,
            "qq_allowed_user_ids": ("10001",),
            "qq_bot_enabled": False,
            "qq_proactive_min_idle_minutes": 120,
            "qq_proactive_max_idle_minutes": 120,
            "qq_proactive_day_start_hour": 0,
            "qq_proactive_day_end_hour": 0,
        }
        originals = {name: getattr(autonomy_service.settings, name) for name in overrides}
        try:
            for name, value in overrides.items():
                object.__setattr__(autonomy_service.settings, name, value)
            autonomy_service.update_policy({"quiet_start_hour": 0, "quiet_end_hour": 0})
            autonomy_service.collect_scheduled_proactive_events(self.now)
            with patch(
                "app.chat_service.generate_qq_proactive_replies",
                new=AsyncMock(side_effect=RuntimeError("invalid token")),
            ):
                result = await autonomy_service.process_once(self.now)

            self.assertEqual(result[0]["decision"], "failed")
            self.assertIn("invalid token", result[0]["reason"])
            self.assertEqual(db.list_autonomy_behaviors(), [])
            messages = db.get_recent_messages(10, conversation_id)
            self.assertEqual([row["role"] for row in messages], ["user"])
        finally:
            for name, value in originals.items():
                object.__setattr__(autonomy_service.settings, name, value)

    async def test_daily_state_and_failed_task_results_can_reach_authorized_goals(self) -> None:
        autonomy_service.update_policy({"quiet_start_hour": 0, "quiet_end_hour": 0})
        autonomy_service.create_goal(
            "关注今日状态",
            conversation_id="desktop_agent_test",
            capabilities=["daily_state"],
        )
        db.upsert_daily_state(
            db.today_string(self.now),
            "partial",
            "平静",
            "完成阶段验收",
            "",
            "继续跑回归",
        )
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE daily_states SET updated_at = ? WHERE date = ?",
                (self.now.isoformat(timespec="seconds"), db.today_string(self.now)),
            )
        autonomy_service.collect_daily_state_event(self.now)
        result = await autonomy_service.process_once(self.now)
        self.assertEqual(result[0]["decision"], "deliver")
        self.assertIn("继续跑回归", result[0]["behavior"]["content"])

        action_id = db.log_companion_action(
            "desktop_agent_test",
            "update_status",
            "{}",
            "running",
        )
        db.update_companion_action(action_id, "failed", "隔离故障")
        task_event = db.list_agent_events(limit=1)[0]
        self.assertEqual(task_event["event_type"], "task_result")
        self.assertEqual(task_event["relevance"], 0.85)
        self.assertEqual(task_event["urgency"], 0.8)

    async def test_high_importance_screen_event_enters_autonomy_without_image_data(self) -> None:
        autonomy_service.update_policy({"quiet_start_hour": 0, "quiet_end_hour": 0})
        autonomy_service.create_goal(
            "关注重要屏幕变化",
            conversation_id="desktop_agent_test",
            capabilities=["screen_event"],
        )
        db.save_screen_event(
            session_id=1,
            frame_id=7,
            event_type="warning",
            event_summary="构建窗口出现失败提示",
            importance=0.82,
            should_speak=False,
            emotion="concerned",
            change_percent=12.5,
            model_id="vision-test",
            request_cost_yuan=0.0,
            occurred_at=self.now.isoformat(timespec="seconds"),
            conversation_id="desktop_agent_test",
        )

        result = await autonomy_service.process_once(self.now)

        self.assertEqual(result[0]["decision"], "deliver")
        self.assertIn("构建窗口出现失败提示", result[0]["behavior"]["content"])
        event = db.list_agent_events(limit=1)[0]
        payload = autonomy_service.public_event(event)["payload"]
        self.assertNotIn("image", payload)
        self.assertNotIn("frame", payload)

    async def test_screen_event_already_spoken_by_realtime_path_is_not_duplicated(self) -> None:
        autonomy_service.update_policy({"quiet_start_hour": 0, "quiet_end_hour": 0})
        autonomy_service.create_goal(
            "关注重要屏幕变化",
            conversation_id="desktop_agent_test",
            capabilities=["screen_event"],
        )
        db.save_screen_event(
            session_id=1,
            frame_id=8,
            event_type="warning",
            event_summary="实时链路已经播报",
            importance=0.9,
            should_speak=True,
            emotion="concerned",
            change_percent=18.0,
            model_id="vision-test",
            request_cost_yuan=0.0,
            occurred_at=self.now.isoformat(timespec="seconds"),
            conversation_id="desktop_agent_test",
        )

        result = await autonomy_service.process_once(self.now)

        self.assertEqual(result[0]["decision"], "ignore")
        self.assertIn("抑制重复", result[0]["reason"])
        self.assertEqual(db.list_autonomy_behaviors(), [])


class AutonomousWebRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        self.original_web_search_enabled = db.settings.web_search_enabled
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "web-recovery.db")
        object.__setattr__(db.settings, "web_search_enabled", True)
        db.init_db()
        self.message_id = db.save_message(
            "user",
            "查一下合川天气",
            conversation_id="desktop_agent_test",
        )

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        object.__setattr__(db.settings, "web_search_enabled", self.original_web_search_enabled)
        self.temp_dir.cleanup()

    async def test_failed_precheck_is_given_to_planner_and_candidate_is_verified(self) -> None:
        precheck = WebLookup(query="合川 天气", sources=[], error="地点没有解析上")
        recovered = WebLookup(
            query="重庆市合川区 天气",
            sources=[WebSource("合川区天气", "https://example.test", "晴，34°C")],
            engine="test",
        )
        planner = AsyncMock(return_value=completion_with_search("重庆市合川区 天气"))
        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.web_search_service.lookup_web_query", new=AsyncMock(return_value=recovered)),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_test",
                source="desktop",
                user_message="查一下合川天气",
                source_message_id=self.message_id,
                request_id="web-recovery-request",
                trace_id="web-recovery-trace",
                model_id="test-model",
                reasoning_level="low",
                web_precheck=precheck,
            )

        planner_prompt = str(planner.await_args.args[0])
        self.assertIn("地点没有解析上", planner_prompt)
        self.assertEqual(result.observations[0].tool_name, "search_web")
        self.assertEqual(result.observations[0].status, "completed")
        self.assertEqual(result.observations[0].result["query"], "重庆市合川区 天气")
        self.assertIn("合川区天气", result.model_context())

    async def test_recovery_is_not_hardcoded_to_hechuan(self) -> None:
        message_id = db.save_message(
            "user",
            "查一下浦东天气",
            conversation_id="desktop_agent_test",
        )
        precheck = WebLookup(query="浦东 天气", sources=[], error="简称没有解析上")
        recovered = WebLookup(
            query="上海市浦东新区 天气",
            sources=[WebSource("浦东新区天气", "https://example.test/pudong", "多云，31°C")],
            engine="test",
        )
        planner = AsyncMock(return_value=completion_with_search("上海市浦东新区 天气"))
        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.web_search_service.lookup_web_query", new=AsyncMock(return_value=recovered)),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_test",
                source="desktop",
                user_message="查一下浦东天气",
                source_message_id=message_id,
                request_id="generic-web-recovery-request",
                trace_id="generic-web-recovery-trace",
                model_id="test-model",
                reasoning_level="low",
                web_precheck=precheck,
            )

        planner_prompt = str(planner.await_args.args[0])
        self.assertIn("简称没有解析上", planner_prompt)
        self.assertIn("查一下浦东天气", planner_prompt)
        self.assertEqual(result.observations[0].status, "completed")
        self.assertEqual(result.observations[0].result["query"], "上海市浦东新区 天气")
        self.assertIn("浦东新区天气", result.model_context())


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZONE)
    return parsed
