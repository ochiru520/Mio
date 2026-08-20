from __future__ import annotations

import json
import re
import threading
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable

from . import (
    companion_service,
    db,
    environment_check_service,
    privacy_service,
    route_observation_service,
    subservice_health,
)
from .model_latency_service import get_latency_stats
from .model_registry import ModelProfile, get_model_profile, list_model_profiles
from .runtime_identity import runtime_identity
from .tool_registry import tool_registry
from .config import settings


SCHEMA_VERSION = 1
ACTIVE_VIEW_STALE_SECONDS = 300.0
CONTEXT_MAX_CHARS = 5200
VIEW_LABELS = {
    "unknown": "未知页面",
    "onboarding": "首次启动向导",
    "home": "首页",
    "chat": "对话",
    "diaries": "日记",
    "memory": "记忆",
    "tasks": "任务",
    "companion": "桌宠",
    "settings": "设置",
    "stats": "统计",
}
SETTINGS_SECTION_LABELS = {
    "general": "基础与启动",
    "appearance": "外观与界面",
    "profile": "人格与关系",
    "conversation": "对话与记忆",
    "diary": "日记与成长",
    "models": "模型与 API",
    "qq": "QQ",
    "pet": "桌宠",
    "data": "数据与隐私",
    "advanced": "高级设置",
}
SNAPSHOT_SCOPES = {
    "overview",
    "active_view",
    "capabilities",
    "service_health",
    "models",
    "budget",
    "last_route",
    "environment",
    "tools",
}

_view_lock = threading.Lock()
_active_view: dict[str, Any] = {
    "view_id": "unknown",
    "section_id": "",
    "visible": False,
    "source": "main_app",
    "reported_at": "",
    "reported_monotonic": 0.0,
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sanitize_diagnostic(value: object) -> str:
    text = str(value or "").strip()[:500]
    if not text:
        return ""
    text = re.sub(r"(?i)https?://\S+", "[url]", text)
    text = re.sub(r"(?i)\b(?:sk|api)[-_][A-Za-z0-9_-]{8,}\b", "[secret]", text)
    text = re.sub(
        r"(?i)\b(?:api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]\s*[^\s,;]+",
        "[secret]",
        text,
    )
    text = re.sub(r"(?i)\b[A-Z]:[\\/][^\r\n;,]+", "[path]", text)
    text = re.sub(r"\\\\[^\\\s]+\\[^\r\n;,]+", "[path]", text)
    text = re.sub(r"(?<![\w.])/(?:[^\s/]+/)+[^\s,;]+", "[path]", text)
    return text[:300]


def report_active_view(
    view_id: str,
    *,
    section_id: str = "",
    visible: bool = True,
    source: str = "main_app",
) -> dict[str, Any]:
    clean_view = str(view_id or "").strip().lower()
    clean_section = str(section_id or "").strip().lower()
    if clean_view not in VIEW_LABELS or clean_view == "unknown":
        raise ValueError("不支持的主应用页面。")
    if clean_view == "settings" and clean_section and clean_section not in SETTINGS_SECTION_LABELS:
        raise ValueError("不支持的设置分区。")
    if clean_view != "settings":
        clean_section = ""
    with _view_lock:
        _active_view.update(
            {
                "view_id": clean_view,
                "section_id": clean_section,
                "visible": bool(visible),
                "source": str(source or "main_app")[:40],
                "reported_at": _now_iso(),
                "reported_monotonic": time.monotonic(),
            }
        )
    return get_active_view()


def get_active_view() -> dict[str, Any]:
    with _view_lock:
        current = dict(_active_view)
    reported = float(current.pop("reported_monotonic", 0.0) or 0.0)
    age = max(0.0, time.monotonic() - reported) if reported else None
    view_id = str(current.get("view_id") or "unknown")
    section_id = str(current.get("section_id") or "")
    return {
        **current,
        "label": VIEW_LABELS.get(view_id, "未知页面"),
        "section_label": SETTINGS_SECTION_LABELS.get(section_id, ""),
        "age_seconds": round(age, 3) if age is not None else None,
        "stale": age is None or age > ACTIVE_VIEW_STALE_SECONDS,
    }


def _public_service_health(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    health = raw if isinstance(raw, dict) else subservice_health.snapshot()
    services: dict[str, dict[str, Any]] = {}
    for service_id, value in dict(health.get("services") or {}).items():
        if not isinstance(value, dict):
            continue
        state = str(value.get("state") or "unknown")
        failure_reason = ""
        if state == "disabled":
            failure_reason = "用户设置已关闭"
        elif state == "offline":
            failure_reason = "当前没有活动连接"
        elif state in {"failed", "degraded"}:
            failure_reason = _sanitize_diagnostic(value.get("last_error")) or "服务报告运行异常"
        services[str(service_id)] = {
            "service_id": str(service_id),
            "state": state,
            "enabled": bool(value.get("enabled")),
            "running": bool(value.get("running")),
            "ready": bool(value.get("ready")),
            "failure_reason": failure_reason,
            "recovery_scope": str(value.get("recovery_scope") or "")[:80],
        }
    degraded = [
        service_id for service_id, item in services.items()
        if item["state"] in {"failed", "degraded"}
    ]
    return {
        "passive": True,
        "overall": "degraded" if degraded else "ok",
        "degraded_services": degraded,
        "services": services,
    }


def get_service_health() -> dict[str, Any]:
    return _public_service_health()


def _privacy_map(privacy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or ""): dict(item)
        for item in list(privacy.get("capabilities") or [])
        if isinstance(item, dict) and item.get("id")
    }


def _service_capability_state(
    services: dict[str, dict[str, Any]],
    service_id: str,
    *,
    enabled: bool,
    on_demand: bool = False,
) -> tuple[str, str]:
    item = services.get(service_id) or {}
    state = str(item.get("state") or "unknown")
    if not enabled:
        return "disabled", "用户设置已关闭"
    if bool(item.get("ready")) or state in {"ready", "idle", "running"}:
        return "available", ""
    if on_demand and state in {"stopped", "unknown"} and not item.get("failure_reason"):
        return "idle", "当前未运行，需要时可启动"
    if state in {"failed", "degraded", "offline"}:
        return "degraded", str(item.get("failure_reason") or "服务当前不可用")
    return "idle", "当前尚未就绪"


def _capability(
    capability_id: str,
    description: str,
    channels: list[str],
    *,
    enabled: bool,
    health: str,
    failure_reason: str,
    risk_level: str,
    cost_mode: str,
    privacy_destination: str,
    tool_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "description": description,
        "channels": channels,
        "enabled": bool(enabled),
        "health": health,
        "failure_reason": _sanitize_diagnostic(failure_reason),
        "risk_level": risk_level,
        "cost": {"mode": cost_mode},
        "privacy_destination": privacy_destination,
        "tool_ids": list(tool_ids or []),
    }


def _build_capabilities(
    *,
    privacy: dict[str, Any],
    service_health: dict[str, Any],
    profiles: list[ModelProfile],
    companion_config: dict[str, Any],
    pet_running: bool,
) -> list[dict[str, Any]]:
    private = _privacy_map(privacy)
    services = dict(service_health.get("services") or {})
    configured_models = [profile for profile in profiles if profile.api_key and profile.base_urls]
    model_ready = bool(configured_models)
    qq_enabled = bool((private.get("qq") or {}).get("enabled"))
    qq_health, qq_failure = _service_capability_state(services, "qq", enabled=qq_enabled)
    tts_enabled = bool(companion_config.get("voice_enabled", True))
    tts_health, tts_failure = _service_capability_state(
        services, "tts", enabled=tts_enabled, on_demand=True
    )
    phone_health, phone_failure = _service_capability_state(
        services, "phone", enabled=True, on_demand=True
    )
    screen_enabled = bool((private.get("screen_observation") or {}).get("enabled"))
    capture_health, capture_failure = _service_capability_state(
        services, "screen_capture", enabled=screen_enabled, on_demand=True
    )
    audio_enabled = bool((private.get("system_audio") or {}).get("enabled"))
    audio_health, audio_failure = _service_capability_state(
        services, "asr_system_audio", enabled=audio_enabled, on_demand=True
    )
    web_search_enabled = bool((private.get("web_search") or {}).get("enabled"))
    proactive_enabled = bool((private.get("proactive") or {}).get("enabled"))
    automatic_records_enabled = bool((private.get("automatic_records") or {}).get("enabled"))
    return [
        _capability(
            "self_awareness", "读取自身能力、页面、模型、预算与服务状态", ["main_app", "desktop_pet", "qq"],
            enabled=True, health="available", failure_reason="", risk_level="read_only", cost_mode="none",
            privacy_destination="脱敏结构化状态留在本机；只有与当前问题相关的字段进入当前模型上下文",
            tool_ids=["get_self_state", "list_capabilities", "get_active_view", "get_service_health", "explain_last_route"],
        ),
        _capability(
            "conversation", "进行主应用、桌宠和 QQ 对话", ["main_app", "desktop_pet", "qq"],
            enabled=model_ready, health="available" if model_ready else "unavailable",
            failure_reason="" if model_ready else "没有已配置凭据的可用模型", risk_level="external_request",
            cost_mode="model_usage", privacy_destination="用户发送的消息会进入当前模型供应商",
        ),
        _capability(
            "attachments", "读取图片、PDF、Word 和文本附件", ["main_app", "qq"],
            enabled=model_ready, health="available" if model_ready else "unavailable",
            failure_reason="" if model_ready else "没有已配置凭据的可用模型", risk_level="external_request",
            cost_mode="model_usage", privacy_destination="附件解析留在本机；需要识图或模型理解时发送给当前模型供应商",
        ),
        _capability(
            "diary", "读取日记素材并在确认后生成或编辑正式日记", ["main_app", "agent_tool"],
            enabled=True, health="available", failure_reason="", risk_level="high_risk_write",
            cost_mode="model_usage", privacy_destination="日记保存在本机；生成时相关上下文会进入当前模型供应商",
            tool_ids=["get_diary", "add_diary_material", "edit_today_diary", "generate_today_diary"],
        ),
        _capability(
            "memory", "检索和维护结构化记忆与待跟进事项", ["main_app", "agent_tool"],
            enabled=True, health="available", failure_reason="", risk_level="low_risk_write",
            cost_mode="none", privacy_destination="记忆保存在本机；只在相关对话中裁剪后进入模型上下文",
            tool_ids=["search_memory", "remember_memory", "remember_thread", "resolve_thread"],
        ),
        _capability(
            "task_confirmation", "显示任务状态并确认或拒绝高风险动作", ["main_app"],
            enabled=True, health="available", failure_reason="", risk_level="high_risk_write",
            cost_mode="none", privacy_destination="任务和回执保存在本机",
        ),
        _capability(
            "model_routing", "按任务选择模型与思考档位", ["main_app", "desktop_pet", "qq"],
            enabled=model_ready, health="available" if model_ready else "unavailable",
            failure_reason="" if model_ready else "没有已配置凭据的可用模型", risk_level="external_request",
            cost_mode="model_usage", privacy_destination="模型请求会发送给被选中的供应商",
        ),
        _capability(
            "web_search", "查询需要时效性的公开网页信息", ["main_app", "desktop_pet", "qq"],
            enabled=web_search_enabled, health="available" if web_search_enabled else "disabled",
            failure_reason="" if web_search_enabled else "用户设置已关闭", risk_level="external_request",
            cost_mode="model_usage", privacy_destination=str((private.get("web_search") or {}).get("destination") or "搜索关键词会发送给搜索服务"),
        ),
        _capability(
            "proactive_messages", "根据时间和待跟进事项生成受控主动消息", ["main_app", "desktop_pet", "qq"],
            enabled=proactive_enabled, health="available" if proactive_enabled else "disabled",
            failure_reason="" if proactive_enabled else "用户设置已关闭", risk_level="low_risk_write",
            cost_mode="model_usage", privacy_destination=str((private.get("proactive") or {}).get("destination") or "可能调用当前模型；QQ 在线时同步发送"),
        ),
        _capability(
            "automatic_records", "按逻辑日生成日记、回顾和周报", ["background", "main_app"],
            enabled=automatic_records_enabled, health="available" if automatic_records_enabled else "disabled",
            failure_reason="" if automatic_records_enabled else "用户设置已关闭", risk_level="high_risk_write",
            cost_mode="model_usage", privacy_destination=str((private.get("automatic_records") or {}).get("destination") or "相关日期的本地上下文会进入当前模型供应商"),
        ),
        _capability(
            "qq", "通过 NapCat/OneBot 收发 QQ 私聊和群聊", ["qq"],
            enabled=qq_enabled, health=qq_health, failure_reason=qq_failure, risk_level="external_request",
            cost_mode="model_usage", privacy_destination=str((private.get("qq") or {}).get("destination") or "QQ 消息进入本地后端；回复会发送到 QQ"),
        ),
        _capability(
            "desktop_pet", "显示 Live2D 桌宠、气泡和快捷输入窗", ["desktop_pet"],
            enabled=True, health="available" if pet_running else "idle",
            failure_reason="" if pet_running else "当前没有活动桌宠渲染器", risk_level="read_only",
            cost_mode="none", privacy_destination="渲染和窗口状态留在本机",
        ),
        _capability(
            "tts", "使用 Mio 音色播放中文或日语语音", ["main_app", "desktop_pet", "qq"],
            enabled=tts_enabled, health=tts_health, failure_reason=tts_failure, risk_level="external_request",
            cost_mode="local_service", privacy_destination="朗读文本发送给本机 GPT-SoVITS 服务",
        ),
        _capability(
            "phone", "进行带 ASR、模型回复和语音播放的持续电话", ["desktop_pet"],
            enabled=True, health=phone_health, failure_reason=phone_failure, risk_level="external_request",
            cost_mode="model_usage", privacy_destination="麦克风音频在本机识别；转写文本进入当前模型供应商",
        ),
        _capability(
            "screen_observation", "观察屏幕变化并在授权时分析或开口", ["desktop_pet", "background"],
            enabled=screen_enabled, health=capture_health, failure_reason=capture_failure, risk_level="external_request",
            cost_mode="budget_limited", privacy_destination=str((private.get("screen_observation") or {}).get("destination") or "本地视觉留在本机；云端视觉发送选定画面"),
        ),
        _capability(
            "system_audio", "转写系统声音并提供屏幕上下文", ["desktop_pet", "background"],
            enabled=audio_enabled, health=audio_health, failure_reason=audio_failure, risk_level="read_only",
            cost_mode="local_service", privacy_destination=str((private.get("system_audio") or {}).get("destination") or "音频在内存中转写，原始片段不保存"),
        ),
        _capability(
            "backup_restore", "创建完整备份并在确认后恢复本地数据", ["main_app"],
            enabled=True, health="available", failure_reason="", risk_level="high_risk_write",
            cost_mode="none", privacy_destination="备份保存在本机数据目录",
        ),
    ]


def _public_models(profiles: list[ModelProfile]) -> list[dict[str, Any]]:
    return [
        {
            "model_id": profile.id,
            "display_name": profile.display_name,
            "provider_name": profile.provider_name,
            "provider_kind": profile.provider_kind,
            "configured": bool(profile.api_key and profile.base_urls),
            "supports_vision": bool(profile.supports_vision),
            "pricing_configured": profile.pricing_source != "unconfigured",
            "input_price_cny_per_million": profile.input_price_cny_per_million,
            "output_price_cny_per_million": profile.output_price_cny_per_million,
            "latency": get_latency_stats(profile.id).public_dict(),
        }
        for profile in profiles
    ]


def _budget_summary(companion_config: dict[str, Any]) -> dict[str, Any]:
    limit = max(0.1, float(companion_config.get("screen_daily_cost_limit_yuan", 5.0)))
    if not settings.db_path.is_file():
        return {
            "usage_available": False,
            "today_tokens": {},
            "screen_observation": {
                "daily_limit_yuan": limit,
                "budget_cost_yuan": None,
                "remaining_yuan": None,
                "paused": False,
                "pending_request_count": 0,
            },
            "general_model_budget": {
                "configured": False,
                "note": "运行数据库尚不存在，当前无法读取费用；普通对话也没有全局硬上限",
            },
        }
    token_usage = db.get_token_usage_summary(days=1)
    screen_usage = db.get_screen_analysis_usage()
    used = max(0.0, float(screen_usage.get("budget_cost_yuan") or 0.0))
    return {
        "usage_available": True,
        "today_tokens": dict(token_usage.get("today") or {}),
        "screen_observation": {
            "daily_limit_yuan": limit,
            "budget_cost_yuan": used,
            "remaining_yuan": round(max(0.0, limit - used), 6),
            "paused": used >= limit,
            "pending_request_count": int(screen_usage.get("pending_request_count") or 0),
        },
        "general_model_budget": {
            "configured": False,
            "note": "当前只有屏幕观察具备硬性每日费用上限；普通对话展示单次费用但没有全局硬上限",
        },
    }


def _overview(identity: dict[str, Any], service_health: dict[str, Any], profiles: list[ModelProfile]) -> dict[str, Any]:
    try:
        active_model = get_model_profile().id
    except ValueError:
        active_model = ""
    warnings = list(identity.get("warnings") or [])
    return {
        "runtime_status": str(identity.get("status") or "unknown"),
        "build_id": str(identity.get("build_id") or "unknown"),
        "app_version": str(identity.get("app_version") or "development"),
        "source_mode": bool(identity.get("source_mode")),
        "runtime_warning_count": len(warnings),
        "runtime_warnings": [_sanitize_diagnostic(item) for item in warnings[:5]],
        "service_health": str(service_health.get("overall") or "unknown"),
        "configured_model_count": sum(bool(profile.api_key and profile.base_urls) for profile in profiles),
        "model_count": len(profiles),
        "active_model_id": active_model,
    }


def _public_last_route(route: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(route, dict):
        return None
    raw_profile = route.get("task_profile")
    task_profile = dict(raw_profile) if isinstance(raw_profile, dict) else {}
    raw_candidates = route.get("candidates")
    candidates = []
    if isinstance(raw_candidates, (list, tuple)):
        for candidate in raw_candidates[:12]:
            if not isinstance(candidate, dict):
                continue
            candidates.append({
                "model_id": str(candidate.get("model_id") or "")[:200],
                "eligible": bool(candidate.get("eligible")),
                "rank": max(0, int(candidate.get("rank") or 0)),
                "sample_count": max(0, int(candidate.get("sample_count") or 0)),
                "success_rate": candidate.get("success_rate"),
                "first_token_p95_ms": candidate.get("first_token_p95_ms"),
                "average_cost_yuan": candidate.get("average_cost_yuan"),
                "estimated_cost_yuan": candidate.get("estimated_cost_yuan"),
                "capability_reasons": [
                    _sanitize_diagnostic(item)
                    for item in list(candidate.get("capability_reasons") or [])[:6]
                ],
            })
    return {
        "recorded_at": str(route.get("recorded_at") or "")[:40],
        "source": str(route.get("source") or "unknown")[:40],
        "mode": "automatic" if route.get("mode") == "automatic" else "manual",
        "selected_model_id": str(route.get("selected_model_id") or "")[:200],
        "selected_reasoning_level": str(route.get("selected_reasoning_level") or "")[:50],
        "actual_model_id": str(route.get("actual_model_id") or "")[:200],
        "connection_route": _sanitize_diagnostic(route.get("connection_route")),
        "difficulty": str(route.get("difficulty") or "")[:40],
        "task_type": str(route.get("task_type") or task_profile.get("task_type") or "conversation")[:60],
        "task_profile": {
            key: task_profile.get(key)
            for key in (
                "task_type",
                "difficulty",
                "modalities",
                "requires_vision",
                "requires_tools",
                "requires_structured_output",
                "estimated_context_tokens",
                "risk_level",
                "latency_priority",
                "cost_priority",
                "reason",
            )
            if key in task_profile
        },
        "candidates": candidates,
        "reason": _sanitize_diagnostic(route.get("reason")),
        "latency_budget_ms": max(0, int(route.get("latency_budget_ms") or 0)),
        "first_token_latency_ms": route.get("first_token_latency_ms"),
        "total_latency_ms": route.get("total_latency_ms"),
        "request_cost_yuan": route.get("request_cost_yuan"),
        "request_cost_source": str(route.get("request_cost_source") or "")[:60],
        "success": bool(route.get("success")),
        "error_code": str(route.get("error_code") or "")[:120],
        "escalated_from_model_id": str(route.get("escalated_from_model_id") or "")[:200],
    }


def build_self_snapshot(
    scopes: Iterable[str] | None = None,
    *,
    privacy: dict[str, Any] | None = None,
    services: dict[str, Any] | None = None,
    profiles: list[ModelProfile] | None = None,
    identity: dict[str, Any] | None = None,
    companion_config: dict[str, Any] | None = None,
    pet_running: bool | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = set(SNAPSHOT_SCOPES) if scopes is None else set(scopes) & SNAPSHOT_SCOPES
    cache: dict[str, Any] = {}

    def privacy_state() -> dict[str, Any]:
        if "privacy" not in cache:
            cache["privacy"] = privacy if isinstance(privacy, dict) else privacy_service.privacy_status()
        return cache["privacy"]

    def public_health() -> dict[str, Any]:
        if "services" not in cache:
            cache["services"] = _public_service_health(services)
        return cache["services"]

    def model_profiles() -> list[ModelProfile]:
        if "profiles" not in cache:
            cache["profiles"] = profiles if profiles is not None else list_model_profiles()
        return cache["profiles"]

    def runtime() -> dict[str, Any]:
        if "identity" not in cache:
            cache["identity"] = identity if isinstance(identity, dict) else runtime_identity()
        return cache["identity"]

    def companion() -> dict[str, Any]:
        if "companion" not in cache:
            cache["companion"] = (
                companion_config
                if isinstance(companion_config, dict)
                else companion_service.load_config()
            )
        return cache["companion"]

    def is_pet_running() -> bool:
        if "pet_running" not in cache:
            cache["pet_running"] = (
                bool(pet_running)
                if pet_running is not None
                else bool(companion_service.pet_running())
            )
        return bool(cache["pet_running"])

    def passive_environment() -> dict[str, Any]:
        if "environment" not in cache:
            cache["environment"] = (
                environment
                if isinstance(environment, dict)
                else environment_check_service.passive_environment_status()
            )
        return cache["environment"]

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "read_only": True,
        "privacy": {
            "contains_private_content": False,
            "contains_secrets": False,
            "contains_absolute_paths": False,
            "model_context_policy": "只把与本轮问题相关的脱敏字段加入上下文",
        },
        "included_scopes": sorted(selected),
    }
    if "overview" in selected:
        result["overview"] = _overview(runtime(), public_health(), model_profiles())
    if "active_view" in selected:
        result["active_view"] = get_active_view()
    if "capabilities" in selected:
        result["capabilities"] = _build_capabilities(
            privacy=privacy_state(),
            service_health=public_health(),
            profiles=model_profiles(),
            companion_config=companion(),
            pet_running=is_pet_running(),
        )
    if "service_health" in selected:
        result["service_health"] = public_health()
    if "models" in selected:
        result["models"] = _public_models(model_profiles())
    if "budget" in selected:
        result["budget"] = _budget_summary(companion())
    if "last_route" in selected:
        result["last_route"] = _public_last_route(route_observation_service.last_route())
    if "environment" in selected:
        result["environment"] = passive_environment()
    if "tools" in selected:
        result["tools"] = [definition.public_dict() for definition in tool_registry.list()]
    return result


SELF_RE = re.compile(r"(?:你|Mio|澪).{0,12}(?:自己|自身|能力|功能|状态|会什么|能做什么)|自检|selfsnapshot", re.IGNORECASE)
VIEW_RE = re.compile(r"(?:当前|现在).{0,8}(?:页面|界面|哪个页)|应用里.{0,8}(?:有什么|在哪)")
SERVICE_RE = re.compile(r"语音|音色|电话|麦克风|桌宠|live2d|qq|napcat|屏幕|系统声音|服务|故障|正常")
MODEL_RE = re.compile(r"模型|思考|路由|为什么.{0,8}(?:选择|选)|上次.{0,8}(?:选择|模型)")
BUDGET_RE = re.compile(r"预算|费用|token|令牌", re.IGNORECASE)


def scopes_for_message(message: str) -> tuple[str, ...]:
    text = str(message or "").strip()
    scopes: set[str] = set()
    broad_self = bool(SELF_RE.search(text))
    if broad_self:
        scopes.update({"overview", "active_view", "capabilities", "service_health", "models", "budget", "last_route", "environment", "tools"})
    if VIEW_RE.search(text):
        scopes.update({"active_view", "capabilities"})
    if SERVICE_RE.search(text) and (broad_self or re.search(r"(?:你|Mio|澪|当前|现在|是否|能不能|可用|正常)", text, re.IGNORECASE)):
        scopes.update({"capabilities", "service_health"})
    if MODEL_RE.search(text) and (broad_self or re.search(r"(?:你|Mio|澪|当前|现在|上次|为什么)", text, re.IGNORECASE)):
        scopes.update({"models", "last_route", "budget"})
    if BUDGET_RE.search(text) and (broad_self or re.search(r"(?:你|Mio|澪|当前|现在|今天)", text, re.IGNORECASE)):
        scopes.update({"budget", "models"})
    return tuple(sorted(scopes))


def _compact_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in snapshot.items()
        if key not in {"privacy", "capabilities", "models", "tools"}
    }
    if "capabilities" in snapshot:
        compact["capabilities"] = [
            {
                "capability_id": item["capability_id"],
                "enabled": item["enabled"],
                "health": item["health"],
                "failure_reason": item["failure_reason"],
            }
            for item in snapshot["capabilities"]
        ]
    if "models" in snapshot:
        compact["models"] = [
            {
                "model_id": item["model_id"],
                "display_name": item["display_name"],
                "configured": item["configured"],
                "supports_vision": item["supports_vision"],
                "latency_samples": item["latency"]["sample_count"],
            }
            for item in snapshot["models"][:12]
        ]
    if "tools" in snapshot:
        compact["tools"] = [
            {"name": item["name"], "permission": item["permission"]}
            for item in snapshot["tools"]
        ]
    return compact


def _shorten_context_strings(value: Any, limit: int = 160) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [_shorten_context_strings(item, limit) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _shorten_context_strings(item, limit)
            for key, item in value.items()
        }
    return value


def _encode_context_with_limit(snapshot: dict[str, Any], max_chars: int = CONTEXT_MAX_CHARS) -> str:
    limit = max(2, int(max_chars))
    working = _shorten_context_strings(deepcopy(snapshot))

    def encode() -> str:
        return json.dumps(working, ensure_ascii=False, separators=(",", ":"))

    encoded = encode()
    if len(encoded) <= limit:
        return encoded

    for key, keep in (("models", 6), ("tools", 20)):
        items = working.get(key)
        if isinstance(items, list) and len(items) > keep:
            working[key] = items[:keep]
    encoded = encode()
    if len(encoded) <= limit:
        return encoded

    service_health = working.get("service_health")
    if isinstance(service_health, dict) and isinstance(service_health.get("services"), dict):
        service_health["services"] = {
            service_id: {
                key: item.get(key)
                for key in ("state", "enabled", "ready", "failure_reason")
                if key in item
            }
            for service_id, item in service_health["services"].items()
            if isinstance(item, dict)
        }
    encoded = encode()
    if len(encoded) <= limit:
        return encoded

    for key in ("tools", "environment", "models", "service_health", "budget", "last_route", "active_view"):
        working.pop(key, None)
        encoded = encode()
        if len(encoded) <= limit:
            return encoded

    capabilities = working.get("capabilities")
    if isinstance(capabilities, list):
        important = [
            item
            for item in capabilities
            if isinstance(item, dict) and item.get("health") not in {"available", "idle"}
        ]
        working["capabilities"] = important or capabilities[:8]
        while working["capabilities"]:
            encoded = encode()
            if len(encoded) <= limit:
                return encoded
            working["capabilities"].pop()

    minimal = {
        key: working[key]
        for key in ("schema_version", "generated_at", "read_only", "included_scopes")
        if key in working
    }
    encoded = json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))
    return encoded if len(encoded) <= limit else "{}"


def context_for_message(message: str) -> str:
    scopes = scopes_for_message(message)
    if not scopes:
        return ""
    snapshot = _compact_context(build_self_snapshot(scopes))
    encoded = _encode_context_with_limit(snapshot)
    return (
        "【Mio 的只读 SelfSnapshot】\n"
        "只能根据以下当前状态说明自己的能力、限制和故障；未知就说未知。"
        "不得声称执行了尚未执行的动作，也不得推测源码、密钥或未提供的私人内容。\n"
        + encoded
    )


def reset_for_tests() -> None:
    global _active_view
    with _view_lock:
        _active_view = {
            "view_id": "unknown",
            "section_id": "",
            "visible": False,
            "source": "main_app",
            "reported_at": "",
            "reported_monotonic": 0.0,
        }


__all__ = [
    "SNAPSHOT_SCOPES",
    "build_self_snapshot",
    "context_for_message",
    "get_active_view",
    "get_service_health",
    "report_active_view",
    "reset_for_tests",
    "scopes_for_message",
]
