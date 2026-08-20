from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings


logger = logging.getLogger("mio.autonomy")

AUTONOMY_LEVELS = {"observe", "suggest", "auto_low_risk", "confirm_high_risk"}
GOAL_STATUSES = {"active", "paused", "completed", "cancelled"}
RISK_LEVELS = {"read_only", "low", "high"}
TERMINAL_BEHAVIOR_STATUSES = {"delivered", "delivery_unknown", "cancelled", "failed", "suppressed"}


def _now() -> datetime:
    try:
        zone = ZoneInfo(settings.timezone)
    except Exception:
        zone = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(zone)


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_now().tzinfo)
    return parsed


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def public_policy(row: object | None = None) -> dict[str, object]:
    item = dict(row or db.get_autonomy_policy())
    return {
        "paused": bool(item.get("paused")),
        "autonomy_level": str(item.get("autonomy_level") or "suggest"),
        "quiet_start_hour": int(item.get("quiet_start_hour") or 0),
        "quiet_end_hour": int(item.get("quiet_end_hour") or 0),
        "minimum_interval_minutes": int(item.get("minimum_interval_minutes") or 0),
        "daily_behavior_limit": int(item.get("daily_behavior_limit") or 0),
        "daily_budget_yuan": float(item.get("daily_budget_yuan") or 0.0),
        "capability_overrides": _json_object(item.get("capability_overrides_json")),
        "updated_at": str(item.get("updated_at") or ""),
    }


def update_policy(changes: dict[str, object]) -> dict[str, object]:
    current = public_policy()
    level = str(changes.get("autonomy_level", current["autonomy_level"]))
    if level not in AUTONOMY_LEVELS:
        raise ValueError("自主权限档位无效。")
    quiet_start = int(changes.get("quiet_start_hour", current["quiet_start_hour"]))
    quiet_end = int(changes.get("quiet_end_hour", current["quiet_end_hour"]))
    interval = int(changes.get("minimum_interval_minutes", current["minimum_interval_minutes"]))
    behavior_limit = int(changes.get("daily_behavior_limit", current["daily_behavior_limit"]))
    budget = float(changes.get("daily_budget_yuan", current["daily_budget_yuan"]))
    if not 0 <= quiet_start <= 23 or not 0 <= quiet_end <= 23:
        raise ValueError("安静时段小时必须在 0 到 23 之间。")
    if not 1 <= interval <= 1440:
        raise ValueError("最小主动间隔必须在 1 到 1440 分钟之间。")
    if not 0 <= behavior_limit <= 100:
        raise ValueError("每日主动次数必须在 0 到 100 之间。")
    if not 0 <= budget <= 100:
        raise ValueError("每日主动预算必须在 0 到 100 元之间。")
    overrides = changes.get("capability_overrides", current["capability_overrides"])
    if not isinstance(overrides, dict):
        raise ValueError("能力覆盖必须是对象。")
    normalized_overrides: dict[str, str] = {}
    for capability, override in overrides.items():
        name = str(capability).strip()[:80]
        mode = str(override).strip()
        if not name or mode not in {*AUTONOMY_LEVELS, "disabled"}:
            raise ValueError(f"能力覆盖无效：{capability}")
        normalized_overrides[name] = mode
    row = db.update_autonomy_policy(
        {
            "paused": 1 if bool(changes.get("paused", current["paused"])) else 0,
            "autonomy_level": level,
            "quiet_start_hour": quiet_start,
            "quiet_end_hour": quiet_end,
            "minimum_interval_minutes": interval,
            "daily_behavior_limit": behavior_limit,
            "daily_budget_yuan": budget,
            "capability_overrides_json": json.dumps(normalized_overrides, ensure_ascii=False),
        }
    )
    return public_policy(row)


def public_goal(row: object) -> dict[str, object]:
    item = dict(row)  # type: ignore[arg-type]
    item["capabilities"] = [str(value) for value in _json_list(item.pop("capabilities_json", "[]"))]
    return item


def create_goal(
    title: str,
    *,
    description: str = "",
    conversation_id: str = "",
    autonomy_level: str = "",
    capabilities: list[str] | tuple[str, ...] = (),
    due_at: str = "",
    source_kind: str = "manual",
    source_ref: str = "",
) -> dict[str, object]:
    normalized_title = " ".join(str(title or "").split()).strip()
    if not normalized_title:
        raise ValueError("目标标题不能为空。")
    if autonomy_level and autonomy_level not in AUTONOMY_LEVELS:
        raise ValueError("目标自主权限档位无效。")
    normalized_capabilities = [str(item).strip()[:80] for item in capabilities if str(item).strip()]
    row = db.create_agent_goal(
        normalized_title,
        description=description,
        conversation_id=conversation_id,
        source_kind=source_kind,
        source_ref=source_ref,
        autonomy_level=autonomy_level,
        capabilities=normalized_capabilities,
        due_at=due_at,
    )
    return public_goal(row)


def _runtime_setting_goal(*args: object, **kwargs: object) -> dict[str, object]:
    """运行设置是持续授权；即使某次被误标完成，下一次到期也应恢复为活动目标。"""
    goal = create_goal(*args, source_kind="runtime_setting", **kwargs)  # type: ignore[arg-type]
    if str(goal.get("status") or "") == "active":
        return goal
    db.update_agent_goal_status(int(goal["id"]), "active")
    refreshed = db.get_agent_goal(int(goal["id"]))
    assert refreshed is not None
    return public_goal(refreshed)


def set_goal_status(goal_id: int, status: str) -> dict[str, object]:
    if status not in GOAL_STATUSES:
        raise ValueError("目标状态无效。")
    if not db.update_agent_goal_status(goal_id, status):
        raise ValueError("没有找到这个目标。")
    row = db.get_agent_goal(goal_id)
    assert row is not None
    return public_goal(row)


def public_event(row: object) -> dict[str, object]:
    item = dict(row)  # type: ignore[arg-type]
    item["payload"] = _json_object(item.pop("payload_json", "{}"))
    return item


def public_behavior(row: object) -> dict[str, object]:
    item = dict(row)  # type: ignore[arg-type]
    item["evidence"] = _json_object(item.pop("evidence_json", "{}"))
    return item


def snapshot(*, limit: int = 100) -> dict[str, object]:
    current = _now()
    start, end = db.logical_day_bounds(db.today_string(current))
    return {
        "policy": public_policy(),
        "goals": [public_goal(row) for row in db.list_agent_goals(limit=limit)],
        "events": [public_event(row) for row in db.list_agent_events(limit=limit)],
        "behaviors": [public_behavior(row) for row in db.list_autonomy_behaviors(limit=limit)],
        "usage": db.autonomy_usage_between(start, end),
        "generated_at": current.isoformat(timespec="seconds"),
    }


def record_application_activity_event(now: datetime | None = None) -> None:
    current = now or _now()
    bucket = current.replace(minute=(current.minute // 15) * 15, second=0, microsecond=0)
    db.record_agent_event(
        f"application_active:{bucket.isoformat(timespec='minutes')}",
        "application_active",
        source="desktop",
        capability="application_activity",
        payload={"active_at": current.isoformat(timespec="seconds")},
        relevance=0.2,
        confidence=1.0,
        urgency=0.0,
        interruption_cost=1.0,
        occurred_at=current.isoformat(timespec="seconds"),
    )


def _proactive_interval() -> timedelta:
    minimum = max(1, int(settings.qq_proactive_min_idle_minutes))
    maximum = max(minimum, int(settings.qq_proactive_max_idle_minutes))
    return timedelta(minutes=random.randint(minimum, maximum))


def _in_proactive_window(current: datetime) -> bool:
    start = int(settings.qq_proactive_day_start_hour)
    end = int(settings.qq_proactive_day_end_hour)
    hour = current.hour + current.minute / 60
    if start == end:
        return True
    return start <= hour < end if start < end else hour >= start or hour < end


def collect_scheduled_proactive_events(now: datetime | None = None) -> int:
    current = now or _now()
    if not settings.qq_proactive_enabled or not settings.qq_allowed_user_ids:
        return 0
    if not _in_proactive_window(current):
        return 0
    created = 0
    for user_id in settings.qq_allowed_user_ids:
        conversation_id = f"qq_private_{user_id}"
        last_message = db.get_last_message(conversation_id=conversation_id, role="user")
        if last_message is None:
            continue
        last_message_at_text = str(last_message["created_at"] or "")
        last_message_at = _parse_time(last_message_at_text)
        if last_message_at is None:
            continue
        state = db.get_qq_proactive_state(user_id)
        if state is None or str(state["last_user_message_at"] or "") != last_message_at_text:
            next_prompt_at = last_message_at + _proactive_interval()
            last_prompt_at = str(state["last_prompt_at"] or "") if state is not None else ""
            db.upsert_qq_proactive_state(
                user_id,
                last_message_at_text,
                next_prompt_at.isoformat(timespec="seconds"),
                last_prompt_at,
            )
        else:
            next_prompt_at = _parse_time(state["next_prompt_at"]) or (last_message_at + _proactive_interval())
            last_prompt_at = str(state["last_prompt_at"] or "")
        if current < next_prompt_at:
            continue
        idle_minutes = max(0, int((current - last_message_at).total_seconds() // 60))
        if idle_minutes < max(1, int(settings.qq_proactive_min_idle_minutes)):
            continue
        goal = _runtime_setting_goal(
            f"允许 Mio 在长时间安静后联系 QQ 用户 {user_id}",
            description="由已启用的主动联系设置授权；关闭设置后不再产生事件。",
            conversation_id=conversation_id,
            capabilities=["proactive_checkin"],
            source_ref=f"qq_proactive:{user_id}",
        )
        event = db.record_agent_event(
            f"proactive_checkin_due:{user_id}:{next_prompt_at.isoformat(timespec='seconds')}",
            "proactive_checkin_due",
            source="schedule",
            conversation_id=conversation_id,
            goal_id=int(goal["id"]),
            capability="proactive_checkin",
            risk_level="low",
            payload={
                "user_id": user_id,
                "idle_minutes": idle_minutes,
                "last_user_message_at": last_message_at_text,
                "scheduled_for": next_prompt_at.isoformat(timespec="seconds"),
            },
            relevance=0.75,
            confidence=1.0,
            urgency=0.35,
            interruption_cost=0.65,
            occurred_at=current.isoformat(timespec="seconds"),
            available_at=current.isoformat(timespec="seconds"),
        )
        if str(event["status"] or "") == "pending" and int(event["attempts"] or 0) == 0:
            created += 1
        db.upsert_qq_proactive_state(
            user_id,
            last_message_at_text,
            (current + _proactive_interval()).isoformat(timespec="seconds"),
            last_prompt_at,
        )
    return created


def _in_night_close_window(current: datetime) -> bool:
    start = int(settings.night_close_start_hour)
    end = int(settings.night_close_end_hour)
    if start == end:
        return False
    return start <= current.hour < end if start < end else current.hour >= start or current.hour < end


def collect_night_close_events(now: datetime | None = None) -> int:
    current = now or _now()
    if not settings.qq_bot_enabled or not settings.night_close_enabled:
        return 0
    if not settings.qq_allowed_user_ids or not _in_night_close_window(current):
        return 0
    logical_today = db.today_string(current)
    if db.get_diary(logical_today) is not None:
        return 0
    created = 0
    for user_id in settings.qq_allowed_user_ids:
        if db.get_night_close_prompted_date(user_id) == logical_today:
            continue
        conversation_id = f"qq_private_{user_id}"
        last_message = db.get_last_message(conversation_id=conversation_id, role="user")
        if last_message is None:
            continue
        last_message_at = _parse_time(last_message["created_at"])
        if last_message_at is None or db.logical_date_for_datetime(last_message_at) != logical_today:
            continue
        quiet_minutes = max(0, int((current - last_message_at).total_seconds() // 60))
        if quiet_minutes < int(settings.night_close_min_quiet_minutes):
            continue
        goal = _runtime_setting_goal(
            f"允许 Mio 在夜间提醒 QQ 用户 {user_id} 收尾",
            description="由已启用的夜间收尾设置授权；关闭设置后不再产生事件。",
            conversation_id=conversation_id,
            capabilities=["night_close"],
            source_ref=f"night_close:{user_id}",
        )
        event = db.record_agent_event(
            f"night_close_due:{user_id}:{logical_today}",
            "night_close_due",
            source="schedule",
            conversation_id=conversation_id,
            goal_id=int(goal["id"]),
            capability="night_close",
            risk_level="low",
            payload={"user_id": user_id, "logical_date": logical_today, "quiet_minutes": quiet_minutes},
            relevance=0.8,
            confidence=1.0,
            urgency=0.6,
            interruption_cost=0.65,
            occurred_at=current.isoformat(timespec="seconds"),
            available_at=current.isoformat(timespec="seconds"),
        )
        if str(event["status"] or "") == "pending" and int(event["attempts"] or 0) == 0:
            created += 1
    return created


def _pending_thread_goal(row: object) -> dict[str, object]:
    item = dict(row)  # type: ignore[arg-type]
    return create_goal(
        f"跟进：{str(item.get('content') or '')[:160]}",
        description="用户明确留下了到期跟进事项。",
        conversation_id=str(item.get("conversation_id") or ""),
        autonomy_level="auto_low_risk",
        capabilities=["follow_up_reminder"],
        due_at=str(item.get("follow_up_after") or ""),
        source_kind="pending_thread",
        source_ref=str(item.get("id") or ""),
    )


def collect_pending_thread_events(now: datetime | None = None) -> int:
    current = now or _now()
    created = 0
    for row in db.list_all_open_pending_threads(limit=500):
        due_at = _parse_time(row["follow_up_after"])
        if due_at is None or due_at > current:
            continue
        goal = _pending_thread_goal(row)
        event = db.record_agent_event(
            f"pending_thread_due:{int(row['id'])}:{str(row['follow_up_after'])}",
            "pending_thread_due",
            source="pending_thread",
            conversation_id=str(row["conversation_id"] or ""),
            goal_id=int(goal["id"]),
            capability="follow_up_reminder",
            risk_level="low",
            payload={
                "thread_id": int(row["id"]),
                "content": str(row["content"] or ""),
                "due_at": str(row["follow_up_after"] or ""),
                "source_message_id": int(row["source_message_id"] or 0),
            },
            relevance=1.0,
            confidence=1.0,
            urgency=0.9,
            interruption_cost=0.45,
            occurred_at=str(row["follow_up_after"] or current.isoformat(timespec="seconds")),
            available_at=str(row["follow_up_after"] or current.isoformat(timespec="seconds")),
        )
        if str(event["status"]) == "pending" and int(event["attempts"] or 0) == 0:
            created += 1
    return created


def collect_daily_state_event(now: datetime | None = None) -> int:
    current = now or _now()
    state = db.get_daily_state(db.today_string(current))
    if state is None:
        return 0
    row = dict(state)
    db.record_agent_event(
        f"daily_state:{row['date']}:{row['updated_at']}",
        "daily_state_changed",
        source="daily_state",
        capability="daily_state",
        payload={
            "date": row["date"],
            "mood": row.get("mood") or "",
            "key_events": row.get("key_events") or "",
            "next_min_action": row.get("next_min_action") or "",
        },
        relevance=0.65,
        confidence=1.0,
        urgency=0.2,
        interruption_cost=0.8,
        occurred_at=str(row["updated_at"]),
    )
    return 1


def collect_service_health_event(now: datetime | None = None) -> int:
    from . import subservice_health

    current = now or _now()
    health = subservice_health.snapshot()
    services = health.get("services") if isinstance(health.get("services"), dict) else {}
    states = {
        str(name): {
            "state": str(item.get("state") or "unknown"),
            "enabled": bool(item.get("enabled")),
            "ready": bool(item.get("ready")),
            "last_error": str(item.get("last_error") or "")[:300],
        }
        for name, item in services.items()
        if isinstance(item, dict)
    }
    digest = hashlib.sha256(
        json.dumps(states, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    degraded = [name for name, item in states.items() if item["enabled"] and not item["ready"]]
    db.record_agent_event(
        f"service_health:{digest}",
        "service_health_changed",
        source="service_health",
        capability="service_health",
        payload={"services": states, "degraded": degraded},
        relevance=0.7 if degraded else 0.3,
        confidence=1.0,
        urgency=0.8 if degraded else 0.1,
        interruption_cost=0.7,
        occurred_at=current.isoformat(timespec="seconds"),
    )
    return 1


def collect_events(now: datetime | None = None) -> int:
    current = now or _now()
    return (
        collect_pending_thread_events(current)
        + collect_scheduled_proactive_events(current)
        + collect_night_close_events(current)
        + collect_daily_state_event(current)
        + collect_service_health_event(current)
    )


def _is_quiet(policy: dict[str, object], current: datetime) -> bool:
    start = int(policy["quiet_start_hour"])
    end = int(policy["quiet_end_hour"])
    if start == end:
        return False
    hour = current.hour + current.minute / 60
    return start <= hour or hour < end if start > end else start <= hour < end


def _next_quiet_end(policy: dict[str, object], current: datetime) -> datetime:
    end_hour = int(policy["quiet_end_hour"])
    target = current.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target


def _effective_level(policy: dict[str, object], goal: dict[str, object], capability: str) -> str:
    overrides = policy.get("capability_overrides")
    if isinstance(overrides, dict) and capability in overrides:
        return str(overrides[capability])
    goal_level = str(goal.get("autonomy_level") or "")
    return goal_level if goal_level in AUTONOMY_LEVELS else str(policy["autonomy_level"])


def _authorized_goal(capability: str, conversation_id: str = "") -> dict[str, object] | None:
    for row in db.list_agent_goals(limit=500, status="active"):
        goal = public_goal(row)
        capabilities = {str(item) for item in goal.get("capabilities", [])}
        if capability not in capabilities and "*" not in capabilities:
            continue
        goal_conversation = str(goal.get("conversation_id") or "")
        if goal_conversation and conversation_id and goal_conversation != conversation_id:
            continue
        return goal
    return None


def _recent_user_activity(conversation_id: str, current: datetime) -> bool:
    row = db.get_last_message(conversation_id=conversation_id, role="user")
    if row is None:
        return False
    created_at = _parse_time(row["created_at"])
    return created_at is not None and current - created_at < timedelta(minutes=2)


def _behavior_content(event: dict[str, object]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if event.get("event_type") == "pending_thread_due":
        content = " ".join(str(payload.get("content") or "").split()).strip()
        return f"到时间了。你之前想让我跟进：{content}"
    if event.get("event_type") == "daily_state_changed":
        next_action = " ".join(str(payload.get("next_min_action") or "").split()).strip()
        return f"我注意到今天的状态有更新。{('下一步是：' + next_action) if next_action else '要不要一起看一下下一步？'}"
    if event.get("event_type") == "proactive_checkin_due":
        return ""
    if event.get("event_type") == "night_close_due":
        return ""
    if event.get("event_type") == "service_health_changed":
        degraded = [str(item) for item in payload.get("degraded", [])] if isinstance(payload.get("degraded"), list) else []
        return f"我发现这些服务状态异常：{'、'.join(degraded)}。要现在检查吗？"
    if event.get("event_type") == "task_result":
        status = str(payload.get("status") or "")
        result = " ".join(str(payload.get("result") or "").split()).strip()
        return f"任务已经变为 {status}。{result}".strip()
    if event.get("event_type") == "screen_event":
        summary = " ".join(str(payload.get("summary") or "").split()).strip()
        return f"我注意到屏幕上出现了值得关注的变化：{summary}"
    return "我注意到一件与你当前目标有关的事，要现在一起处理吗？"


def _decision(event: dict[str, object], goal: dict[str, object], current: datetime) -> dict[str, object]:
    policy = public_policy()
    capability = str(event.get("capability") or "")
    level = _effective_level(policy, goal, capability)
    reasons: list[str] = []
    if policy["paused"]:
        return {"decision": "wait", "reason": "自主行为已暂停。", "available_at": current + timedelta(minutes=5)}
    if capability == "proactive_checkin" and not settings.qq_proactive_enabled:
        return {"decision": "ignore", "reason": "主动联系设置已经关闭。"}
    if capability == "night_close" and (not settings.qq_bot_enabled or not settings.night_close_enabled):
        return {"decision": "ignore", "reason": "QQ 或夜间收尾设置已经关闭。"}
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if capability == "night_close":
        event_logical_date = str(payload.get("logical_date") or "")
        current_logical_date = db.today_string(current)
        if not _in_night_close_window(current):
            return {"decision": "ignore", "reason": "夜间收尾事件已经错过有效时段。"}
        if event_logical_date and event_logical_date != current_logical_date:
            return {"decision": "ignore", "reason": "夜间收尾事件属于上一记录日，已经过期。"}
    if (
        capability == "proactive_checkin"
        and settings.qq_bot_enabled
        and settings.night_close_enabled
        and _in_night_close_window(current)
        and db.get_diary(db.today_string(current)) is None
    ):
        return {"decision": "ignore", "reason": "当前应由夜间收尾消息统一联系，已抑制同周期普通主动消息。"}
    if capability == "screen_event" and bool(payload.get("legacy_should_speak")):
        return {"decision": "ignore", "reason": "实时屏幕链路已经处理该事件，已抑制重复主动消息。"}
    if level == "disabled":
        return {"decision": "ignore", "reason": f"能力 {capability} 已单独关闭。"}
    if _is_quiet(policy, current):
        return {"decision": "wait", "reason": "当前处于安静时段。", "available_at": _next_quiet_end(policy, current)}
    if float(event.get("relevance") or 0) < 0.5:
        return {"decision": "ignore", "reason": "事件与授权目标的相关性不足。"}
    if float(event.get("confidence") or 0) < 0.55:
        return {"decision": "ignore", "reason": "事件证据置信度不足。"}
    if float(event.get("interruption_cost") or 0) >= 0.5 and _recent_user_activity(str(event.get("conversation_id") or ""), current):
        return {"decision": "wait", "reason": "前台会话正在进行，主动行为延后。", "available_at": current + timedelta(minutes=5)}
    start, end = db.logical_day_bounds(db.today_string(current))
    usage = db.autonomy_usage_between(start, end)
    if int(usage["behavior_count"]) >= int(policy["daily_behavior_limit"]):
        tomorrow = _next_quiet_end({**policy, "quiet_end_hour": 8}, current.replace(hour=23, minute=59))
        return {"decision": "wait", "reason": "已达到今日主动行为次数上限。", "available_at": tomorrow}
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    estimated_cost = max(0.0, float(payload.get("estimated_cost_yuan") or 0.0))
    spent_cost = float(usage["cost_yuan"])
    budget = float(policy["daily_budget_yuan"])
    if spent_cost > budget or (estimated_cost > 0 and spent_cost + estimated_cost > budget):
        return {"decision": "wait", "reason": "已达到今日主动预算。", "available_at": current + timedelta(hours=6)}
    last_completed = _parse_time(usage.get("last_completed_at"))
    minimum = timedelta(minutes=int(policy["minimum_interval_minutes"]))
    if last_completed is not None and current - last_completed < minimum:
        return {"decision": "wait", "reason": "距离上一次主动行为过近。", "available_at": last_completed + minimum}
    if level == "observe":
        return {"decision": "ignore", "reason": "当前权限为只观察。"}
    risk = str(event.get("risk_level") or "read_only")
    if risk == "high" and level == "auto_low_risk":
        return {"decision": "ignore", "reason": "当前只允许自动执行低风险动作。"}
    if risk == "high" and level == "confirm_high_risk":
        reasons.append("动作风险较高，等待用户确认。")
        return {"decision": "confirm", "reason": " ".join(reasons), "permission_mode": level}
    behavior_type = "suggestion" if level == "suggest" else "notification"
    reasons.append("事件属于已授权目标。")
    reasons.append("相关性、证据、时段、频率和预算门禁均通过。")
    return {
        "decision": "deliver",
        "reason": " ".join(reasons),
        "permission_mode": level,
        "behavior_type": behavior_type,
    }


async def _deliver_behavior(behavior_row: object, event: dict[str, object]) -> dict[str, object]:
    behavior = public_behavior(behavior_row)
    behavior_id = int(behavior["id"])
    if str(behavior["status"]) in TERMINAL_BEHAVIOR_STATUSES:
        if str(behavior["status"]) in {"delivered", "delivery_unknown"}:
            db.finish_agent_event(
                int(event["id"]),
                "processed",
                reason=str(behavior.get("reason") or "主动行为已完成。"),
            )
        return behavior
    delivery_key = f"autonomy:{behavior_id}:app"
    app_message_id = int(behavior.get("app_message_id") or 0)
    if app_message_id <= 0:
        app_message_id = db.save_message(
            "assistant",
            str(behavior["content"]),
            source="proactive",
            conversation_id=str(behavior["conversation_id"] or "default"),
            request_id=str(behavior["request_id"] or ""),
            model_id=str(behavior.get("model_id") or ""),
            provider_model=str(behavior.get("provider_model") or ""),
            reasoning_level=str(behavior.get("reasoning_level") or ""),
            prompt_tokens=int(behavior.get("prompt_tokens") or 0),
            cached_prompt_tokens=int(behavior.get("cached_prompt_tokens") or 0),
            completion_tokens=int(behavior.get("completion_tokens") or 0),
            reasoning_tokens=int(behavior.get("reasoning_tokens") or 0),
            request_cost_yuan=float(behavior.get("cost_yuan") or 0.0),
            request_cost_source=str(behavior.get("cost_source") or "local_autonomy"),
            first_token_latency_ms=behavior.get("first_token_latency_ms"),
            total_latency_ms=behavior.get("total_latency_ms"),
            delivery_key=delivery_key,
        )
        db.update_autonomy_behavior(
            behavior_id,
            {"app_message_id": app_message_id, "delivery_status": "app_delivered"},
        )

    qq_status = str(behavior.get("qq_delivery_status") or "not_attempted")
    conversation_id = str(behavior.get("conversation_id") or "")
    user_id = conversation_id.removeprefix("qq_private_") if conversation_id.startswith("qq_private_") else ""
    destination = str(behavior.get("destination") or "app")
    if "qq" in destination and user_id:
        from .routes.onebot import active_websocket_count, send_private_message

        if not settings.qq_bot_enabled:
            qq_status = "disabled"
            db.update_autonomy_behavior(behavior_id, {"qq_delivery_status": qq_status})
        elif user_id not in settings.qq_allowed_user_ids:
            qq_status = "not_allowed"
            db.update_autonomy_behavior(behavior_id, {"qq_delivery_status": qq_status})
        elif qq_status == "sending":
            qq_status = "delivery_unknown"
            db.update_autonomy_behavior(behavior_id, {"qq_delivery_status": qq_status})
        elif qq_status not in {"delivered", "delivery_unknown"}:
            if active_websocket_count() <= 0:
                qq_status = "not_connected"
                db.update_autonomy_behavior(behavior_id, {"qq_delivery_status": qq_status})
            else:
                db.update_autonomy_behavior(behavior_id, {"qq_delivery_status": "sending"})
                try:
                    sent = await send_private_message(user_id, str(behavior["content"]))
                except Exception:
                    qq_status = "delivery_unknown"
                else:
                    qq_status = "delivered" if sent else "failed"
                db.update_autonomy_behavior(behavior_id, {"qq_delivery_status": qq_status})
    else:
        qq_status = "not_requested"
        db.update_autonomy_behavior(behavior_id, {"qq_delivery_status": qq_status})

    delivery_status = (
        "app_and_qq" if qq_status == "delivered" else
        "app_qq_unknown" if qq_status == "delivery_unknown" else
        "app_only"
    )
    final_status = "delivery_unknown" if qq_status == "delivery_unknown" else "delivered"
    completed_at = db.now_iso()
    db.update_autonomy_behavior(
        behavior_id,
        {
            "status": final_status,
            "delivery_status": delivery_status,
            "completed_at": completed_at,
        },
    )
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if event.get("event_type") == "pending_thread_due" and payload.get("thread_id"):
        db.mark_pending_thread_mentioned(int(payload["thread_id"]))
    if event.get("event_type") == "proactive_checkin_due" and payload.get("user_id"):
        user_id = str(payload["user_id"])
        state = db.get_qq_proactive_state(user_id)
        if state is not None:
            db.upsert_qq_proactive_state(
                user_id,
                str(state["last_user_message_at"] or ""),
                str(state["next_prompt_at"] or ""),
                completed_at,
            )
    if event.get("event_type") == "night_close_due" and payload.get("user_id"):
        db.set_night_close_prompted_date(
            str(payload["user_id"]),
            str(payload.get("logical_date") or db.today_string()),
        )
    db.finish_agent_event(
        int(event["id"]),
        "processed",
        reason=str(behavior.get("reason") or "主动行为已完成。"),
    )
    row = db.get_autonomy_behavior(behavior_id)
    assert row is not None
    return public_behavior(row)


async def _generate_scheduled_behavior(event: dict[str, object]):
    event_type = str(event.get("event_type") or "")
    if event_type not in {"proactive_checkin_due", "night_close_due"}:
        return None
    from .chat_service import generate_qq_night_close_replies, generate_qq_proactive_replies

    conversation_id = str(event.get("conversation_id") or "default")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if event_type == "night_close_due":
        return await generate_qq_night_close_replies(conversation_id)
    return await generate_qq_proactive_replies(
        conversation_id,
        max(0, int(payload.get("idle_minutes") or 0)),
    )


async def process_claimed_event(row: object, now: datetime | None = None) -> dict[str, object]:
    current = now or _now()
    event = public_event(row)
    goal_row = db.get_agent_goal(int(event.get("goal_id") or 0))
    goal = (
        public_goal(goal_row)
        if goal_row is not None and str(goal_row["status"] or "") == "active"
        else _authorized_goal(
            str(event.get("capability") or ""),
            str(event.get("conversation_id") or ""),
        )
    )
    if goal is None:
        reason = "事件没有对应的活动授权目标。"
        db.finish_agent_event(int(event["id"]), "ignored", reason=reason)
        return {"event_id": event["id"], "decision": "ignore", "reason": reason}
    behavior_key = f"event:{int(event['id'])}:primary"
    existing_behavior = db.get_autonomy_behavior_by_key(behavior_key)
    if existing_behavior is not None:
        existing_status = str(existing_behavior["status"] or "")
        if existing_status == "awaiting_confirmation":
            db.finish_agent_event(
                int(event["id"]),
                "waiting_confirmation",
                reason=str(existing_behavior["reason"] or "等待用户确认。"),
            )
            return {
                "event_id": event["id"],
                "decision": "confirm",
                "behavior": public_behavior(existing_behavior),
            }
        delivered = await _deliver_behavior(existing_behavior, event)
        return {"event_id": event["id"], "decision": "recovered", "behavior": delivered}
    decision = _decision(event, goal, current)
    if decision["decision"] == "wait":
        available = decision["available_at"]
        assert isinstance(available, datetime)
        db.reschedule_agent_event(int(event["id"]), available.isoformat(timespec="seconds"), str(decision["reason"]))
        return {"event_id": event["id"], "decision": "wait", "reason": decision["reason"]}
    if decision["decision"] == "ignore":
        db.finish_agent_event(int(event["id"]), "ignored", reason=str(decision["reason"]))
        return {"event_id": event["id"], "decision": "ignore", "reason": decision["reason"]}

    generated = None
    try:
        generated = await _generate_scheduled_behavior(event)
    except Exception as exc:
        reason = f"到点后模型生成主动消息失败：{exc}"
        db.finish_agent_event(int(event["id"]), "failed", reason=reason, error=str(exc)[:1000])
        logger.exception("主动消息模型生成失败：event_id=%s", event["id"])
        return {"event_id": event["id"], "decision": "failed", "reason": reason}

    content = _behavior_content(event)
    if generated is not None:
        content = "\n\n".join(
            part.strip() for part in generated.replies if str(part or "").strip()
        ).strip()
        if not content:
            reason = "到点后模型没有生成可发送的主动消息。"
            db.finish_agent_event(int(event["id"]), "failed", reason=reason, error=reason)
            return {"event_id": event["id"], "decision": "failed", "reason": reason}

    status = "awaiting_confirmation" if decision["decision"] == "confirm" else "planned"
    destination = "app+qq" if str(event.get("conversation_id") or "").startswith("qq_private_") else "app"
    behavior = db.create_autonomy_behavior(
        behavior_key,
        event_id=int(event["id"]),
        goal_id=int(goal["id"]),
        conversation_id=str(event.get("conversation_id") or "default"),
        behavior_type=str(decision.get("behavior_type") or "confirmation_request"),
        capability=str(event.get("capability") or ""),
        risk_level=str(event.get("risk_level") or "read_only"),
        permission_mode=str(decision.get("permission_mode") or "observe"),
        status=status,
        reason=str(decision["reason"]),
        evidence={
            "event_key": event.get("event_key"),
            "goal_id": goal.get("id"),
            "goal_title": goal.get("title"),
            "relevance": event.get("relevance"),
            "confidence": event.get("confidence"),
            "urgency": event.get("urgency"),
            "interruption_cost": event.get("interruption_cost"),
        },
        content=content,
        destination=destination,
        request_id=(str(generated.request_id or "") if generated is not None else f"autonomy-{int(event['id'])}"),
        model_id=str(generated.model_id or "") if generated is not None else "",
        provider_id=str(generated.provider_id or "") if generated is not None else "",
        provider_name=str(generated.provider_name or "") if generated is not None else "",
        provider_model=str(generated.provider_model or "") if generated is not None else "",
        provider_request_id=str(generated.provider_request_id or "") if generated is not None else "",
        reasoning_level=str(generated.reasoning_level or "") if generated is not None else "",
        prompt_tokens=int(generated.prompt_tokens or 0) if generated is not None else 0,
        cached_prompt_tokens=int(generated.cached_prompt_tokens or 0) if generated is not None else 0,
        completion_tokens=int(generated.completion_tokens or 0) if generated is not None else 0,
        reasoning_tokens=int(generated.reasoning_tokens or 0) if generated is not None else 0,
        first_token_latency_ms=generated.first_token_latency_ms if generated is not None else None,
        total_latency_ms=generated.total_latency_ms if generated is not None else None,
        cost_yuan=float(generated.request_cost_yuan or 0.0) if generated is not None else 0.0,
        cost_source=str(generated.request_cost_source or "") if generated is not None else "",
    )
    if status == "awaiting_confirmation":
        db.finish_agent_event(int(event["id"]), "waiting_confirmation", reason=str(decision["reason"]))
        return {"event_id": event["id"], "decision": "confirm", "behavior": public_behavior(behavior)}
    delivered = await _deliver_behavior(behavior, event)
    if generated is not None and generated.cost_references:
        from .cost_reconciliation_service import queue_cost_reconciliation

        queue_cost_reconciliation(
            str(generated.request_id or ""),
            str(event.get("conversation_id") or "default"),
            generated.cost_references,
        )
    return {"event_id": event["id"], "decision": "deliver", "behavior": delivered}


async def process_once(now: datetime | None = None, *, limit: int = 20) -> list[dict[str, object]]:
    current = now or _now()
    results: list[dict[str, object]] = []
    stale_before = current - timedelta(minutes=5)
    for _ in range(max(1, min(int(limit), 100))):
        claim_token = uuid.uuid4().hex
        row = db.claim_next_agent_event(
            current.isoformat(timespec="seconds"),
            stale_before.isoformat(timespec="seconds"),
            claim_token,
        )
        if row is None:
            break
        try:
            results.append(await process_claimed_event(row, current))
        except Exception as exc:
            db.finish_agent_event(int(row["id"]), "failed", error=str(exc)[:1000])
            logger.exception("自主事件处理失败：event_id=%s", row["id"])
            results.append({"event_id": int(row["id"]), "decision": "failed", "error": str(exc)[:500]})
    return results


async def run_autonomy_cycle(now: datetime | None = None) -> dict[str, object]:
    current = now or _now()
    collect_events(current)
    results = await process_once(current)
    return {"processed": len(results), "results": results, "checked_at": current.isoformat(timespec="seconds")}


async def approve_behavior(behavior_id: int) -> dict[str, object]:
    row = db.get_autonomy_behavior(behavior_id)
    if row is None:
        raise ValueError("没有找到这个主动行为。")
    if str(row["status"] or "") != "awaiting_confirmation":
        raise ValueError("这个主动行为不在等待确认状态。")
    event_rows = [item for item in db.list_agent_events(limit=500) if int(item["id"]) == int(row["event_id"])]
    if not event_rows:
        raise ValueError("对应事件已经不存在。")
    db.update_autonomy_behavior(behavior_id, {"status": "planned", "reason": f"{row['reason']} 用户已确认。"})
    refreshed = db.get_autonomy_behavior(behavior_id)
    assert refreshed is not None
    return await _deliver_behavior(refreshed, public_event(event_rows[0]))


def cancel_behavior(behavior_id: int) -> dict[str, object]:
    row = db.get_autonomy_behavior(behavior_id)
    if row is None:
        raise ValueError("没有找到这个主动行为。")
    if str(row["status"] or "") not in {"planned", "awaiting_confirmation"}:
        raise ValueError("这个主动行为已经开始或结束。")
    db.update_autonomy_behavior(
        behavior_id,
        {"status": "cancelled", "reason": "用户已取消。", "completed_at": db.now_iso()},
    )
    db.finish_agent_event(int(row["event_id"] or 0), "cancelled", reason="用户已取消主动行为。")
    refreshed = db.get_autonomy_behavior(behavior_id)
    assert refreshed is not None
    return public_behavior(refreshed)


async def autonomy_loop() -> None:
    await asyncio.sleep(15)
    from . import proactive_service

    while True:
        try:
            await proactive_service.maintain_qq_connection_once()
            if proactive_service.desktop_app_is_active():
                await run_autonomy_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自主事件循环失败")
        await proactive_service.wait_for_proactive_wake(max(30, settings.qq_proactive_check_seconds))


__all__ = [
    "AUTONOMY_LEVELS",
    "approve_behavior",
    "autonomy_loop",
    "cancel_behavior",
    "collect_events",
    "collect_night_close_events",
    "collect_scheduled_proactive_events",
    "create_goal",
    "process_once",
    "public_behavior",
    "public_event",
    "public_goal",
    "public_policy",
    "record_application_activity_event",
    "run_autonomy_cycle",
    "set_goal_status",
    "snapshot",
    "update_policy",
]
