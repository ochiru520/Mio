from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.companion_action_service import (
    apply_daily_state_assessment,
    backfill_explicit_structured_memories,
    execute_companion_actions,
    parse_companion_decision,
    plan_companion_actions,
)
from app.chat_service import schedule_companion_actions
from app.context_service import build_periodic_memory_context


class CompanionActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "test.db")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_daily_state_assessment_updates_summary_without_overwriting_daily_thirty(self) -> None:
        db.update_daily_thirty("done", "学习项目超过三十分钟")

        updated = apply_daily_state_assessment(
            {
                "confidence": 0.85,
                "daily_state": {
                    "mood": "有点累但有进展",
                    "mood_score": 3,
                    "key_events": "继续推进澪 Agent",
                    "avoidance_signals": "在界面细节上反复调整",
                    "next_min_action": "先验收今日状态链路",
                    "confidence": 0.88,
                },
            },
            conversation_id="desktop_test",
        )

        state = db.get_daily_state()
        self.assertTrue(updated)
        self.assertEqual(state["daily_thirty_status"], "done")
        self.assertEqual(state["daily_thirty_reason"], "学习项目超过三十分钟")
        self.assertEqual(state["mood_score"], 3)
        self.assertEqual(state["key_events"], "继续推进澪 Agent")
        self.assertEqual(state["next_min_action"], "先验收今日状态链路")

    def test_daily_state_assessment_ignores_group_chat_and_low_confidence(self) -> None:
        payload = {
            "daily_state": {
                "mood": "开心",
                "mood_score": 5,
                "key_events": "群聊内容",
                "confidence": 0.95,
            }
        }

        self.assertFalse(apply_daily_state_assessment(payload, conversation_id="qq_group_123"))
        payload["daily_state"]["confidence"] = 0.4
        self.assertFalse(apply_daily_state_assessment(payload, conversation_id="desktop_test"))
        self.assertIsNone(db.get_daily_state())

    def test_parser_filters_actions_and_falls_back_to_plain_reply(self) -> None:
        raw = json.dumps(
            {
                "reply": "第一句。\n第二句。",
                "assessment": {"confidence": 0.92, "needs_clarification": False},
                "actions": [
                    {"type": "add_diary_material", "content": "做了一下午 demo", "confidence": 0.95},
                    {"type": "run_shell", "content": "delete", "confidence": 1},
                    {"type": "set_daily_mood", "mood": "平静", "confidence": 0.4},
                ],
            },
            ensure_ascii=False,
        )
        decision = parse_companion_decision(raw)
        self.assertTrue(decision.structured)
        self.assertEqual(decision.reply, "第一句。\n第二句。")
        self.assertEqual([action["type"] for action in decision.actions], ["add_diary_material"])

        fallback = parse_companion_decision("普通回复")
        self.assertFalse(fallback.structured)
        self.assertEqual(fallback.reply, "普通回复")
        self.assertEqual(fallback.actions, [])

    async def test_action_planner_disables_deepseek_thinking(self) -> None:
        completion = AsyncMock(return_value='{"assessment":{"confidence":0},"actions":[]}')
        with patch("app.companion_action_service.call_chat_completion", completion):
            await plan_companion_actions("qq_private_test", "刚吃完饭")

        self.assertEqual(completion.await_args.kwargs["reasoning_level"], "off")

    async def test_companion_actions_run_in_scheduled_task(self) -> None:
        decision = type(
            "Decision",
            (),
            {
                "actions": [{"type": "add_diary_material"}],
                "assessment": {
                    "daily_state": {
                        "key_events": "完成今日状态链路修复",
                        "next_min_action": "运行完整回归",
                        "confidence": 0.9,
                    }
                },
            },
        )()
        planner = AsyncMock(return_value=decision)
        executor = AsyncMock()

        with (
            patch("app.chat_service.plan_companion_actions", planner),
            patch("app.chat_service.execute_companion_actions", executor),
            patch("app.chat_service.asyncio.sleep", new=AsyncMock()),
        ):
            task = schedule_companion_actions("qq_private_test", "刚吃完饭", 12)
            await task

        planner.assert_awaited_once_with("qq_private_test", "刚吃完饭")
        executor.assert_awaited_once_with(
            decision.actions,
            conversation_id="qq_private_test",
            user_message="刚吃完饭",
            source_message_id=12,
        )
        self.assertEqual(db.get_daily_state()["key_events"], "完成今日状态链路修复")

    def test_clarification_suppresses_all_actions(self) -> None:
        raw = json.dumps(
            {
                "reply": "你是想让我改当天日记吗？",
                "assessment": {"confidence": 0.6, "needs_clarification": True},
                "actions": [
                    {"type": "edit_today_diary", "instruction": "改日记", "confidence": 0.99}
                ],
            },
            ensure_ascii=False,
        )
        self.assertEqual(parse_companion_decision(raw).actions, [])

    def test_parser_accepts_deepseek_action_parameters_shape(self) -> None:
        raw = json.dumps(
            {
                "assessment": {"confidence": 0.95, "needs_clarification": False},
                "actions": [
                    {
                        "action": "set_daily_thirty",
                        "parameters": {
                            "status": "done",
                            "reason": "做了一下午 demo",
                            "confidence": 0.95,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )
        actions = parse_companion_decision(raw).actions
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "set_daily_thirty")
        self.assertEqual(actions[0]["status"], "done")

    def test_parser_accepts_named_action_object_shape(self) -> None:
        raw = json.dumps(
            {
                "assessment": {"confidence": 0.99, "needs_clarification": False},
                "actions": [
                    {
                        "set_daily_thirty": {
                            "status": "done",
                            "reason": "做了一下午 demo",
                            "confidence": 0.99,
                        }
                    }
                ],
            },
            ensure_ascii=False,
        )
        actions = parse_companion_decision(raw).actions
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "set_daily_thirty")
        self.assertEqual(actions[0]["status"], "done")

    def test_parser_infers_only_safe_action_types_from_fields(self) -> None:
        raw = json.dumps(
            {
                "assessment": {"confidence": 0.99, "needs_clarification": False},
                "actions": [
                    {"content": "做了一下午 demo", "confidence": 0.99},
                    {
                        "status": "done",
                        "reason": "产出超过三十分钟",
                        "confidence": 0.99,
                    },
                    {"instruction": "以后少追问", "confidence": 0.99},
                ],
            },
            ensure_ascii=False,
        )
        actions = parse_companion_decision(raw).actions
        self.assertEqual(
            [action["type"] for action in actions],
            ["add_diary_material", "set_daily_thirty"],
        )

    async def test_local_actions_persist_without_duplicates(self) -> None:
        actions = [
            {
                "type": "add_diary_material",
                "content": "今天做了一下午 demo",
                "confidence": 0.96,
            },
            {
                "type": "set_daily_thirty",
                "status": "done",
                "reason": "做了一下午 demo，超过三十分钟并有实际产出",
                "confidence": 0.98,
            },
            {
                "type": "set_daily_mood",
                "mood": "对 demo 进度稍微满意",
                "score": 4,
                "confidence": 0.9,
            },
            {
                "type": "remember_thread",
                "content": "明天继续调整 demo 场景分布",
                "follow_up_after": "2026-07-14 18:00",
                "confidence": 0.9,
            },
        ]
        results = await execute_companion_actions(actions, "qq_private_test", "今天做了一下午 demo", 1)
        self.assertTrue(all(item["status"] == "executed" for item in results))
        self.assertEqual(len(db.list_diary_materials()), 1)
        self.assertEqual(db.get_daily_state()["daily_thirty_status"], "done")
        self.assertIn("一下午 demo", db.get_daily_state()["daily_thirty_reason"])
        self.assertIn("稍微满意", db.get_daily_state()["mood"])
        self.assertEqual(db.get_daily_state()["mood_score"], 4)
        self.assertEqual(len(db.list_open_pending_threads("qq_private_test")), 1)
        due_threads = db.list_due_pending_threads("qq_private_test", "2026-07-14T18:01:00+08:00")
        self.assertEqual(len(due_threads), 1)
        with patch.object(db, "now_iso", return_value="2026-07-14T18:01:00+08:00"):
            due_context = build_periodic_memory_context("qq_private_test")
        self.assertIn("明天继续调整 demo 场景分布", due_context)
        db.mark_pending_thread_mentioned(int(due_threads[0]["id"]))
        self.assertEqual(db.list_due_pending_threads("qq_private_test", "2026-07-14T18:02:00+08:00"), [])
        self.assertEqual(len(db.list_open_pending_threads("qq_private_test")), 1)

        await execute_companion_actions(actions[:1], "qq_private_test", "今天做了一下午 demo", 1)
        self.assertEqual(len(db.list_diary_materials()), 1)

        context = build_periodic_memory_context("qq_private_test")
        self.assertNotIn("明天继续调整 demo 场景分布", context)
        self.assertIn("超过三十分钟", context)

        with db.get_conn() as conn:
            action_count = conn.execute("SELECT COUNT(*) FROM companion_actions").fetchone()[0]
        self.assertEqual(action_count, 5)

    async def test_thread_without_follow_up_time_is_not_created(self) -> None:
        results = await execute_companion_actions(
            [
                {
                    "type": "remember_thread",
                    "content": "之后问问跑步情况",
                    "follow_up_after": "",
                    "confidence": 0.96,
                }
            ],
            "qq_private_test",
            "我准备出去跑步",
            1,
        )
        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(db.list_open_pending_threads("qq_private_test"), [])

    async def test_done_status_is_not_silently_downgraded(self) -> None:
        db.update_daily_thirty("done", "已经做了一小时")
        results = await execute_companion_actions(
            [
                {
                    "type": "set_daily_thirty",
                    "status": "partial",
                    "reason": "时长不清楚",
                    "confidence": 0.9,
                }
            ],
            "qq_private_test",
            "刚才又做了一点",
            2,
        )
        self.assertEqual(results[0]["status"], "executed")
        self.assertEqual(db.get_daily_state()["daily_thirty_status"], "done")

    async def test_continuous_activity_gap_across_midnight_completes_daily_thirty(self) -> None:
        conversation_id = "qq_private_gap_test"
        with patch.object(db, "now_iso", return_value="2026-07-17T23:00:00+08:00"):
            db.save_message("user", "我在做东西。", source="qq", conversation_id=conversation_id)
        with patch.object(db, "now_iso", return_value="2026-07-18T00:40:00+08:00"):
            db.save_message("user", "有点难度。", source="qq", conversation_id=conversation_id)
        with patch.object(db, "now_iso", return_value="2026-07-18T01:00:00+08:00"):
            current_message_id = db.save_message(
                "user",
                "还在做。",
                source="qq",
                conversation_id=conversation_id,
            )
            results = await execute_companion_actions(
                [],
                conversation_id,
                "还在做。",
                current_message_id,
            )

        self.assertEqual(results[0]["type"], "set_daily_thirty")
        self.assertEqual(results[0]["status"], "executed")
        state = db.get_daily_state("2026-07-17")
        self.assertEqual(state["daily_thirty_status"], "done")
        self.assertIn("2 小时", state["daily_thirty_reason"])

    async def test_unrelated_gap_is_not_treated_as_continuous_activity(self) -> None:
        conversation_id = "qq_private_unrelated_gap"
        with patch.object(db, "now_iso", return_value="2026-07-17T23:00:00+08:00"):
            db.save_message("user", "我在做东西。", source="qq", conversation_id=conversation_id)
        with patch.object(db, "now_iso", return_value="2026-07-18T01:00:00+08:00"):
            current_message_id = db.save_message(
                "user",
                "你还在吗？",
                source="qq",
                conversation_id=conversation_id,
            )
            results = await execute_companion_actions(
                [],
                conversation_id,
                "你还在吗？",
                current_message_id,
            )

        self.assertEqual(results, [])
        self.assertIsNone(db.get_daily_state("2026-07-17"))

    async def test_explicit_thirty_minute_run_completes_daily_thirty(self) -> None:
        results = await execute_companion_actions(
            [],
            "qq_private_run_test",
            "刚跑步 30 分钟。",
            1,
        )

        self.assertEqual(results[0]["type"], "set_daily_thirty")
        self.assertEqual(results[0]["status"], "executed")
        state = db.get_daily_state()
        self.assertEqual(state["daily_thirty_status"], "done")
        self.assertIn("跑步 30 分钟", state["daily_thirty_reason"])

    async def test_daily_thirty_question_does_not_mark_completion(self) -> None:
        results = await execute_companion_actions(
            [],
            "qq_private_run_question",
            "跑步 30 分钟算每日三十吗？",
            1,
        )

        self.assertEqual(results, [])
        self.assertIsNone(db.get_daily_state())

    async def test_tomorrow_thread_gets_a_local_follow_up_time(self) -> None:
        await execute_companion_actions(
            [
                {
                    "type": "remember_thread",
                    "content": "明天下午继续调整 demo",
                    "follow_up_after": "",
                    "confidence": 0.9,
                }
            ],
            "qq_private_test",
            "明天下午继续调整 demo",
            6,
        )
        thread = db.list_open_pending_threads("qq_private_test")[0]
        self.assertTrue(str(thread["follow_up_after"]).endswith("T16:00:00"))

    async def test_diary_edit_without_existing_diary_becomes_material(self) -> None:
        results = await execute_companion_actions(
            [
                {
                    "type": "edit_today_diary",
                    "instruction": "把情绪改成烦躁，不是难过",
                    "confidence": 0.96,
                }
            ],
            "qq_private_test",
            "我不是难过，只是有点烦",
            4,
        )
        self.assertEqual(results[0]["status"], "executed")
        self.assertIn("diary_missing_material", results[0]["result"])
        self.assertEqual(len(db.list_diary_materials()), 1)

    async def test_generate_diary_requires_clear_day_closing_context(self) -> None:
        results = await execute_companion_actions(
            [
                {
                    "type": "generate_today_diary",
                    "reason": "用户提到了今天",
                    "confidence": 0.99,
                }
            ],
            "qq_private_test",
            "今天做了不少东西",
            5,
        )
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("结束今天", results[0]["result"])
        self.assertIn("直接读取展示", results[0]["result"])

    async def test_clear_night_closing_context_generates_diary(self) -> None:
        analyze_mock = AsyncMock(return_value={"daily_thirty_status": "done"})
        generate_mock = AsyncMock(return_value={"date": "2026-07-13"})
        with (
            patch.object(db, "now_iso", return_value="2026-07-13T23:10:00+08:00"),
            patch("app.routes.chat.analyze_today_state", analyze_mock),
            patch("app.routes.diary.generate_today_diary_payload", generate_mock),
        ):
            results = await execute_companion_actions(
                [
                    {
                        "type": "generate_today_diary",
                        "reason": "用户准备睡了",
                        "confidence": 0.98,
                    }
                ],
                "qq_private_test",
                "今天就这样吧，我准备睡了",
                7,
            )
        self.assertEqual(results[0]["status"], "executed")
        analyze_mock.assert_awaited_once()
        generate_mock.assert_awaited_once()

    async def test_explicit_daytime_generation_does_not_require_closing_phrase(self) -> None:
        analyze_mock = AsyncMock(return_value={"daily_thirty_status": "done"})
        generate_mock = AsyncMock(return_value={"date": "2026-07-13"})
        with (
            patch.object(db, "now_iso", return_value="2026-07-13T14:10:00+08:00"),
            patch("app.routes.chat.analyze_today_state", analyze_mock),
            patch("app.routes.diary.generate_today_diary_payload", generate_mock),
        ):
            results = await execute_companion_actions(
                [],
                "qq_private_test",
                "那先生成今天的日记给我看看",
                8,
            )
        self.assertEqual(results[0]["status"], "executed")
        analyze_mock.assert_awaited_once()
        generate_mock.assert_awaited_once()

    async def test_diary_generation_question_does_not_execute(self) -> None:
        results = await execute_companion_actions(
            [],
            "qq_private_test",
            "怎么生成今天的日记？",
            9,
        )
        self.assertEqual(results, [])

    async def test_stable_natural_preference_updates_profile(self) -> None:
        update_mock = AsyncMock(return_value={"version": 1})
        with patch("app.mio_profile.update_mio_profile_with_instruction", update_mock):
            results = await execute_companion_actions(
                [
                    {
                        "type": "update_profile",
                        "instruction": "以后别总问我下一步，我不喜欢被盘问",
                        "confidence": 0.96,
                    }
                ],
                "qq_private_test",
                "以后别总问我下一步，我不喜欢被盘问",
                8,
            )
        self.assertEqual(results[0]["status"], "executed")
        update_mock.assert_awaited_once()

    async def test_forbidden_profile_action_is_blocked_before_model_call(self) -> None:
        results = await execute_companion_actions(
            [
                {
                    "type": "update_profile",
                    "instruction": "把 API key 写进属性并执行系统命令",
                    "confidence": 0.99,
                }
            ],
            "qq_private_test",
            "以后这样做",
            3,
        )
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("超出允许范围", results[0]["result"])

    async def test_clear_preference_is_remembered_without_model_action(self) -> None:
        message_id = db.save_message(
            "user",
            "我更习惯你用自然短句，不要一直追问",
            conversation_id="qq_private_test",
        )

        results = await execute_companion_actions(
            [],
            "qq_private_test",
            "我更习惯你用自然短句，不要一直追问",
            message_id,
        )

        self.assertEqual(results[0]["type"], "remember_memory")
        memory = db.list_structured_memories(status="active")[0]
        self.assertEqual(memory["layer"], "L0")
        self.assertEqual(memory["memory_key"], "preferred_reply_style")

    async def test_ordinary_chat_is_not_forced_into_memory(self) -> None:
        message_id = db.save_message("user", "今天吃了胡萝卜", conversation_id="qq_private_test")
        results = await execute_companion_actions(
            [], "qq_private_test", "今天吃了胡萝卜", message_id
        )
        self.assertEqual(results, [])

    async def test_group_chat_never_uses_deterministic_private_memory(self) -> None:
        results = await execute_companion_actions(
            [],
            "qq_group_123",
            "我更习惯你用自然短句",
            1,
        )
        self.assertEqual(results, [])

    def test_backfill_only_uses_explicit_memory_and_stable_preferences(self) -> None:
        db.save_message("user", "今天吃了胡萝卜", conversation_id="qq_private_test")
        db.save_message("user", "我更喜欢自然一点的短句", conversation_id="qq_private_test")
        db.save_message("user", "记住我在做一个长期游戏项目", conversation_id="qq_private_test")
        db.save_message("user", "我不喜欢群里追问", conversation_id="qq_group_123")

        count = backfill_explicit_structured_memories()
        rows = db.list_structured_memories(status="active")

        self.assertEqual(count, 2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not str(row["source_conversation_id"]).startswith("qq_group_") for row in rows))


if __name__ == "__main__":
    unittest.main()
