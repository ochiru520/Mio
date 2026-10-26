"""Model-selected context maintenance, scoped to the current private conversation."""
from __future__ import annotations

import asyncio
import hashlib
import json

from .repositories import context_tools_repository as repository

from . import db, maintenance_service
from .tool_types import EmptyToolInput, ToolDefinition, ToolPermission

NAME = "compress_current_context"
STATUSES = {"compressed", "noop", "failed", "queued"}
DEFINITIONS = [ToolDefinition(NAME, ToolPermission.LOW_RISK_WRITE,
    "按用户要求压缩当前会话上下文，保留原始消息；只处理当前会话。返回 compressed/noop/failed；活跃 Agent 仅排队 queued，不能声称已完成。",
    arguments_model=EmptyToolInput, timeout_seconds=600.0)]


def _failed(message):
    return {"status": "failed", "error": message, "original_messages_preserved": True}


def _bounded_result(value, *, agent):
    allowed = {"queued", "noop", "failed"} if agent else {"compressed", "noop", "failed"}
    if not isinstance(value, dict) or value.get("status") not in allowed:
        return _failed("压缩入口没有返回可确认的执行状态，未报告压缩完成。")
    status = value["status"]
    messages = {"compressed": "当前上下文已压缩，原始消息保留。", "noop": "当前没有可压缩的旧上下文，未执行压缩。",
                "queued": "压缩请求已持久排队，等待后续规划轮次执行；尚未完成。", "failed": "上下文压缩未成功，未报告压缩完成。"}
    result = {"status": status, "message": messages[status], "original_messages_preserved": True}
    # Return measurements, never summary contents or other conversations' data.
    for key in ("before_tokens", "after_tokens", "compressed_messages", "through_message_id", "summary_through_message_id", "request_version"):
        if isinstance(value.get(key), (int, float)) and not isinstance(value[key], bool):
            result[key] = max(0, value[key])
    for key in ("before", "after", "before_usage", "after_usage"):
        if isinstance(value.get(key), dict):
            result[key] = {name: count for name, count in value[key].items()
                           if name in {"used_tokens", "max_tokens", "message_count"} and isinstance(count, (int, float))}
    if status == "failed":
        result["error"] = messages[status]
    request_id = value.get("request_id")
    if isinstance(request_id, str) and 0 < len(request_id) <= 120 and all(ch.isalnum() or ch in "-_" for ch in request_id):
        result["request_id"] = request_id
    return result


async def compress_for_context(context):
    conversation_id = str(getattr(context, "conversation_id", "") or "").strip()
    source = str(getattr(context, "source", "") or "")
    if not conversation_id or conversation_id.startswith("qq_group_") or source in {"qq_group", "group"}:
        raise ValueError("上下文压缩只适用于当前私人会话，群聊不能访问私人上下文。")
    if maintenance_service.status().get("blocked"):
        raise ValueError("Mio 正在维护数据，暂不修改上下文工作记忆。")
    source_id = int(getattr(context, "source_message_id", 0) or 0)
    identity = str(source_id) if source_id > 0 else str(getattr(context, "request_id", "") or getattr(context, "run_id", ""))
    if not identity:
        raise ValueError("缺少当前消息的执行标识，未重复发起压缩。")
    key = hashlib.sha256(f"{conversation_id}\x1f{identity}".encode()).hexdigest()
    with repository.unit_of_work() as conn:
        db.assert_conversation_writable(conversation_id, conn.connection)
        conn.ensure_request_table()
        created = conn.claim_request((key, conversation_id))
        if not created:
            row = conn.stored_request((key,))
            if row["status"] in STATUSES:
                return {**json.loads(row["result_json"]), "replayed": True}
            return _failed("同一消息的压缩已启动但尚未确认结果，未自动重复执行。")
    try:
        if conversation_id.startswith("desktop_agent_"):
            from .agent_context_service import request_force_compression
            value = request_force_compression(conversation_id)
        else:
            from .context_service import force_compress_context
            value = await force_compress_context(conversation_id, model_id=None)
        result = _bounded_result(value, agent=conversation_id.startswith("desktop_agent_"))
    except asyncio.CancelledError:
        with repository.unit_of_work() as conn:
            conn.cancel_request((key,))
        raise
    except Exception:
        result = _failed("上下文压缩暂未成功，请检查模型状态；未报告压缩完成。")
    with repository.unit_of_work() as conn:
        conn.finish_request((result["status"], json.dumps(result, ensure_ascii=False), key))
    return result
