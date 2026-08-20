from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import db
from .agent_tool_service import (
    ToolExecutionContext,
    ToolExecutionResult,
    execute_tool_call,
    tool_availability,
)
from .companion_action_service import ALLOWED_ACTION_TYPES, enrich_companion_actions
from .llm import CompletionResult, ModelRequestError, ToolCall, call_chat_completion_result
from .tool_registry import ToolPermission, tool_registry
from .web_search_service import WebLookup


logger = logging.getLogger("mio.agent_loop")
MAX_STEPS = 10
MAX_MODEL_CALLS = 3
MAX_TOOL_CALLS = 6
RUN_DEADLINE_SECONDS = 90


@dataclass(frozen=True)
class PlannedToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {"call_id": self.call_id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class AgentLoopResult:
    run_id: str
    status: str
    plan_mode: str
    observations: tuple[ToolExecutionResult, ...]
    model_results: tuple[CompletionResult, ...]
    replanned: bool
    next_step_index: int
    error: str = ""

    @property
    def has_visible_receipts(self) -> bool:
        return bool(self.observations)

    def public_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "plan_mode": self.plan_mode,
            "replanned": self.replanned,
            "error": self.error,
            "receipts": [item.public_dict() for item in self.observations],
        }

    def model_context(self) -> str:
        if not self.observations:
            return ""
        payload = [
            {
                "tool": item.tool_name,
                "status": item.status,
                "result": item.result,
                "error": item.error,
                "receipt_id": item.receipt_id,
                "task_id": item.action_id if item.status == "needs_confirmation" else 0,
            }
            for item in self.observations
        ]
        return (
            "以下是本轮回复前已经真实执行并验证的工具结果。只能依据这些结果描述完成状态；"
            "needs_confirmation 表示只创建了待确认任务，不能说已经完成；失败或超时必须明确说明。\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:12000]
        )


def _run_id(request_id: str) -> str:
    digest = hashlib.sha256(str(request_id).encode("utf-8")).hexdigest()[:24]
    return f"run_{digest}"


def _json(value: object, max_chars: int = 12000) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)[:max_chars]


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("计划响应中没有 JSON 对象。")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("计划响应不是 JSON 对象。")
    return parsed


def _parse_arguments(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    parsed = json.loads(str(raw or "{}"))
    if not isinstance(parsed, dict):
        raise ValueError("工具参数不是 JSON 对象。")
    return parsed


def _calls_from_native(items: tuple[ToolCall, ...]) -> tuple[list[PlannedToolCall], list[str]]:
    calls: list[PlannedToolCall] = []
    errors: list[str] = []
    for index, item in enumerate(items[:MAX_TOOL_CALLS]):
        try:
            arguments = _parse_arguments(item.arguments_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{item.name}: {exc}")
            continue
        calls.append(
            PlannedToolCall(
                call_id=item.call_id or f"native_{index + 1}",
                name=item.name,
                arguments=arguments,
            )
        )
    return calls, errors


def _calls_from_json(raw: str) -> list[PlannedToolCall]:
    payload = _extract_json_object(raw)
    source = payload.get("tool_calls")
    if not isinstance(source, list):
        source = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    calls: list[PlannedToolCall] = []
    for index, item in enumerate(source[:MAX_TOOL_CALLS]):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("type") or item.get("action") or "").strip()
        arguments = item.get("arguments")
        if arguments is None:
            arguments = item.get("parameters")
        if arguments is None:
            arguments = {
                key: value
                for key, value in item.items()
                if key not in {"name", "type", "action", "call_id", "id"}
            }
        calls.append(
            PlannedToolCall(
                call_id=str(item.get("call_id") or item.get("id") or f"json_{index + 1}")[:120],
                name=name,
                arguments=_parse_arguments(arguments),
            )
        )
    return calls


def _planner_history(conversation_id: str, source_message_id: int) -> str:
    rows = db.get_recent_messages(limit=10, conversation_id=conversation_id)
    lines: list[str] = []
    for row in rows:
        role = "用户" if row["role"] == "user" else "Mio"
        content = " ".join(str(row["content"] or "").split()).strip()[:500]
        if content:
            marker = "（本轮）" if int(row["id"]) == int(source_message_id) else ""
            lines.append(f"[{row['created_at']}] {role}{marker}：{content}")
    return "\n".join(lines[-10:]) or "无"


def _planner_messages(
    conversation_id: str,
    user_message: str,
    source_message_id: int,
    *,
    observations: list[ToolExecutionResult] | None = None,
    web_precheck: WebLookup | None = None,
) -> list[dict[str, str]]:
    retry_context = ""
    if observations:
        retry_context = (
            "\n这是前一轮工具结果，只能因失败、超时或缺失证据重新规划；"
            "已成功的相同工具和参数不要重复：\n"
            + _json([item.public_dict() for item in observations], 8000)
        )
    precheck_context = ""
    if web_precheck is not None:
        precheck_context = (
            "\n回复生成前的联网预查结果如下。预查成功时不要重复相同查询；"
            "预查失败时，把错误当作需要修正参数或提出候选的观察，不要立刻把问题退回用户：\n"
            + _json(
                {
                    "query": web_precheck.query,
                    "sources": [
                        {"title": item.title, "url": item.url, "snippet": item.snippet}
                        for item in web_precheck.sources
                    ],
                    "attempts": list(web_precheck.attempts),
                    "error": web_precheck.error,
                },
                8000,
            )
        )
    system = """你是 Mio 的执行规划器，不负责向用户聊天。
你的任务是判断本轮是否需要工具，并在回复生成前调用最少且足够的工具。

规则：
- 只根据用户原话、最近对话和工具 Schema 选择工具，不执行自由文本、代码、命令、Git、删除、购买或未授权外发。
- 只读工具可以自动调用；低风险可撤销写入可自动调用；高风险动作即使被选择，也必须由本地权限层判断是否等待确认。
- 用户明确说出的事件、情绪、持续活动、稳定偏好、明确到期计划，可用状态、日记素材、记忆和待跟进工具记录。
- 不猜测情绪、事实、完成状态和时间。没有足够证据就不调用写工具。
- 用户询问“今日状态”“今日成长”“成长判断”“每日三十判断”时，必须先 get_today_state 读取当前状态，再调用 update_today_state 完成今日判断写入：根据最近对话中已有的事实给出判断结论（包括判定为完成/未完成/信息不足），不能只读查询后声称已记录或已判断；状态判断属于低风险可撤销写入，允许自动调用。
- 用户只要求“查看/看看/预览”已有的今天日记、素材或状态时，用 get_diary、get_today_state、search_memory 等只读工具直接读取并展示；不要调用 generate_today_diary。用户明确说“生成/写/创建今天的日记”时直接调用 generate_today_diary，即使句尾带“给我看看”也属于生成指令；后台自动收尾才要求“晚安、睡觉了、今天就这样、准备休息、结束今天”等结束语境。
- 信息不完整时先区分可查证歧义和必须由用户决定的歧义。只读、低风险且能由工具结果验证的请求，先生成少量候选并调用最少工具查证；证据收敛后继续，不要仅因简称、别名或缺少上级行政区而放弃。
- 多个候选仍无法区分，或参数会改变写入、外发、付费、删除等结果时，不替用户决定；不要调用有副作用的工具，由最终回复只追问一个最小必要问题。
- 涉及 Mio 自身能力、页面、模型、语音或服务状态时，先调用对应的自我状态工具，不凭提示词猜。
- 多步任务按依赖顺序调用，最多 6 个工具。失败后最多重新规划一次，不重复已成功副作用。
- 你现在只规划工具，不回答用户。没有工具需求时输出 NO_TOOL。"""
    user = f"""当前时间：{db.now_iso()}
当前逻辑日：{db.today_string()}
会话：{conversation_id}

最近对话：
{_planner_history(conversation_id, source_message_id)}

本轮用户原话：
{user_message}
{precheck_context}
{retry_context}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _json_compatibility_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    schemas = [
        definition.native_schema()["function"]
        for definition in tool_registry.list()
        if tool_availability(definition)[0]
    ]
    instruction = """当前供应商不支持或拒绝原生 function calling。改用 JSON 兼容层。
只输出一个 JSON 对象，不要 Markdown，不要解释：
{"plan":["简短步骤"],"tool_calls":[{"call_id":"call_1","name":"工具名","arguments":{}}]}
name 必须来自下方工具，arguments 必须严格符合对应 parameters；无工具时 tool_calls 为空。
工具 Schema：
""" + _json(schemas, 18000)
    return [*messages, {"role": "system", "content": instruction}]


def _native_tools() -> list[dict[str, object]]:
    return [
        definition.native_schema()
        for definition in tool_registry.list()
        if tool_availability(definition)[0]
    ]


async def _plan(
    messages: list[dict[str, str]],
    *,
    model_id: str,
    reasoning_level: str,
    request_id: str,
    allow_native: bool,
) -> tuple[list[PlannedToolCall], str, list[CompletionResult], list[str], int]:
    model_results: list[CompletionResult] = []
    errors: list[str] = []
    calls_made = 0
    if allow_native:
        calls_made += 1
        try:
            result = await call_chat_completion_result(
                messages,
                temperature=0.1,
                model_id=model_id,
                reasoning_level=reasoning_level,
                retry_attempts=1,
                request_id=f"{request_id}:native-plan",
                tools=_native_tools(),
                tool_choice="auto",
            )
        except ModelRequestError as exc:
            errors.append(str(exc)[:500])
        else:
            model_results.append(result)
            calls, parse_errors = _calls_from_native(result.tool_calls)
            errors.extend(parse_errors)
            return calls, "native", model_results, errors, calls_made

    calls_made += 1
    result = await call_chat_completion_result(
        _json_compatibility_messages(messages),
        temperature=0.1,
        model_id=model_id,
        reasoning_level=reasoning_level,
        retry_attempts=1,
        request_id=f"{request_id}:json-plan",
    )
    model_results.append(result)
    return _calls_from_json(result.content), "json", model_results, errors, calls_made


def _enrich_calls(
    calls: list[PlannedToolCall],
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> list[PlannedToolCall]:
    reads = [call for call in calls if call.name not in ALLOWED_ACTION_TYPES]
    writes = [{"type": call.name, **call.arguments} for call in calls if call.name in ALLOWED_ACTION_TYPES]
    enriched = enrich_companion_actions(writes, conversation_id, user_message, source_message_id)
    result = list(reads)
    existing = {(call.name, _json(call.arguments, 8000)) for call in result}
    for index, action in enumerate(enriched):
        name = str(action.get("type") or "")
        arguments = {key: value for key, value in action.items() if key != "type" and not key.startswith("_")}
        identity = (name, _json(arguments, 8000))
        if name and identity not in existing:
            result.append(PlannedToolCall(f"enriched_{index + 1}", name, arguments))
            existing.add(identity)
    return result[:MAX_TOOL_CALLS]


def _replan_needed(observations: list[ToolExecutionResult]) -> bool:
    return any(item.status in {"failed", "timed_out"} for item in observations)


def _persist_rejected_call(
    run_id: str,
    step_index: int,
    call: PlannedToolCall,
    error: str,
) -> ToolExecutionResult:
    digest = hashlib.sha256(
        f"{call.name}\x1f{_json(call.arguments, 8000)}\x1f{error}".encode("utf-8")
    ).hexdigest()
    _, row = db.claim_agent_run_step(
        run_id,
        step_index,
        "tool_call",
        f"agent-rejected:{run_id}:{digest}",
        tool_call_id=call.call_id,
        tool_name=call.name,
        arguments_json=_json(call.arguments, 8000),
    )
    step_id = int(row["id"])
    db.update_agent_run_step(step_id, "failed", error=error)
    return ToolExecutionResult(call.name, "failed", {}, step_id, error=error)


async def run_agent_loop(
    *,
    conversation_id: str,
    source: str,
    user_message: str,
    source_message_id: int,
    request_id: str,
    trace_id: str,
    model_id: str,
    reasoning_level: str,
    allow_native_tools: bool = True,
    web_precheck: WebLookup | None = None,
) -> AgentLoopResult:
    run_id = _run_id(request_id)
    deadline = datetime.fromisoformat(db.now_iso()) + timedelta(seconds=RUN_DEADLINE_SECONDS)
    existing = db.create_agent_run(
        run_id,
        request_id,
        trace_id=trace_id,
        conversation_id=conversation_id,
        source=source,
        source_message_id=source_message_id,
        model_id=model_id,
        reasoning_level=reasoning_level,
        max_steps=MAX_STEPS,
        max_model_calls=MAX_MODEL_CALLS,
        max_tool_calls=MAX_TOOL_CALLS,
        deadline_at=deadline.isoformat(timespec="seconds"),
    )
    if str(existing["status"] or "") == "completed":
        steps = db.list_agent_run_steps(run_id)
        observations = tuple(
            ToolExecutionResult(
                tool_name=str(row["tool_name"] or ""),
                status=str(row["status"] or ""),
                result=json.loads(str(row["result_json"] or "{}")),
                step_id=int(row["id"]),
                action_id=int(row["action_id"] or 0),
                receipt_id=int(row["receipt_id"] or 0),
                replayed=True,
                error=str(row["error"] or ""),
            )
            for row in steps
            if str(row["step_kind"]) == "tool_call"
        )
        return AgentLoopResult(
            run_id,
            "completed",
            "replayed",
            observations,
            (),
            bool(existing["replan_count"]),
            len(steps),
        )

    db.update_agent_run(run_id, "planning", error="")
    plan_key = f"agent-plan:{run_id}:initial"
    plan_created, plan_step = db.claim_agent_run_step(run_id, 0, "plan", plan_key)
    calls: list[PlannedToolCall]
    plan_mode = "replayed"
    model_results: list[CompletionResult] = []
    planner_errors: list[str] = []
    model_calls = int(existing["model_calls"] or 0)
    if not plan_created and str(plan_step["status"] or "") == "completed":
        saved = json.loads(str(plan_step["result_json"] or "{}"))
        calls = [
            PlannedToolCall(str(item.get("call_id") or ""), str(item.get("name") or ""), dict(item.get("arguments") or {}))
            for item in saved.get("tool_calls", [])
            if isinstance(item, dict)
        ]
        plan_mode = str(saved.get("mode") or "replayed")
    else:
        db.update_agent_run_step(int(plan_step["id"]), "running")
        try:
            calls, plan_mode, planned_results, planner_errors, calls_made = await _plan(
                _planner_messages(
                    conversation_id,
                    user_message,
                    source_message_id,
                    web_precheck=web_precheck,
                ),
                model_id=model_id,
                reasoning_level=reasoning_level,
                request_id=request_id,
                allow_native=allow_native_tools,
            )
            model_results.extend(planned_results)
            model_calls += calls_made
        except asyncio.CancelledError:
            db.update_agent_run_step(int(plan_step["id"]), "cancelled", error="对话已取消。")
            db.update_agent_run(run_id, "cancelled", error="对话已取消。", model_calls=model_calls)
            raise
        except Exception as exc:
            error = str(exc)[:500]
            db.update_agent_run_step(int(plan_step["id"]), "failed", error=error)
            db.update_agent_run(run_id, "awaiting_response", error=error, model_calls=model_calls)
            return AgentLoopResult(run_id, "planning_failed", "failed", (), (), False, 1, error)
        calls = _enrich_calls(calls, conversation_id, user_message, source_message_id)
        plan_payload = {
            "mode": plan_mode,
            "tool_calls": [call.public_dict() for call in calls],
            "errors": planner_errors,
        }
        db.update_agent_run_step(int(plan_step["id"]), "completed", result_json=_json(plan_payload))
        db.update_agent_run(run_id, "executing", plan_json=_json(plan_payload), model_calls=model_calls)

    observations: list[ToolExecutionResult] = []
    step_index = 1
    for call in calls[:MAX_TOOL_CALLS]:
        if step_index >= MAX_STEPS - 1:
            break
        try:
            result = await execute_tool_call(
                call.name,
                call.arguments,
                ToolExecutionContext(
                    run_id=run_id,
                    request_id=request_id,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    source_message_id=source_message_id,
                    user_message=user_message,
                    step_index=step_index,
                    tool_call_id=call.call_id,
                ),
            )
        except asyncio.CancelledError:
            db.update_agent_run(run_id, "cancelled", error="对话已取消。", tool_calls=len(observations))
            raise
        except Exception as exc:
            result = _persist_rejected_call(run_id, step_index, call, str(exc)[:500])
        observations.append(result)
        step_index += 1

    replanned = False
    if (
        _replan_needed(observations)
        and model_calls < MAX_MODEL_CALLS - 1
        and len(observations) < MAX_TOOL_CALLS
        and step_index < MAX_STEPS - 2
    ):
        replanned = True
        replan_key = f"agent-plan:{run_id}:replan"
        _, replan_step = db.claim_agent_run_step(run_id, step_index, "replan", replan_key)
        db.update_agent_run_step(int(replan_step["id"]), "running")
        db.update_agent_run(run_id, "replanning", replan_count=1)
        try:
            extra_calls, replan_mode, replan_results, replan_errors, calls_made = await _plan(
                _planner_messages(
                    conversation_id,
                    user_message,
                    source_message_id,
                    observations=observations,
                    web_precheck=web_precheck,
                ),
                model_id=model_id,
                reasoning_level=reasoning_level,
                request_id=f"{request_id}:replan",
                allow_native=allow_native_tools and plan_mode == "native",
            )
            model_results.extend(replan_results)
            model_calls += calls_made
            extra_calls = _enrich_calls(extra_calls, conversation_id, user_message, source_message_id)
            replan_payload = {
                "mode": replan_mode,
                "tool_calls": [call.public_dict() for call in extra_calls],
                "errors": replan_errors,
            }
            db.update_agent_run_step(
                int(replan_step["id"]), "completed", result_json=_json(replan_payload)
            )
            step_index += 1
            remaining = MAX_TOOL_CALLS - len(observations)
            for call in extra_calls[:remaining]:
                if step_index >= MAX_STEPS - 1:
                    break
                try:
                    result = await execute_tool_call(
                        call.name,
                        call.arguments,
                        ToolExecutionContext(
                            run_id=run_id,
                            request_id=request_id,
                            trace_id=trace_id,
                            conversation_id=conversation_id,
                            source_message_id=source_message_id,
                            user_message=user_message,
                            step_index=step_index,
                            tool_call_id=call.call_id,
                        ),
                    )
                except Exception as exc:
                    result = _persist_rejected_call(run_id, step_index, call, str(exc)[:500])
                observations.append(result)
                step_index += 1
        except asyncio.CancelledError:
            db.update_agent_run_step(int(replan_step["id"]), "cancelled", error="对话已取消。")
            db.update_agent_run(run_id, "cancelled", error="对话已取消。")
            raise
        except Exception as exc:
            db.update_agent_run_step(int(replan_step["id"]), "failed", error=str(exc)[:500])
            step_index += 1

    observation_payload = [item.public_dict() for item in observations]
    db.update_agent_run(
        run_id,
        "awaiting_response",
        observation_json=_json(observation_payload, 20000),
        model_calls=model_calls,
        tool_calls=len(observations),
        replan_count=1 if replanned else 0,
        error="; ".join(planner_errors)[:1000],
    )
    return AgentLoopResult(
        run_id,
        "awaiting_response",
        plan_mode,
        tuple(observations),
        tuple(model_results),
        replanned,
        step_index,
        "; ".join(planner_errors)[:500],
    )


def begin_final_response(agent: AgentLoopResult) -> int:
    key = f"agent-final:{agent.run_id}"
    _, step = db.claim_agent_run_step(
        agent.run_id,
        agent.next_step_index,
        "final_response",
        key,
    )
    db.update_agent_run_step(int(step["id"]), "running")
    db.update_agent_run(agent.run_id, "responding")
    return int(step["id"])


def finish_final_response(
    agent: AgentLoopResult,
    step_id: int,
    *,
    reply: str = "",
    error: str = "",
    model_calls: int | None = None,
) -> None:
    run = db.get_agent_run(agent.run_id)
    final_model_calls = (
        max(0, int(model_calls))
        if model_calls is not None
        else max(0, int(run["model_calls"] or 0) if run is not None else 0) + 1
    )
    if error:
        db.update_agent_run_step(step_id, "failed", error=error)
        db.update_agent_run(agent.run_id, "failed", error=error, model_calls=final_model_calls)
        return
    summary = agent.public_dict()
    summary["reply_preview"] = " ".join(str(reply or "").split())[:300]
    db.update_agent_run_step(
        step_id,
        "completed",
        result_json=_json({"reply_preview": summary["reply_preview"]}),
    )
    db.update_agent_run(
        agent.run_id,
        "completed",
        summary_json=_json(summary),
        model_calls=final_model_calls,
    )


def defer_final_response(agent: AgentLoopResult, step_id: int, *, reply: str) -> None:
    run = db.get_agent_run(agent.run_id)
    model_calls = max(0, int(run["model_calls"] or 0) if run is not None else 0) + 1
    summary = agent.public_dict()
    summary["reply_preview"] = " ".join(str(reply or "").split())[:300]
    db.update_agent_run_step(
        step_id,
        "awaiting_commit",
        result_json=_json({"reply_preview": summary["reply_preview"]}),
    )
    db.update_agent_run(
        agent.run_id,
        "awaiting_commit",
        summary_json=_json(summary),
        model_calls=model_calls,
    )


def commit_deferred_final_response(run_id: str, source_message_id: int) -> None:
    run = db.get_agent_run(run_id)
    if run is None:
        raise ValueError("没有找到待提交的 Agent run。")
    if str(run["status"] or "") == "completed":
        return
    if str(run["status"] or "") != "awaiting_commit":
        raise ValueError(f"Agent run 当前不能提交：{run['status']}")
    final_steps = [
        row
        for row in db.list_agent_run_steps(run_id)
        if str(row["step_kind"] or "") == "final_response"
    ]
    if not final_steps:
        raise ValueError("Agent run 缺少最终回复步骤。")
    final_step = final_steps[-1]
    db.relink_agent_run_source_message(run_id, source_message_id)
    db.update_agent_run_step(
        int(final_step["id"]),
        "completed",
        result_json=str(final_step["result_json"] or "{}"),
    )
    db.update_agent_run(
        run_id,
        "completed",
        summary_json=str(run["summary_json"] or "{}"),
        model_calls=int(run["model_calls"] or 0),
    )


def cancel_final_response(agent: AgentLoopResult, step_id: int, *, error: str) -> None:
    reason = str(error or "对话已取消。")[:500]
    db.update_agent_run_step(step_id, "cancelled", error=reason)
    db.update_agent_run(agent.run_id, "cancelled", error=reason)


def abandon_deferred_final_response(run_id: str, *, error: str) -> None:
    run = db.get_agent_run(run_id)
    if run is None or str(run["status"] or "") in {"completed", "cancelled"}:
        return
    reason = str(error or "暂存回复未提交。")[:500]
    for row in db.list_agent_run_steps(run_id):
        if str(row["step_kind"] or "") == "final_response" and str(row["status"] or "") not in {
            "completed",
            "failed",
            "cancelled",
        }:
            db.update_agent_run_step(int(row["id"]), "cancelled", error=reason)
    db.update_agent_run(run_id, "cancelled", error=reason)


__all__ = [
    "AgentLoopResult",
    "PlannedToolCall",
    "abandon_deferred_final_response",
    "begin_final_response",
    "cancel_final_response",
    "commit_deferred_final_response",
    "defer_final_response",
    "finish_final_response",
    "run_agent_loop",
]
