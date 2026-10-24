"""Synthetic context-tool requests: no live profile, model, or user database."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import db, context_service, agent_context_service, context_tools
from app import companion_action_service as actions
from app.agent_tool_service import ToolExecutionContext, execute_tool_call
from app.tool_discovery import CORE, exposed
from app.tool_registry import tool_registry


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setitem(db.settings.__dict__, "db_path", tmp_path / "context-tools.db")
    db.init_db()
    return tmp_path


def bound(conversation="desktop_synthetic", source_id=1, **kwargs):
    return SimpleNamespace(conversation_id=conversation, source_message_id=source_id,
                           source=kwargs.get("source", "desktop"), request_id="synthetic-request", run_id="synthetic-run")


def test_tool_schema_and_discovery_have_no_arbitrary_conversation_scope():
    definition = tool_registry.require(context_tools.NAME)
    assert definition.validate_arguments({}) == {}
    with pytest.raises(ValueError):
        definition.validate_arguments({"conversation_id": "someone_else"})
    assert not definition.explicit_intent_pattern
    assert context_tools.NAME in CORE
    assert context_tools.NAME in exposed(CORE | {f"synthetic_{i}" for i in range(20)}, [])


def test_model_selected_companion_action_reaches_real_async_entry(isolated, monkeypatch):
    conversation = "desktop_synthetic"
    source_id = db.save_message("user", "把前面聊过的整理成简短工作记忆吧", conversation_id=conversation)
    backend = AsyncMock(return_value={"status": "compressed", "before_tokens": 28000, "after_tokens": 2000,
                                    "summary": "This private summary must not enter the tool receipt"})
    monkeypatch.setattr(context_service, "force_compress_context", backend, raising=False)
    model = AsyncMock(return_value=json.dumps({"assessment": {}, "actions": [{"type": context_tools.NAME, "confidence": .99}]}))
    monkeypatch.setattr(actions, "call_chat_completion", model)
    async def run():
        decision = await actions.plan_companion_actions(conversation, "把前面聊过的整理成简短工作记忆吧")
        return await actions.execute_companion_actions(decision.actions, conversation,
            "把前面聊过的整理成简短工作记忆吧", source_id)
    result = asyncio.run(run())
    backend.assert_awaited_once_with(conversation, model_id=None)
    assert context_tools.NAME in model.await_args.args[0][0]["content"]
    assert result[0]["status"] == "compressed"
    payload = json.loads(result[0]["result"])
    assert payload["before_tokens"] == 28000 and payload["after_tokens"] == 2000
    assert "summary" not in payload
    assert db.get_message_by_id(source_id)["content"] == "把前面聊过的整理成简短工作记忆吧"


@pytest.mark.parametrize("status", ["noop", "failed"])
def test_companion_status_never_fakes_compression(isolated, monkeypatch, status):
    backend = AsyncMock(return_value={"status": status})
    monkeypatch.setattr(context_service, "force_compress_context", backend, raising=False)
    results = asyncio.run(actions.execute_companion_actions(
        [{"type": context_tools.NAME, "confidence": .99}], "desktop_synthetic", "维护当前工作记忆", 1))
    assert results[0]["status"] == status
    assert json.loads(results[0]["result"])["status"] == status
    assert db.list_tool_execution_receipts(limit=1)[0]["status"] == status


def test_same_message_is_durably_idempotent_and_new_message_can_request_again(isolated, monkeypatch):
    backend = AsyncMock(return_value={"status": "compressed"})
    monkeypatch.setattr(context_service, "force_compress_context", backend, raising=False)
    first = asyncio.run(context_tools.compress_for_context(bound()))
    repeated = asyncio.run(context_tools.compress_for_context(bound()))
    assert first["status"] == "compressed" and repeated["replayed"]
    backend.assert_awaited_once()
    asyncio.run(context_tools.compress_for_context(bound(source_id=2)))
    assert backend.await_count == 2


def test_agent_queues_without_reentering_chat_compression(isolated, monkeypatch):
    queue = Mock(return_value={"status": "queued"})
    backend = AsyncMock()
    monkeypatch.setattr(agent_context_service, "request_force_compression", queue, raising=False)
    monkeypatch.setattr(context_service, "force_compress_context", backend, raising=False)
    result = asyncio.run(context_tools.compress_for_context(bound("desktop_agent_synthetic")))
    assert result["status"] == "queued" and "尚未完成" in result["message"]
    queue.assert_called_once_with("desktop_agent_synthetic")
    backend.assert_not_awaited()


@pytest.mark.parametrize("conversation,source", [("qq_group_42", "qq"), ("desktop_synthetic", "qq_group"), ("", "desktop")])
def test_group_and_missing_scope_rejected_before_backend(isolated, monkeypatch, conversation, source):
    backend = AsyncMock()
    queue = Mock()
    monkeypatch.setattr(context_service, "force_compress_context", backend, raising=False)
    monkeypatch.setattr(agent_context_service, "request_force_compression", queue, raising=False)
    with pytest.raises(ValueError):
        asyncio.run(context_tools.compress_for_context(bound(conversation, source=source)))
    backend.assert_not_awaited()
    queue.assert_not_called()


def test_companion_cannot_supply_another_conversation(isolated, monkeypatch):
    backend = AsyncMock()
    monkeypatch.setattr(context_service, "force_compress_context", backend, raising=False)
    result = asyncio.run(actions.execute_companion_actions(
        [{"type": context_tools.NAME, "confidence": .99, "conversation_id": "someone_else"}],
        "desktop_synthetic", "压缩当前上下文", 1))
    assert result[0]["status"] == "failed"
    backend.assert_not_awaited()


def test_backend_exception_and_invalid_queue_success_cannot_claim_compressed(isolated, monkeypatch):
    backend = AsyncMock(side_effect=RuntimeError("synthetic private model error"))
    monkeypatch.setattr(context_service, "force_compress_context", backend, raising=False)
    result = asyncio.run(context_tools.compress_for_context(bound()))
    assert result["status"] == "failed" and "synthetic private" not in json.dumps(result)
    monkeypatch.setattr(agent_context_service, "request_force_compression", Mock(return_value={"status": "compressed"}), raising=False)
    result = asyncio.run(context_tools.compress_for_context(bound("desktop_agent_synthetic")))
    assert result["status"] == "failed"


def test_cancelled_request_propagates_and_is_not_restarted(isolated, monkeypatch):
    started = asyncio.Event()
    async def backend(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    stub = AsyncMock(side_effect=backend)
    monkeypatch.setattr(context_service, "force_compress_context", stub, raising=False)
    async def run():
        task = asyncio.create_task(context_tools.compress_for_context(bound()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await context_tools.compress_for_context(bound())
    result = asyncio.run(run())
    assert result["status"] == "failed"
    stub.assert_awaited_once()


def create_execution(conversation="desktop_agent_synthetic"):
    source_id = db.save_message("user", "压缩当前上下文", conversation_id=conversation)
    db.create_agent_run("synthetic-run", "synthetic-request", trace_id="synthetic-trace",
        conversation_id=conversation, source="desktop", source_message_id=source_id, model_id="synthetic-model")
    return ToolExecutionContext(run_id="synthetic-run", request_id="synthetic-request", trace_id="synthetic-trace",
        conversation_id=conversation, source_message_id=source_id, user_message="压缩当前上下文", step_index=1)


def test_agent_queued_receipt_and_step_are_truthful_and_not_duplicated(isolated, monkeypatch):
    from app import execution_policy_service as policy
    context = create_execution()
    monkeypatch.setattr(policy, "decision", lambda *args: ("allow", ""))
    queue = Mock(return_value={"status": "queued"})
    monkeypatch.setattr(agent_context_service, "request_force_compression", queue, raising=False)
    async def run():
        first = await execute_tool_call(context_tools.NAME, {}, context)
        repeated = await execute_tool_call(context_tools.NAME, {}, replace(context, step_index=2))
        return first, repeated
    first, repeated = asyncio.run(run())
    assert first.status == first.result["status"] == "queued"
    assert repeated.replayed and repeated.step_id == first.step_id
    assert db.get_agent_run_step(first.step_id)["status"] == "queued"
    assert db.list_tool_execution_receipts(limit=1)[0]["status"] == "queued"
    assert '"status": "queued"' in actions.build_context_maintenance_feedback(context.conversation_id)
    queue.assert_called_once()


def test_executor_failed_result_is_failed_receipt(isolated, monkeypatch):
    from app import execution_policy_service as policy
    context = create_execution("desktop_synthetic")
    monkeypatch.setattr(policy, "decision", lambda *args: ("allow", ""))
    monkeypatch.setattr(context_service, "force_compress_context", AsyncMock(return_value={"status": "failed"}), raising=False)
    result = asyncio.run(execute_tool_call(context_tools.NAME, {}, context))
    assert result.status == "failed"
    assert db.get_agent_run_step(result.step_id)["status"] == "failed"
    assert db.list_tool_execution_receipts(limit=1)[0]["status"] == "failed"


@pytest.mark.parametrize("repeat", [False, True])
def test_real_agent_loop_consumes_model_selected_queue_on_next_tick_once(isolated, monkeypatch, repeat):
    from app import llm, agent_loop_service as loop, execution_policy_service as policy
    from app.config import settings
    monkeypatch.setitem(settings.__dict__, "chat_context_max_tokens", 272000)
    monkeypatch.setattr(policy, "decision", lambda *args: ("allow", ""))
    conversation = "desktop_agent_synthetic_loop"
    old = "早期约束：禁止发布，先核实工具回执。" + "已读旧资料。" * 100
    db.save_message("user", old, conversation_id=conversation)
    source_id = db.save_message("user", "请把此前资料整理成精简工作记忆", conversation_id=conversation)
    def completion(*calls, content=""):
        return llm.CompletionResult(content=content, model="synthetic-model", prompt_tokens=10,
            cached_prompt_tokens=0, completion_tokens=5, reasoning_tokens=0, cost_yuan=.001,
            cost_source="synthetic", tool_calls=tuple(calls))
    planner = AsyncMock(side_effect=[completion(llm.ToolCall("compress-one", context_tools.NAME, "{}")),
        completion(llm.ToolCall("compress-repeat", context_tools.NAME, "{}")) if repeat else completion()])
    summary = AsyncMock(return_value=completion(content="早期要求禁止发布，工具完成须核实回执。"))
    monkeypatch.setattr(loop, "call_chat_completion_result", planner)
    monkeypatch.setattr(llm, "call_chat_completion_result", summary)
    result = asyncio.run(loop.run_agent_loop(conversation_id=conversation, source="desktop",
        user_message="请把此前资料整理成精简工作记忆", source_message_id=source_id,
        request_id="synthetic-loop-request", trace_id="synthetic-loop-trace", model_id="synthetic-model",
        reasoning_level="low", allowed_tool_names={context_tools.NAME},
        context_snapshot=[{"role": "user", "content": old},
                          {"role": "user", "content": "请把此前资料整理成精简工作记忆"}]))
    assert planner.await_count == 2
    assert len(result.observations) == 1
    assert result.observations[0].status == result.observations[0].result["status"] == "queued"
    assert db.get_agent_run_step(result.observations[0].step_id)["status"] == "queued"
    assert db.list_tool_execution_receipts(limit=1)[0]["status"] == "queued"
    assert summary.await_count == 1
    usage = agent_context_service.usage_for_conversation(conversation)
    assert usage["force_compression_status"] == "compressed" and usage["compression_count"] == 1
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_context_compression_requests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM context_tool_requests").fetchone()[0] == 1
    assert "已读旧资料" in str(summary.await_args.args[0])
    assert "已读旧资料" not in str(planner.await_args_list[1].args[0])
    assert db.get_message_by_id(source_id - 1)["content"] == old


@pytest.mark.parametrize("status", ["compressed", "noop", "failed", "queued"])
def test_companion_receipt_feedback_includes_actual_state_and_scoped_measurements(isolated, monkeypatch, status):
    conversation = "desktop_agent_feedback" if status == "queued" else "desktop_feedback"
    value = {"status": status, "before": {"used_tokens": 9000, "max_tokens": 272000},
             "after": {"used_tokens": 1000, "max_tokens": 272000}}
    if status == "queued":
        monkeypatch.setattr(agent_context_service, "request_force_compression", Mock(return_value=value))
    else:
        monkeypatch.setattr(context_service, "force_compress_context", AsyncMock(return_value=value))
    asyncio.run(actions.execute_companion_actions([{ "type": context_tools.NAME, "confidence": .99}],
        conversation, "精简当前工作记忆", 1))
    feedback = actions.build_context_maintenance_feedback(conversation)
    assert f'"status": "{status}"' in feedback
    assert '"used_tokens": 9000' in feedback and '"used_tokens": 1000' in feedback
    assert actions.build_context_maintenance_feedback("desktop_someone_else") == ""
    assert actions.build_context_maintenance_feedback("qq_group_synthetic") == ""


def test_companion_duplicate_compression_uses_one_expensive_action(isolated, monkeypatch):
    backend = AsyncMock(return_value={"status": "compressed"})
    monkeypatch.setattr(context_service, "force_compress_context", backend)
    result = asyncio.run(actions.execute_companion_actions(
        [{"type": context_tools.NAME, "confidence": .99}] * 2,
        "desktop_synthetic", "精简当前工作记忆", 1))
    assert [item["status"] for item in result] == ["compressed", "skipped"]
    backend.assert_awaited_once()


@pytest.mark.parametrize("status", ["compressed", "noop", "failed", "queued"])
def test_ordinary_chat_model_sees_actual_planned_maintenance_receipt(isolated, monkeypatch, status):
    from app import chat_service as chat, companion_service, llm
    conversation = "desktop_agent_feedback" if status == "queued" else "desktop_feedback"
    source_id = db.save_message("user", "请整理精简当前工作记忆", conversation_id=conversation)
    value = {"status": status, "before": {"used_tokens": 9000, "max_tokens": 272000},
             "after": {"used_tokens": 1000, "max_tokens": 272000}}
    if status == "queued":
        monkeypatch.setattr(agent_context_service, "request_force_compression", Mock(return_value=value))
    else:
        monkeypatch.setattr(context_service, "force_compress_context", AsyncMock(return_value=value))
    planner = AsyncMock(return_value=json.dumps({"assessment": {},
        "actions": [{"type": context_tools.NAME, "confidence": .99}]}))
    monkeypatch.setattr(actions, "call_chat_completion", planner)
    completion = llm.CompletionResult(content="我会依据实际回执说明。", model="synthetic-model", prompt_tokens=10,
        cached_prompt_tokens=0, completion_tokens=5, reasoning_tokens=0, cost_yuan=.001, cost_source="synthetic")
    reply = AsyncMock(return_value=(completion, "off", ""))
    monkeypatch.setattr(chat, "resolve_model_id", lambda *_: "synthetic")
    monkeypatch.setattr(chat, "require_configured", lambda *_: None)
    monkeypatch.setattr(chat, "get_model_profile", lambda *_: SimpleNamespace(model="synthetic-model"))
    monkeypatch.setattr(chat, "normalize_model_reasoning", lambda *_: "off")
    monkeypatch.setattr(chat, "load_manuals", lambda **_: [])
    monkeypatch.setattr(chat, "build_system_prompt", lambda *_, **__: "固定角色规则")
    monkeypatch.setattr(chat, "build_chat_context_snapshot", lambda _cid, rows:
        SimpleNamespace(raw_messages=list(rows), system_context=""))
    monkeypatch.setattr(chat, "perform_web_lookup", AsyncMock(return_value=None))
    monkeypatch.setattr(chat.system_audio_service, "chat_context", lambda *_: "")
    monkeypatch.setattr(chat, "_self_snapshot_context_for_message", AsyncMock(return_value=""))
    monkeypatch.setattr(chat, "_complete_chat_reply_with_single_fallback", reply)
    monkeypatch.setattr(companion_service, "set_pet_activity", lambda *_, **__: None)
    async def run():
        decision = await actions.plan_companion_actions(conversation, "请整理精简当前工作记忆")
        await actions.execute_companion_actions(decision.actions, conversation, "请整理精简当前工作记忆", source_id)
        return await chat._chat_with_ai_unlocked("刚才维护的实际结果怎么样", conversation_id=conversation,
            source="desktop", model_id="synthetic", agent_tools_enabled=False, persist=False)
    asyncio.run(run())
    planner.assert_awaited_once()
    reply.assert_awaited_once()
    payload = "\n".join(str(item.get("content", "")) for item in reply.await_args.args[0])
    assert f'"status": "{status}"' in payload.replace(chr(92) + chr(34), chr(34))
    assert "9000" in payload and "1000" in payload
    assert "只有 compressed 表示已压缩" in payload
