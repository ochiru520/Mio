from __future__ import annotations

import asyncio
import base64
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status

from .. import companion_service, db as database, route_observation_service
from ..auto_router import AutoRoute, select_auto_route
from ..chat_service import LLMConfigError, chat_in_qq_group, chat_with_ai, clean_chat_reply
from ..config import settings
from ..conversation_runtime import runtime_traces
from ..image_service import archive_image_attachments, load_image_attachments
from ..qq_group_service import (
    append_group_exchange,
    get_group_history,
    group_is_allowed,
    group_mention_required,
    public_group_status,
)


router = APIRouter()

logger = logging.getLogger("mio.onebot")

SendJson = Callable[[dict[str, Any]], Awaitable[None]]
TODAY_DIARY_COMMAND_RE = re.compile(
    r"(日终整理|(?:重新)?(?:生成|写|整理).{0,8}(?:今天|今日|当天)?.{0,4}日记|(?:今天|今日|当天).{0,4}日记.{0,8}(?:生成|写|整理))"
)
# “还没写日记”“我把日记写好了”这类陈述不是生成命令。
TODAY_DIARY_EXCLUDE_RE = re.compile(
    r"(?:还没|没有|没来得及|不用|先不|别急着|不想|懒得|忘了?).{0,6}(?:写|生成|整理).{0,4}日记"
    r"|(?:写|生成|整理)(?:完|好)了"
    r"|日记.{0,4}(?:写|生成)(?:完|好)?了"
)
DIARY_EDIT_COMMAND_RE = re.compile(
    r"(?:把|给|在|帮我)?(?:\d{4}-\d{2}-\d{2}|今天|今日|当天)?.{0,8}日记.{0,8}(?:修改|改成|改为|改一下|补上|补充|加上|加入|写进|删掉|删除|调整|更新)"
    r"|(?:修改|改成|改为|改一下|补上|补充|加上|加入|写进|删掉|删除|调整|更新).{0,8}(?:\d{4}-\d{2}-\d{2}|今天|今日|当天)?.{0,8}日记"
)
# “我把日记补上了”“已经改过了”这类已完成的陈述不是修改命令；
# “改了吧”这种带请求语气词的仍然算命令。
DIARY_EDIT_EXCLUDE_RE = re.compile(
    r"我.{0,10}(?:补上|加上|写进|改|删|调整|更新)(?:好|完)?了(?![吧呗])"
    r"|(?:补上|加上|写进|改|删|调整|更新)(?:好|完)了(?![吧呗])"
    r"|已经.{0,6}(?:改|补|加|更新)"
)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
QUESTION_RE = re.compile(r"(怎么|如何|怎样|能不能|可以吗|行不行|为什么|啥意思|[吗呢？?])")
PROFILE_UPDATE_RE = re.compile(
    r"(?:修改|调整|更新|改一下|改成|设定|设置|记住|加入|加上|加到|加进|补进|补充到|写入|写进|记到|放到|放进|存进|收进|以后你|你以后|把你|你的)"
    r".{0,20}(?:属性|人设|人格|设定|底层设定|底层属性|底层代码|基础设定|说话方式|说话风格|语气|口吻|称呼|主动|日记偏好|回复方式|偏好)"
    r"|(?:属性|人设|人格|设定|底层设定|底层属性|底层代码|基础设定|说话方式|说话风格|语气|口吻|称呼|主动|日记偏好|回复方式|偏好)"
    r".{0,20}(?:修改|调整|更新|改一下|改成|设定|设置|记住|加入|加上|加到|加进|补进|补充到|写入|写进|记到|放到|放进|存进|收进)"
)
# 属性命令必须是在对 Mio 说（含“你/Mio/澪”或明确提属性、人设），
# 否则“我想调整一下说话方式”这类自述会误触发；剩余情况交给行动判断器。
PROFILE_ADDRESSEE_RE = re.compile(r"(你|Mio|澪|属性|人设|人格|底层|基础设定)", re.IGNORECASE)


@dataclass
class OneBotConnection:
    websocket: WebSocket
    lock: asyncio.Lock
    self_id: str = ""
    outbox: asyncio.Queue[tuple[dict[str, Any], asyncio.Future[Any]]] = field(
        default_factory=asyncio.Queue
    )
    pending_acks: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    worker: asyncio.Task[None] | None = None
    debouncer: OneBotEventDebouncer | None = None
    processing_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    closing: bool = False


_delivery_debug: dict[str, Any] = {
    "queued_count": 0,
    "sent_count": 0,
    "failed_count": 0,
    "retry_count": 0,
    "pending_count": 0,
    "last_status": "",
    "last_error": "",
    "last_echo": "",
    "last_at": "",
}


_active_connections: dict[int, OneBotConnection] = {}
INCOMPLETE_MESSAGE_RE = re.compile(
    r"(?:[,，、:：;；…]|\.\.\.|然后|但是|不过|因为|还有|就是|其实|等我)$"
)
VOICE_REPLY_REQUEST_RE = re.compile(
    r"(?:发|来)(?:一|几)?(?:条|段|个|句)?(?:短)?语音"
    r"|(?:可以|能不能|能否|能|想|要|请|拜托).{0,20}(?:来|发|用|说|给).{0,20}(?:语音|声音|说话)"
    r"|(?:来|发|给我|让我听(?:听)?).{0,20}(?:语音|声音)"
    r"|用(?:你的|Mio的|澪的)?(?:语音|声音).{0,8}(?:说|回|回复|回答|告诉|念|读|讲|聊|介绍|自我介绍|接受)"
    r"|(?:语音|声音).{0,6}(?:说|回|回复|回答|告诉|念|读|讲|聊|介绍|自我介绍|接受)"
    r"|(?:说|念|读)(?:一|几)?(?:句|段)?(?:话)?给我听"
    r"|能不能发语音|可以发语音吗|能发语音吗"
)
MIO_TEXT_MENTION_RE = re.compile(r"^\s*[@＠]\s*(?:Mio|澪)(?=\s|[,，:：、]|$)[\s,，:：、]*", re.IGNORECASE)
_event_debug: dict[str, Any] = {
    "received_event_count": 0,
    "processed_message_count": 0,
    "last_event_at": "",
    "last_event_post_type": "",
    "last_event_message_type": "",
    "last_event_user_id": "",
    "last_event_self_id": "",
    "last_event_authorized": False,
    "last_event_ignore_reason": "",
    "last_event_text_preview": "",
    "last_event_image_count": 0,
    "last_processed_message_at": "",
    "last_error": "",
}


def active_websocket_count() -> int:
    return len(_active_connections)


def connected_self_ids() -> list[str]:
    return sorted({connection.self_id for connection in _active_connections.values() if connection.self_id})


def runtime_health_status() -> dict[str, Any]:
    connections = list(_active_connections.values())
    return {
        "enabled": settings.qq_bot_enabled,
        "websocket_connections": len(connections),
        "connected_accounts": connected_self_ids(),
        "active_queue_count": sum(connection.outbox.qsize() for connection in connections),
        "active_pending_ack_count": sum(len(connection.pending_acks) for connection in connections),
        "delivery": dict(_delivery_debug),
    }


def _debug_now() -> str:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(tz).isoformat(timespec="seconds")


def _as_id(value: object) -> str:
    return str(value or "").strip()


def _text_mentions_mio(text: str) -> bool:
    return MIO_TEXT_MENTION_RE.match(text or "") is not None


def _strip_text_mio_mention(text: str) -> str:
    return MIO_TEXT_MENTION_RE.sub("", text or "", count=1).strip()


def _extract_text_message(event: dict[str, Any]) -> str:
    message = event.get("message")
    if isinstance(message, list):
        parts: list[str] = []
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "text":
                continue
            data = segment.get("data")
            if isinstance(data, dict):
                parts.append(str(data.get("text") or ""))
        text = "".join(parts).strip()
        if text:
            if event.get("message_type") == "group":
                return _strip_text_mio_mention(text)
            return text
        raw_message = str(event.get("raw_message") or "")
    else:
        raw_message = str(event.get("raw_message") or message or "")

    raw_message = re.sub(r"\[CQ:at,qq=[^\]]+\]", "", raw_message)
    raw_message = re.sub(r"\[CQ:image,[^\]]+\]", "", raw_message)
    return raw_message.strip()


def _parse_cq_params(segment_text: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in segment_text.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        decoded = html_unescape(value.strip())
        params[key.strip()] = unquote(decoded)
    return params


def _extract_image_sources(event: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    message = event.get("message")
    if isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "image":
                continue
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            for key in ("url", "file"):
                value = str(data.get(key) or "").strip()
                if value:
                    sources.append(value)
                    break

    raw_message = str(event.get("raw_message") or "")
    for match in re.finditer(r"\[CQ:image,([^\]]+)\]", raw_message):
        params = _parse_cq_params(match.group(1))
        value = params.get("url") or params.get("file") or ""
        if value:
            sources.append(value)

    seen: set[str] = set()
    unique_sources: list[str] = []
    for source in sources:
        if source not in seen:
            unique_sources.append(source)
            seen.add(source)
    return unique_sources


def _is_group_mention(event: dict[str, Any]) -> bool:
    if event.get("_mio_group_mentioned") is True:
        return True

    self_id = _as_id(event.get("self_id"))
    if not self_id:
        return False

    message = event.get("message")
    if isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict):
                continue
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            if segment.get("type") == "at" and _as_id(data.get("qq")) in {self_id, "all"}:
                return True
            if segment.get("type") == "text":
                text = str(data.get("text") or "")
                if _text_mentions_mio(text):
                    return True
                # 部分 NapCat 事件会把 @ 段降级成 text，并保留目标账号字段。
                at_type = _as_id(data.get("atType") or data.get("at_type"))
                target_id = _as_id(
                    data.get("qq")
                    or data.get("target_id")
                    or data.get("atUid")
                    or data.get("at_uid")
                )
                if at_type == "6" and target_id in {self_id, "all"}:
                    return True

    raw_message = str(event.get("raw_message") or "")
    return (
        f"[CQ:at,qq={self_id}]" in raw_message
        or "[CQ:at,qq=all]" in raw_message
        or _text_mentions_mio(raw_message)
    )


def _authorization_result(event: dict[str, Any]) -> tuple[bool, str]:
    if event.get("post_type") != "message":
        return False, "not_message_event"

    message_type = event.get("message_type")
    user_id = _as_id(event.get("user_id"))
    self_id = _as_id(event.get("self_id"))
    if not user_id or user_id == self_id:
        return False, "empty_or_self_user"

    allowed_users = set(settings.qq_allowed_user_ids)
    if message_type == "private":
        if not allowed_users:
            return False, "private_allowed_users_empty"
        if user_id not in allowed_users:
            return False, f"private_user_not_allowed:{user_id}"
        return True, ""

    if message_type == "group":
        group_id = _as_id(event.get("group_id"))
        if not group_is_allowed(group_id):
            return False, f"group_not_allowed:{group_id}"
        if group_mention_required() and not _is_group_mention(event):
            return False, "group_mention_required"
        return True, ""

    return False, f"unsupported_message_type:{message_type}"


def _is_authorized_message(event: dict[str, Any]) -> bool:
    return _authorization_result(event)[0]


def _record_event_debug(
    event: dict[str, Any],
    authorized: bool,
    reason: str,
    text: str = "",
    image_count: int = 0,
) -> None:
    _event_debug.update(
        {
            "received_event_count": int(_event_debug.get("received_event_count") or 0) + 1,
            "last_event_at": _debug_now(),
            "last_event_post_type": str(event.get("post_type") or ""),
            "last_event_message_type": str(event.get("message_type") or ""),
            "last_event_user_id": _as_id(event.get("user_id")),
            "last_event_self_id": _as_id(event.get("self_id")),
            "last_event_authorized": authorized,
            "last_event_ignore_reason": reason,
            "last_event_text_preview": text[:80],
            "last_event_image_count": image_count,
        }
    )


def _record_processed_message() -> None:
    _event_debug.update(
        {
            "processed_message_count": int(_event_debug.get("processed_message_count") or 0) + 1,
            "last_processed_message_at": _debug_now(),
            "last_error": "",
        }
    )


def _record_event_error(exc: Exception) -> None:
    _event_debug["last_error"] = str(exc)[:240]


def _is_today_diary_command(text: str) -> bool:
    stripped = text.strip()
    if TODAY_DIARY_EXCLUDE_RE.search(stripped):
        return False
    return TODAY_DIARY_COMMAND_RE.search(stripped) is not None


def _diary_edit_date(text: str) -> str | None:
    from .. import db

    stripped = text.strip()
    if re.search(r"日记.{0,4}(?:偏好|设定|设置|属性|风格)", stripped):
        return None
    if QUESTION_RE.search(stripped):
        return None
    if DIARY_EDIT_EXCLUDE_RE.search(stripped):
        return None
    if not DIARY_EDIT_COMMAND_RE.search(stripped):
        return None
    match = DATE_RE.search(stripped)
    if match:
        return match.group(0)
    return db.today_string()


def _is_profile_update_command(text: str) -> bool:
    stripped = text.strip()
    if QUESTION_RE.search(stripped):
        return False
    if not PROFILE_ADDRESSEE_RE.search(stripped):
        return False
    return PROFILE_UPDATE_RE.search(stripped) is not None


def _is_profile_update_followup(text: str, conversation_id: str) -> bool:
    from .. import db

    stripped = text.strip()
    if QUESTION_RE.search(stripped):
        return False
    if not re.search(r"(写进去|加进去|加入吧|记住吧|记下来|放进去|收进去|就这样|嗯，?写|嗯，?记)", stripped):
        return False
    rows = db.get_recent_messages(limit=6, conversation_id=conversation_id)
    recent = "\n".join(str(row["content"]) for row in rows)
    return re.search(r"(属性|人设|人格|设定|底层设定|底层属性|底层代码|说话方式|说话风格|语气|口吻|称呼|日记偏好)", recent) is not None


def _friendly_error_text(exc: Exception, fallback: str) -> str:
    # HTTPException 的 detail 是后端自己写的中文提示，可以直接给用户看；
    # 其他异常可能带 URL、堆栈等技术细节，只写日志，不发到 QQ。
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:100]
    return fallback


def _save_assistant_replies(conversation_id: str, replies: list[str]) -> None:
    from .. import db

    for reply in replies:
        db.save_message(
            "assistant",
            reply,
            source="qq",
            conversation_id=conversation_id,
            request_cost_yuan=0.0,
            request_cost_source="local_fallback",
        )


async def _generate_today_diary_replies() -> list[str]:
    from .chat import analyze_today_state
    from .diary import generate_today_diary_payload

    try:
        await analyze_today_state()
    except Exception:
        pass

    try:
        await generate_today_diary_payload()
    except Exception as exc:
        logger.exception("QQ 日记生成失败")
        return [f"日记没生成成功：{_friendly_error_text(exc, '我这边出了点小状况，等会儿再试一次。')}"]

    return ["生成完毕。"]


async def _edit_diary_replies(date: str, instruction: str) -> list[str]:
    from .diary import edit_diary_with_instruction

    try:
        await edit_diary_with_instruction(date, instruction)
    except Exception as exc:
        logger.exception("QQ 日记修改失败")
        return [f"日记没改成功：{_friendly_error_text(exc, '我这边出了点小状况，等会儿再试一次。')}"]

    return ["改好了。"]


def _profile_update_context(conversation_id: str, current_message: str) -> list[str]:
    from .. import db

    rows = db.get_recent_messages(limit=8, conversation_id=conversation_id)
    context = [
        f"{row['role']}: {row['content']}"
        for row in rows
        if row["role"] in {"user", "assistant"} and str(row["content"]).strip()
    ]
    if not context or context[-1] != f"user: {current_message}":
        context.append(f"user: {current_message}")
    return context[-8:]


async def _update_profile_replies(instruction: str, conversation_id: str) -> list[str]:
    from ..mio_profile import update_mio_profile_with_instruction

    try:
        context_messages = _profile_update_context(conversation_id, instruction)
        await update_mio_profile_with_instruction(instruction, context_messages)
    except Exception as exc:
        logger.exception("QQ 属性更新失败")
        return [f"属性没改成功：{_friendly_error_text(exc, '我这边出了点小状况，等会儿再试一次。')}"]

    return ["记住了。"]


def _conversation_id(event: dict[str, Any]) -> str:
    message_type = event.get("message_type")
    if message_type == "group":
        return f"qq_group_{_as_id(event.get('group_id'))}"
    return f"qq_private_{_as_id(event.get('user_id'))}"


def _event_batch_key(event: dict[str, Any]) -> str:
    conversation_id = _conversation_id(event)
    if event.get("message_type") == "group":
        return f"{conversation_id}_user_{_as_id(event.get('user_id'))}"
    return conversation_id


def _group_sender_name(event: dict[str, Any]) -> str:
    sender = event.get("sender")
    if isinstance(sender, dict):
        for key in ("card", "nickname"):
            value = str(sender.get(key) or "").strip()
            if value:
                return value
    return _as_id(event.get("user_id")) or "群成员"


def _shared_agent_route(
    user_message: str,
    *,
    history_rows: list[Any],
    image_count: int,
) -> tuple[str, str, AutoRoute | None]:
    automatic_route = select_auto_route(
        user_message,
        history_rows=history_rows,
        image_count=image_count,
    )
    shared = companion_service.load_config()
    configured_model = str(shared.get("chat_model_id") or "auto").strip()
    configured_reasoning = str(shared.get("chat_reasoning_level") or "auto").strip()
    uses_automatic_model = configured_model == "auto"
    return (
        automatic_route.model_id if uses_automatic_model else configured_model,
        automatic_route.reasoning_level if configured_reasoning == "auto" else configured_reasoning,
        automatic_route if uses_automatic_model else None,
    )


def _qq_route_metadata(route: AutoRoute | None) -> tuple[str, dict[str, object], tuple[dict[str, object], ...]]:
    if route is None:
        return "conversation", {}, ()
    task_profile = dict(getattr(route, "task_profile", {}) or {})
    return (
        str(task_profile.get("task_type") or "conversation"),
        task_profile,
        tuple(getattr(route, "candidates", ()) or ()),
    )


def _record_qq_route_failure(
    route: AutoRoute | None,
    *,
    request_id: str,
    selected_model_id: str,
    selected_reasoning_level: str,
    error_code: str,
) -> None:
    task_type, task_profile, candidates = _qq_route_metadata(route)
    route_observation_service.record_failed_route(
        source="qq",
        mode="automatic" if route is not None else "manual",
        request_id=request_id,
        selected_model_id=selected_model_id,
        selected_reasoning_level=selected_reasoning_level,
        actual_model_id=selected_model_id,
        difficulty=str(getattr(route, "difficulty", "") or "") if route is not None else "",
        reason=(
            str(getattr(route, "reason", "自动路由") or "自动路由")
            if route is not None
            else "QQ 共享设置指定模型与思考档位"
        ),
        latency_budget_ms=int(getattr(route, "latency_budget_ms", 0) or 0) if route is not None else 0,
        error_code=error_code,
        task_type=task_type,
        task_profile=task_profile,
        candidates=candidates,
    )


def _record_qq_route_success(
    route: AutoRoute | None,
    *,
    request_id: str,
    selected_model_id: str,
    selected_reasoning_level: str,
    result: object,
) -> None:
    task_type, task_profile, candidates = _qq_route_metadata(route)
    escalated_from = str(getattr(result, "route_escalated_from_model_id", "") or "")
    if route is not None and escalated_from:
        _record_qq_route_failure(
            route,
            request_id=request_id,
            selected_model_id=escalated_from,
            selected_reasoning_level=selected_reasoning_level,
            error_code="primary_model_failed_before_final_reply",
        )
    route_observation_service.record_completed_route(
        source="qq",
        mode="automatic" if route is not None else "manual",
        request_id=request_id,
        selected_model_id=selected_model_id,
        selected_reasoning_level=str(getattr(result, "reasoning_level", selected_reasoning_level) or selected_reasoning_level),
        actual_model_id=str(getattr(result, "model_id", selected_model_id) or selected_model_id),
        connection_route=str(getattr(result, "route", "") or ""),
        difficulty=str(getattr(route, "difficulty", "") or "") if route is not None else "",
        reason=(
            str(getattr(route, "reason", "自动路由") or "自动路由")
            if route is not None
            else "QQ 共享设置指定模型与思考档位"
        ),
        latency_budget_ms=int(getattr(route, "latency_budget_ms", 0) or 0) if route is not None else 0,
        first_token_latency_ms=getattr(result, "first_token_latency_ms", None),
        total_latency_ms=getattr(result, "total_latency_ms", None),
        request_cost_yuan=getattr(result, "request_cost_yuan", None),
        request_cost_source=str(getattr(result, "request_cost_source", "") or ""),
        task_type=task_type,
        task_profile=task_profile,
        candidates=candidates,
        escalated_from_model_id=escalated_from,
    )


def _send_action(event: dict[str, Any], message: str) -> dict[str, Any]:
    message_type = event.get("message_type")
    if message_type == "group":
        return {
            "action": "send_group_msg",
            "params": {
                "group_id": event.get("group_id"),
                "message": message,
            },
            "echo": f"qq-{uuid.uuid4().hex}",
        }

    return {
        "action": "send_private_msg",
        "params": {
            "user_id": event.get("user_id"),
            "message": message,
        },
        "echo": f"qq-{uuid.uuid4().hex}",
    }


def _voice_send_action(event: dict[str, Any], wav_content: bytes) -> dict[str, Any]:
    message = [{
        "type": "record",
        "data": {"file": "base64://" + base64.b64encode(wav_content).decode("ascii")},
    }]
    if event.get("message_type") == "group":
        return {
            "action": "send_group_msg",
            "params": {
                "group_id": event.get("group_id"),
                "message": message,
            },
            "echo": f"qq-{uuid.uuid4().hex}",
        }

    return {
        "action": "send_private_msg",
        "params": {
            "user_id": event.get("user_id"),
            "message": message,
        },
        "echo": f"qq-{uuid.uuid4().hex}",
    }


def _requests_voice_reply(message: str) -> bool:
    return bool(message and VOICE_REPLY_REQUEST_RE.search(message.strip()))


def _voice_reply_text(replies: list[str]) -> str:
    parts = [companion_service.clean_speech_text(clean_chat_reply(str(reply or ""))) for reply in replies]
    clean_parts = [part for part in parts if part]
    if not clean_parts:
        return "嗯，我在"
    utterances = []
    for part in clean_parts:
        check = part.rstrip("”\"’'）)")
        utterances.append(part if check.endswith(("。", "！", "？", "!", "?", "…")) else f"{part}。")
    return "".join(utterances)[:600]


def _private_user_id_param(user_id: str) -> int | str:
    return int(user_id) if user_id.isdigit() else user_id


def _private_send_action(user_id: str, message: str) -> dict[str, Any]:
    return {
        "action": "send_private_msg",
        "params": {
            "user_id": _private_user_id_param(user_id),
            "message": message,
        },
        "echo": f"qq-{uuid.uuid4().hex}",
    }


async def _send_with_lock(connection: OneBotConnection, payload: dict[str, Any]) -> dict[str, Any] | None:
    echo = _as_id(payload.get("echo"))
    last_error: Exception | None = None
    attempts = max(0, int(settings.qq_delivery_max_retries)) + 1
    for attempt in range(attempts):
        ack_future: asyncio.Future[dict[str, Any]] | None = None
        try:
            response_payload: dict[str, Any] | None = None
            if echo:
                ack_future = asyncio.get_running_loop().create_future()
                connection.pending_acks[echo] = ack_future
            async with connection.lock:
                await connection.websocket.send_json(payload)
            if ack_future is not None:
                response = await asyncio.wait_for(
                    asyncio.shield(ack_future),
                    timeout=max(1.0, float(settings.qq_delivery_ack_timeout_seconds)),
                )
                if str(response.get("status") or "ok").lower() != "ok" or int(
                    response.get("retcode") or 0
                ) != 0:
                    raise RuntimeError(str(response.get("message") or "QQ接口拒绝了消息"))
                response_payload = response
            _delivery_debug.update(
                sent_count=int(_delivery_debug["sent_count"]) + 1,
                pending_count=len(connection.pending_acks),
                last_status="sent",
                last_error="",
                last_echo=echo,
                last_at=_debug_now(),
            )
            return response_payload
        except asyncio.TimeoutError:
            # The request may have reached QQ even if its response was lost.
            # Do not blindly resend and create duplicate messages.
            _delivery_debug.update(
                failed_count=int(_delivery_debug["failed_count"]) + 1,
                pending_count=len(connection.pending_acks),
                last_status="ack_timeout",
                last_error="QQ接口确认超时，未自动重复发送",
                last_echo=echo,
                last_at=_debug_now(),
            )
            raise
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            _delivery_debug["retry_count"] = int(_delivery_debug["retry_count"]) + 1
            await asyncio.sleep(0.25 * (attempt + 1))
        finally:
            if echo and connection.pending_acks.get(echo) is ack_future:
                connection.pending_acks.pop(echo, None)
    _delivery_debug.update(
        failed_count=int(_delivery_debug["failed_count"]) + 1,
        pending_count=len(connection.pending_acks),
        last_status="failed",
        last_error=str(last_error or "QQ发送失败")[:240],
        last_echo=echo,
        last_at=_debug_now(),
    )
    raise last_error or RuntimeError("QQ发送失败")


async def _outbox_worker(connection: OneBotConnection) -> None:
    while True:
        payload, future = await connection.outbox.get()
        try:
            receipt = await _send_with_lock(connection, payload)
            if not future.done():
                future.set_result(receipt)
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
        finally:
            connection.outbox.task_done()


def _fail_connection_pending(connection: OneBotConnection, exc: BaseException) -> None:
    """让断线时所有等待中的发送立即结束，避免调用方永久挂起。"""
    for future in list(connection.pending_acks.values()):
        if not future.done():
            future.set_exception(exc)
    connection.pending_acks.clear()
    while True:
        try:
            _, future = connection.outbox.get_nowait()
        except asyncio.QueueEmpty:
            break
        if not future.done():
            future.set_exception(exc)
        connection.outbox.task_done()
    _delivery_debug.update(
        pending_count=connection.outbox.qsize() + len(connection.pending_acks),
        last_status="disconnected",
        last_error=str(exc)[:240],
        last_at=_debug_now(),
    )


async def _close_connection(
    connection_key: int,
    connection: OneBotConnection,
    reason: str,
    *,
    close_websocket: bool = True,
    close_code: int = status.WS_1001_GOING_AWAY,
) -> bool:
    registered = _active_connections.get(connection_key) is connection
    if connection.closing and not registered:
        return False
    connection.closing = True
    disconnect_error = RuntimeError(reason)
    _fail_connection_pending(connection, disconnect_error)
    if connection.debouncer is not None:
        await connection.debouncer.close()
        connection.debouncer = None
    current_task = asyncio.current_task()
    tasks = [task for task in connection.processing_tasks if task is not current_task]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    connection.processing_tasks.difference_update(tasks)
    if connection.worker is not None and connection.worker is not current_task:
        connection.worker.cancel()
        await asyncio.gather(connection.worker, return_exceptions=True)
        connection.worker = None
    if close_websocket:
        try:
            await connection.websocket.close(code=close_code, reason=reason[:120])
        except (OSError, RuntimeError):
            pass
    if _active_connections.get(connection_key) is connection:
        _active_connections.pop(connection_key, None)
    if not _active_connections:
        from .. import proactive_service

        proactive_service.note_qq_connection_state(False)
    return registered


def _resolve_delivery_ack(connection: OneBotConnection, event: dict[str, Any]) -> bool:
    """Resolve a matching OneBot API response without entering message handling."""
    echo = _as_id(event.get("echo"))
    if not echo:
        return False
    ack_future = connection.pending_acks.get(echo)
    if ack_future is None:
        return False
    if not ack_future.done():
        ack_future.set_result(event)
    return True


async def _enqueue_connection(
    connection: OneBotConnection,
    payload: dict[str, Any],
) -> Any:
    if connection.closing:
        raise ConnectionError("OneBot 连接正在关闭")
    future = asyncio.get_running_loop().create_future()
    connection.outbox.put_nowait((payload, future))
    _delivery_debug["queued_count"] = int(_delivery_debug["queued_count"]) + 1
    _delivery_debug["pending_count"] = connection.outbox.qsize() + len(connection.pending_acks)
    return await asyncio.shield(future)


async def send_private_message(user_id: str, message: str) -> bool:
    for key, connection in list(_active_connections.items()):
        try:
            await _enqueue_connection(connection, _private_send_action(user_id, message))
            return True
        except Exception as exc:
            delivery_unknown = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            reason = "QQ接口确认超时，消息可能已送达，连接已关闭且不会自动重发" if delivery_unknown else f"QQ发送失败：{exc}"
            await _close_connection(
                key,
                connection,
                reason,
                close_code=status.WS_1011_INTERNAL_ERROR,
            )
            _delivery_debug.update(
                last_status="delivery_unknown" if delivery_unknown else "failed_closed",
                last_error=reason[:240],
                last_at=_debug_now(),
            )
            return False
    return False


async def send_private_message_receipt(user_id: str, message: str) -> dict[str, Any]:
    """Send a private message and return the OneBot API acknowledgement payload."""
    for key, connection in list(_active_connections.items()):
        try:
            receipt = await _enqueue_connection(connection, _private_send_action(user_id, message))
            return {
                "sent": True,
                "acknowledged": True,
                "message_id": (receipt or {}).get("data", {}).get("message_id") if isinstance(receipt, dict) else None,
                "response": receipt or {},
            }
        except Exception as exc:
            delivery_unknown = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            reason = "QQ接口确认超时，消息可能已送达，连接已关闭且不会自动重发" if delivery_unknown else f"QQ发送失败：{exc}"
            await _close_connection(key, connection, reason, close_code=status.WS_1011_INTERNAL_ERROR)
            return {"sent": False, "acknowledged": False, "error": reason}
    return {"sent": False, "acknowledged": False, "error": "没有已连接的 OneBot 通道。"}


async def _send_replies(event: dict[str, Any], replies: list[str], send_json: SendJson) -> None:
    for index, reply in enumerate(replies):
        delay = settings.qq_reply_initial_delay_seconds if index == 0 else settings.qq_reply_delay_seconds
        if delay > 0:
            await asyncio.sleep(delay)
        await send_json(_send_action(event, reply))


async def _send_voice_replies(
    event: dict[str, Any],
    replies: list[str],
    send_json: SendJson,
    *,
    user_message: str = "",
    emotion: str = "",
    model_id: str = "",
) -> None:
    reply = _voice_reply_text(replies)
    if settings.qq_reply_initial_delay_seconds > 0:
        await asyncio.sleep(settings.qq_reply_initial_delay_seconds)
    try:
        synthesis_options: dict[str, Any] = {
            "context": user_message,
            "require_configured_engine": True,
        }
        if emotion:
            synthesis_options["emotion"] = emotion
        if model_id:
            synthesis_options["model_id"] = model_id
        wav_content = await asyncio.to_thread(
            companion_service.synthesize_speech_wav,
            reply,
            **synthesis_options,
        )
        await send_json(_voice_send_action(event, wav_content))
    except Exception:
        logger.exception("QQ 语音生成或发送失败，已退回文字消息")
        await send_json(_send_action(event, f"语音刚刚没发出来，我先打字告诉你：{reply}"))


def _debounce_seconds(message: str) -> float:
    if message and INCOMPLETE_MESSAGE_RE.search(message.strip()):
        return max(
            settings.qq_message_debounce_seconds,
            settings.qq_message_incomplete_debounce_seconds,
        )
    return max(0.0, settings.qq_message_debounce_seconds)


def _merge_onebot_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {}
    merged = dict(events[-1])
    merged["_mio_group_mentioned"] = any(_is_group_mention(event) for event in events)
    text_parts: list[str] = []
    other_segments: list[dict[str, Any]] = []
    seen_segments: set[str] = set()

    for event in events:
        text = _extract_text_message(event)
        if text:
            text_parts.append(text)
        message = event.get("message")
        if not isinstance(message, list):
            continue
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") not in {"image", "at"}:
                continue
            identity = repr(segment)
            if identity in seen_segments:
                continue
            seen_segments.add(identity)
            other_segments.append(segment)

    combined_text = "\n".join(part for part in text_parts if part).strip()
    segments: list[dict[str, Any]] = []
    if combined_text:
        segments.append({"type": "text", "data": {"text": combined_text}})
    segments.extend(other_segments)
    merged["message"] = segments
    merged["raw_message"] = combined_text
    return merged


@dataclass
class PendingEventBatch:
    events: list[dict[str, Any]]
    generation: int


class OneBotEventDebouncer:
    def __init__(self, send_json: SendJson):
        self.send_json = send_json
        self.batches: dict[str, PendingEventBatch] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    def enqueue(self, event: dict[str, Any]) -> bool:
        if event.get("post_type") != "message":
            return False

        user_message = _extract_text_message(event)
        image_sources = _extract_image_sources(event)
        authorized, ignore_reason = _authorization_result(event)
        _record_event_debug(event, authorized, ignore_reason, user_message, len(image_sources))
        if not authorized or (not user_message and not image_sources):
            return True

        key = _event_batch_key(event)
        batch = self.batches.get(key)
        generation = (batch.generation + 1) if batch else 1
        events = [*batch.events, event] if batch else [event]
        self.batches[key] = PendingEventBatch(events=events, generation=generation)

        delay = _debounce_seconds(user_message)
        task = asyncio.create_task(self._flush_after(key, generation, delay))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return True

    async def _flush_after(self, key: str, generation: int, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            batch = self.batches.get(key)
            if batch is None or batch.generation != generation:
                return
            self.batches.pop(key, None)
            merged = _merge_onebot_events(batch.events)
            await process_onebot_event(merged, self.send_json, record_debug=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _record_event_error(exc)

    async def close(self) -> None:
        tasks = list(self.tasks)
        self.batches.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def disconnect_all_connections(reason: str = "Mio 已暂停 QQ 通道") -> int:
    connections = list(_active_connections.items())
    for connection_key, connection in connections:
        await _close_connection(connection_key, connection, reason)
    return len(connections)


async def _process_group_event(
    event: dict[str, Any],
    user_message: str,
    image_sources: list[str],
    send_json: SendJson,
) -> bool:
    group_id = _as_id(event.get("group_id"))
    sender_name = _group_sender_name(event)
    explicit_voice_request = _requests_voice_reply(user_message)
    voice_reply_requested = companion_service.should_use_qq_voice(
        user_message,
        explicitly_requested=explicit_voice_request,
    )
    image_attachments = []
    if image_sources:
        if not settings.qq_image_enabled:
            replies = ["这张图我现在看不了"]
            await _send_replies(event, replies, send_json)
            append_group_exchange(group_id, sender_name, user_message or "[图片]", replies)
            _record_processed_message()
            return True
        image_attachments, image_errors = await load_image_attachments(image_sources)
        if not image_attachments:
            message = image_errors[0] if image_errors else "这张图好像没传过来"
            replies = [message]
            await _send_replies(event, replies, send_json)
            append_group_exchange(group_id, sender_name, user_message or "[图片]", replies)
            _record_processed_message()
            return True

    history = get_group_history(group_id)
    try:
        selected_model, selected_reasoning, automatic_route = _shared_agent_route(
            user_message,
            history_rows=history,
            image_count=len(image_attachments),
        )
        route_request_id = f"qq-group-{uuid.uuid4().hex}"
        result = await chat_in_qq_group(
            user_message,
            sender_name=sender_name,
            history=history,
            conversation_id=f"qq_group_{group_id}",
            image_attachments=image_attachments,
            reasoning_level=selected_reasoning,
            model_id=selected_model,
            fallback_model_id=str(getattr(automatic_route, "fallback_model_id", "") or ""),
            fallback_reasoning_level=str(getattr(automatic_route, "fallback_reasoning_level", "") or ""),
            voice_reply_requested=voice_reply_requested,
            capture_follow_ups=True,
            request_id=route_request_id,
        )
        replies = result.replies
        _record_qq_route_success(
            automatic_route,
            request_id=route_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            result=result,
        )
    except LLMConfigError as exc:
        if "route_request_id" in locals():
            _record_qq_route_failure(
                automatic_route if "automatic_route" in locals() else None,
                request_id=route_request_id,
                selected_model_id=selected_model,
                selected_reasoning_level=selected_reasoning,
                error_code="model_not_configured",
            )
        logger.error("群聊 LLM 配置错误：%s", exc)
        replies = ["我这边现在还说不了话，等设置好模型再叫我吧"]
    except Exception:
        if "route_request_id" in locals():
            _record_qq_route_failure(
                automatic_route if "automatic_route" in locals() else None,
                request_id=route_request_id,
                selected_model_id=selected_model,
                selected_reasoning_level=selected_reasoning,
                error_code="model_request_failed",
            )
        logger.exception("QQ群聊处理失败：group_id=%s", group_id)
        replies = ["我刚刚卡了一下，你再叫我一次？"]

    if voice_reply_requested:
        await _send_voice_replies(
            event,
            replies,
            send_json,
            user_message=user_message,
            emotion=result.speech_emotion if "result" in locals() else "",
            model_id=result.model_id if "result" in locals() and result.model_id else selected_model,
        )
    else:
        await _send_replies(event, replies, send_json)
    append_group_exchange(
        group_id,
        sender_name,
        user_message or ("[图片]" if image_sources else ""),
        replies,
    )
    _record_processed_message()
    return True


async def process_onebot_event(
    event: dict[str, Any],
    send_json: SendJson,
    record_debug: bool = True,
) -> bool:
    user_message = _extract_text_message(event)
    image_sources = _extract_image_sources(event)
    authorized, ignore_reason = _authorization_result(event)
    if record_debug:
        _record_event_debug(event, authorized, ignore_reason, user_message, len(image_sources))
    if not authorized:
        return False

    if not user_message and not image_sources:
        return False

    if event.get("message_type") == "group":
        return await _process_group_event(event, user_message, image_sources, send_json)

    conversation_id = _conversation_id(event)

    if user_message and not image_sources and _is_today_diary_command(user_message):
        from .. import db

        # 先把整条消息存进聊天记录（含命令前的日常内容），生成日记时才能看到；
        # “生成完毕”等确认语在生成之后再存，不会混进当天日记。
        db.save_message("user", user_message, source="qq", conversation_id=conversation_id)
        replies = await _generate_today_diary_replies()
        await _send_replies(event, replies, send_json)
        _save_assistant_replies(conversation_id, replies)
        _record_processed_message()
        return True

    diary_edit_date = _diary_edit_date(user_message) if user_message and not image_sources else None
    if diary_edit_date:
        from .. import db

        db.save_message("user", user_message, source="qq", conversation_id=conversation_id)
        replies = await _edit_diary_replies(diary_edit_date, user_message)
        await _send_replies(event, replies, send_json)
        _save_assistant_replies(conversation_id, replies)
        _record_processed_message()
        return True

    if (
        user_message
        and not image_sources
        and (_is_profile_update_command(user_message) or _is_profile_update_followup(user_message, conversation_id))
    ):
        from .. import db

        db.save_message("user", user_message, source="qq", conversation_id=conversation_id)
        replies = await _update_profile_replies(user_message, conversation_id)
        await _send_replies(event, replies, send_json)
        _save_assistant_replies(conversation_id, replies)
        _record_processed_message()
        return True

    image_attachments = []
    if image_sources:
        if not settings.qq_image_enabled:
            await _send_replies(event, ["我收到图片了，但现在看图功能还没开。"], send_json)
            return True
        image_attachments, image_errors = await load_image_attachments(image_sources)
        if not image_attachments:
            message = image_errors[0] if image_errors else "图片好像没传到我这边，你重新发一次试试？"
            await _send_replies(event, [message], send_json)
            return True
        try:
            archive_image_attachments(image_attachments)
        except Exception:
            logger.exception("照片存档失败")

    try:
        explicit_voice_request = _requests_voice_reply(user_message)
        voice_reply_requested = companion_service.should_use_qq_voice(
            user_message,
            explicitly_requested=explicit_voice_request,
        )
        selected_model, selected_reasoning, automatic_route = _shared_agent_route(
            user_message,
            history_rows=database.get_recent_messages(limit=12, conversation_id=conversation_id),
            image_count=len(image_attachments),
        )
        route_request_id = f"qq-{uuid.uuid4().hex}"
        result = await chat_with_ai(
            user_message,
            conversation_id=conversation_id,
            source="qq",
            image_attachments=image_attachments,
            reasoning_level=selected_reasoning,
            model_id=selected_model,
            fallback_model_id=str(getattr(automatic_route, "fallback_model_id", "") or ""),
            fallback_reasoning_level=str(getattr(automatic_route, "fallback_reasoning_level", "") or ""),
            voice_reply_requested=voice_reply_requested,
            request_id=route_request_id,
        )
        replies = result.replies
        _record_qq_route_success(
            automatic_route,
            request_id=route_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            result=result,
        )
    except LLMConfigError as exc:
        if "route_request_id" in locals():
            _record_qq_route_failure(
                automatic_route if "automatic_route" in locals() else None,
                request_id=route_request_id,
                selected_model_id=selected_model,
                selected_reasoning_level=selected_reasoning,
                error_code="model_not_configured",
            )
        logger.error("LLM 配置错误：%s", exc)
        replies = ["我这边模型配置好像还没弄好，先帮我看一眼后台设置吧。"]
        _save_assistant_replies(conversation_id, replies)
    except Exception:
        if "route_request_id" in locals():
            _record_qq_route_failure(
                automatic_route if "automatic_route" in locals() else None,
                request_id=route_request_id,
                selected_model_id=selected_model,
                selected_reasoning_level=selected_reasoning,
                error_code="model_request_failed",
            )
        logger.exception("QQ 对话处理失败")
        if image_sources:
            replies = ["我收到图了，但这边刚刚没看成。", "你等一会儿再发我一次试试？"]
        else:
            replies = ["呜，我这边刚刚卡了一下。", "你再说一遍好不好？"]
        _save_assistant_replies(conversation_id, replies)

    if voice_reply_requested:
        await _send_voice_replies(
            event,
            replies,
            send_json,
            user_message=user_message,
            emotion=result.speech_emotion if "result" in locals() else "",
            model_id=result.model_id if "result" in locals() and result.model_id else selected_model,
        )
    else:
        await _send_replies(event, replies, send_json)
    _record_processed_message()
    return True


def _token_matches(websocket: WebSocket) -> bool:
    token = settings.qq_onebot_token
    if not token:
        return True

    auth = websocket.headers.get("authorization", "").strip()
    access_token = websocket.query_params.get("access_token", "").strip()
    return auth in {token, f"Bearer {token}", f"Token {token}"} or access_token == token


def _request_token_matches(request: Request) -> bool:
    token = settings.qq_onebot_token
    if not token:
        return True
    auth = request.headers.get("authorization", "").strip()
    access_token = request.query_params.get("access_token", "").strip()
    return auth in {token, f"Bearer {token}", f"Token {token}"} or access_token == token


@router.websocket("/onebot/ws")
async def onebot_ws(websocket: WebSocket):
    if not settings.qq_bot_enabled or not _token_matches(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    connection_key = id(websocket)
    connection = OneBotConnection(websocket=websocket, lock=asyncio.Lock())
    _active_connections[connection_key] = connection
    # QQ 断线重连后，主动消息不再依赖下一轮固定轮询。
    from .. import proactive_service

    proactive_service.note_qq_connection_state(True)

    async def send_json(payload: dict[str, Any]) -> None:
        await _enqueue_connection(connection, payload)

    debouncer = OneBotEventDebouncer(send_json)
    connection.debouncer = debouncer
    connection.worker = asyncio.create_task(_outbox_worker(connection))

    async def process_event(event: dict[str, Any]) -> None:
        try:
            await process_onebot_event(event, send_json)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _record_event_error(exc)
            logger.exception("OneBot 入站事件处理失败")

    def schedule_event(event: dict[str, Any]) -> None:
        if connection.closing:
            return
        task = asyncio.create_task(process_event(event))
        connection.processing_tasks.add(task)
        task.add_done_callback(connection.processing_tasks.discard)

    try:
        while True:
            event = await websocket.receive_json()
            if connection.closing:
                break
            if isinstance(event, dict):
                try:
                    event_self_id = _as_id(event.get("self_id"))
                    if event_self_id:
                        connection.self_id = event_self_id
                    # OneBot API 响应带有 echo。它不是 QQ 入站消息，交给对应发送任务即可。
                    if _resolve_delivery_ack(connection, event):
                        continue
                    if not debouncer.enqueue(event):
                        schedule_event(event)
                except Exception as exc:
                    _record_event_error(exc)
    except WebSocketDisconnect:
        return
    finally:
        await _close_connection(
            connection_key,
            connection,
            "OneBot WebSocket 已断开",
            close_websocket=False,
        )


@router.post("/onebot/debug/send-test")
async def onebot_debug_send_test(request: Request):
    if not _request_token_matches(request):
        raise HTTPException(status_code=403, detail="OneBot token mismatch")
    if not settings.qq_allowed_user_ids:
        raise HTTPException(status_code=400, detail="QQ_ALLOWED_USER_IDS is empty")

    user_id = settings.qq_allowed_user_ids[0]
    sent = await send_private_message(user_id, "Mio 链路测试：后端到 QQ 发送正常。")
    return {
        "target_user_id": user_id,
        "sent": sent,
        "websocket_connections": active_websocket_count(),
        "connected_accounts": connected_self_ids(),
    }


@router.get("/onebot/status")
async def onebot_status():
    from .. import proactive_service

    group_status = public_group_status()
    return {
        "enabled": settings.qq_bot_enabled,
        "token_set": bool(settings.qq_onebot_token),
        "allowed_user_count": len(settings.qq_allowed_user_ids),
        "allowed_group_count": len(group_status["group_ids"]),
        "group_mention_required": group_status["mention_required"],
        "group_chat": group_status,
        "image_enabled": settings.qq_image_enabled,
        "image_max_count": settings.qq_image_max_count,
        "image_max_bytes": settings.qq_image_max_bytes,
        "image_detail": settings.qq_image_detail,
        "image_send_to_model": settings.qq_image_send_to_model,
        "message_debounce_seconds": settings.qq_message_debounce_seconds,
        "message_incomplete_debounce_seconds": settings.qq_message_incomplete_debounce_seconds,
        "reply_initial_delay_seconds": settings.qq_reply_initial_delay_seconds,
        "reply_delay_seconds": settings.qq_reply_delay_seconds,
        "proactive_enabled": settings.qq_proactive_enabled,
        "proactive_min_idle_minutes": settings.qq_proactive_min_idle_minutes,
        "proactive_max_idle_minutes": settings.qq_proactive_max_idle_minutes,
        "proactive_day_start_hour": settings.qq_proactive_day_start_hour,
        "proactive_day_end_hour": settings.qq_proactive_day_end_hour,
        "proactive_check_seconds": settings.qq_proactive_check_seconds,
        "web_search_enabled": settings.web_search_enabled,
        "web_search_max_results": settings.web_search_max_results,
        "websocket_connections": active_websocket_count(),
        "delivery": {
            **_delivery_debug,
            "active_queue_count": sum(connection.outbox.qsize() for connection in _active_connections.values()),
            "active_pending_ack_count": sum(
                len(connection.pending_acks) for connection in _active_connections.values()
            ),
        },
        "websocket_url": f"ws://127.0.0.1:{settings.app_port}/onebot/ws",
        "runtime_traces": await runtime_traces.summary(),
        "proactive_runtime": proactive_service.get_proactive_status(),
        "event_debug": dict(_event_debug),
    }
