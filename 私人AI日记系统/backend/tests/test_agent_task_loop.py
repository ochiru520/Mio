from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import db, agent_task_service as tasks
from app.agent_loop_service import run_agent_loop, _planner_history
from app.agent_tool_service import ToolExecutionResult
from app.chat_service import _guard_agent_creation_completion_claim
from app.llm import ToolCall
from tests.test_agent_model_first import _completion


def call(name, **arguments):
    return ToolCall(name, name, json.dumps(arguments, ensure_ascii=False))


class AgentTaskLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp.name) / "test.db")
        db.init_db()
        self.conversation = "desktop_agent_task_test"
        self.message_id = db.save_message("user", "读取状态，根据结果记录再核验", conversation_id=self.conversation)

    def tearDown(self):
        object.__setattr__(db.settings, "db_path", self.old_path)
        self.temp.cleanup()

    async def run_loop(self, responses, **kwargs):
        planner = AsyncMock(side_effect=responses)
        with patch("app.agent_loop_service.call_chat_completion_result", planner):
            result = await run_agent_loop(conversation_id=self.conversation, source="desktop",
                user_message="读取状态，根据结果记录再核验", source_message_id=self.message_id,
                request_id=kwargs.pop("request_id", "task-loop"), trace_id="test", model_id="test-model",
                reasoning_level="low", model_first_creation=True, **kwargs)
        return result, planner

    async def test_successful_read_write_verify_has_separate_model_turns(self):
        result, model = await self.run_loop([
            _completion(call("get_today_state")),
            _completion(call("add_diary_material", content="已根据观察记录")),
            _completion(call("get_diary")), _completion(),
        ])
        self.assertEqual(model.await_count, 4)
        self.assertEqual([item.tool_name for item in result.observations], ["get_today_state", "add_diary_material", "get_diary"])
        self.assertEqual(result.status, "completed")
        self.assertTrue(all(item.status == "completed" for item in result.observations))
        self.assertIn("get_today_state", model.await_args_list[1].args[0][-1]["content"])

    async def test_same_read_after_mutation_observes_new_state(self):
        result, model = await self.run_loop([
            _completion(call("get_today_state")),
            _completion(call("update_today_state", mood="平稳", key_events="已完成测试", confidence=1)),
            _completion(call("get_today_state")), _completion(),
        ])
        self.assertEqual(model.await_count, 4)
        self.assertEqual([item.tool_name for item in result.observations],
                         ["get_today_state", "update_today_state", "get_today_state"])
        self.assertEqual(result.observations[1].status, "completed")
        self.assertNotEqual(result.observations[0].result, result.observations[2].result)

    async def test_repeated_plan_stops_without_claiming_goal_complete(self):
        result, model = await self.run_loop([
            _completion(call("get_today_state")), _completion(call("get_today_state")),
        ])
        self.assertEqual(model.await_count, 2)
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.status, "waiting_user")

    async def test_read_only_workflow_check_never_forces_generation(self):
        execute = AsyncMock(return_value=ToolExecutionResult("creation_check_workflow", "completed",
            {"ok": True, "workflows": [{"id": "minimax-h3-video", "status": "ready"}]}, 1))
        with patch("app.agent_loop_service.execute_tool_call", execute):
            result, model = await self.run_loop([
                _completion(call("creation_check_workflow", workflow_id="minimax-h3-video")), _completion(),
            ])
        self.assertEqual(execute.await_count, 1)
        self.assertNotIn("本轮必须调用", json.dumps(model.await_args_list[1].args[0], ensure_ascii=False))
        self.assertEqual(result.status, "completed")

    async def test_every_round_defers_write_until_reads_observed(self):
        result, model = await self.run_loop([
            _completion(call("get_today_state")),
            _completion(call("get_diary"), call("add_diary_material", content="未读取前编写的错误内容")),
            _completion(call("add_diary_material", content="观察完成后的正确内容")), _completion(),
        ])
        materials = [row["content"] for row in db.list_diary_materials()]
        self.assertNotIn("未读取前编写的错误内容", materials)
        self.assertIn("观察完成后的正确内容", materials)

    async def test_no_tool_is_valid_completion_without_forced_correction(self):
        result, model = await self.run_loop([_completion()])
        self.assertEqual(model.await_count, 1)
        self.assertEqual(result.observations, ())

    async def test_invalid_tool_is_observed_before_recovery(self):
        result, model = await self.run_loop([
            _completion(call("unknown_tool")), _completion(call("get_today_state")), _completion(),
        ])
        self.assertEqual([item.status for item in result.observations], ["failed", "completed"])
        self.assertEqual(result.status, "completed")

    async def test_independent_reads_overlap(self):
        entered = 0
        both_entered = asyncio.Event()

        async def execute(name, arguments, context):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), 1)
            return ToolExecutionResult(name, "completed", {}, context.step_index)

        with patch("app.agent_loop_service.execute_tool_call", execute):
            result, _ = await self.run_loop([_completion(call("get_today_state"), call("get_diary")), _completion()])
        self.assertEqual([item.status for item in result.observations], ["completed", "completed"])

    async def test_pending_job_suspends_without_model_polling(self):
        execute = AsyncMock(return_value=ToolExecutionResult("comfyui_generate_image", "completed",
            {"job": {"id": "test-job", "status": "queued"}}, 1))
        with patch("app.agent_loop_service.execute_tool_call", execute):
            result, model = await self.run_loop([_completion(call("comfyui_generate_image", prompt="test"))])
        self.assertEqual(model.await_count, 1)
        self.assertEqual(result.status, "waiting_jobs")
        self.assertEqual(tasks.get(result.task_id)["waiting_jobs"], ["test-job"])

    async def test_budget_exhaustion_preserves_goal_for_resume(self):
        tasks.limits({"enabled": True, "model_calls": 2})
        result, _ = await self.run_loop([_completion(call("get_today_state")), _completion(call("get_diary"))])
        self.assertEqual(result.status, "budget_exhausted")
        resumed = tasks.resume(result.task_id)
        self.assertEqual(resumed["status"], "ready")
        self.assertEqual(resumed["original_goal"], "读取状态，根据结果记录再核验")

    async def test_task_plan_and_user_question_are_persisted(self):
        result, model = await self.run_loop([_completion(call("agent_task_update",
            constraints=["保留蓝裙"], plan=["确认输出尺寸", "生成"], decision="ask_user", blocker="需要尺寸"))])
        self.assertEqual(result.status, "waiting_user")
        self.assertEqual(tasks.get(result.task_id)["snapshot"]["constraints"], ["保留蓝裙"])
        self.assertEqual(model.await_count, 1)

    def test_planner_history_keeps_user_turns(self):
        db.save_message("user", "关键约束：蓝色裙子", conversation_id=self.conversation)
        for turn in range(2):
            for bubble in range(5):
                db.save_message("assistant", f"气泡 {turn}-{bubble}", conversation_id=self.conversation)
            db.save_message("user", "继续", conversation_id=self.conversation)
        self.assertIn("蓝色裙子", _planner_history(self.conversation, self.message_id))

    def test_failed_jobs_are_never_rewritten_as_running(self):
        for status in ("failed", "cancelled", "timed_out", "needs_confirmation"):
            with self.subTest(status=status):
                agent = SimpleNamespace(observations=[ToolExecutionResult("creation_get_job", "completed",
                    {"job": {"id": "test", "status": status}}, 1)])
                replies = _guard_agent_creation_completion_claim(["图片已经生成完成。"], self.conversation, agent)
                self.assertNotIn("正在后台生成", "".join(replies))
                self.assertNotIn("已提交", "".join(replies))
