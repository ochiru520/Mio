from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import companion_service, db
from app.chat_service import ChatResult
from app.config import settings
from app.proactive_service import (
    _in_night_close_window,
    _maybe_recover_qq_connection,
    _maybe_send_to_user,
    plan_proactive_topic,
    run_proactive_once,
    run_desktop_startup_greeting_once,
    start_qq_on_app_startup,
)
from app.daily_review_service import _notify_review_ready
from app.routes.diary import _extract_tags
from app.routes.memory import _normalize_follow_up, api_memory
from app.routes.review import api_reviews_list
from app.routes.weekly import api_weekly_list
from app.weekly_review_service import last_completed_week_start, week_end_for, week_start_for


class WeeklyReviewDateTests(unittest.TestCase):
    def test_week_start_is_monday(self) -> None:
        self.assertEqual(week_start_for(date(2026, 7, 26)).isoformat(), "2026-07-20")
        self.assertEqual(week_start_for(date(2026, 7, 20)).isoformat(), "2026-07-20")

    def test_last_completed_week(self) -> None:
        # 2026-07-26 是周日，上一个完整周从 07-13（周一）开始
        self.assertEqual(last_completed_week_start(date(2026, 7, 26)), "2026-07-13")
        # 周一当天：上一周刚结束
        self.assertEqual(last_completed_week_start(date(2026, 7, 27)), "2026-07-20")

    def test_week_end(self) -> None:
        self.assertEqual(week_end_for("2026-07-20"), "2026-07-26")


class DiaryTagsTests(unittest.TestCase):
    def test_tags_line_is_extracted_and_removed(self) -> None:
        markdown = "# 2026-07-26\n\n## 今日事件\n- 面试\n\n标签：面试、家人、游戏开发"
        cleaned, tags = _extract_tags(markdown)
        self.assertEqual(tags, "面试、家人、游戏开发")
        self.assertNotIn("标签：", cleaned)
        self.assertIn("## 今日事件", cleaned)

    def test_comma_variants_are_normalized(self) -> None:
        _, tags = _extract_tags("正文\n标签：面试，家人, 复盘")
        self.assertEqual(tags, "面试、家人、复盘")

    def test_no_tags_line_keeps_markdown(self) -> None:
        markdown = "# 2026-07-26\n\n## 今日事件\n- 无"
        cleaned, tags = _extract_tags(markdown)
        self.assertEqual(cleaned, markdown)
        self.assertEqual(tags, "")


class NightCloseWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_start = settings.night_close_start_hour
        self.original_end = settings.night_close_end_hour
        object.__setattr__(settings, "night_close_start_hour", 23)
        object.__setattr__(settings, "night_close_end_hour", 1)

    def tearDown(self) -> None:
        object.__setattr__(settings, "night_close_start_hour", self.original_start)
        object.__setattr__(settings, "night_close_end_hour", self.original_end)

    def test_window_wraps_past_midnight(self) -> None:
        self.assertTrue(_in_night_close_window(datetime(2026, 7, 26, 23, 10)))
        self.assertTrue(_in_night_close_window(datetime(2026, 7, 27, 0, 30)))
        self.assertFalse(_in_night_close_window(datetime(2026, 7, 27, 1, 5)))
        self.assertFalse(_in_night_close_window(datetime(2026, 7, 26, 21, 0)))


class VoiceStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_voice_startup_does_not_start_runtime(self) -> None:
        with (
            patch("app.companion_service.load_config", return_value={
                "voice_startup_enabled": False,
                "voice_enabled": True,
                "voice_engine": "gpt_sovits",
            }),
            patch("app.companion_service.start_voice_service") as start,
        ):
            self.assertFalse(await companion_service.start_voice_on_app_startup())
        start.assert_not_called()

    async def test_local_voice_startup_starts_runtime_in_worker_thread(self) -> None:
        with (
            patch("app.companion_service.load_config", return_value={
                "voice_startup_enabled": True,
                "voice_enabled": True,
                "voice_engine": "gpt_sovits",
            }),
            patch("app.companion_service.start_voice_service", return_value={"service_running": True}) as start,
        ):
            self.assertTrue(await companion_service.start_voice_on_app_startup())
        start.assert_called_once_with()

    async def test_cloud_voice_does_not_start_local_runtime(self) -> None:
        with (
            patch("app.companion_service.load_config", return_value={
                "voice_startup_enabled": True,
                "voice_enabled": True,
                "voice_engine": "cloud",
            }),
            patch("app.companion_service.start_voice_service") as start,
        ):
            self.assertFalse(await companion_service.start_voice_on_app_startup())
        start.assert_not_called()


class ProactiveDesktopDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        self.original_qq_bot_enabled = settings.qq_bot_enabled
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "proactive.db")
        object.__setattr__(settings, "qq_bot_enabled", True)
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(settings, "qq_bot_enabled", self.original_qq_bot_enabled)
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_topic_planner_prefers_due_thread(self) -> None:
        conversation_id = "qq_private_topic"
        db.save_message("user", "我正在整理自己的作品集", conversation_id=conversation_id)

        selected = plan_proactive_topic(
            conversation_id,
            due_threads=["问问明天的面试准备得怎么样了"],
        )

        self.assertEqual(selected["kind"], "due_thread")
        self.assertIn("面试", selected["text"])

    def test_topic_planner_penalizes_recently_used_topic(self) -> None:
        conversation_id = "qq_private_topic_repeat"
        repeated = "问问明天的面试准备得怎么样了"
        db.save_message("user", "我刚刚把作品集又改了一版", conversation_id=conversation_id)
        first = plan_proactive_topic(conversation_id, due_threads=[repeated])
        db.record_proactive_topic(
            conversation_id,
            str(first["key"]),
            str(first["kind"]),
            str(first["text"]),
            float(first["score"]),
        )

        selected = plan_proactive_topic(conversation_id, due_threads=[repeated])

        self.assertEqual(selected["kind"], "recent_message")
        self.assertIn("作品集", selected["text"])

    def test_unreachable_napcat_is_started_before_login_check(self) -> None:
        import app.proactive_service as proactive_service

        now = datetime(2026, 8, 9, 15, 0, tzinfo=datetime.now().astimezone().tzinfo)
        original_missing = proactive_service._missing_connection_since
        original_recovery = proactive_service._last_recovery_attempt_at
        proactive_service._missing_connection_since = now - timedelta(minutes=2)
        proactive_service._last_recovery_attempt_at = None
        try:
            with (
                patch("app.proactive_service.active_websocket_count", side_effect=[0, 0]),
                patch("app.proactive_service.napcat_auto_recovery_allowed", return_value=True),
                patch("app.companion_service.load_config", return_value={"qq_startup_enabled": True}),
                patch(
                    "app.proactive_service.get_napcat_login_status",
                    new=AsyncMock(return_value={"login_checked": False, "diagnostic_code": "webui_unreachable"}),
                ),
                patch(
                    "app.proactive_service.run_napcat_control",
                    return_value={"ok": True, "output": "started"},
                ) as control,
            ):
                self.assertFalse(asyncio.run(_maybe_recover_qq_connection(now)))
            control.assert_called_once_with("start")
        finally:
            proactive_service._missing_connection_since = original_missing
            proactive_service._last_recovery_attempt_at = original_recovery

    def test_qq_startup_switch_blocks_automatic_launch(self) -> None:
        with (
            patch("app.companion_service.load_config", return_value={"qq_startup_enabled": False}),
            patch("app.proactive_service.run_napcat_control") as control,
        ):
            self.assertFalse(asyncio.run(start_qq_on_app_startup()))
        control.assert_not_called()

    def test_qq_startup_switch_blocks_disconnected_recovery(self) -> None:
        import app.proactive_service as proactive_service

        now = datetime(2026, 8, 11, 15, 0, tzinfo=datetime.now().astimezone().tzinfo)
        original_missing = proactive_service._missing_connection_since
        original_recovery = proactive_service._last_recovery_attempt_at
        proactive_service._missing_connection_since = now - timedelta(minutes=2)
        proactive_service._last_recovery_attempt_at = None
        try:
            with (
                patch("app.proactive_service.active_websocket_count", return_value=0),
                patch("app.proactive_service.napcat_auto_recovery_allowed", return_value=True),
                patch("app.companion_service.load_config", return_value={"qq_startup_enabled": False}),
                patch("app.proactive_service.get_napcat_login_status", new=AsyncMock()) as login_status,
                patch("app.proactive_service.run_napcat_control") as control,
            ):
                self.assertFalse(asyncio.run(_maybe_recover_qq_connection(now)))
            login_status.assert_not_awaited()
            control.assert_not_called()
            self.assertEqual(proactive_service.get_proactive_status()["connection_result"], "startup_disabled")
        finally:
            proactive_service._missing_connection_since = original_missing
            proactive_service._last_recovery_attempt_at = original_recovery

    def test_qq_startup_switch_launches_napcat(self) -> None:
        with (
            patch("app.companion_service.load_config", return_value={"qq_startup_enabled": True}),
            patch("app.proactive_service.asyncio.sleep", new=AsyncMock()),
            patch(
                "app.proactive_service.get_napcat_login_status",
                new=AsyncMock(return_value={"napcat_process_running": False, "qq_process_running": False}),
            ),
            patch(
                "app.proactive_service.run_napcat_control",
                return_value={"ok": True, "output": "started"},
            ) as control,
        ):
            self.assertTrue(asyncio.run(start_qq_on_app_startup()))
        control.assert_called_once_with("start")

    def test_qq_startup_switch_restarts_when_only_napcat_is_running(self) -> None:
        with (
            patch("app.companion_service.load_config", return_value={"qq_startup_enabled": True}),
            patch("app.proactive_service.asyncio.sleep", new=AsyncMock()),
            patch("app.proactive_service.active_websocket_count", return_value=0),
            patch(
                "app.proactive_service.get_napcat_login_status",
                new=AsyncMock(return_value={"napcat_process_running": True, "qq_process_running": False}),
            ),
            patch(
                "app.proactive_service.run_napcat_control",
                return_value={"ok": True, "output": "restarted"},
            ) as control,
        ):
            self.assertTrue(asyncio.run(start_qq_on_app_startup()))
        control.assert_called_once_with("restart")

    def test_manual_qq_control_clears_stale_recovery_error(self) -> None:
        import app.proactive_service as proactive_service

        original = dict(proactive_service._last_check)
        proactive_service._last_check.update(
            connection_result="recovery_failed",
            recovery_error="旧的 NAPCAT_DIR 错误",
            error="旧的控制脚本错误",
            websocket_connections=0,
        )
        try:
            with patch("app.proactive_service.active_websocket_count", return_value=0):
                proactive_service.note_manual_qq_control_result("start", connected=False)

            status = proactive_service.get_proactive_status()
            self.assertEqual(status["connection_result"], "start_sent")
            self.assertEqual(status["recovery_error"], "")
            self.assertEqual(status["error"], "")
        finally:
            proactive_service._last_check.clear()
            proactive_service._last_check.update(original)

    def test_offline_qq_still_delivers_to_open_desktop_app(self) -> None:
        user_id = "10001"
        conversation_id = f"qq_private_{user_id}"
        db.save_message("user", "我先去忙了", source="desktop", conversation_id=conversation_id)
        last_user = db.get_last_message(conversation_id, role="user")
        now = datetime.fromisoformat(str(last_user["created_at"])) + timedelta(hours=4)
        generated = ChatResult(
            reply="忙完了吗？",
            replies=["忙完了吗？"],
            request_id="proactive-request",
            model_id="deepseek-v4-flash",
            reasoning_level="standard",
            prompt_tokens=120,
            cached_prompt_tokens=20,
            completion_tokens=8,
            reasoning_tokens=3,
            request_cost_yuan=0.00125,
            request_cost_source="provider_reported",
        )

        with (
            patch("app.proactive_service._idle_delta", return_value=timedelta(hours=3)),
            patch("app.proactive_service.active_websocket_count", return_value=0),
            patch(
                "app.proactive_service.generate_qq_proactive_replies",
                return_value=generated,
            ) as generate,
            patch("app.proactive_service.send_private_message", return_value=False) as send,
        ):
            delivered = asyncio.run(_maybe_send_to_user(user_id, now))

        state = db.get_qq_proactive_state(user_id)
        assistants = [
            row
            for row in db.get_recent_messages(limit=10, conversation_id=conversation_id)
            if row["role"] == "assistant"
        ]
        self.assertTrue(delivered)
        self.assertEqual([row["content"] for row in assistants], ["忙完了吗？"])
        self.assertEqual(str(state["last_prompt_at"]), now.isoformat(timespec="seconds"))
        generate.assert_called_once()
        send.assert_not_called()

    def test_failed_qq_sync_keeps_desktop_delivery_record(self) -> None:
        user_id = "10001"
        conversation_id = f"qq_private_{user_id}"
        db.save_message("user", "我先去忙了", source="desktop", conversation_id=conversation_id)
        last_user = db.get_last_message(conversation_id, role="user")
        now = datetime.fromisoformat(str(last_user["created_at"])) + timedelta(hours=4)
        generated = ChatResult(reply="忙完了吗？", replies=["忙完了吗？"])

        with (
            patch("app.proactive_service._idle_delta", return_value=timedelta(hours=3)),
            patch("app.proactive_service.active_websocket_count", return_value=1),
            patch("app.proactive_service.generate_qq_proactive_replies", return_value=generated),
            patch("app.proactive_service.send_private_message", return_value=False),
        ):
            delivered = asyncio.run(_maybe_send_to_user(user_id, now))

        assistants = [
            row
            for row in db.get_recent_messages(limit=10, conversation_id=conversation_id)
            if row["role"] == "assistant"
        ]
        self.assertTrue(delivered)
        self.assertEqual([row["content"] for row in assistants], ["忙完了吗？"])
        self.assertEqual(
            str(db.get_qq_proactive_state(user_id)["last_prompt_at"]),
            now.isoformat(timespec="seconds"),
        )

    def test_online_qq_syncs_the_same_proactive_reply_after_app_delivery(self) -> None:
        import app.proactive_service as proactive_service

        user_id = "10001"
        conversation_id = f"qq_private_{user_id}"
        db.save_message("user", "我先去忙了", source="desktop", conversation_id=conversation_id)
        last_user = db.get_last_message(conversation_id, role="user")
        now = datetime.fromisoformat(str(last_user["created_at"])) + timedelta(hours=4)
        generated = ChatResult(reply="忙完了吗？\n\n我刚刚想起你了", replies=["忙完了吗？", "我刚刚想起你了"])

        with (
            patch("app.proactive_service._idle_delta", return_value=timedelta(hours=3)),
            patch("app.proactive_service.active_websocket_count", return_value=1),
            patch("app.proactive_service.generate_qq_proactive_replies", return_value=generated),
            patch("app.proactive_service.send_private_message", new=AsyncMock(return_value=True)) as send,
        ):
            delivered = asyncio.run(_maybe_send_to_user(user_id, now))

        self.assertTrue(delivered)
        self.assertEqual(send.await_count, 2)
        self.assertEqual(
            [call.args for call in send.await_args_list],
            [(user_id, "忙完了吗？"), (user_id, "我刚刚想起你了")],
        )
        self.assertEqual(proactive_service.get_proactive_status()["delivery_result"], "app_and_qq")

    def test_closed_desktop_app_pauses_proactive_generation(self) -> None:
        original_enabled = settings.qq_proactive_enabled
        original_users = settings.qq_allowed_user_ids
        object.__setattr__(settings, "qq_proactive_enabled", True)
        object.__setattr__(settings, "qq_allowed_user_ids", ("10001",))
        try:
            with (
                patch("app.proactive_service.desktop_app_is_active", return_value=False),
                patch("app.proactive_service.generate_qq_proactive_replies") as generate,
            ):
                sent = asyncio.run(run_proactive_once())
            self.assertEqual(sent, 0)
            generate.assert_not_called()
        finally:
            object.__setattr__(settings, "qq_proactive_enabled", original_enabled)
            object.__setattr__(settings, "qq_allowed_user_ids", original_users)


class DesktopStartupGreetingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        self.original_allowed_users = settings.qq_allowed_user_ids
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "startup.db")
        object.__setattr__(settings, "qq_allowed_user_ids", ("10001",))
        db.init_db()
        import app.proactive_service as proactive_service

        proactive_service._startup_greeting_sent = False

    def tearDown(self) -> None:
        import app.proactive_service as proactive_service

        proactive_service._startup_greeting_sent = False
        object.__setattr__(settings, "qq_allowed_user_ids", self.original_allowed_users)
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_startup_greeting_is_generated_once_and_defers_proactive_message(self) -> None:
        user_id = "10001"
        conversation_id = f"qq_private_{user_id}"
        db.save_message("user", "昨晚还在改项目", source="desktop", conversation_id=conversation_id)
        now = datetime(2026, 8, 4, 9, 30, tzinfo=datetime.now().astimezone().tzinfo)
        generated = ChatResult(
            reply="醒啦？\n\n昨晚那个项目后来改顺了吗？",
            replies=["醒啦？", "昨晚那个项目后来改顺了吗？"],
            request_id="startup-request",
            model_id="deepseek-v4-flash",
            reasoning_level="off",
            prompt_tokens=240,
            cached_prompt_tokens=40,
            completion_tokens=16,
            reasoning_tokens=0,
            request_cost_yuan=0.0025,
            request_cost_source="official_estimate",
        )

        with (
            patch("app.proactive_service._idle_delta", return_value=timedelta(hours=2)),
            patch(
                "app.proactive_service.generate_desktop_startup_replies",
                return_value=generated,
            ),
        ):
            first = asyncio.run(run_desktop_startup_greeting_once(conversation_id, now))
            second = asyncio.run(run_desktop_startup_greeting_once(conversation_id, now))

        messages = db.get_recent_messages(limit=10, conversation_id=conversation_id)
        state = db.get_qq_proactive_state(user_id)
        self.assertEqual(first, ["醒啦？", "昨晚那个项目后来改顺了吗？"])
        self.assertEqual(second, [])
        self.assertEqual(
            [row["source"] for row in messages if row["role"] == "assistant"],
            ["startup", "startup"],
        )
        startup_messages = [row for row in messages if row["role"] == "assistant"]
        self.assertTrue(all(row["request_id"] == "startup-request" for row in startup_messages))
        self.assertTrue(all(row["model_id"] == "deepseek-v4-flash" for row in startup_messages))
        self.assertEqual([row["prompt_tokens"] for row in startup_messages], [240, 0])
        self.assertEqual([row["request_cost_yuan"] for row in startup_messages], [0.0025, 0.0])
        self.assertEqual(
            [row["request_cost_source"] for row in startup_messages],
            ["official_estimate", "shared_request"],
        )
        self.assertEqual(
            state["next_prompt_at"],
            (now + timedelta(hours=2)).isoformat(timespec="seconds"),
        )


class LocalNotificationCostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        self.original_notify = settings.daily_review_auto_notify_qq
        self.original_bot = settings.qq_bot_enabled
        self.original_users = settings.qq_allowed_user_ids
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "notification.db")
        object.__setattr__(settings, "daily_review_auto_notify_qq", True)
        object.__setattr__(settings, "qq_bot_enabled", True)
        object.__setattr__(settings, "qq_allowed_user_ids", ("test-user",))
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(settings, "daily_review_auto_notify_qq", self.original_notify)
        object.__setattr__(settings, "qq_bot_enabled", self.original_bot)
        object.__setattr__(settings, "qq_allowed_user_ids", self.original_users)
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_daily_review_notification_is_recorded_as_free_local_message(self) -> None:
        with patch(
            "app.daily_review_service.send_private_message",
            new=AsyncMock(return_value=True),
        ):
            asyncio.run(_notify_review_ready("2026-08-08"))

        row = db.get_recent_messages(5, "qq_private_test-user")[0]
        self.assertEqual(row["request_cost_yuan"], 0.0)
        self.assertEqual(row["request_cost_source"], "local_fallback")


class AgentMemoryApiTests(unittest.TestCase):
    def test_follow_up_time_uses_configured_timezone(self) -> None:
        original_timezone = settings.timezone
        object.__setattr__(settings, "timezone", "Asia/Tokyo")
        try:
            normalized = _normalize_follow_up("2026-08-11T09:30:00")
        finally:
            object.__setattr__(settings, "timezone", original_timezone)

        self.assertEqual(normalized, "2026-08-11T09:30:00+09:00")

    def test_memory_api_returns_desktop_ready_data(self) -> None:
        thread = {
            "id": 7,
            "conversation_id": "qq_private_test",
            "content": "下次继续聊项目",
            "created_at": "2026-08-01T12:00:00+08:00",
        }
        summary = {
            "tags": "qq_private_test",
            "content": "用户正在做澪应用。\n<!-- last_message_id:12 -->",
            "updated_at": "2026-08-01T12:00:00+08:00",
        }
        with (
            patch("app.routes.memory.db.list_all_open_pending_threads", return_value=[thread]),
            patch("app.routes.memory.db.list_memories_by_type", return_value=[summary]),
            patch("app.routes.memory.db.list_structured_memories", return_value=[]),
            patch("app.routes.memory.db.list_follow_up_results", return_value=[]),
            patch("app.routes.memory.load_mio_profile", return_value={"identity": {"name": "澪"}}),
        ):
            result = asyncio.run(api_memory())

        self.assertEqual(result["threads"][0]["conversation_label"], "QQ 私聊")
        self.assertEqual(result["summaries"][0]["content"], "用户正在做澪应用。")
        self.assertEqual(result["profile"]["identity"]["name"], "澪")

    def test_daily_review_api_returns_markdown(self) -> None:
        row = {"date": "2026-08-01", "markdown_content": "# 今日回顾"}
        with patch("app.routes.review.db.list_reviews", return_value=[row]):
            result = asyncio.run(api_reviews_list())
        self.assertEqual(result, [row])

    def test_weekly_review_api_adds_week_end(self) -> None:
        row = {"week_start": "2026-07-27", "markdown_content": "# 一周回顾"}
        with patch("app.routes.weekly.db.list_weekly_reviews", return_value=[row]):
            result = asyncio.run(api_weekly_list())
        self.assertEqual(result[0]["week_end"], "2026-08-02")


if __name__ == "__main__":
    unittest.main()
