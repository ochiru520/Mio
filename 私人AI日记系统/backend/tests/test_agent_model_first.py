from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.agent_loop_service import ToolExecutionResult, run_agent_loop
from app.llm import CompletionResult, ToolCall


def _completion(*calls: ToolCall) -> CompletionResult:
    return CompletionResult(
        content="",
        model="test-model",
        prompt_tokens=10,
        cached_prompt_tokens=0,
        completion_tokens=5,
        reasoning_tokens=0,
        cost_yuan=0.001,
        cost_source="test",
        tool_calls=tuple(calls),
    )


class AgentModelFirstTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "test.db")
        db.init_db()
        self.message_id = db.save_message(
            "user",
            "保持萝莉角色",
            source="desktop",
            conversation_id="desktop_agent_model_first",
            request_id="model-first",
        )

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        self.temp_dir.cleanup()

    async def test_successful_creation_discovery_gets_an_automatic_follow_up_plan(self) -> None:
        planner = AsyncMock(
            side_effect=[
                _completion(ToolCall("read-workflow", "creation_check_workflow", json.dumps({"workflow_id": "anima-2.9b-image"}))),
                _completion(ToolCall("generate", "comfyui_generate_image", json.dumps({
                    "prompt": "a character",
                    "negative_prompt": "low quality",
                }))),
            ]
        )

        async def execute(name: str, _arguments: object, context: object) -> ToolExecutionResult:
            if name == "creation_check_workflow":
                return ToolExecutionResult(name, "completed", {
                    "ok": True,
                    "workflows": [{"id": "anima-2.9b-image", "status": "ready"}],
                }, int(getattr(context, "step_index", 1)))
            return ToolExecutionResult(name, "completed", {
                "job": {"id": "job_model_first", "status": "created"},
            }, int(getattr(context, "step_index", 1)))

        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.agent_loop_service.execute_tool_call", new=execute),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_model_first",
                source="desktop",
                user_message="保持萝莉角色",
                source_message_id=self.message_id,
                request_id="model-first-run",
                trace_id="model-first-trace",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={"creation_check_workflow", "comfyui_generate_image"},
            )
        self.assertEqual(planner.await_count, 2)
        self.assertEqual(
            [item.tool_name for item in result.observations],
            ["creation_check_workflow", "comfyui_generate_image"],
        )
        self.assertTrue(result.replanned)

    async def test_natural_follow_up_can_choose_generation_without_keyword_gate(self) -> None:
        planner = AsyncMock(
            return_value=_completion(ToolCall("generate", "comfyui_generate_image", json.dumps({
                "prompt": "a consistent character",
                "negative_prompt": "low quality",
            })))
        )

        async def execute(name: str, _arguments: object, context: object) -> ToolExecutionResult:
            return ToolExecutionResult(name, "completed", {
                "job": {"id": "job_natural_follow_up", "status": "created"},
            }, int(getattr(context, "step_index", 1)))

        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.agent_loop_service.execute_tool_call", new=execute),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_model_first",
                source="desktop",
                user_message="保持萝莉角色",
                source_message_id=self.message_id,
                request_id="model-first-natural-run",
                trace_id="model-first-natural-trace",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={"comfyui_generate_image"},
            )

        planner.assert_awaited_once()
        self.assertEqual([item.tool_name for item in result.observations], ["comfyui_generate_image"])

    async def test_model_first_creation_skips_keyword_preflight(self) -> None:
        """Agent mode lets the planner discover or generate from any wording."""
        planner = AsyncMock(
            return_value=_completion(ToolCall("generate", "comfyui_generate_image", json.dumps({
                "prompt": "a quiet portrait by the window",
                "negative_prompt": "blurry, watermark",
            })))
        )

        async def execute(name: str, _arguments: object, context: object) -> ToolExecutionResult:
            return ToolExecutionResult(name, "completed", {
                "job": {"id": "job_model_first_direct", "status": "created"},
            }, int(getattr(context, "step_index", 1)))

        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.agent_loop_service.execute_tool_call", new=execute),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_model_first",
                source="desktop",
                user_message="把刚才那种感觉做成一张安静的窗边肖像",
                source_message_id=self.message_id,
                request_id="model-first-direct-run",
                trace_id="model-first-direct-trace",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={
                    "creation_list_presets",
                    "creation_list_loras",
                    "creation_check_workflow",
                    "comfyui_generate_image",
                },
                model_first_creation=True,
            )

        planner.assert_awaited_once()
        self.assertEqual(
            [item.tool_name for item in result.observations],
            ["comfyui_generate_image"],
        )

    async def test_model_first_does_not_call_legacy_creation_intent_detector(self) -> None:
        """Agent intent must come from the planner, even for ambiguous wording."""
        planner = AsyncMock(
            return_value=_completion(ToolCall("generate", "comfyui_generate_image", json.dumps({
                "prompt": "a quiet portrait",
                "negative_prompt": "blurry",
            })))
        )

        async def execute(name: str, _arguments: object, context: object) -> ToolExecutionResult:
            return ToolExecutionResult(name, "completed", {
                "job": {"id": "job_model_owned_intent", "status": "created"},
            }, int(getattr(context, "step_index", 1)))

        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.agent_loop_service.execute_tool_call", new=execute),
            patch(
                "app.agent_loop_service._agent_creation_target",
                side_effect=AssertionError("legacy intent detector must not run in Agent mode"),
            ),
            patch(
                "app.agent_loop_service._inherit_continuation_arguments",
                side_effect=AssertionError("legacy continuation rewrite must not run in Agent mode"),
            ),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_model_first",
                source="desktop",
                user_message="给我一个安静的窗边肖像",
                source_message_id=self.message_id,
                request_id="model-first-no-legacy-intent",
                trace_id="model-first-no-legacy-intent-trace",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={"comfyui_generate_image"},
                model_first_creation=True,
            )

        self.assertEqual([item.tool_name for item in result.observations], ["comfyui_generate_image"])

    async def test_model_first_creation_splits_discovery_and_generation_batch(self) -> None:
        planner = AsyncMock(
            side_effect=[
                _completion(
                    ToolCall("loras", "creation_list_loras", json.dumps({"workflow_id": "anima-2.9b-image"})),
                    ToolCall("generate-too-early", "comfyui_generate_image", json.dumps({
                        "prompt": "portrait",
                        "negative_prompt": "blurry",
                        "lora_choices": ["clear_lineart"],
                    })),
                ),
                _completion(ToolCall("generate", "comfyui_generate_image", json.dumps({
                    "prompt": "portrait",
                    "negative_prompt": "blurry",
                    "lora_choices": ["clear_lineart"],
                }))),
            ]
        )
        executed: list[str] = []

        async def execute(name: str, _arguments: object, context: object) -> ToolExecutionResult:
            executed.append(name)
            result = {"loras": []} if name == "creation_list_loras" else {
                "job": {"id": "job_model_first_split", "status": "created"},
            }
            return ToolExecutionResult(name, "completed", result, int(getattr(context, "step_index", 1)))

        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.agent_loop_service.execute_tool_call", new=execute),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_model_first",
                source="desktop",
                user_message="做一张线稿肖像",
                source_message_id=self.message_id,
                request_id="model-first-split-run",
                trace_id="model-first-split-trace",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={"creation_list_loras", "comfyui_generate_image"},
                model_first_creation=True,
            )
        self.assertEqual(planner.await_count, 2)
        self.assertEqual(executed, ["creation_list_loras", "comfyui_generate_image"])
        self.assertTrue(result.replanned)

    async def test_discovery_does_not_force_generation_after_text_only_plan(self) -> None:
        """A model choosing no tools after discovery does not authorize a job."""
        planner = AsyncMock(
            side_effect=[
                _completion(
                    ToolCall("loras", "creation_list_loras", json.dumps({"workflow_id": "anima-2.9b-image"})),
                    ToolCall("workflow", "creation_check_workflow", json.dumps({"workflow_id": "anima-2.9b-image"})),
                ),
                _completion(),
                _completion(ToolCall("generate", "comfyui_generate_image", json.dumps({
                    "prompt": "an adult woman, elegant portrait",
                    "negative_prompt": "low quality, blurry",
                    "lora_choices": [],
                }))),
            ]
        )
        executed: list[str] = []

        async def execute(name: str, _arguments: object, context: object) -> ToolExecutionResult:
            executed.append(name)
            if name == "creation_list_loras":
                return ToolExecutionResult(name, "completed", {"loras": []}, int(getattr(context, "step_index", 1)))
            if name == "creation_check_workflow":
                return ToolExecutionResult(
                    name,
                    "completed",
                    {"ok": True, "workflows": [{"id": "anima-2.9b-image", "status": "ready"}]},
                    int(getattr(context, "step_index", 1)),
                )
            return ToolExecutionResult(
                name,
                "completed",
                {"job": {"id": "job_model_first_recovered", "status": "created"}},
                int(getattr(context, "step_index", 1)),
            )

        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.agent_loop_service.execute_tool_call", new=execute),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_model_first",
                source="desktop",
                user_message="来一张色色风格的女孩子",
                source_message_id=self.message_id,
                request_id="model-first-text-replan-recovery",
                trace_id="model-first-text-replan-recovery-trace",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={
                    "creation_list_loras",
                    "creation_check_workflow",
                    "comfyui_generate_image",
                },
                model_first_creation=True,
            )
        self.assertEqual(planner.await_count, 2)
        self.assertEqual(
            executed,
            ["creation_list_loras", "creation_check_workflow"],
        )
        self.assertFalse(any(item.tool_name == "comfyui_generate_image" for item in result.observations))
        self.assertNotIn("兜底提交", result.error)

    async def test_zero_tool_plan_does_not_force_creation(self) -> None:
        planner = AsyncMock(
            side_effect=[
                _completion(),
                _completion(ToolCall("generate", "comfyui_generate_image", json.dumps({
                    "prompt": "a young adult woman portrait",
                    "negative_prompt": "low quality",
                }))),
            ]
        )

        async def execute(name: str, _arguments: object, context: object) -> ToolExecutionResult:
            return ToolExecutionResult(name, "completed", {
                "job": {"id": "job_intent_correction", "status": "created"},
            }, int(getattr(context, "step_index", 1)))

        with (
            patch("app.agent_loop_service.call_chat_completion_result", planner),
            patch("app.agent_loop_service.execute_tool_call", new=execute),
        ):
            result = await run_agent_loop(
                conversation_id="desktop_agent_model_first",
                source="desktop",
                user_message="来个刚满18的",
                source_message_id=self.message_id,
                request_id="model-first-intent-correction",
                trace_id="model-first-intent-correction-trace",
                model_id="test-model",
                reasoning_level="low",
                allowed_tool_names={"comfyui_generate_image"},
                model_first_creation=True,
            )

        self.assertEqual(planner.await_count, 1)
        self.assertEqual(result.observations, ())


if __name__ == "__main__":
    unittest.main()
