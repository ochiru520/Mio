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
    "creation_inspect_workflow", "creation_read_workflow_source", "creation_remember_workflow",
    "agent_search_files", "creation_find_local_workflows", "creation_import_local_workflow", "creation_read_local_workflow",
    "agent_list_files", "agent_read_document", "agent_write_document", "birefnet_remove_background", "agent_task_update",
    "get_today_state",
    "search_memory",
    "get_diary",
    "creation_list_presets",
    "creation_list_loras",
    "creation_list_assets",
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
    source: str = "desktop"
    tool_call_id: str = ""
    task_id: str = ""


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
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


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
        elif dependency == "comfyui_configured":
            from . import creation_service

            root = settings.comfyui_root.expanduser().resolve()
            if not root.is_dir() or not (root / "main.py").is_file():
                return False, "没有找到位于 D:\\AI 内的 ComfyUI。"
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
    if name == 'creation_read_workflow_reference':
        from .workflow_research import read_reference
        return await read_reference(arguments['url'])
    if name in {'creation_inspect_workflow', 'creation_read_workflow_source'}:
        from . import workflow_research
        callback = workflow_research.inspect if name == 'creation_inspect_workflow' else workflow_research.read_source
        return await callback(**arguments)
    if name == 'creation_read_local_workflow':
        from .local_discovery import read_workflow
        return await asyncio.to_thread(read_workflow, **arguments)
    if name in {"agent_search_files", "creation_find_local_workflows"}:
        from . import local_discovery
        callback = local_discovery.search_files if name == "agent_search_files" else local_discovery.discover_workflows
        return await asyncio.to_thread(callback, **arguments)
    from . import self_snapshot_service
    if name == "birefnet_status":
        from .birefnet_service import health
        return await health()
    if name in {"agent_list_files", "agent_read_document"}:
        from . import agent_file_service
        if name == "agent_list_files":
            return agent_file_service.list_files(**arguments)
        return agent_file_service.read_document(**arguments)

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
    if name == "creation_list_presets":
        from .creation_service import list_presets

        return {"presets": list_presets(str(arguments.get("kind") or ""))}
    if name == "creation_list_loras":
        from .creation_lora_catalog import list_image_loras

        return list_image_loras(str(arguments.get("workflow_id") or "anima-2.9b-image"))
    if name == "creation_list_assets":
        from .creation_service import list_assets

        return {"assets": list_assets(int(arguments.get("limit") or 100))}
    if name == "creation_check_workflow":
        from .creation_service import comfyui_preflight

        report = await comfyui_preflight(str(arguments.get("workflow_id") or ""))
        # The planner only needs a decision-ready summary.  Do not put local
        # absolute paths or the full ComfyUI system report into persisted tool
        # receipts or model context.
        workflows = []
        for item in report.get("workflows") or []:
            if not isinstance(item, dict):
                continue
            workflows.append({
                "id": item.get("id"),
                "label": item.get("label"),
                "requires_reference": item.get("requires_reference"),
                "filename": item.get("filename"),
                "media_type": item.get("media_type"),
                "status": item.get("status"),
                "errors": list(item.get("errors") or [])[:5],
                "missing_nodes": list((item.get("nodes") or {}).get("missing") or [])[:20],
                "missing_models": [
                    str(model.get("requested") or model.get("selected") or "")
                    for model in item.get("models") or []
                    if isinstance(model, dict) and not model.get("available")
                ][:20],
            })
        from .creation_custom import default_ids
        return {"ok": bool(report.get("ok")), "checked": bool(report.get("checked")), "workflows": workflows, "defaults": default_ids()}
    if name == "creation_get_job":
        from .creation_service import get_job

        job = get_job(str(arguments.get("job_id") or ""))
        if job is None:
            raise ValueError("找不到创作任务。")
        return {"job": job}
    if name == "creation_get_output":
        from .creation_service import get_job

        job = get_job(str(arguments.get("job_id") or ""))
        if job is None:
            raise ValueError("找不到创作任务。")
        index = int(arguments.get("index") or 0)
        outputs = job.get("outputs") if isinstance(job, dict) else []
        if not isinstance(outputs, list) or index < 0 or index >= len(outputs):
            raise ValueError("找不到这个创作输出。")
        return {"job_id": job["id"], "output": outputs[index]}
    raise ValueError(f"只读工具尚未接入执行器：{name}")


async def _dispatch_write_tool(
    name: str,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> dict[str, Any]:
    if name == 'creation_remember_workflow':
        from .workflow_research import remember
        return await remember(**arguments)
    if name == "creation_import_local_workflow":
        from .local_discovery import import_local_workflow
        return await import_local_workflow(**arguments)
    if name == "handoff_to_agent":
        from .agent_handoff_service import create_handoff
        return create_handoff(arguments, context)
    if name == "birefnet_remove_background":
        from .birefnet_service import create_cutout
        return create_cutout(arguments, context)
    if name == "agent_write_document":
        from . import agent_file_service
        return agent_file_service.write_document(**arguments, task_id=context.task_id)
    if name == "agent_task_update":
        from . import agent_task_service
        task = agent_task_service.for_run(context.run_id)
        if task is None:
            raise ValueError("当前没有可更新的任务。")
        snapshot = {key: value for key, value in arguments.items() if value or key in {"decision", "blocker"}}
        decision_status = {"continue": "running", "ask_user": "waiting_user", "completed": "completed", "failed": "failed"}
        updated = agent_task_service.update(task["id"], snapshot=snapshot,
            status=decision_status[arguments.get("decision", "continue")])
        return {"task_id": task["id"], "snapshot": updated["snapshot"], "status": updated["status"]}
    if name in {"comfyui_generate_image", "comfyui_generate_video", "remote_generate_image"}:
        from .creation_models import CreationJobRequest
        from .creation_service import create_job

        media_type = "video" if name == "comfyui_generate_video" else "image"
        backend = "remote_api" if name == "remote_generate_image" else "comfyui"
        parent_job_id = str(arguments.get("parent_job_id") or "")
        spec = {
            "media_type": media_type,
            "backend": backend,
            "workflow_id": arguments.get("workflow_id") or "",
            "prompt": arguments.get("prompt") or "",
            "negative_prompt": arguments.get("negative_prompt") or "",
            "character_preset_id": arguments.get("character_preset_id") or "",
            "style_preset_id": arguments.get("style_preset_id") or "",
            "project_preset_id": arguments.get("project_preset_id") or "",
            "reference_asset_ids": [arguments["reference_asset_id"]] if arguments.get("reference_asset_id") else [],
            "width": arguments.get("width"),
            "height": arguments.get("height"),
            "steps": arguments.get("steps"),
            "cfg": arguments.get("cfg"),
            "lora_choices": arguments.get("lora_choices"),
            "seed": arguments.get("seed", -1),
            "duration_seconds": arguments.get("duration_seconds"),
            "fps": arguments.get("fps"),
            "provider_id": arguments.get("provider_id") or "",
            "model_id": arguments.get("model_id") or "",
            "remote_api_mode": arguments.get("remote_api_mode") or "auto",
            "idempotency_key": "agent:" + hashlib.sha256(
                f"{context.task_id or context.run_id}:{context.source_message_id}:{name}:{json.dumps(arguments, sort_keys=True)}".encode("utf-8")
            ).hexdigest(),
        }
        job, created = create_job(
            CreationJobRequest.model_validate(spec),
            source=context.source,
            conversation_id=context.conversation_id,
            parent_job_id=parent_job_id,
        )
        return {"job": job, "created": created, "message": "创作任务已创建；请根据任务状态等待完成或确认。"}
    if name == "creation_cancel_job":
        from .creation_service import cancel_job

        return {"job": await cancel_job(str(arguments.get("job_id") or ""))}
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

    scope = f"{context.task_id}:{context.source_message_id}" if context.task_id else context.run_id
    if definition.permission == ToolPermission.READ_ONLY or definition.name == "agent_task_update":
        scope = f"{context.run_id}:{context.step_index}"
    key = _idempotency_key(scope, definition.name, validated)
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

    # Intent is the model's responsibility.  Explicit-intent regexes remain
    # descriptive metadata for compatibility, but they must not silently
    # authorize a high-risk mutation.  The runtime always gates that class;
    # local creation and other low-risk actions can proceed automatically.
    requires_confirmation = definition.permission == ToolPermission.HIGH_RISK_WRITE
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
