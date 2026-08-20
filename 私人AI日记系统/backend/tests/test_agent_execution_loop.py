from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app import conversation_service, db
from app.agent_loop_service import (
    AgentLoopResult,
    begin_final_response,
    commit_deferred_final_response,
    defer_final_response,
    finish_final_response,
    run_agent_loop,
)
from app.agent_tool_service import ToolExecutionContext, execute_tool_call
from app.chat_service import (
    ChatResult,
    _complete_chat_reply_with_single_fallback,
    persist_generated_chat_result,
)
from app.companion_action_service import approve_companion_action
from app.llm import (
    CompletionResult,
    CompletionRoute,
    ModelRequestError,
    ToolCall,
    call_chat_completion_result,
)
from app.model_registry import ModelProfile
from app.routes.agent import cancel_agent_task
from app.tool_registry import tool_registry


def completion(
    content: str = "",
    *,
    tool_calls: tuple[ToolCall, ...] = (),
) -> CompletionResult:
    return CompletionResult(
        content=content,
        model="test-model",
        prompt_tokens=10,
        cached_prompt_tokens=0,
        completion_tokens=5,
        reasoning_tokens=0,
        cost_yuan=0.001,
        cost_source="configured_estimate",
        tool_calls=tool_calls,
    )


class AgentExecutionLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "test.db")
        db.init_db()
        self.source_message_id = db.save_message(
            "user",
            "今晚提醒我复测语音",
            source="desktop",
            conversation_id="desktop_agent_test",
            request_id="request-agent-test",
        )

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def context(self, *, step_index: int = 1, user_message: str = "今晚提醒我复测语音"):
        return ToolExecutionContext(
            run_id="run-test",
            request_id="request-agent-test",
            trace_id="trace-test",
            conversation_id="desktop_agent_test",
            source_message_id=self.source_message_id,
            user_message=user_message,
            step_index=step_index,
            tool_call_id=f"call-{step_index}",
        )

    def create_run(self) -> None:
        db.create_agent_run(
            "run-test",
            "request-agent-test",
            trace_id="trace-test",
            conversation_id="desktop_agent_test",
            source="desktop",
            source_message_id=self.source_message_id,
            model_id="test-model",
        )

    async def test_read_tool_persists_correlated_step_and_receipt(self) -> None:
        self.create_run()

        result = await execute_tool_call("get_today_state", {}, self.context())

        self.assertEqual(result.status, "completed")
        self.assertGreater(result.receipt_id, 0)
        step = db.get_agent_run_step(result.step_id)
        self.assertEqual(step["run_id"], "run-test")
        self.assertEqual(step["receipt_id"], result.receipt_id)
        receipt = db.list_tool_execution_receipts(limit=1)[0]
        self.assertEqual(receipt["request_id"], "request-agent-test")
        self.assertEqual(receipt["trace_id"], "trace-test")
        self.assertEqual(receipt["agent_run_id"], "run-test")

    async def test_low_risk_write_replay_has_no_duplicate_side_effect(self) -> None:
        self.create_run()
        arguments = {"content": "完成阶段六幂等验收", "confidence": 0.99}

        first = await execute_tool_call("add_diary_material", arguments, self.context(step_index=1))
        replay = await execute_tool_call("add_diary_material", arguments, self.context(step_index=2))

        self.assertEqual(first.status, "completed")
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.step_id, first.step_id)
        materials = [row for row in db.list_diary_materials() if row["content"] == arguments["content"]]
        self.assertEqual(len(materials), 1)
        actions = db.list_companion_actions(conversation_id="desktop_agent_test")
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(db.list_tool_execution_receipts()), 1)

    async def test_high_risk_write_waits_for_confirmation_with_same_ids(self) -> None:
        self.create_run()
        with patch("app.agent_tool_service.require_configured"):
            result = await execute_tool_call(
                "edit_today_diary",
                {"instruction": "补充今天很累", "confidence": 0.99},
                self.context(user_message="今天很累"),
            )

        self.assertEqual(result.status, "needs_confirmation")
        self.assertGreater(result.action_id, 0)
        action = db.get_companion_action(result.action_id)
        self.assertEqual(action["request_id"], "request-agent-test")
        self.assertEqual(action["trace_id"], "trace-test")
        self.assertEqual(action["agent_run_id"], "run-test")
        self.assertEqual(action["agent_step_id"], result.step_id)

    async def test_approval_refreshes_original_message_receipt_to_completed(self) -> None:
        self.create_run()
        with patch("app.agent_tool_service.require_configured"):
            pending = await execute_tool_call(
                "edit_today_diary",
                {"instruction": "补充今天很累", "confidence": 0.99},
                self.context(user_message="今天很累"),
            )
        assistant_id = db.save_message(
            "assistant",
            "这项修改需要你确认。",
            source="desktop",
            conversation_id="desktop_agent_test",
            request_id="request-agent-test",
        )

        with patch(
            "app.companion_action_service.execute_companion_action_primitive",
            new=AsyncMock(return_value="diary:updated"),
        ):
            approved = await approve_companion_action(pending.action_id)

        self.assertEqual(approved["status"], "executed")
        assistant_row = next(
            row
            for row in db.get_recent_messages(limit=10, conversation_id="desktop_agent_test")
            if int(row["id"]) == assistant_id
        )
        message = conversation_service.public_message(assistant_row)
        self.assertEqual(message["tool_receipts"][0]["status"], "completed")
        self.assertEqual(message["tool_receipts"][0]["result"], {"result": "diary:updated"})
        receipts = db.list_tool_execution_receipts(limit=10)
        self.assertEqual([row["status"] for row in receipts[:2]], ["executed", "approved"])

    async def test_cancellation_refreshes_original_message_receipt_to_cancelled(self) -> None:
        self.create_run()
        with patch("app.agent_tool_service.require_configured"):
            pending = await execute_tool_call(
                "edit_today_diary",
                {"instruction": "补充今天很累", "confidence": 0.99},
                self.context(user_message="今天很累"),
            )
        assistant_id = db.save_message(
            "assistant",
            "这项修改需要你确认。",
            source="desktop",
            conversation_id="desktop_agent_test",
            request_id="request-agent-test",
        )

        cancelled = await cancel_agent_task(pending.action_id)

        self.assertEqual(cancelled["status"], "cancelled")
        assistant_row = next(
            row
            for row in db.get_recent_messages(limit=10, conversation_id="desktop_agent_test")
            if int(row["id"]) == assistant_id
        )
        message = conversation_service.public_message(assistant_row)
        self.assertEqual(message["tool_receipts"][0]["status"], "cancelled")
        self.assertEqual(message["tool_receipts"][0]["error"], "用户已取消。")
        receipt = db.list_tool_execution_receipts(limit=1)[0]
        self.assertEqual(receipt["status"], "cancelled")

    async def test_malformed_legacy_observation_does_not_break_message_receipts(self) -> None:
        self.create_run()
        executed = await execute_tool_call("get_today_state", {}, self.context())
        db.update_agent_run(
            "run-test",
            "completed",
            observation_json=json.dumps([
                {"step_id": "legacy-invalid", "tool_name": "broken"},
                {"step_id": None, "receipt_id": "not-a-number"},
            ]),
        )
        assistant_id = db.save_message(
            "assistant",
            "已经读取今日状态。",
            source="desktop",
            conversation_id="desktop_agent_test",
            request_id="request-agent-test",
        )

        assistant_row = next(
            row
            for row in db.get_recent_messages(limit=10, conversation_id="desktop_agent_test")
            if int(row["id"]) == assistant_id
        )
        message = conversation_service.public_message(assistant_row)

        self.assertEqual(len(message["tool_receipts"]), 1)
        self.assertEqual(message["tool_receipts"][0]["step_id"], executed.step_id)
        self.assertEqual(message["tool_receipts"][0]["status"], "completed")

    async def test_schema_rejects_extra_arguments_before_any_receipt(self) -> None:
        self.create_run()

        with self.assertRaisesRegex(ValueError, "Schema"):
            await execute_tool_call(
                "get_today_state",
                {"command": "rm -rf"},
                self.context(),
            )

        self.assertEqual(db.list_tool_execution_receipts(), [])
        self.assertEqual(db.list_agent_run_steps("run-test"), [])

    async def test_tool_timeout_is_terminal_and_visible(self) -> None:
        self.create_run()
        definition = replace(tool_registry.require("get_today_state"), timeout_seconds=0.01)

        async def slow_dispatch(*_args, **_kwargs):
            await asyncio.sleep(0.2)
            return {"late": True}

        with (
            patch("app.agent_tool_service.tool_registry.require", return_value=definition),
            patch("app.agent_tool_service._dispatch_read_tool", new=slow_dispatch),
        ):
            result = await execute_tool_call("get_today_state", {}, self.context())

        self.assertEqual(result.status, "timed_out")
        self.assertIn("0.01", result.error)
        self.assertEqual(db.get_agent_run_step(result.step_id)["status"], "timed_out")

    async def test_cancellation_marks_tool_step_and_receipt(self) -> None:
        self.create_run()

        async def slow_dispatch(*_args, **_kwargs):
            await asyncio.sleep(10)
            return {"late": True}

        with patch("app.agent_tool_service._dispatch_read_tool", new=slow_dispatch):
            task = asyncio.create_task(execute_tool_call("get_today_state", {}, self.context()))
            await asyncio.sleep(0.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        step = db.list_agent_run_steps("run-test")[0]
        self.assertEqual(step["status"], "cancelled")
        receipt = db.list_tool_execution_receipts(limit=1)[0]
        self.assertEqual(receipt["status"], "cancelled")

    async def test_native_multi_step_run_executes_before_final_and_replays(self) -> None:
        native = completion(tool_calls=(
            ToolCall("call-state", "get_today_state", "{}"),
            ToolCall(
                "call-material",
                "add_diary_material",
                json.dumps({"content": "阶段六多步任务", "confidence": 0.99}, ensure_ascii=False),
            ),
        ))
        with patch(
            "app.agent_loop_service.call_chat_completion_result",
            new=AsyncMock(return_value=native),
        ) as planner:
            result = await run_agent_loop(
                conversation_id="desktop_agent_test",
                source="desktop",
                user_message="读取今日状态并记录阶段六多步任务",
                source_message_id=self.source_message_id,
                request_id="request-native-multi-step",
                trace_id="trace-native",
                model_id="test-model",
                reasoning_level="low",
            )

        self.assertEqual(planner.await_count, 1)
        self.assertEqual([item.status for item in result.observations], ["completed", "completed"])
        final_step = begin_final_response(result)
        finish_final_response(result, final_step, reply="已经读取并记录。")
        self.assertEqual(db.get_agent_run(result.run_id)["status"], "completed")

        with patch(
            "app.agent_loop_service.call_chat_completion_result",
            new=AsyncMock(),
        ) as replay_planner:
            replay = await run_agent_loop(
                conversation_id="desktop_agent_test",
                source="desktop",
                user_message="读取今日状态并记录阶段六多步任务",
                source_message_id=self.source_message_id,
                request_id="request-native-multi-step",
                trace_id="trace-native-new",
                model_id="test-model",
                reasoning_level="low",
            )

        replay_planner.assert_not_awaited()
        self.assertTrue(all(item.replayed for item in replay.observations))
        self.assertEqual(len([row for row in db.list_diary_materials() if row["content"] == "阶段六多步任务"]), 1)

    async def test_staged_phone_agent_commits_against_real_saved_message(self) -> None:
        self.create_run()
        observation = await execute_tool_call(
            "add_diary_material",
            {"content": "电话暂存提交验收", "confidence": 0.99},
            self.context(step_index=1),
        )
        agent = AgentLoopResult(
            run_id="run-test",
            status="awaiting_response",
            plan_mode="native",
            observations=(observation,),
            model_results=(),
            replanned=False,
            next_step_index=2,
        )
        final_step = begin_final_response(agent)
        defer_final_response(agent, final_step, reply="已经记下电话里的复测事项。")
        self.assertEqual(db.get_agent_run("run-test")["status"], "awaiting_commit")

        result = ChatResult(
            reply="已经记下电话里的复测事项。",
            replies=["已经记下电话里的复测事项。"],
            request_id="request-agent-test",
            model_id="test-model",
            reasoning_level="low",
            agent_run_id="run-test",
            agent_run_status="awaiting_commit",
            tool_receipts=(observation.public_dict(),),
        )
        saved_message_id = persist_generated_chat_result(
            "今晚提醒我复测语音",
            result,
            conversation_id="desktop_agent_test",
            source="desktop_pet_call",
            voice_reply_requested=True,
        )

        commit_deferred_final_response("run-test", saved_message_id)
        run = db.get_agent_run("run-test")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["source_message_id"], saved_message_id)
        action = db.list_companion_actions(conversation_id="desktop_agent_test")[0]
        self.assertEqual(action["source_message_id"], saved_message_id)
        final = [
            row
            for row in db.list_agent_run_steps("run-test")
            if row["step_kind"] == "final_response"
        ][0]
        self.assertEqual(final["status"], "completed")

    async def test_native_rejection_falls_back_to_json_compatibility(self) -> None:
        profile = ModelProfile(
            id="test-model",
            provider_id="provider-test",
            provider_name="测试供应商",
            display_name="测试模型",
            model="test-model",
            base_urls=("https://example.test/v1",),
            api_key="secret",
        )
        json_plan = completion(
            json.dumps(
                {
                    "plan": ["读取今日状态"],
                    "tool_calls": [
                        {"call_id": "json-state", "name": "get_today_state", "arguments": {}}
                    ],
                },
                ensure_ascii=False,
            )
        )
        planner = AsyncMock(
            side_effect=[
                ModelRequestError("供应商不支持 tools", profile=profile, http_status=400),
                json_plan,
            ]
        )

        with patch("app.agent_loop_service.call_chat_completion_result", planner):
            result = await run_agent_loop(
                conversation_id="desktop_agent_test",
                source="desktop",
                user_message="读取今日状态",
                source_message_id=self.source_message_id,
                request_id="request-json-fallback",
                trace_id="trace-json-fallback",
                model_id="test-model",
                reasoning_level="low",
            )

        self.assertEqual(planner.await_count, 2)
        self.assertEqual(result.plan_mode, "json")
        self.assertEqual([item.status for item in result.observations], ["completed"])
        self.assertIn("不支持 tools", result.error)
        self.assertNotIn("tools", planner.await_args_list[1].kwargs)

    async def test_failed_tool_replans_once_and_uses_recovery_call(self) -> None:
        planner = AsyncMock(
            side_effect=[
                completion(tool_calls=(ToolCall("bad-call", "missing_tool", "{}"),)),
                completion(tool_calls=(ToolCall("recovery-call", "get_today_state", "{}"),)),
            ]
        )

        with patch("app.agent_loop_service.call_chat_completion_result", planner):
            result = await run_agent_loop(
                conversation_id="desktop_agent_test",
                source="desktop",
                user_message="先读取不可用能力，失败后改为读取今日状态",
                source_message_id=self.source_message_id,
                request_id="request-single-replan",
                trace_id="trace-single-replan",
                model_id="test-model",
                reasoning_level="low",
            )

        self.assertEqual(planner.await_count, 2)
        self.assertTrue(result.replanned)
        self.assertEqual(
            [(item.tool_name, item.status) for item in result.observations],
            [("missing_tool", "failed"), ("get_today_state", "completed")],
        )
        steps = db.list_agent_run_steps(result.run_id)
        self.assertEqual(len([row for row in steps if row["step_kind"] == "replan"]), 1)
        self.assertEqual(db.get_agent_run(result.run_id)["replan_count"], 1)

    async def test_model_fallback_does_not_repeat_completed_tool_side_effect(self) -> None:
        planner = AsyncMock(return_value=completion(tool_calls=(
            ToolCall(
                "write-once",
                "add_diary_material",
                json.dumps({"content": "阶段七降级副作用验收", "confidence": 0.99}, ensure_ascii=False),
            ),
        )))
        with patch("app.agent_loop_service.call_chat_completion_result", planner):
            agent = await run_agent_loop(
                conversation_id="desktop_agent_test",
                source="desktop",
                user_message="把阶段七降级副作用验收加入日记素材",
                source_message_id=self.source_message_id,
                request_id="request-model-fallback-side-effect",
                trace_id="trace-model-fallback-side-effect",
                model_id="primary-model",
                reasoning_level="medium",
            )

        messages = [
            {"role": "user", "content": "把阶段七降级副作用验收加入日记素材"},
            {"role": "system", "content": agent.model_context()},
        ]
        final = completion("已经记下了。")
        complete = AsyncMock(side_effect=[TimeoutError("首选模型超时"), (final, "high")])
        with (
            patch("app.chat_service._complete_chat_reply", complete),
            patch("app.chat_service.require_configured"),
            patch("app.chat_service.get_model_profile", return_value=SimpleNamespace(model="fallback-model")),
            patch("app.chat_service.normalize_model_reasoning", return_value="high"),
        ):
            _, _, escalated_from = await _complete_chat_reply_with_single_fallback(
                messages,
                temperature=0.7,
                model_id="primary-model",
                model_name="primary-model",
                reasoning_level="medium",
                fallback_model_id="fallback-model",
                fallback_reasoning_level="high",
                request_id="request-model-fallback-side-effect",
            )

        materials = [
            row for row in db.list_diary_materials()
            if row["content"] == "阶段七降级副作用验收"
        ]
        self.assertEqual(escalated_from, "primary-model")
        self.assertEqual(complete.await_count, 2)
        self.assertEqual(len(materials), 1)
        self.assertIn("已经真实执行并验证的工具结果", complete.await_args_list[1].args[0][1]["content"])


class NativeToolCallProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_tool_calls_allow_empty_content_and_keep_schema_payload(self) -> None:
        captured_payload: dict[str, object] = {}

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, *_args, **kwargs):
                captured_payload.update(kwargs["json"])
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
                    json={
                        "model": "test-model",
                        "choices": [{
                            "message": {
                                "content": None,
                                "tool_calls": [{
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_today_state", "arguments": "{}"},
                                }],
                            }
                        }],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                    },
                )

        profile = ModelProfile(
            id="test-model",
            provider_id="provider-test",
            provider_name="测试供应商",
            display_name="测试模型",
            model="test-model",
            base_urls=("https://example.test/v1",),
            api_key="secret",
        )
        tools = [tool_registry.require("get_today_state").native_schema()]
        with (
            patch("app.llm.require_configured"),
            patch("app.llm.resolve_model_id", return_value=profile.id),
            patch("app.llm.get_model_profile", return_value=profile),
            patch(
                "app.llm._completion_routes",
                return_value=[CompletionRoute("https://example.test/v1", "", "直连")],
            ),
            patch("app.llm.httpx.AsyncClient", FakeClient),
        ):
            result = await call_chat_completion_result(
                [{"role": "user", "content": "读取今日状态"}],
                model_id=profile.id,
                request_id="native-protocol-test",
                tools=tools,
                tool_choice="auto",
            )

        self.assertEqual(result.content, "")
        self.assertEqual(result.tool_calls[0].name, "get_today_state")
        self.assertEqual(captured_payload["tools"], tools)
        self.assertEqual(captured_payload["tool_choice"], "auto")


if __name__ == "__main__":
    unittest.main()
