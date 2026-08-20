from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .chat_service import (
    ChatResult,
    LLMConfigError,
    generate_desktop_startup_replies,
    generate_qq_night_close_replies,
    generate_qq_proactive_replies,
)
from .config import settings
from .cost_reconciliation_service import queue_cost_reconciliation
from .napcat_service import (
    get_napcat_login_status,
    napcat_auto_recovery_allowed,
    run_napcat_control,
)
from .routes.onebot import active_websocket_count, send_private_message


logger = logging.getLogger(__name__)
_startup_greeting_lock = asyncio.Lock()
_startup_greeting_sent = False
_wake_event: asyncio.Event | None = None
_connection_is_connected: bool | None = None
_missing_connection_since: datetime | None = None
_last_recovery_attempt_at: datetime | None = None
_desktop_app_active_at: datetime | None = None
_last_check: dict[str, object] = {
    "checked_at": "",
    "result": "not_checked",
    "sent_count": 0,
    "websocket_connections": 0,
    "connection_result": "not_checked",
    "recovery_attempted_at": "",
    "recovery_error": "",
    "error": "",
    "schedule_user_id": "",
    "last_user_message_at": "",
    "next_prompt_at": "",
    "last_prompt_at": "",
    "skip_reason": "",
}


def _get_wake_event() -> asyncio.Event:
    global _wake_event
    if _wake_event is None:
        _wake_event = asyncio.Event()
    return _wake_event


def wake_proactive_loop() -> None:
    """让 QQ 重连后立即触发一次检查，不必等待下一轮五分钟轮询。"""
    event = _wake_event
    if event is not None:
        event.set()


async def maintain_qq_connection_once(now: datetime | None = None) -> bool:
    """Keep the QQ transport healthy without deciding or sending proactive content."""
    return await _maybe_recover_qq_connection(now)


async def wait_for_proactive_wake(timeout_seconds: float) -> None:
    """Wait until state changes or the next scheduled autonomy poll is due."""
    event = _get_wake_event()
    try:
        await asyncio.wait_for(event.wait(), timeout=max(1.0, float(timeout_seconds)))
    except asyncio.TimeoutError:
        pass
    finally:
        event.clear()


def note_desktop_app_active(now: datetime | None = None) -> None:
    """记录 Agent 主窗口仍在运行；从离线恢复时立即唤醒主动消息检查。"""
    global _desktop_app_active_at

    current = now or _now()
    was_active = desktop_app_is_active(current)
    _desktop_app_active_at = current
    if not was_active:
        try:
            from .autonomy_service import record_application_activity_event

            record_application_activity_event(current)
        except Exception:
            logger.exception("记录应用活动事件失败")
        wake_proactive_loop()


def desktop_app_is_active(now: datetime | None = None) -> bool:
    current = now or _now()
    if _desktop_app_active_at is None:
        return False
    return current - _desktop_app_active_at <= timedelta(seconds=30)


def note_qq_connection_state(connected: bool) -> None:
    global _connection_is_connected, _missing_connection_since

    current = _now()
    changed = _connection_is_connected is not connected
    _connection_is_connected = connected
    if connected:
        _missing_connection_since = None
        _last_check.update(
            websocket_connections=active_websocket_count(),
            connection_result="connected",
            recovery_error="",
        )
    elif _missing_connection_since is None:
        _missing_connection_since = current
        _last_check.update(
            websocket_connections=0,
            connection_result="waiting_for_reconnect",
        )
    if changed:
        wake_proactive_loop()


def get_proactive_status() -> dict[str, object]:
    result = dict(_last_check)
    result.update(
        app_active=desktop_app_is_active(),
        app_last_seen_at=_to_iso(_desktop_app_active_at) if _desktop_app_active_at else "",
    )
    return result


def note_manual_qq_control_result(action: str, connected: bool = False) -> None:
    """手动控制成功后清除上一次自动恢复失败的残留提示。"""
    if action == "stop":
        connection_result = "manually_stopped"
    elif connected:
        connection_result = "connected"
    else:
        connection_result = "start_sent"
    _last_check.update(
        connection_result=connection_result,
        websocket_connections=active_websocket_count() if connected else 0,
        recovery_error="",
        error="",
    )


async def _maybe_recover_qq_connection(now: datetime | None = None) -> bool:
    global _missing_connection_since, _last_recovery_attempt_at

    current = now or _now()
    connection_count = active_websocket_count()
    if connection_count > 0:
        note_qq_connection_state(True)
        return False
    if not settings.qq_bot_enabled:
        _last_check.update(connection_result="disabled", websocket_connections=0)
        return False
    if not napcat_auto_recovery_allowed():
        _last_check.update(connection_result="manually_stopped", websocket_connections=0)
        return False
    from . import companion_service

    if not companion_service.load_config().get("qq_startup_enabled", False):
        _last_check.update(
            connection_result="startup_disabled",
            websocket_connections=0,
            recovery_error="",
        )
        return False
    if _missing_connection_since is None:
        _missing_connection_since = current
    if current - _missing_connection_since < timedelta(seconds=45):
        _last_check.update(connection_result="waiting_for_reconnect", websocket_connections=0)
        return False
    if (
        _last_recovery_attempt_at is not None
        and current - _last_recovery_attempt_at < timedelta(minutes=10)
    ):
        _last_check.update(connection_result="recovery_cooldown", websocket_connections=0)
        return False

    _last_recovery_attempt_at = current
    _last_check.update(
        recovery_attempted_at=current.isoformat(timespec="seconds"),
        recovery_error="",
    )
    login_status = await get_napcat_login_status(cache_seconds=0, websocket_connected=False)
    if not login_status.get("login_checked"):
        # WebUI 不可达通常意味着 NapCat 进程已经退出。此时无法检查登录态，
        # 但只要本地安装和配置完整，仍然可以先启动 NapCat，让下一轮检查接管登录判断。
        diagnostic_code = str(login_status.get("diagnostic_code") or "")
        if diagnostic_code in {"napcat_process_stopped", "qq_process_stopped", "webui_unreachable"}:
            action = "restart" if diagnostic_code == "qq_process_stopped" else "start"
            _last_check.update(
                connection_result="starting",
                websocket_connections=0,
                recovery_error="",
            )
            try:
                result = await asyncio.to_thread(run_napcat_control, action)
            except (OSError, RuntimeError, ValueError) as exc:
                _last_check.update(connection_result="recovery_failed", recovery_error=str(exc))
                logger.exception("NapCat 启动恢复失败")
                return False
            if not result.get("ok"):
                error = str(result.get("output") or "NapCat 启动失败")[-500:]
                _last_check.update(connection_result="recovery_failed", recovery_error=error)
                logger.error("NapCat 启动恢复失败：%s", error)
                return False
            connection_count = active_websocket_count()
            connected = connection_count > 0
            _last_check.update(
                connection_result="connected" if connected else "start_sent",
                websocket_connections=connection_count,
            )
            logger.info("NapCat %s 恢复完成：connected=%s", action, connected)
            return connected
        diagnostic = str(login_status.get("diagnostic_message") or "NapCat状态不可用")
        _last_check.update(
            connection_result=str(login_status.get("diagnostic_code") or "napcat_unavailable"),
            websocket_connections=0,
            recovery_error=diagnostic,
        )
        return False
    if not login_status.get("logged_in"):
        _last_check.update(
            connection_result="login_required",
            websocket_connections=0,
            recovery_error=str(login_status.get("diagnostic_message") or "QQ账号尚未登录"),
        )
        return False

    _last_check.update(
        connection_result="recovering",
        recovery_attempted_at=current.isoformat(timespec="seconds"),
        recovery_error="",
    )
    try:
        result = await asyncio.to_thread(run_napcat_control, "restart")
    except (OSError, RuntimeError, ValueError) as exc:
        _last_check.update(connection_result="recovery_failed", recovery_error=str(exc))
        logger.exception("QQ 通道自动恢复失败")
        return False
    if not result.get("ok"):
        error = str(result.get("output") or "NapCat 重启失败")[-500:]
        _last_check.update(connection_result="recovery_failed", recovery_error=error)
        logger.error("QQ 通道自动恢复失败：%s", error)
        return False
    connection_count = active_websocket_count()
    connected = connection_count > 0
    _last_check.update(
        connection_result="connected" if connected else "restart_sent",
        websocket_connections=connection_count,
    )
    logger.info("QQ 通道自动恢复完成：connected=%s", connected)
    return connected


async def start_qq_on_app_startup() -> bool:
    """按本地开关在 Agent 启动时拉起 QQ；关闭时不影响手动控制。"""
    from . import companion_service

    if not companion_service.load_config().get("qq_startup_enabled", False):
        _last_check.update(connection_result="startup_disabled", recovery_error="")
        return False

    await asyncio.sleep(1)
    status = await get_napcat_login_status(cache_seconds=0, websocket_connected=False)
    connection_count = active_websocket_count()
    if connection_count > 0:
        _last_check.update(
            connection_result="connected",
            websocket_connections=connection_count,
            recovery_error="",
        )
        return False

    napcat_running = bool(status.get("napcat_process_running"))
    qq_running = bool(status.get("qq_process_running"))
    if napcat_running and qq_running:
        _last_check.update(
            connection_result="already_running",
            websocket_connections=0,
            recovery_error="",
        )
        return False

    action = "restart" if napcat_running else "start"

    try:
        result = await asyncio.to_thread(run_napcat_control, action)
    except (OSError, RuntimeError, ValueError) as exc:
        _last_check.update(connection_result="startup_failed", recovery_error=str(exc))
        logger.exception("QQ 开机启动失败")
        return False
    if not result.get("ok"):
        error = str(result.get("output") or "QQ 开机启动失败")[-500:]
        _last_check.update(connection_result="startup_failed", recovery_error=error)
        return False
    _last_check.update(
        connection_result="restart_sent" if action == "restart" else "start_sent",
        recovery_error="",
    )
    logger.info("QQ 通道已按开机启动设置执行 %s", action)
    return True


def _now() -> datetime:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(tz)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_now().tzinfo)
    return parsed


def _to_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _is_daytime(now: datetime) -> bool:
    start = settings.qq_proactive_day_start_hour
    end = settings.qq_proactive_day_end_hour
    current = now.hour + now.minute / 60
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _idle_delta() -> timedelta:
    min_minutes = max(1, settings.qq_proactive_min_idle_minutes)
    max_minutes = max(min_minutes, settings.qq_proactive_max_idle_minutes)
    return timedelta(minutes=random.randint(min_minutes, max_minutes))


def _fallback_replies() -> list[str]:
    return ["你还在吗？", "我来轻轻戳一下。"]


def _local_result(replies: list[str]) -> ChatResult:
    return ChatResult(
        reply="\n\n".join(replies),
        replies=replies,
        request_cost_yuan=0.0,
        request_cost_source="local_fallback",
    )


def _topic_key(kind: str, text: str) -> str:
    normalized = re.sub(r"\s+", "", str(text or "").casefold())
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


def plan_proactive_topic(
    conversation_id: str,
    *,
    due_threads: list[str] | None = None,
) -> dict[str, object]:
    """Pick one useful topic locally so proactive prompts do not chase one point repeatedly."""
    candidates: list[dict[str, object]] = []
    for index, content in enumerate(due_threads or []):
        clean = " ".join(str(content or "").split()).strip()[:300]
        if clean:
            candidates.append(
                {"kind": "due_thread", "text": clean, "score": 100.0 - index * 4}
            )

    state = db.get_daily_state()
    if state is not None:
        next_action = " ".join(str(state["next_min_action"] or "").split()).strip()
        key_events = " ".join(str(state["key_events"] or "").split()).strip()
        if next_action and next_action != "未确认":
            candidates.append({"kind": "next_action", "text": next_action[:300], "score": 72.0})
        if key_events and key_events != "未确认":
            candidates.append({"kind": "today_event", "text": key_events[:300], "score": 58.0})

    history_rows = db.get_recent_messages(limit=16, conversation_id=conversation_id)
    recent_user_rows = [row for row in history_rows if row["role"] == "user"][-5:]
    for index, row in enumerate(reversed(recent_user_rows)):
        text = " ".join(str(row["content"] or "").split()).strip()[:300]
        if len(text) < 5 or re.fullmatch(r"(?:在吗|早|早上好|晚安|你好|嗯+|哦+|好+)[？?！!。 ]*", text):
            continue
        candidates.append({"kind": "recent_message", "text": text, "score": 64.0 - index * 6})

    recent_topics = db.list_recent_proactive_topics(conversation_id, limit=12)
    used_keys = {str(row["topic_key"] or "") for row in recent_topics}
    used_texts = [re.sub(r"\s+", "", str(row["topic_text"] or "").casefold()) for row in recent_topics[:5]]
    for candidate in candidates:
        key = _topic_key(str(candidate["kind"]), str(candidate["text"]))
        compact = re.sub(r"\s+", "", str(candidate["text"]).casefold())
        penalty = 85.0 if key in used_keys else 0.0
        if any(compact in previous or previous in compact for previous in used_texts if compact and previous):
            penalty = max(penalty, 55.0)
        candidate["key"] = key
        candidate["score"] = float(candidate["score"]) - penalty

    if candidates:
        candidates.sort(key=lambda item: float(item["score"]), reverse=True)
        selected = candidates[0]
        if float(selected["score"]) >= 20:
            return selected
    return {
        "kind": "light_check_in",
        "text": "轻松问候，不强行追问旧事",
        "score": 20.0,
        "key": _topic_key("light_check_in", db.today_string()),
    }


async def _deliver_replies(user_id: str, conversation_id: str, result: ChatResult) -> bool:
    delivered = False
    for index, reply in enumerate(result.replies):
        if index > 0 and settings.qq_reply_delay_seconds > 0:
            await asyncio.sleep(settings.qq_reply_delay_seconds)
        qq_sent = await send_private_message(user_id, reply)
        if not qq_sent:
            logger.info("QQ 不在线，主动消息未送达且不写入会话：user_id=%s", user_id)
            break
        db.save_message(
            "assistant",
            reply,
            source="proactive",
            conversation_id=conversation_id,
            request_id=result.request_id,
            model_id=result.model_id,
            reasoning_level=result.reasoning_level,
            prompt_tokens=result.prompt_tokens if index == 0 else 0,
            cached_prompt_tokens=result.cached_prompt_tokens if index == 0 else 0,
            completion_tokens=result.completion_tokens if index == 0 else 0,
            reasoning_tokens=result.reasoning_tokens if index == 0 else 0,
            request_cost_yuan=result.request_cost_yuan if index == 0 else 0.0,
            request_cost_source=result.request_cost_source if index == 0 else "shared_request",
        )
        logger.info("主动消息已送达并写入应用：user_id=%s", user_id)
        delivered = True
    if delivered:
        queue_cost_reconciliation(result.request_id, conversation_id, result.cost_references)
    return delivered


async def _deliver_proactive_replies(
    user_id: str,
    conversation_id: str,
    result: ChatResult,
) -> bool:
    """应用是主投递通道；QQ 在线时再同步同一组消息。"""
    qq_connected = active_websocket_count() > 0
    qq_delivery_failed = False
    for index, reply in enumerate(result.replies):
        db.save_message(
            "assistant",
            reply,
            source="proactive",
            conversation_id=conversation_id,
            request_id=result.request_id,
            model_id=result.model_id,
            reasoning_level=result.reasoning_level,
            prompt_tokens=result.prompt_tokens if index == 0 else 0,
            cached_prompt_tokens=result.cached_prompt_tokens if index == 0 else 0,
            completion_tokens=result.completion_tokens if index == 0 else 0,
            reasoning_tokens=result.reasoning_tokens if index == 0 else 0,
            request_cost_yuan=result.request_cost_yuan if index == 0 else 0.0,
            request_cost_source=result.request_cost_source if index == 0 else "shared_request",
        )
        if not qq_connected or qq_delivery_failed:
            continue
        if index > 0 and settings.qq_reply_delay_seconds > 0:
            await asyncio.sleep(settings.qq_reply_delay_seconds)
        if not await send_private_message(user_id, reply):
            qq_delivery_failed = True
            logger.info("主动消息已写入应用，但 QQ 同步失败：user_id=%s", user_id)

    queue_cost_reconciliation(result.request_id, conversation_id, result.cost_references)
    _last_check.update(
        delivery_result=(
            "app_and_qq"
            if qq_connected and not qq_delivery_failed
            else "app_only_qq_failed"
            if qq_delivery_failed
            else "app_only"
        )
    )
    logger.info("主动消息已写入应用：user_id=%s qq_connected=%s", user_id, qq_connected)
    return True


def _defer_proactive_messages_after_startup(now: datetime) -> None:
    for user_id in settings.qq_allowed_user_ids:
        conversation_id = f"qq_private_{user_id}"
        last_user_message = db.get_last_message(conversation_id=conversation_id, role="user")
        if last_user_message is None:
            continue
        last_user_message_at = str(last_user_message["created_at"])
        state = db.get_qq_proactive_state(user_id)
        last_prompt_at = str(state["last_prompt_at"] or "") if state else ""
        existing_next = _parse_iso(str(state["next_prompt_at"] or "")) if state else None
        next_prompt_at = existing_next if existing_next and existing_next > now else now + _idle_delta()
        db.upsert_qq_proactive_state(
            user_id,
            last_user_message_at,
            _to_iso(next_prompt_at),
            last_prompt_at,
        )


async def run_desktop_startup_greeting_once(
    conversation_id: str,
    now: datetime | None = None,
) -> list[str]:
    global _startup_greeting_sent

    async with _startup_greeting_lock:
        if _startup_greeting_sent:
            return []
        _startup_greeting_sent = True
        current = now or _now()
        _defer_proactive_messages_after_startup(current)
        try:
            result = await generate_desktop_startup_replies(conversation_id)
        except (LLMConfigError, RuntimeError):
            result = _local_result(["我在。"])
        for index, reply in enumerate(result.replies):
            db.save_message(
                "assistant",
                reply,
                source="startup",
                conversation_id=conversation_id,
                request_id=result.request_id,
                model_id=result.model_id,
                reasoning_level=result.reasoning_level,
                prompt_tokens=result.prompt_tokens if index == 0 else 0,
                cached_prompt_tokens=result.cached_prompt_tokens if index == 0 else 0,
                completion_tokens=result.completion_tokens if index == 0 else 0,
                reasoning_tokens=result.reasoning_tokens if index == 0 else 0,
                request_cost_yuan=result.request_cost_yuan if index == 0 else 0.0,
                request_cost_source=result.request_cost_source if index == 0 else "shared_request",
            )
        queue_cost_reconciliation(result.request_id, conversation_id, result.cost_references)
        logger.info("桌面启动开场已写入应用：conversation_id=%s", conversation_id)
        return result.replies


async def _maybe_send_to_user(user_id: str, now: datetime) -> bool:
    conversation_id = f"qq_private_{user_id}"
    state = db.get_qq_proactive_state(user_id)
    last_user_message = db.get_last_message(conversation_id=conversation_id, role="user")
    _last_check.update(
        schedule_user_id=user_id,
        last_user_message_at=str(last_user_message["created_at"]) if last_user_message else "",
        next_prompt_at=str(state["next_prompt_at"] or "") if state else "",
        last_prompt_at=str(state["last_prompt_at"] or "") if state else "",
        skip_reason="",
    )
    if last_user_message is None:
        _last_check.update(skip_reason="no_user_message")
        return False

    last_user_message_at_text = str(last_user_message["created_at"])
    last_user_at = _parse_iso(last_user_message_at_text)
    if last_user_at is None:
        _last_check.update(skip_reason="invalid_message_time")
        return False

    due_threads = db.list_due_pending_threads(conversation_id, _to_iso(now), limit=3)
    if state is None or state["last_user_message_at"] != last_user_message_at_text:
        next_prompt_at = last_user_at + _idle_delta()
        last_prompt_at = str(state["last_prompt_at"]) if state else ""
        db.upsert_qq_proactive_state(user_id, last_user_message_at_text, _to_iso(next_prompt_at), last_prompt_at)
    else:
        next_prompt_at = _parse_iso(str(state["next_prompt_at"])) or (last_user_at + _idle_delta())
        last_prompt_at = str(state["last_prompt_at"])

    _last_check.update(
        last_user_message_at=last_user_message_at_text,
        next_prompt_at=_to_iso(next_prompt_at),
        last_prompt_at=last_prompt_at,
    )

    if now < next_prompt_at and not due_threads:
        _last_check.update(skip_reason="waiting_for_idle")
        return False

    idle_minutes = int((now - last_user_at).total_seconds() // 60)
    minimum_idle = 30 if due_threads else settings.qq_proactive_min_idle_minutes
    if idle_minutes < minimum_idle:
        next_prompt_at = last_user_at + _idle_delta()
        db.upsert_qq_proactive_state(user_id, last_user_message_at_text, _to_iso(next_prompt_at), last_prompt_at)
        _last_check.update(next_prompt_at=_to_iso(next_prompt_at), skip_reason="idle_below_minimum")
        return False

    topic_plan = {
        "kind": "light_check_in",
        "text": "轻松问候，不强行追问旧事",
        "score": 20.0,
        "key": _topic_key("light_check_in", db.today_string()),
    }
    try:
        topic_plan = plan_proactive_topic(
            conversation_id,
            due_threads=[str(row["content"]) for row in due_threads],
        )
        result = await generate_qq_proactive_replies(
            conversation_id,
            idle_minutes,
            due_threads=[str(row["content"]) for row in due_threads],
            topic_plan=topic_plan,
        )
    except (LLMConfigError, RuntimeError):
        result = _local_result(_fallback_replies())

    sent_any = await _deliver_proactive_replies(user_id, conversation_id, result)

    if sent_any:
        db.record_proactive_topic(
            conversation_id,
            str(topic_plan.get("key") or ""),
            str(topic_plan.get("kind") or "light_check_in"),
            str(topic_plan.get("text") or ""),
            float(topic_plan.get("score") or 0.0),
        )
        _last_check.update(topic_plan=topic_plan)
        for thread in due_threads:
            db.mark_pending_thread_mentioned(int(thread["id"]))
        db.upsert_qq_proactive_state(
            user_id,
            last_user_message_at_text,
            _to_iso(now + _idle_delta()),
            _to_iso(now),
        )
        _last_check.update(
            next_prompt_at=str(db.get_qq_proactive_state(user_id)["next_prompt_at"]),
            last_prompt_at=_to_iso(now),
            skip_reason="",
        )
    else:
        _last_check.update(skip_reason="qq_send_failed")

    return sent_any


async def run_proactive_once(now: datetime | None = None) -> int:
    current = now or _now()
    _last_check.update(
        checked_at=current.isoformat(timespec="seconds"),
        websocket_connections=active_websocket_count(),
        error="",
    )
    if not settings.qq_proactive_enabled:
        _last_check.update(result="disabled", sent_count=0)
        return 0
    if not desktop_app_is_active(current):
        _last_check.update(result="app_inactive", sent_count=0, skip_reason="app_inactive")
        return 0
    if not settings.qq_allowed_user_ids:
        _last_check.update(result="no_allowed_users", sent_count=0)
        return 0
    if not _is_daytime(current):
        _last_check.update(result="outside_daytime", sent_count=0)
        return 0

    sent_count = 0
    try:
        for user_id in settings.qq_allowed_user_ids:
            if await _maybe_send_to_user(user_id, current):
                sent_count += 1
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _last_check.update(result="error", sent_count=sent_count, error=str(exc))
        logger.exception("主动消息检查失败")
        return sent_count
    _last_check.update(
        result="sent" if sent_count else "checked",
        sent_count=sent_count,
    )
    return sent_count


def _in_night_close_window(now: datetime) -> bool:
    start = settings.night_close_start_hour
    end = settings.night_close_end_hour
    if start == end:
        return False
    if start < end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end


async def _maybe_night_close_user(user_id: str, now: datetime) -> bool:
    if active_websocket_count() <= 0:
        return False
    conversation_id = f"qq_private_{user_id}"
    logical_today = db.today_string(now)

    if db.get_night_close_prompted_date(user_id) == logical_today:
        return False
    if db.get_diary(logical_today) is not None:
        return False

    last_user_message = db.get_last_message(conversation_id=conversation_id, role="user")
    if last_user_message is None:
        return False
    last_user_at = _parse_iso(str(last_user_message["created_at"]))
    if last_user_at is None:
        return False
    # 今天没聊过天就不打扰；正聊着也不插话。
    if db.logical_date_for_datetime(last_user_at) != logical_today:
        return False
    quiet_minutes = int((now - last_user_at).total_seconds() // 60)
    if quiet_minutes < settings.night_close_min_quiet_minutes:
        return False

    try:
        result = await generate_qq_night_close_replies(conversation_id)
    except (LLMConfigError, RuntimeError):
        result = _local_result(["今天要收尾了吗？"])

    sent_any = await _deliver_replies(user_id, conversation_id, result)

    if sent_any:
        db.set_night_close_prompted_date(user_id, logical_today)
    return sent_any


async def run_night_close_once(now: datetime | None = None) -> int:
    current = now or _now()
    if not settings.qq_bot_enabled or not settings.night_close_enabled:
        return 0
    if not settings.qq_allowed_user_ids:
        return 0
    if not _in_night_close_window(current):
        return 0

    sent_count = 0
    for user_id in settings.qq_allowed_user_ids:
        if await _maybe_night_close_user(user_id, current):
            sent_count += 1
    return sent_count


async def proactive_loop() -> None:
    await asyncio.sleep(15)
    wake_event = _get_wake_event()
    while True:
        try:
            await _maybe_recover_qq_connection()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("QQ 通道状态检查失败")
        try:
            await run_proactive_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("主动消息调度失败")
        try:
            await run_night_close_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("夜间收尾调度失败")
        try:
            await asyncio.wait_for(
                wake_event.wait(),
                timeout=max(30, settings.qq_proactive_check_seconds),
            )
        except asyncio.TimeoutError:
            pass
        finally:
            wake_event.clear()
