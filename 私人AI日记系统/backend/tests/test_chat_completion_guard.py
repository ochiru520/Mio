from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent_loop_service import AgentLoopResult
from app.agent_tool_service import ToolExecutionResult
from app.chat_service import (
    ChatResult,
    _chat_with_ai_unlocked,
    _complete_chat_reply,
    _complete_chat_reply_with_single_fallback,
    _is_placeholder_only_reply,
    _self_snapshot_context_for_message,
    chat_in_qq_group,
    chat_with_ai,
)
from app.conversation_runtime import ConversationRunCoordinator, RuntimeTraceStore
from app.llm import CompletionResult


def _completion(content: str, *, tokens: int, cost: float) -> CompletionResult:
    return CompletionResult(
        content=content,
        model="deepseek-v4-flash",
        prompt_tokens=10,
        cached_prompt_tokens=2,
        completion_tokens=tokens,
        reasoning_tokens=0,
        cost_yuan=cost,
        cost_source="official_estimate",
    )


def _agent_result(run_id: str) -> AgentLoopResult:
    return AgentLoopResult(
        run_id=run_id,
        status="awaiting_response",
        plan_mode="native",
        observations=(),
        model_results=(),
        replanned=False,
        next_step_index=1,
    )


class ChatCompletionGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_detects_placeholder_only_replies(self) -> None:
        self.assertTrue(_is_placeholder_only_reply("我想想。"))
        self.assertTrue(_is_placeholder_only_reply("嗯……让我先想一下。"))
        self.assertTrue(_is_placeholder_only_reply("稍等。"))
        self.assertFalse(_is_placeholder_only_reply("我想想，最需要的是更稳定的上下文。"))

    async def test_repeat_recitation_bypasses_model_and_reuses_exact_target(self) -> None:
        history = [
            {"id": 1, "role": "user", "content": "跟我说，1 2 3 3 2 1 啊 啊"},
            {"id": 2, "role": "assistant", "content": "1 2 3 3 2 1 啊 啊"},
        ]
        with (
            patch("app.chat_service.db.get_recent_messages", return_value=history),
            patch("app.chat_service.db.save_message") as save_message,
            patch("app.chat_service.resolve_model_id") as resolve_model,
            patch("app.chat_service.require_configured") as require_model,
            patch("app.chat_service._complete_chat_reply") as complete,
            patch("app.companion_service.infer_speech_emotion", return_value="neutral"),
            patch("app.companion_service.set_pet_activity"),
        ):
            result = await _chat_with_ai_unlocked(
                "再来一遍",
                conversation_id="recitation-test",
                source="desktop_pet",
                model_id="unavailable-model",
                request_id="recitation-request-1",
            )

        self.assertEqual(result.replies, ["1 2 3 3 2 1 啊 啊"])
        self.assertEqual(result.route, "local_deterministic_recitation")
        self.assertEqual(save_message.call_count, 2)
        resolve_model.assert_not_called()
        require_model.assert_not_called()
        complete.assert_not_called()

    async def test_placeholder_reply_is_retried_with_real_content(self) -> None:
        completion_call = AsyncMock(side_effect=[
            _completion("我想想。", tokens=4, cost=0.001),
            _completion("我希望先把上下文衔接做得更稳定。", tokens=12, cost=0.002),
        ])
        with patch("app.chat_service.call_chat_completion_result", completion_call):
            result, reasoning_level = await _complete_chat_reply(
                [{"role": "user", "content": "你想加什么功能？"}],
                temperature=0.7,
                model_id="deepseek-v4-flash",
                model_name="deepseek-v4-flash",
                reasoning_level="off",
            )

        self.assertEqual(completion_call.await_count, 2)
        self.assertEqual(completion_call.await_args_list[1].kwargs["reasoning_level"], "low")
        self.assertEqual(reasoning_level, "low")
        self.assertEqual(result.content, "我希望先把上下文衔接做得更稳定。")
        self.assertEqual(result.completion_tokens, 16)
        self.assertAlmostEqual(result.cost_yuan, 0.003)

    async def test_untagged_reasoning_without_final_boundary_is_retried(self) -> None:
        completion_call = AsyncMock(side_effect=[
            _completion(
                "We need answer Chinese.\nNeed ask user maybe.\nPerhaps voice issue.",
                tokens=12,
                cost=0.001,
            ),
            _completion("先看当前语音服务的实际错误。", tokens=10, cost=0.002),
        ])
        with patch("app.chat_service.call_chat_completion_result", completion_call):
            result, _ = await _complete_chat_reply(
                [{"role": "user", "content": "语音怎么了"}],
                temperature=0.7,
                model_id="deepseek-v4-flash",
                model_name="deepseek-v4-flash",
                reasoning_level="low",
            )

        self.assertEqual(completion_call.await_count, 2)
        self.assertEqual(result.content, "先看当前语音服务的实际错误。")
        self.assertAlmostEqual(result.cost_yuan, 0.003)

    async def test_cross_model_fallback_reuses_observations_and_runs_once(self) -> None:
        messages = [
            {"role": "user", "content": "检查状态后告诉我结果"},
            {"role": "system", "content": "工具观察：状态读取成功，status=ok"},
        ]
        completion = _completion("状态正常。", tokens=8, cost=0.002)
        complete = AsyncMock(side_effect=[TimeoutError("首选模型超时"), (completion, "high")])
        fallback_profile = SimpleNamespace(model="fallback-model")
        with (
            patch("app.chat_service._complete_chat_reply", complete),
            patch("app.chat_service.require_configured"),
            patch("app.chat_service.get_model_profile", return_value=fallback_profile),
            patch("app.chat_service.normalize_model_reasoning", return_value="high"),
        ):
            result, reasoning, escalated_from = await _complete_chat_reply_with_single_fallback(
                messages,
                temperature=0.7,
                model_id="primary-model",
                model_name="primary-model",
                reasoning_level="medium",
                fallback_model_id="fallback-model-id",
                fallback_reasoning_level="high",
                request_id="fallback-once",
            )

        self.assertEqual(complete.await_count, 2)
        fallback_messages = complete.await_args_list[1].args[0]
        self.assertEqual(fallback_messages[:2], messages)
        self.assertIn("不得重新执行", fallback_messages[-1]["content"])
        self.assertEqual(result.content, "状态正常。")
        self.assertEqual(reasoning, "high")
        self.assertEqual(escalated_from, "primary-model")

    async def test_cross_model_fallback_does_not_try_a_third_model(self) -> None:
        complete = AsyncMock(side_effect=[TimeoutError("首选失败"), TimeoutError("备用失败")])
        with (
            patch("app.chat_service._complete_chat_reply", complete),
            patch("app.chat_service.require_configured"),
            patch("app.chat_service.get_model_profile", return_value=SimpleNamespace(model="fallback-model")),
            patch("app.chat_service.normalize_model_reasoning", return_value="high"),
        ):
            with self.assertRaises(TimeoutError):
                await _complete_chat_reply_with_single_fallback(
                    [{"role": "user", "content": "测试"}],
                    temperature=0.7,
                    model_id="primary-model",
                    model_name="primary-model",
                    reasoning_level="medium",
                    fallback_model_id="fallback-model-id",
                    fallback_reasoning_level="high",
                    request_id="fallback-fails",
                )

        self.assertEqual(complete.await_count, 2)

    async def test_same_conversation_requests_are_serialized(self) -> None:
        active = 0
        maximum_active = 0

        async def fake_chat(*args, **kwargs):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return args[0]

        with patch("app.chat_service._chat_with_ai_unlocked", side_effect=fake_chat):
            results = await asyncio.gather(
                chat_with_ai("第一条", conversation_id="shared-order-test"),
                chat_with_ai("第二条", conversation_id="shared-order-test"),
            )

        self.assertEqual(results, ["第一条", "第二条"])
        self.assertEqual(maximum_active, 1)

    async def test_persist_false_stages_result_without_database_side_effects(self) -> None:
        completion = _completion("这是暂存回复。", tokens=8, cost=0.001)
        staged_agent = _agent_result("run-staged-call-test")
        defer_final = unittest.mock.Mock()
        with (
            patch("app.chat_service.resolve_model_id", return_value="deepseek-v4-flash"),
            patch("app.chat_service.require_configured"),
            patch(
                "app.chat_service.get_model_profile",
                return_value=SimpleNamespace(model="deepseek-v4-flash"),
            ),
            patch("app.chat_service.normalize_model_reasoning", return_value="off"),
            patch("app.chat_service.load_manuals", return_value=[]),
            patch("app.chat_service.build_system_prompt", return_value="system"),
            patch(
                "app.chat_service.build_chat_context_snapshot",
                side_effect=lambda _conversation_id, rows: SimpleNamespace(
                    raw_messages=list(rows),
                    system_context="",
                ),
            ),
            patch("app.chat_service.perform_web_lookup", new=AsyncMock(return_value=None)),
            patch("app.chat_service.system_audio_service.chat_context", return_value=""),
            patch("app.chat_service.db.get_latest_message_id", return_value=40),
            patch("app.chat_service.db.get_recent_messages", return_value=[]),
            patch("app.chat_service.db.now_iso", return_value="2026-08-15T12:00:00+08:00"),
            patch("app.chat_service.db.save_message") as save_message,
            patch("app.companion_service.set_pet_activity"),
            patch(
                "app.chat_service.run_agent_loop",
                new=AsyncMock(return_value=staged_agent),
            ) as run_agent,
            patch("app.chat_service.begin_final_response", return_value=9),
            patch("app.chat_service.defer_final_response", new=defer_final),
            patch(
                "app.chat_service._complete_chat_reply",
                new=AsyncMock(return_value=(completion, "off")),
            ),
            patch("app.chat_service.queue_cost_reconciliation") as queue_cost,
            patch("app.chat_service.schedule_companion_actions") as schedule_actions,
        ):
            result = await _chat_with_ai_unlocked(
                "这条消息只能暂存",
                conversation_id="staged-call-test",
                source="desktop_pet_call",
                reasoning_level="off",
                model_id="deepseek-v4-flash",
                request_id="staged-response-1",
                persist=False,
            )

        self.assertEqual(result.reply, "这是暂存回复")
        self.assertEqual(result.request_id, "staged-response-1")
        self.assertEqual(result.agent_run_id, "run-staged-call-test")
        self.assertEqual(result.agent_run_status, "awaiting_commit")
        run_agent.assert_awaited_once()
        defer_final.assert_called_once_with(staged_agent, 9, reply="这是暂存回复")
        save_message.assert_not_called()
        queue_cost.assert_not_called()
        schedule_actions.assert_not_called()

    async def test_self_snapshot_collection_runs_off_the_event_loop(self) -> None:
        def blocking_collection(_message: str) -> str:
            time.sleep(0.05)
            return "snapshot-context"

        with patch(
            "app.chat_service._self_snapshot_context_for_message_sync",
            side_effect=blocking_collection,
        ):
            task = asyncio.create_task(_self_snapshot_context_for_message("你现在在哪个页面"))
            await asyncio.sleep(0.005)
            self.assertFalse(task.done())
            result = await task

        self.assertEqual(result, "snapshot-context")

    async def test_self_snapshot_is_injected_into_system_context(self) -> None:
        completion = _completion("我现在在设置页面。", tokens=8, cost=0.001)
        captured_messages: list[dict[str, object]] = []
        staged_agent = _agent_result("run-self-snapshot-test")

        async def complete(messages, **_kwargs):
            captured_messages.extend(messages)
            return completion, "off"

        with (
            patch("app.chat_service.resolve_model_id", return_value="deepseek-v4-flash"),
            patch("app.chat_service.require_configured"),
            patch(
                "app.chat_service.get_model_profile",
                return_value=SimpleNamespace(model="deepseek-v4-flash"),
            ),
            patch("app.chat_service.normalize_model_reasoning", return_value="off"),
            patch("app.chat_service.load_manuals", return_value=[]),
            patch("app.chat_service.build_system_prompt", return_value="system"),
            patch(
                "app.chat_service.build_chat_context_snapshot",
                side_effect=lambda _conversation_id, rows: SimpleNamespace(
                    raw_messages=list(rows),
                    system_context="",
                ),
            ),
            patch("app.chat_service.perform_web_lookup", new=AsyncMock(return_value=None)),
            patch("app.chat_service.system_audio_service.chat_context", return_value=""),
            patch("app.chat_service.db.get_latest_message_id", return_value=40),
            patch("app.chat_service.db.get_recent_messages", return_value=[]),
            patch("app.chat_service.db.now_iso", return_value="2026-08-15T12:00:00+08:00"),
            patch("app.companion_service.set_pet_activity"),
            patch(
                "app.chat_service.run_agent_loop",
                new=AsyncMock(return_value=staged_agent),
            ),
            patch("app.chat_service.begin_final_response", return_value=9),
            patch("app.chat_service.defer_final_response"),
            patch(
                "app.chat_service._self_snapshot_context_for_message",
                new=AsyncMock(return_value="SELF-SNAPSHOT-CONTEXT"),
            ),
            patch("app.chat_service._complete_chat_reply", new=complete),
        ):
            await _chat_with_ai_unlocked(
                "你现在在哪个页面",
                conversation_id="self-snapshot-context-test",
                source="desktop",
                reasoning_level="off",
                model_id="deepseek-v4-flash",
                persist=False,
            )

        self.assertTrue(captured_messages)
        self.assertIn("SELF-SNAPSHOT-CONTEXT", str(captured_messages[0]["content"]))

    async def test_final_reply_model_receives_verified_tool_observation(self) -> None:
        completion = _completion("今天的状态是平静。", tokens=8, cost=0.001)
        captured_messages: list[dict[str, object]] = []
        observation = ToolExecutionResult(
            tool_name="get_today_state",
            status="completed",
            result={"date": "2026-08-15", "state": {"mood": "平静"}},
            step_id=2,
            receipt_id=3,
        )
        agent_result = AgentLoopResult(
            run_id="run-observation-test",
            status="awaiting_response",
            plan_mode="native",
            observations=(observation,),
            model_results=(),
            replanned=False,
            next_step_index=3,
        )

        async def complete(messages, **_kwargs):
            captured_messages.extend(messages)
            return completion, "off"

        history_row = {
            "id": 40,
            "role": "user",
            "content": "读取今天的状态",
            "source": "desktop",
            "conversation_id": "agent-observation-test",
            "created_at": "2026-08-15T12:00:00+08:00",
            "request_id": "agent-observation-request",
        }
        finish_final = unittest.mock.Mock()
        with (
            patch("app.chat_service.resolve_model_id", return_value="deepseek-v4-flash"),
            patch("app.chat_service.require_configured"),
            patch(
                "app.chat_service.get_model_profile",
                return_value=SimpleNamespace(model="deepseek-v4-flash"),
            ),
            patch("app.chat_service.normalize_model_reasoning", return_value="off"),
            patch("app.chat_service.load_manuals", return_value=[]),
            patch("app.chat_service.build_system_prompt", return_value="system"),
            patch(
                "app.chat_service.build_chat_context",
                new=AsyncMock(
                    return_value=SimpleNamespace(raw_messages=[history_row], system_context="")
                ),
            ),
            patch("app.chat_service.perform_web_lookup", new=AsyncMock(return_value=None)),
            patch("app.chat_service._self_snapshot_context_for_message", new=AsyncMock(return_value="")),
            patch("app.chat_service.system_audio_service.chat_context", return_value=""),
            patch("app.chat_service.db.get_recent_messages", return_value=[history_row]),
            patch("app.chat_service.db.now_iso", return_value="2026-08-15T12:00:00+08:00"),
            patch("app.chat_service.db.save_message", return_value=40),
            patch("app.companion_service.set_pet_activity"),
            patch("app.chat_service.run_agent_loop", new=AsyncMock(return_value=agent_result)),
            patch("app.chat_service.begin_final_response", return_value=9),
            patch("app.chat_service.finish_final_response", new=finish_final),
            patch("app.chat_service._complete_chat_reply", new=complete),
            patch("app.chat_service.queue_cost_reconciliation"),
        ):
            result = await _chat_with_ai_unlocked(
                "读取今天的状态",
                conversation_id="agent-observation-test",
                source="desktop",
                reasoning_level="off",
                model_id="deepseek-v4-flash",
                request_id="agent-observation-request",
                persist=True,
            )

        system_payload = "\n".join(
            str(item.get("content") or "")
            for item in captured_messages
            if item.get("role") == "system"
        )
        self.assertIn('"tool":"get_today_state"', system_payload)
        self.assertIn('"mood":"平静"', system_payload)
        self.assertEqual(result.tool_receipts[0]["status"], "completed")
        finish_final.assert_called_once_with(agent_result, 9, reply="今天的状态是平静")

    async def test_immediate_follow_up_replaces_run_after_first_message_is_captured(self) -> None:
        traces = RuntimeTraceStore()
        coordinator: ConversationRunCoordinator[str] = ConversationRunCoordinator(traces)
        calls: list[str] = []

        def runner(name: str):
            async def run() -> str:
                calls.append(name)
                await asyncio.sleep(0.02)
                return name

            return run

        first, second = await asyncio.gather(
            coordinator.submit("capture-test", "desktop", runner("第一条"), capture_seconds=1),
            coordinator.submit("capture-test", "desktop", runner("第二条"), capture_seconds=1),
        )

        self.assertEqual(calls, ["第一条", "第二条"])
        self.assertEqual((first, second), ("第二条", "第二条"))
        records = await traces.list(limit=10)
        completed = next(record for record in records if record["status"] == "completed")
        self.assertEqual(completed["replaced_count"], 1)
        self.assertIn("follow_up_captured", completed["stages"])
        self.assertIn("model_started", completed["stages"])
        self.assertIn("completed", completed["stages"])

    async def test_group_chat_accepts_capture_options_and_uses_shared_coordinator(self) -> None:
        expected = ChatResult(reply="群聊回复", replies=["群聊回复"])
        with patch(
            "app.chat_service._chat_in_qq_group_unlocked",
            new=AsyncMock(return_value=expected),
        ) as inner:
            result = await chat_in_qq_group(
                "还有一句",
                sender_name="群成员",
                history=[],
                conversation_id="qq_group_123",
                capture_follow_ups=True,
            )

        self.assertIs(result, expected)
        inner.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
