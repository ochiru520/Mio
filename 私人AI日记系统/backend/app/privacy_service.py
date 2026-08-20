from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import autonomy_service, companion_service, db, screen_observation_service, system_audio_service
from .config import load_runtime_settings, save_runtime_settings, settings


RUNTIME_SENSITIVE_KEYS = (
    "web_search_enabled",
    "qq_bot_enabled",
    "qq_image_send_to_model",
    "qq_proactive_enabled",
    "daily_diary_auto_enabled",
    "daily_review_auto_enabled",
    "daily_review_auto_notify_qq",
    "weekly_review_enabled",
    "weekly_review_notify_qq",
    "night_close_enabled",
    "photo_archive_enabled",
)

PAUSE_ACTION_LABELS = {
    "runtime_settings": "关闭联网、QQ 与自动任务设置",
    "companion_settings": "关闭屏幕、系统声音与主动语音设置",
    "autonomy_policy": "暂停自主行为",
    "screen_capture": "停止屏幕捕获",
    "screen_session": "结束屏幕观察会话",
    "system_audio": "停止系统声音监听",
    "qq_connections": "断开 QQ 连接与处理中任务",
}

COMPANION_SENSITIVE_KEYS = (
    "screen_ai_enabled",
    "screen_audio_enabled",
    "speak_screen_observations",
    "speak_game_observations",
    "speak_proactive",
)


def _state_path() -> Path:
    return settings.data_dir / "隐私暂停.json"


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_state(data: dict[str, Any]) -> dict[str, Any]:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return data


def _disable_sensitive_settings() -> list[str]:
    failures: list[str] = []
    for action, callback in (
        ("runtime_settings", lambda: save_runtime_settings({key: False for key in RUNTIME_SENSITIVE_KEYS})),
        ("companion_settings", lambda: companion_service.save_config({key: False for key in COMPANION_SENSITIVE_KEYS})),
        ("autonomy_policy", lambda: autonomy_service.update_policy({"paused": True})),
    ):
        try:
            callback()
        except Exception as exc:
            failures.append(f"{action}: {str(exc)[:300] or exc.__class__.__name__}")
    return failures


def _capabilities() -> list[dict[str, object]]:
    runtime = load_runtime_settings()
    companion = companion_service.load_config()
    screen_status = companion_service.window_observer.status()
    return [
        {
            "id": "cloud_chat",
            "label": "云端对话与附件",
            "enabled": True,
            "destination": "发送给当前模型供应商",
            "control": "发送前由用户主动确认消息和附件",
        },
        {
            "id": "web_search",
            "label": "联网搜索",
            "enabled": bool(runtime.get("web_search_enabled")),
            "destination": "搜索关键词会发送给搜索服务",
            "control": "web_search_enabled",
        },
        {
            "id": "qq",
            "label": "QQ 通道",
            "enabled": bool(runtime.get("qq_bot_enabled")),
            "destination": "QQ 消息进入本地后端；回复会发送到 QQ",
            "control": "qq_bot_enabled",
        },
        {
            "id": "qq_images",
            "label": "QQ 图片发送给模型",
            "enabled": bool(runtime.get("qq_image_send_to_model")),
            "destination": "图片会发送给当前视觉模型供应商",
            "control": "qq_image_send_to_model",
        },
        {
            "id": "screen_observation",
            "label": "屏幕观察",
            "enabled": bool(screen_status.get("running")) or bool(companion.get("screen_ai_enabled")),
            "destination": "本地视觉留在本机；云端视觉发送选定画面",
            "control": "桌宠与观察设置",
        },
        {
            "id": "system_audio",
            "label": "系统声音监听",
            "enabled": bool(companion.get("screen_audio_enabled")),
            "destination": "音频在内存中转写，原始片段不保存",
            "control": "screen_audio_enabled",
        },
        {
            "id": "proactive",
            "label": "主动联系",
            "enabled": bool(runtime.get("qq_proactive_enabled")),
            "destination": "可能调用当前模型；QQ 在线时同步发送",
            "control": "qq_proactive_enabled",
        },
        {
            "id": "automatic_records",
            "label": "自动日记与回顾",
            "enabled": any(
                bool(runtime.get(key))
                for key in ("daily_diary_auto_enabled", "daily_review_auto_enabled", "weekly_review_enabled")
            ),
            "destination": "生成时会把对应日期的本地上下文发送给当前模型",
            "control": "日记与成长设置",
        },
    ]


def privacy_status() -> dict[str, Any]:
    state = _load_state()
    return {
        "paused": bool(state.get("paused")),
        "paused_at": str(state.get("paused_at") or ""),
        "transition": str(state.get("transition") or "idle"),
        "transition_error": str(state.get("transition_error") or ""),
        "failed_actions": list(state.get("failed_actions") or []),
        "capabilities": _capabilities(),
        "local_data": {
            "database": str(settings.db_path),
            "diaries": str(settings.diary_dir),
            "attachments": str(settings.agent_attachment_dir),
            "backups": str(settings.data_dir / "备份"),
        },
    }


async def pause_sensitive_capabilities() -> dict[str, Any]:
    state = _load_state()
    has_snapshot = isinstance(state.get("runtime_snapshot"), dict) and isinstance(
        state.get("companion_snapshot"), dict
    )
    if not state.get("paused") and not has_snapshot:
        runtime = load_runtime_settings()
        companion = companion_service.load_config()
        state = {
            "paused": False,
            "paused_at": db.now_iso(),
            "transition": "pausing",
            "runtime_snapshot": {key: runtime.get(key) for key in RUNTIME_SENSITIVE_KEYS},
            "companion_snapshot": {key: companion.get(key) for key in COMPANION_SENSITIVE_KEYS},
            "autonomy_paused_snapshot": bool(autonomy_service.public_policy()["paused"]),
        }
        _save_state(state)

    failures: list[dict[str, str]] = []

    def run(action: str, callback) -> None:
        try:
            callback()
        except Exception as exc:
            failures.append(
                {
                    "action": action,
                    "label": PAUSE_ACTION_LABELS[action],
                    "error": str(exc)[:500] or exc.__class__.__name__,
                }
            )

    run("runtime_settings", lambda: save_runtime_settings({key: False for key in RUNTIME_SENSITIVE_KEYS}))
    run("companion_settings", lambda: companion_service.save_config({key: False for key in COMPANION_SENSITIVE_KEYS}))
    run("screen_capture", companion_service.window_observer.stop)
    run("screen_session", screen_observation_service.end_session)
    run("system_audio", system_audio_service.stop)
    run("autonomy_policy", lambda: autonomy_service.update_policy({"paused": True}))
    from .routes.onebot import disconnect_all_connections

    disconnected_connections = 0
    try:
        disconnected_connections = await disconnect_all_connections()
    except Exception as exc:
        failures.append(
            {
                "action": "qq_connections",
                "label": PAUSE_ACTION_LABELS["qq_connections"],
                "error": str(exc)[:500] or exc.__class__.__name__,
            }
        )

    if failures:
        labels = "、".join(item["label"] for item in failures)
        _save_state(
            {
                **state,
                "paused": False,
                "transition": "pause_incomplete",
                "transition_error": f"以下能力未能确认停止：{labels}",
                "failed_actions": failures,
            }
        )
        raise ValueError(f"未能确认全部敏感能力已经停止：{labels}。可以再次暂停重试。")

    _save_state(
        {
            **state,
            "paused": True,
            "transition": "paused",
            "transition_error": "",
            "failed_actions": [],
        }
    )
    return {**privacy_status(), "qq_connections_disconnected": disconnected_connections}


def resume_sensitive_capabilities() -> dict[str, Any]:
    state = _load_state()
    if not state.get("paused"):
        return privacy_status()
    runtime_snapshot = state.get("runtime_snapshot")
    companion_snapshot = state.get("companion_snapshot")
    failures: list[dict[str, str]] = []
    if isinstance(runtime_snapshot, dict):
        try:
            save_runtime_settings(
                {key: bool(value) for key, value in runtime_snapshot.items() if key in RUNTIME_SENSITIVE_KEYS}
            )
        except Exception as exc:
            failures.append({"action": "runtime_settings", "error": str(exc)[:500] or exc.__class__.__name__})
    if isinstance(companion_snapshot, dict):
        try:
            companion_service.save_config(
                {key: bool(value) for key, value in companion_snapshot.items() if key in COMPANION_SENSITIVE_KEYS}
            )
        except Exception as exc:
            failures.append({"action": "companion_settings", "error": str(exc)[:500] or exc.__class__.__name__})
    try:
        autonomy_service.update_policy({"paused": bool(state.get("autonomy_paused_snapshot", False))})
    except Exception as exc:
        failures.append({"action": "autonomy_policy", "error": str(exc)[:500] or exc.__class__.__name__})
    if failures:
        rollback_failures = _disable_sensitive_settings()
        detail = "、".join(item["action"] for item in failures)
        transition = "resume_incomplete"
        user_message = f"恢复暂停前设置失败：{detail}。敏感能力仍按暂停处理。"
        if rollback_failures:
            detail += f"；保持暂停也失败：{'；'.join(rollback_failures)}"
            transition = "state_uncertain"
            user_message = f"恢复失败且无法确认继续暂停：{detail}。请保持应用关闭并检查数据目录权限。"
        _save_state(
            {
                **state,
                "paused": True,
                "transition": transition,
                "transition_error": detail,
                "failed_actions": failures,
            }
        )
        raise ValueError(user_message)
    try:
        _save_state({"paused": False, "resumed_at": db.now_iso()})
    except Exception as state_error:
        rollback_failures = _disable_sensitive_settings()
        detail = f"保存恢复状态失败：{state_error}"
        transition = "resume_incomplete"
        if rollback_failures:
            detail += f"；重新暂停也失败：{'；'.join(rollback_failures)}"
            transition = "state_uncertain"
        try:
            _save_state(
                {
                    **state,
                    "paused": True,
                    "transition": transition,
                    "transition_error": detail,
                    "failed_actions": [{"action": "privacy_state", "error": str(state_error)[:500]}],
                }
            )
        except Exception:
            pass
        if rollback_failures:
            raise ValueError(f"恢复状态无法保存且无法确认重新暂停：{detail}") from state_error
        raise ValueError(f"恢复状态无法保存，已重新暂停敏感能力：{state_error}") from state_error
    return privacy_status()
