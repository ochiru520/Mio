from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from . import db, maintenance_service
from .config import settings
from .llm import LLMConfigError, require_configured
from .tool_registry import ToolDefinition, ToolPermission, tool_registry


TERMINAL_STEP_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "needs_confirmation",
    "skipped",
}
PRIVATE_DATA_TOOLS = {
    "get_today_state",
    "search_memory",
    "get_diary",
    "add_diary_material",
    "set_daily_thirty",
    "set_daily_mood",
    "update_today_state",
    "remember_thread",
    "resolve_thread",
    "record_follow_up_result",
    "remember_memory",
    "edit_today_diary",
    "generate_today_diary",
    "update_profile",
}


@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    request_id: str
    trace_id: str
    conversation_id: str
    source_message_id: int
    user_message: str
    step_index: int
    tool_call_id: str = ""


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_name: str
    status: str
    result: dict[str, Any]
    step_id: int
    action_id: int = 0
    receipt_id: int = 0
    replayed: bool = False
    error: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "result": self.result,
            "step_id": self.step_id,
            "action_id": self.action_id,
            "receipt_id": self.receipt_id,
            "replayed": self.replayed,
            "error": self.error,
        }


def _json(value: object, max_chars: int = 12000) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)[:max_chars]


def _parsed_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _idempotency_key(run_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    payload = _json(arguments, 8000)
    digest = hashlib.sha256(f"{tool_name}\x1f{payload}".encode("utf-8")).hexdigest()
    return f"agent-tool:{str(run_id)[:80]}:{digest}"


def tool_availability(definition: ToolDefinition) -> tuple[bool, str]:
    for dependency in definition.dependencies:
        if dependency == "configured_model":
            try:
                require_configured()
            except LLMConfigError as exc:
                return False, str(exc)
        elif dependency == "web_search_enabled":
            if not settings.web_search_enabled:
                return False, "联网搜索已关闭。"
        else:
            return False, f"未知工具依赖：{dependency}"
    if definition.permission != ToolPermission.READ_ONLY:
        maintenance = maintenance_service.status()
        if maintenance.get("blocked"):
            return False, "Mio 正在维护数据，当前只允许读取。"
    return True, ""


def public_tool_catalog() -> list[dict[str, object]]:
    catalog: list[dict[str, object]] = []
    for definition in tool_registry.list():
        available, reason = tool_availability(definition)
        catalog.append({**definition.public_dict(), "available": available, "unavailable_reason": reason})
    return catalog


def _safe_row(row: object, *, max_text: int = 3000) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)  # type: ignore[arg-type]
    for key, value in list(result.items()):
        if isinstance(value, str) and len(value) > max_text:
            result[key] = value[:max_text] + "..."
    return result


async def _dispatch_read_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from . import self_snapshot_service

    if name == "get_self_state":
        scopes = tuple(str(item).strip() for item in arguments.get("scopes", []) if str(item).strip())
        return await asyncio.to_thread(self_snapshot_service.build_self_snapshot, scopes or None)
    if name == "list_capabilities":
        snapshot = await asyncio.to_thread(self_snapshot_service.build_self_snapshot, ("capabilities",))
        return {"capabilities": snapshot.get("capabilities", []), "generated_at": snapshot.get("generated_at", "")}
    if name == "get_active_view":
        return self_snapshot_service.get_active_view()
    if name == "get_service_health":
        return await asyncio.to_thread(self_snapshot_service.get_service_health)
    if name == "explain_last_route":
        snapshot = await asyncio.to_thread(self_snapshot_service.build_self_snapshot, ("last_route",))
        return {"last_route": snapshot.get("last_route"), "generated_at": snapshot.get("generated_at", "")}
    if name == "get_today_state":
        return {"date": db.today_string(), "state": _safe_row(db.get_daily_state())}
    if name == "search_web":
        from .web_search_service import lookup_web_query

        lookup = await lookup_web_query(str(arguments.get("query") or ""))
        return {
            "query": lookup.query,
            "engine": lookup.engine,
            "sources": [
                {"title": item.title, "url": item.url, "snippet": item.snippet}
                for item in lookup.sources
            ],
            "attempts": list(lookup.attempts),
            "error": lookup.error,
        }
    if name == "search_memory":
        from .memory_service import public_memory_item, retrieve_memory_items

        rows = await asyncio.to_thread(
            retrieve_memory_items,
            str(arguments.get("query") or ""),
            int(arguments.get("limit") or 12),
        )
        return {"items": [public_memory_item(row) for row in rows]}
    if name == "get_diary":
        target_date = str(arguments.get("date") or db.today_string())
        return {"date": target_date, "diary": _safe_row(db.get_diary(target_date), max_text=8000)}
    raise ValueError(f"只读工具尚未接入执行器：{name}")


async def _dispatch_write_tool(
    name: str,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> dict[str, Any]:
    from .companion_action_service import execute_companion_action_primitive

    action = {"type": name, **arguments}
    with maintenance_service.mutation_scope():
        result = await execute_companion_action_primitive(
            action,
            context.conversation_id,
            context.user_message,
            context.source_message_id,
        )
    return {"result": result}


def _verified_result(definition: ToolDefinition, result: dict[str, Any]) -> tuple[bool, str]:
    if definition.verifier == "non_error_result":
        if not isinstance(result, dict):
            return False, "工具结果不是对象。"
        if result.get("error"):
            return False, str(result.get("error"))[:500]
        return True, ""
    return False, f"未知结果验证器：{definition.verifier}"


def _existing_result(row: object, tool_name: str) -> ToolExecutionResult:
    item = dict(row)  # type: ignore[arg-type]
    return ToolExecutionResult(
        tool_name=tool_name,
        status=str(item.get("status") or "failed"),
        result=_parsed_object(item.get("result_json")),
        step_id=int(item.get("id") or 0),
        action_id=int(item.get("action_id") or 0),
        receipt_id=int(item.get("receipt_id") or 0),
        replayed=True,
        error=str(item.get("error") or ""),
    )


async def execute_tool_call(
    tool_name: str,
    arguments: object,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    definition = tool_registry.require(tool_name)
    try:
        validated = definition.validate_arguments(arguments)
    except ValidationError as exc:
        raise ValueError(f"工具参数不符合 Schema：{exc.errors(include_url=False)}") from exc
    except ValueError as exc:
        raise ValueError(f"工具参数不符合 Schema：{exc}") from exc

    key = _idempotency_key(context.run_id, definition.name, validated)
    created, step = db.claim_agent_run_step(
        context.run_id,
        context.step_index,
        "tool_call",
        key,
        tool_call_id=context.tool_call_id,
        tool_name=definition.name,
        permission=definition.permission.name.lower(),
        arguments_json=_json(validated, 8000),
    )
    if not created and str(step["status"] or "") in TERMINAL_STEP_STATUSES:
        return _existing_result(step, definition.name)
    step_id = int(step["id"])

    available, unavailable_reason = tool_availability(definition)
    if context.conversation_id.startswith("qq_group_") and definition.name in PRIVATE_DATA_TOOLS:
        available = False
        unavailable_reason = "群聊不能读取或写入私人日记、状态和记忆。"
    if not available:
        result = {"available": False, "reason": unavailable_reason}
        db.update_agent_run_step(
            step_id,
            "failed",
            result_json=_json(result),
            error=unavailable_reason,
        )
        return ToolExecutionResult(
            definition.name,
            "failed",
            result,
            step_id,
            error=unavailable_reason,
        )

    requires_confirmation = (
        definition.permission == ToolPermission.HIGH_RISK_WRITE
        and not definition.has_explicit_intent(context.user_message)
    )
    action_id = 0
    if definition.permission != ToolPermission.READ_ONLY:
        action_id = db.log_companion_action(
            context.conversation_id,
            definition.name,
            _json({"type": definition.name, **validated}, 4000),
            "needs_confirmation" if requires_confirmation else "running",
            source_message_id=context.source_message_id,
            requires_confirmation=requires_confirmation,
            request_id=context.request_id,
            trace_id=context.trace_id,
            agent_run_id=context.run_id,
            agent_step_id=step_id,
            idempotency_key=f"{key}:action",
        )
    receipt_id = db.start_tool_execution_receipt(
        context.conversation_id,
        definition.name,
        definition.permission.name.lower(),
        _json(validated, 4000),
        "needs_confirmation" if requires_confirmation else "running",
        request_id=context.request_id,
        trace_id=context.trace_id,
        agent_run_id=context.run_id,
        agent_step_id=step_id,
        action_id=action_id,
        idempotency_key=f"{key}:receipt",
    )
    db.update_agent_run_step(
        step_id,
        "running",
        action_id=action_id,
        receipt_id=receipt_id,
    )

    if requires_confirmation:
        result = {"task_id": action_id, "message": "等待用户确认后执行。"}
        db.finish_tool_execution_receipt(receipt_id, "needs_confirmation", f"task:{action_id}")
        db.update_agent_run_step(step_id, "needs_confirmation", result_json=_json(result))
        return ToolExecutionResult(
            definition.name,
            "needs_confirmation",
            result,
            step_id,
            action_id=action_id,
            receipt_id=receipt_id,
        )

    async def run() -> dict[str, Any]:
        if definition.permission == ToolPermission.READ_ONLY:
            return await _dispatch_read_tool(definition.handler or definition.name, validated)
        return await _dispatch_write_tool(definition.name, validated, context)

    try:
        result = await asyncio.wait_for(run(), timeout=max(0.1, float(definition.timeout_seconds)))
        verified, verify_error = _verified_result(definition, result)
        if not verified:
            raise RuntimeError(verify_error)
    except asyncio.CancelledError:
        if action_id:
            db.update_companion_action(action_id, "cancelled", "对话已取消。")
        db.finish_tool_execution_receipt(receipt_id, "cancelled", "对话已取消。")
        db.update_agent_run_step(step_id, "cancelled", error="对话已取消。")
        raise
    except asyncio.TimeoutError:
        error = f"工具执行超过 {definition.timeout_seconds:g} 秒。"
        if action_id:
            db.update_companion_action(action_id, "failed", error)
        db.finish_tool_execution_receipt(receipt_id, "timed_out", error)
        db.update_agent_run_step(step_id, "timed_out", error=error)
        return ToolExecutionResult(
            definition.name,
            "timed_out",
            {},
            step_id,
            action_id=action_id,
            receipt_id=receipt_id,
            error=error,
        )
    except Exception as exc:
        error = str(exc)[:500]
        if action_id:
            db.update_companion_action(action_id, "failed", error)
        db.finish_tool_execution_receipt(receipt_id, "failed", error)
        db.update_agent_run_step(step_id, "failed", error=error)
        return ToolExecutionResult(
            definition.name,
            "failed",
            {},
            step_id,
            action_id=action_id,
            receipt_id=receipt_id,
            error=error,
        )

    if action_id:
        db.update_companion_action(action_id, "executed", str(result.get("result") or "执行完成"))
    db.finish_tool_execution_receipt(receipt_id, "executed", _json(result, 1000))
    db.update_agent_run_step(step_id, "completed", result_json=_json(result))
    return ToolExecutionResult(
        definition.name,
        "completed",
        result,
        step_id,
        action_id=action_id,
        receipt_id=receipt_id,
    )


__all__ = [
    "ToolExecutionContext",
    "ToolExecutionResult",
    "execute_tool_call",
    "public_tool_catalog",
    "tool_availability",
]
