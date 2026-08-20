from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import date, timedelta
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from .. import (
    companion_service,
    conversation_service,
    daily_diary_service,
    db,
    onboarding_service,
    privacy_service,
    proactive_service,
    route_observation_service,
    screen_observation_service,
)
from .. import dependency_installer
from ..auto_router import AutoRoute, build_task_profile, select_auto_route
from ..agent_tool_service import public_tool_catalog
from ..chat_service import TextAttachment, chat_with_ai
from ..chat_idempotency import (
    claim_request,
    content_fingerprint,
    normalize_error_detail,
    pending_error,
    request_fingerprint,
)
from ..companion_action_service import approve_companion_action
from ..config import save_runtime_settings, settings
from ..conversation_runtime import ChatRunCancelledError, chat_run_coordinator, runtime_traces
from ..context_service import preview_chat_context_usage
from ..image_service import ImageAttachment, image_attachment_from_data_url
from ..llm import LLMConfigError, ModelRequestError, resolve_model_id
from ..model_registry import (
    get_model_profile,
    hidden_default_provider_records,
    list_model_profiles,
    public_model_profile,
)
from ..provider_presets import public_provider_presets
from ..runtime_identity import runtime_identity
from ..napcat_service import (
    configure_napcat,
    get_napcat_login_status,
    get_napcat_qrcode,
    refresh_napcat_qrcode,
    run_napcat_control,
)
from ..qq_group_service import (
    clear_group_histories,
    public_group_status,
    save_group_config,
)
from ..tool_registry import tool_registry
from .onebot import active_websocket_count, connected_self_ids, send_private_message_receipt
from .conversations import (
    ConversationUpdateRequest,
    delete_conversation,
    rename_conversation,
)
from .models import (
    ModelProfileRequest,
    _discovery_records,
    _new_api_pricing_map,
    create_model_profile,
    remove_model_profile,
)
from . import conversations as conversation_routes, models as model_routes, self_state as self_state_routes


router = APIRouter(prefix="/api/agent")
router.include_router(conversation_routes.router, prefix="")
router.include_router(model_routes.router, prefix="")
router.include_router(self_state_routes.router, prefix="")

ACTION_LABELS = {
    "add_diary_material": "暂存日记素材",
    "set_daily_thirty": "更新每日三十",
    "set_daily_mood": "更新今日情绪",
    "update_today_state": "更新今日状态",
    "edit_today_diary": "修改今日日记",
    "generate_today_diary": "生成今日日记",
    "update_profile": "更新 Mio 属性",
    "remember_thread": "记录待跟进话题",
    "resolve_thread": "完成待跟进话题",
    "remember_memory": "写入分层记忆",
}


def _record_failed_auto_route(
    route: AutoRoute | None,
    *,
    request_id: str,
    selected_model_id: str,
    selected_reasoning_level: str,
    error_code: str,
) -> None:
    if route is None:
        return
    route_observation_service.record_failed_route(
        source="desktop",
        mode="automatic",
        request_id=request_id,
        selected_model_id=selected_model_id,
        selected_reasoning_level=selected_reasoning_level,
        actual_model_id=selected_model_id,
        difficulty=route.difficulty,
        reason=route.reason,
        latency_budget_ms=route.latency_budget_ms,
        error_code=error_code,
        task_type=str(route.task_profile.get("task_type") or "conversation"),
        task_profile=route.task_profile,
        candidates=route.candidates,
    )


class AgentAttachmentRequest(BaseModel):
    kind: str
    name: str
    mime_type: str = ""
    data_url: str = ""
    text: str = ""
    size: int = 0
    ephemeral: bool = False


class AgentChatRequest(BaseModel):
    message: str = ""
    reasoning_level: str = "standard"
    model_id: str = "auto"
    conversation_id: str = ""
    attachments: list[AgentAttachmentRequest] = Field(default_factory=list)
    client_request_id: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9._:-]*$")


class AgentChatCancelRequest(BaseModel):
    conversation_id: str = ""
    client_request_id: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9._:-]*$")


class GroupChatSettingsRequest(BaseModel):
    enabled: bool = False
    group_ids: list[str] = Field(default_factory=list)
    mention_required: bool = True


class QQSetupRequest(BaseModel):
    account: str = Field(min_length=5, max_length=12)
    target_user_id: str = ""


class StartupGreetingRequest(BaseModel):
    conversation_id: str = ""


_background_state_analysis_tasks: set[asyncio.Task[None]] = set()
# 已在本进程内为某逻辑日调度过状态判定写入的标记，避免每次聊天都重复触发 analyze 覆盖已有状态。
_analyzed_state_days: set[str] = set()


def _schedule_today_state_analysis() -> None:
    """聊天成功后，若当天状态仍未判定且今天已有聊天/素材、且本进程内尚未分析过，后台自动判定写入，不阻塞回复。"""

    async def _run() -> None:
        try:
            today = db.today_string()
            state = db.get_daily_state(today)
            # 已判定或已在本进程分析过 → 跳过，避免每次聊天重复分析并覆盖已有状态。
            if state is None or state["daily_thirty_status"] != "unknown" or today in _analyzed_state_days:
                return
            messages = db.get_today_messages(today)
            materials = db.list_diary_materials(today)
            if not messages and not materials:
                return
            _analyzed_state_days.add(today)
            from .chat import analyze_today_state

            try:
                await analyze_today_state()
            except Exception:
                _analyzed_state_days.discard(today)
                raise
        except Exception:
            logger.warning("后台今日状态判定失败", exc_info=True)

    task = asyncio.create_task(_run())
    _background_state_analysis_tasks.add(task)
    task.add_done_callback(_background_state_analysis_tasks.discard)


@router.get("/runtime/traces")
async def runtime_trace_status(limit: int = 50):
    return {
        "summary": await runtime_traces.summary(),
        "traces": await runtime_traces.list(limit=limit),
    }


@router.get("/tools")
async def agent_tools():
    return {"tools": public_tool_catalog()}


@router.get("/tool-receipts")
async def agent_tool_receipts(limit: int = Query(default=50, ge=1, le=500)):
    receipts = []
    for row in db.list_tool_execution_receipts(limit=limit):
        item = dict(row)
        try:
            item["request"] = json.loads(str(item.pop("request_json") or "{}"))
        except json.JSONDecodeError:
            item["request"] = {}
        receipts.append(item)
    return {"receipts": receipts}


def _agent_run_dict(row, *, include_steps: bool = False) -> dict[str, object]:
    item = dict(row)
    for key, fallback in (
        ("plan_json", {}),
        ("observation_json", []),
        ("summary_json", {}),
    ):
        raw = item.pop(key, "")
        try:
            item[key.removesuffix("_json")] = json.loads(str(raw or ""))
        except json.JSONDecodeError:
            item[key.removesuffix("_json")] = fallback
    if include_steps:
        steps = []
        for row_step in db.list_agent_run_steps(str(item.get("run_id") or "")):
            step = dict(row_step)
            for key in ("arguments_json", "result_json"):
                raw = step.pop(key, "")
                try:
                    step[key.removesuffix("_json")] = json.loads(str(raw or "{}"))
                except json.JSONDecodeError:
                    step[key.removesuffix("_json")] = {}
            steps.append(step)
        item["steps"] = steps
    return item


@router.get("/runs")
async def agent_runs(
    limit: int = Query(default=50, ge=1, le=500),
    conversation_id: str = "",
):
    return {
        "runs": [
            _agent_run_dict(row)
            for row in db.list_agent_runs(limit=limit, conversation_id=conversation_id)
        ]
    }


@router.get("/runs/by-request/{request_id}")
async def agent_run_by_request(request_id: str):
    row = db.get_agent_run_by_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有找到这次 Agent 运行。")
    return _agent_run_dict(row, include_steps=True)


@router.get("/runs/{run_id}")
async def agent_run_detail(run_id: str):
    row = db.get_agent_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有找到这次 Agent 运行。")
    return _agent_run_dict(row, include_steps=True)


TEXT_FILE_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".log",
    ".py", ".js", ".ts", ".vue", ".html", ".css", ".xml", ".yaml", ".yml",
}
DESKTOP_PET_CONVERSATION_ID = conversation_service.DESKTOP_PET_CONVERSATION_ID
_primary_conversation_id = conversation_service.primary_conversation_id
_conversation_id = conversation_service.resolve_conversation_id
_conversation_list = conversation_service.list_conversations
_message_dict = conversation_service.public_message
_message_list = conversation_service.public_messages


def _safe_attachment_name(name: str) -> str:
    clean = Path(name.strip()).name[:120]
    return clean or "未命名附件"


def _archive_attachment(name: str, content: bytes, mime_type: str, kind: str) -> dict[str, object]:
    logical_date = db.today_string()
    folder = settings.agent_attachment_dir / logical_date
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    (folder / stored_name).write_bytes(content)
    return {
        "kind": kind,
        "name": name,
        "mime_type": mime_type,
        "size": len(content),
        "url": f"/agent-files/{logical_date}/{stored_name}",
    }


_delete_archived_attachments = conversation_service.delete_archived_attachments


def _prepare_attachments_unchecked(
    requested: list[AgentAttachmentRequest],
    metadata: list[dict[str, object]],
) -> tuple[list[ImageAttachment], list[TextAttachment], list[dict[str, object]]]:
    # PDF/Word/Excel parsing imports openpyxl and other heavy document stacks.
    # Keep ordinary text-chat startup free of those imports and load them only
    # when an attachment request actually needs document decoding.
    from ..document_service import decode_attachment_data_url, parse_document_data_url

    if len(requested) > settings.agent_attachment_max_count:
        raise ValueError(f"每次最多添加 {settings.agent_attachment_max_count} 个附件。")

    images: list[ImageAttachment] = []
    text_files: list[TextAttachment] = []
    for item in requested:
        name = _safe_attachment_name(item.name)
        if item.kind == "image":
            try:
                image = image_attachment_from_data_url(item.data_url, source=name)
            except RuntimeError:
                content = decode_attachment_data_url(item.data_url, name)
                mime_type = item.mime_type or "application/octet-stream"
                text_files.append(TextAttachment(
                    name=name,
                    content=(
                        f"[附件信息]\n文件名：{name}\n类型：{mime_type}\n"
                        "这张图片已保存在本地，但当前无法解析图片内容。"
                    ),
                    mime_type="text/plain",
                ))
                if not item.ephemeral:
                    metadata.append(_archive_attachment(name, content, mime_type, "image"))
                continue
            images.append(image)
            if not item.ephemeral:
                metadata.append(_archive_attachment(name, image.content, image.mime_type, "image"))
            continue

        if item.kind == "document":
            parsed = parse_document_data_url(item.data_url, name)
            if len(parsed.text_attachment.content) > settings.agent_text_attachment_max_chars:
                raise ValueError(f"文档提取后的文字太长：{name}")
            images.extend(parsed.images)
            text_files.append(parsed.text_attachment)
            metadata.append(_archive_attachment(name, parsed.content, parsed.mime_type, "document"))
            continue

        if item.kind == "file":
            content = decode_attachment_data_url(item.data_url, name)
            mime_type = item.mime_type or "application/octet-stream"
            text_files.append(TextAttachment(
                name=name,
                content=(
                    f"[附件信息]\n文件名：{name}\n类型：{mime_type}\n大小：{len(content)} 字节\n"
                    "文件已保存在本地，但当前没有对应的正文解析器，不要声称已经读取文件内容。"
                ),
                mime_type="text/plain",
            ))
            metadata.append(_archive_attachment(name, content, mime_type, "file"))
            continue

        if item.kind != "text":
            raise ValueError(f"暂不支持这个附件：{name}")
        suffix = Path(name).suffix.lower()
        if suffix not in TEXT_FILE_EXTENSIONS and not item.mime_type.lower().startswith("text/"):
            raise ValueError(f"暂不支持读取这个文件：{name}")
        if len(item.text) > settings.agent_text_attachment_max_chars:
            raise ValueError(f"文本文件太长：{name}")
        content = item.text.encode("utf-8")
        text_attachment = TextAttachment(
            name=name,
            content=item.text,
            mime_type=item.mime_type or "text/plain",
        )
        text_files.append(text_attachment)
        metadata.append(_archive_attachment(name, content, text_attachment.mime_type, "text"))
    return images, text_files, metadata


def _prepare_attachments(
    requested: list[AgentAttachmentRequest],
) -> tuple[list[ImageAttachment], list[TextAttachment], list[dict[str, object]]]:
    metadata: list[dict[str, object]] = []
    try:
        return _prepare_attachments_unchecked(requested, metadata)
    except Exception:
        _delete_archived_attachments([json.dumps(metadata, ensure_ascii=False)])
        raise


async def _qq_status() -> dict[str, object]:
    count = active_websocket_count()
    websocket_connected = count > 0
    login_status = await get_napcat_login_status(websocket_connected=websocket_connected)
    configured_account = str(settings.napcat_account or "").strip()
    connected_accounts = connected_self_ids()
    connected_account = connected_accounts[0] if len(connected_accounts) == 1 else ""
    if not connected_account:
        connected_account = str(login_status.get("webui_account") or "").strip()
    logged_in = bool(login_status.get("logged_in") or connected_accounts)
    account_matches = bool(
        configured_account
        and connected_account
        and configured_account == connected_account
    )
    account_ready = logged_in and (not configured_account or account_matches)
    diagnostic_code = str(login_status.get("diagnostic_code") or "")
    diagnostic_message = str(login_status.get("diagnostic_message") or "")
    if configured_account and connected_account and not account_matches:
        diagnostic_code = "account_mismatch"
        diagnostic_message = (
            f"当前实际登录的是 QQ {connected_account}，但 Mio 配置的是 {configured_account}。"
            "测试发送已被阻止；请彻底退出旧机器人 QQ 后重新启动。"
        )
    elif account_ready and connected_account:
        diagnostic_code = "ready"
        diagnostic_message = f"机器人 QQ {connected_account} 已登录并连接 OneBot。"
    connected = websocket_connected and account_ready
    return {
        "enabled": settings.qq_bot_enabled,
        "connected": connected,
        "websocket_connected": websocket_connected,
        "websocket_connections": count,
        "configured_account": configured_account,
        "connected_account": connected_account,
        "connected_accounts": connected_accounts,
        "account_matches": account_matches,
        "account_ready": account_ready,
        "logged_in": logged_in,
        "login_source": "onebot" if connected_accounts else ("webui" if login_status.get("login_checked") else ""),
        "diagnostic_code": diagnostic_code,
        "diagnostic_message": diagnostic_message,
        "group_chat": public_group_status(),
        "proactive_runtime": proactive_service.get_proactive_status(),
        **{key: value for key, value in login_status.items() if key not in {"logged_in", "diagnostic_code", "diagnostic_message"}},
    }


def _context_usage(conversation_id: str) -> dict[str, object]:
    history_rows = db.get_recent_messages(
        limit=settings.chat_raw_history_limit,
        conversation_id=conversation_id,
    )
    return preview_chat_context_usage(conversation_id, list(history_rows))


def _day_dashboard_payload() -> dict[str, object]:
    logical_date = db.today_string()
    history_start = (date.fromisoformat(logical_date) - timedelta(days=6)).isoformat()
    state = db.get_daily_state(logical_date)
    token_usage = db.get_token_usage_summary(days=1)
    return {
        "diaries": [dict(row) for row in db.list_diaries()[:8]],
        "today_state": dict(state) if state is not None else None,
        "state_history": [dict(row) for row in db.list_daily_states_since(history_start)],
        "logical_date": logical_date,
        "current_time": db.now_iso(),
        "auto_diary": daily_diary_service.get_daily_diary_status(),
        "today_token_usage": token_usage["today"],
        "total_token_usage": token_usage["total"]["total_tokens"],
    }


def _agent_task_dict(row) -> dict[str, object]:
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": int(row["id"]),
        "date": str(row["date"] or ""),
        "conversation_id": str(row["conversation_id"] or ""),
        "source_message_id": int(row["source_message_id"] or 0),
        "action_type": str(row["action_type"] or ""),
        "title": ACTION_LABELS.get(str(row["action_type"] or ""), str(row["action_type"] or "任务")),
        "payload": payload if isinstance(payload, dict) else {},
        "status": str(row["status"] or ""),
        "result": str(row["result"] or ""),
        "requires_confirmation": bool(row["requires_confirmation"]),
        "approved_at": str(row["approved_at"] or ""),
        "created_at": str(row["created_at"] or ""),
        "finished_at": str(row["finished_at"] or ""),
        "request_id": str(row["request_id"] or ""),
        "trace_id": str(row["trace_id"] or ""),
        "agent_run_id": str(row["agent_run_id"] or ""),
        "agent_step_id": int(row["agent_step_id"] or 0),
    }


def _agent_stats_payload(year: int = 0, month: int = 0) -> dict[str, object]:
    today = date.fromisoformat(db.today_string())
    selected_year = year if 1970 <= year <= 2200 else today.year
    selected_month = month if 1 <= month <= 12 else today.month
    mood_start = (today - timedelta(days=29)).isoformat()
    mood_trend = [
        {
            "date": row["date"],
            "mood": row["mood"] or "",
            "mood_score": int(row["mood_score"] or 0),
            "daily_thirty_status": row["daily_thirty_status"],
        }
        for row in db.list_daily_states_since(mood_start)
    ]
    return {
        "summary": db.get_diary_stats(),
        "calendar": db.get_calendar_data(selected_year, selected_month),
        "mood_trend": mood_trend,
        "year": selected_year,
        "month": selected_month,
        "logical_date": today.isoformat(),
    }


@router.get("/bootstrap")
async def bootstrap():
    proactive_service.note_desktop_app_active()
    conversation_id = _primary_conversation_id()
    dashboard = _day_dashboard_payload()
    models = [public_model_profile(profile) for profile in list_model_profiles()]
    try:
        active_model = public_model_profile(get_model_profile())
    except ValueError:
        active_model = None
    return {
        "onboarding": onboarding_service.onboarding_status(),
        "privacy": privacy_service.privacy_status(),
        "conversation_id": conversation_id,
        "conversations": _conversation_list(),
        "messages": _message_list(db.get_recent_messages(limit=120, conversation_id=conversation_id)),
        "model": active_model,
        "models": models,
        "provider_presets": public_provider_presets(),
        "hidden_model_providers": hidden_default_provider_records(),
        "runtime_identity": runtime_identity(),
        "attachment_limits": {
            "max_count": settings.agent_attachment_max_count,
            "image_max_bytes": settings.qq_image_max_bytes,
            "document_max_bytes": settings.agent_document_attachment_max_bytes,
            "text_max_chars": settings.agent_text_attachment_max_chars,
            "text_max_bytes": min(
                settings.agent_document_attachment_max_bytes,
                max(512 * 1024, settings.agent_text_attachment_max_chars * 4),
            ),
        },
        "qq": await _qq_status(),
        "context_usage": _context_usage(conversation_id),
        **dashboard,
    }


@router.get("/day-dashboard")
async def day_dashboard():
    return _day_dashboard_payload()


@router.get("/qq/status")
async def qq_status():
    proactive_service.note_desktop_app_active()
    return await _qq_status()


@router.post("/qq/setup")
async def setup_qq_channel(payload: QQSetupRequest):
    """Configure the local OneBot client, then start NapCat for QR login."""
    current = await _qq_status()
    if not current.get("napcat_executable_exists"):
        try:
            install = await asyncio.to_thread(dependency_installer.install_dependency, "napcat")
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "ok": True,
            "stage": "installing",
            "message": "NapCat 正在安装，完成后再次点击配置即可写入 OneBot。",
            "install": install,
        }
    try:
        configured = await asyncio.to_thread(configure_napcat, payload.account)
        target = str(payload.target_user_id or "").strip()
        if target:
            if not target.isdigit() or not 5 <= len(target) <= 12:
                raise ValueError("接收测试消息的 QQ 号必须是 5 到 12 位数字。")
            allowed = list(settings.qq_allowed_user_ids)
            if target not in allowed:
                allowed.append(target)
                await asyncio.to_thread(save_runtime_settings, {"qq_allowed_user_ids": allowed})
        connected_account = str(current.get("connected_account") or "").strip()
        switch_required = bool(
            configured.get("account_changed")
            or (connected_account and connected_account != payload.account)
        )
        control = await asyncio.to_thread(
            run_napcat_control,
            "restart" if switch_required else "start",
            force_qr_login=switch_required,
        )
    except (OSError, RuntimeError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not control.get("ok"):
        raise HTTPException(status_code=500, detail=control.get("output") or "NapCat 启动失败。")
    configured["stage"] = "login"
    configured["force_qr_login"] = switch_required
    configured["control"] = control
    configured["qq"] = await _qq_status()
    return configured


@router.post("/qq/test-delivery")
async def qq_test_delivery(payload: QQSetupRequest):
    target = str(payload.target_user_id or "").strip()
    if not target:
        target = str(settings.qq_allowed_user_ids[0] if settings.qq_allowed_user_ids else "").strip()
    if not target.isdigit():
        raise HTTPException(status_code=400, detail="请先填写一个用于接收测试消息的 QQ 号白名单。")
    status = await _qq_status()
    requested_account = str(payload.account or settings.napcat_account or "").strip()
    connected_account = str(status.get("connected_account") or "").strip()
    if connected_account and requested_account and connected_account != requested_account:
        raise HTTPException(
            status_code=409,
            detail=f"已阻止发送：当前实际登录 QQ {connected_account}，配置目标是 {requested_account}。请先切换账号。",
        )
    if not status.get("account_ready"):
        raise HTTPException(status_code=409, detail=status.get("diagnostic_message") or "机器人 QQ 尚未按配置账号连接。")
    receipt = await send_private_message_receipt(target, "Mio 链路测试：NapCat 已确认接受这条消息。")
    return {
        "target_user_id": target,
        "delivery_confirmed": bool(receipt.get("acknowledged")),
        "message_id": receipt.get("message_id"),
        "diagnostic": receipt.get("error") or "NapCat 已返回发送 ACK；这只代表接口确认，不等于真人已看到消息。",
        "receipt": receipt,
        "qq": await _qq_status(),
    }


@router.get("/stats")
async def agent_stats(year: int = 0, month: int = 0):
    return _agent_stats_payload(year, month)


@router.get("/token-usage")
async def token_usage(days: int = Query(default=30, ge=1, le=365)):
    return db.get_token_usage_summary(days)


@router.get("/tasks")
async def agent_tasks(
    limit: int = Query(default=100, ge=1, le=500),
    conversation_id: str = "",
):
    return [_agent_task_dict(row) for row in db.list_companion_actions(limit, conversation_id)]


@router.post("/tasks/{task_id}/approve")
async def approve_agent_task(task_id: int):
    try:
        await approve_companion_action(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = db.get_companion_action(task_id)
    return _agent_task_dict(row)


@router.post("/tasks/{task_id}/cancel")
async def cancel_agent_task(task_id: int):
    row = db.get_companion_action(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if str(row["status"] or "") not in {"needs_confirmation", "queued"}:
        raise HTTPException(status_code=400, detail="这个任务已经开始或结束，不能取消。")
    db.update_companion_action(task_id, "cancelled", "用户已取消。")
    agent_step_id = int(row["agent_step_id"] or 0)
    if agent_step_id:
        step = db.get_agent_run_step(agent_step_id)
        if step is not None and int(step["receipt_id"] or 0):
            db.finish_tool_execution_receipt(
                int(step["receipt_id"]),
                "cancelled",
                "用户已取消。",
            )
        db.update_agent_run_step(agent_step_id, "cancelled", error="用户已取消。")
    return _agent_task_dict(db.get_companion_action(task_id))


@router.get("/messages")
async def list_messages(
    limit: int = Query(default=120, ge=1, le=500),
    conversation_id: str = "",
):
    proactive_service.note_desktop_app_active()
    try:
        selected_conversation = _conversation_id(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _message_list(db.get_recent_messages(
        limit=limit,
        conversation_id=selected_conversation,
    ))


@router.post("/startup-greeting")
async def startup_greeting(payload: StartupGreetingRequest):
    if os.getenv("MIO_DESKTOP_APP", "").strip() != "1":
        return {"sent": False, "replies": []}
    if not companion_service.load_config().get("startup_greeting_enabled", True):
        return {"sent": False, "replies": [], "disabled": True}
    try:
        selected_conversation = _conversation_id(payload.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    replies = await proactive_service.run_desktop_startup_greeting_once(selected_conversation)
    return {"sent": bool(replies), "replies": replies}


@router.get("/context-usage")
async def context_usage(conversation_id: str = ""):
    try:
        selected_conversation = _conversation_id(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _context_usage(selected_conversation)


@router.post("/chat")
async def agent_chat(payload: AgentChatRequest):
    message = payload.message.strip()
    automatic_route: AutoRoute | None = None
    screen_follow_up = False
    selected_model = ""
    try:
        selected_conversation = _conversation_id(payload.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    attachment_fingerprints = [
        {
            "kind": item.kind,
            "name": item.name,
            "mime_type": item.mime_type,
            "size": item.size,
            "ephemeral": item.ephemeral,
            "content_hash": content_fingerprint(item.data_url or item.text),
        }
        for item in payload.attachments
    ]
    try:
        claim = claim_request(
            payload.client_request_id,
            request_fingerprint({
                "channel": "agent_chat",
                "conversation_id": selected_conversation,
                "message": message,
                "model_id": payload.model_id,
                "reasoning_level": payload.reasoning_level,
                "attachments": attachment_fingerprints,
            }),
            conversation_id=selected_conversation,
            source="desktop",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not claim.created:
        if claim.status == "succeeded":
            return claim.response
        if claim.status == "failed":
            raise HTTPException(status_code=claim.http_status or 500, detail=claim.error)
        raise HTTPException(status_code=409, detail=pending_error(claim))

    try:
        images, text_files, attachment_metadata = _prepare_attachments(payload.attachments)
        if not message and not images and not text_files:
            raise ValueError("消息或附件不能为空。")
        # 跨天后首次对话：确保今天有状态记录（未确认），避免把昨天的状态当成今天的。
        try:
            db.ensure_daily_state_today()
        except Exception:
            logger.warning("今日状态初始化失败", exc_info=True)
        routing_history = list(
            db.get_recent_messages(limit=12, conversation_id=selected_conversation)
        )
        screen_follow_up = bool(
            not images
            and not text_files
            and screen_observation_service.is_screen_chat_follow_up(message, routing_history)
        )
        if screen_follow_up:
            result = await screen_observation_service.analyze_screen_chat_follow_up(
                message,
                conversation_id=selected_conversation,
                request_id=claim.client_request_id,
                source="desktop",
            )
            selected_model = result.model_id
            selected_reasoning = result.reasoning_level
        else:
            task_profile = build_task_profile(
                message,
                history_rows=routing_history,
                image_count=len(images),
                text_attachment_chars=sum(len(item.content) for item in text_files),
            )
            selected_reasoning = payload.reasoning_level
            if not payload.model_id.strip() or payload.model_id == "auto":
                automatic_route = select_auto_route(
                    message,
                    history_rows=routing_history,
                    image_count=len(images),
                    text_attachment_chars=sum(len(item.content) for item in text_files),
                )
                selected_model = automatic_route.model_id
                selected_reasoning = automatic_route.reasoning_level
            else:
                selected_model = resolve_model_id(payload.model_id)
            if (
                selected_conversation.startswith("desktop_")
                and selected_conversation != DESKTOP_PET_CONVERSATION_ID
            ):
                db.touch_agent_conversation(selected_conversation, message or "附件对话")
            # 普通聊天（简单难度、无附件）跳过 Agent 规划循环，直达回复，响应更快。
            # 记忆保存、日记素材、今日状态等动作仍由后台异步完成，不阻塞、不显示。
            use_fast_chat = bool(
                not task_profile.requires_tools
                and task_profile.task_type in {"conversation", "analysis"}
                and not images
                and not text_files
            )
            if use_fast_chat and selected_reasoning in {"", "auto", "off", "low"}:
                selected_reasoning = "off"
            result = await chat_with_ai(
                message,
                conversation_id=selected_conversation,
                source="desktop",
                image_attachments=images,
                text_attachments=text_files,
                attachment_metadata=attachment_metadata,
                reasoning_level=selected_reasoning,
                model_id=selected_model,
                fallback_model_id=automatic_route.fallback_model_id if automatic_route is not None else "",
                fallback_reasoning_level=(
                    automatic_route.fallback_reasoning_level if automatic_route is not None else ""
                ),
                capture_follow_ups=True,
                request_id=claim.client_request_id,
                agent_tools_enabled=not use_fast_chat,
                fast_path=use_fast_chat,
            )
    except ValueError as exc:
        detail = normalize_error_detail(
            str(exc),
            code="invalid_chat_request",
            request_id=claim.client_request_id,
        )
        db.fail_chat_request(claim.client_request_id, http_status=400, error=detail)
        raise HTTPException(status_code=400, detail=detail) from exc
    except ChatRunCancelledError as exc:
        detail = normalize_error_detail(
            f"回复已取消：{exc}",
            code="request_cancelled",
            request_id=claim.client_request_id,
        )
        db.fail_chat_request(claim.client_request_id, http_status=409, error=detail)
        raise HTTPException(status_code=409, detail=detail) from exc
    except LLMConfigError as exc:
        _record_failed_auto_route(
            automatic_route,
            request_id=claim.client_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            error_code="model_not_configured",
        )
        detail = normalize_error_detail(
            str(exc),
            code="model_not_configured",
            request_id=claim.client_request_id,
        )
        db.fail_chat_request(claim.client_request_id, http_status=400, error=detail)
        raise HTTPException(status_code=400, detail=detail) from exc
    except ModelRequestError as exc:
        _record_failed_auto_route(
            automatic_route,
            request_id=claim.client_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            error_code="model_request_failed",
        )
        detail = exc.public_detail()
        detail["request_id"] = claim.client_request_id
        db.fail_chat_request(claim.client_request_id, http_status=502, error=detail)
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        _record_failed_auto_route(
            automatic_route,
            request_id=claim.client_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            error_code="model_request_failed",
        )
        profile = None
        try:
            profile = get_model_profile(selected_model)
        except ValueError:
            pass
        detail = {
            "code": "model_request_failed",
            "message": f"模型请求失败：{exc}",
            "request_id": claim.client_request_id,
            "model_id": selected_model or payload.model_id or settings.openai_model,
            "provider_id": profile.provider_id if profile else "",
            "provider_name": profile.provider_name if profile else "",
            "provider_model": profile.model if profile else "",
            "http_status": None,
            "route": "",
            "attempts": [],
        }
        db.fail_chat_request(claim.client_request_id, http_status=502, error=detail)
        raise HTTPException(status_code=502, detail=detail) from exc

    # 聊天成功后，若当天状态仍是“未判定”且今天已有聊天/素材，后台自动完成今日状态判定写入，
    # 不依赖 agent 是否主动调用写入工具，也不阻塞本轮回复。
    try:
        _schedule_today_state_analysis()
    except Exception:
        logger.warning("后台今日状态判定调度失败", exc_info=True)

    response = {
        "reply": result.reply,
        "replies": result.replies,
        "speech_emotion": getattr(result, "speech_emotion", ""),
        "request_id": result.request_id,
        "client_request_id": claim.client_request_id,
        "model_id": result.model_id,
        "provider_id": result.provider_id,
        "provider_name": result.provider_name,
        "provider_model": result.provider_model,
        "provider_request_id": result.provider_request_id,
        "route": result.route,
        "http_status": result.http_status,
        "reasoning_level": result.reasoning_level,
        "prompt_tokens": result.prompt_tokens,
        "cached_prompt_tokens": result.cached_prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "request_cost_yuan": result.request_cost_yuan,
        "request_cost_source": result.request_cost_source,
        "first_token_latency_ms": result.first_token_latency_ms,
        "total_latency_ms": result.total_latency_ms,
        "agent_run_id": result.agent_run_id,
        "agent_run_status": result.agent_run_status,
        "tool_receipts": list(result.tool_receipts),
        "route_candidate_model_ids": list(result.route_candidate_model_ids),
        "route_escalated_from_model_id": result.route_escalated_from_model_id,
        "context_usage": _context_usage(selected_conversation),
        "auto_routing": automatic_route.public_dict() if automatic_route is not None else None,
    }
    db.complete_chat_request(claim.client_request_id, response)
    if automatic_route is not None and result.route_escalated_from_model_id:
        _record_failed_auto_route(
            automatic_route,
            request_id=claim.client_request_id,
            selected_model_id=result.route_escalated_from_model_id,
            selected_reasoning_level=selected_reasoning,
            error_code="primary_model_failed_before_final_reply",
        )
    if not screen_follow_up:
        route_observation_service.record_completed_route(
            source="desktop",
            mode="automatic" if automatic_route is not None else "manual",
            request_id=claim.client_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=result.reasoning_level,
            actual_model_id=result.model_id,
            connection_route=result.route,
            difficulty=automatic_route.difficulty if automatic_route is not None else "",
            reason=automatic_route.reason if automatic_route is not None else "用户手动选择模型与思考档位",
            latency_budget_ms=automatic_route.latency_budget_ms if automatic_route is not None else 0,
            first_token_latency_ms=result.first_token_latency_ms,
            total_latency_ms=result.total_latency_ms,
            request_cost_yuan=result.request_cost_yuan,
            request_cost_source=result.request_cost_source,
            task_type=(
                str(automatic_route.task_profile.get("task_type") or "conversation")
                if automatic_route is not None
                else "conversation"
            ),
            task_profile=automatic_route.task_profile if automatic_route is not None else {},
            candidates=automatic_route.candidates if automatic_route is not None else (),
            escalated_from_model_id=result.route_escalated_from_model_id,
        )
    return response


@router.post("/chat/cancel")
async def cancel_agent_chat(payload: AgentChatCancelRequest):
    try:
        selected_conversation = _conversation_id(payload.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cancelled = await chat_run_coordinator.cancel(
        selected_conversation,
        source="desktop",
        reason=f"user_cancelled:{payload.client_request_id or 'unknown'}",
    )
    return {
        "cancelled": cancelled,
        "conversation_id": selected_conversation,
        "client_request_id": payload.client_request_id,
    }


@router.patch("/qq/group-settings")
async def update_group_chat_settings(payload: GroupChatSettingsRequest):
    group_status = save_group_config(payload.model_dump())
    return {"ok": True, "group_chat": group_status}


@router.post("/qq/group-context/clear")
async def clear_group_chat_context():
    return {"ok": True, "group_chat": clear_group_histories()}


@router.get("/qq/qrcode")
async def qq_login_qrcode():
    qrcode = await get_napcat_qrcode()
    if qrcode is None:
        raise HTTPException(status_code=404, detail="QQ登录二维码还没有准备好。")
    content, media_type = qrcode
    return Response(content=content, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.post("/qq/{action}")
async def control_qq(action: str):
    if action not in {"start", "stop", "restart", "login"}:
        raise HTTPException(status_code=404, detail="不支持的QQ控制操作。")
    if action == "login":
        try:
            result = await asyncio.to_thread(
                run_napcat_control,
                "restart",
                force_qr_login=True,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if not result["ok"]:
            raise HTTPException(status_code=500, detail=result["output"] or "NapCat 无法进入二维码登录模式。")
        # The shell is started asynchronously. The frontend polls the uncached
        # QR endpoint, while this short attempt helps already-ready WebUI builds.
        for _attempt in range(8):
            await asyncio.sleep(0.35)
            if await refresh_napcat_qrcode():
                break
        return {
            "ok": True,
            "action": action,
            "forced_qr_login": True,
            "control": result,
            "qrcode_url": "/api/agent/qq/qrcode",
            "qq": await _qq_status(),
        }
    try:
        result = await asyncio.to_thread(run_napcat_control, action)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["output"] or "QQ控制操作失败。")
    result["qq"] = await _qq_status()
    proactive_service.note_manual_qq_control_result(
        action,
        connected=bool(result["qq"].get("websocket_connected")),
    )
    result["qq"] = await _qq_status()
    return result
