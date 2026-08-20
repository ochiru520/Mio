from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Any

from .config import settings


DEFAULT_CONTEXT_MESSAGES = 18
MAX_CONTEXT_MESSAGES = 40
_config_lock = threading.RLock()
_history_lock = threading.RLock()
_histories: dict[str, deque[dict[str, str]]] = {}


def _normalise_group_ids(value: object) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []

    result: list[str] = []
    for item in raw_items:
        group_id = str(item or "").strip()
        if group_id and group_id not in result:
            result.append(group_id)
    return result


def _default_config() -> dict[str, object]:
    group_ids = _normalise_group_ids(settings.qq_allowed_group_ids)
    return {
        "enabled": bool(group_ids),
        "group_ids": group_ids,
        "mention_required": bool(settings.qq_group_mention_required),
        "context_messages": DEFAULT_CONTEXT_MESSAGES,
    }


def _normalise_config(payload: dict[str, Any] | None) -> dict[str, object]:
    defaults = _default_config()
    source = payload or {}
    try:
        context_messages = int(source.get("context_messages", defaults["context_messages"]))
    except (TypeError, ValueError):
        context_messages = DEFAULT_CONTEXT_MESSAGES
    return {
        "enabled": bool(source.get("enabled", defaults["enabled"])),
        "group_ids": _normalise_group_ids(source.get("group_ids", defaults["group_ids"])),
        "mention_required": bool(source.get("mention_required", defaults["mention_required"])),
        "context_messages": min(MAX_CONTEXT_MESSAGES, max(4, context_messages)),
    }


def load_group_config() -> dict[str, object]:
    path = settings.qq_group_config_path
    with _config_lock:
        if not path.is_file():
            return _default_config()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _default_config()
    return _normalise_config(payload if isinstance(payload, dict) else None)


def save_group_config(payload: dict[str, Any]) -> dict[str, object]:
    config = _normalise_config(payload)
    path = settings.qq_group_config_path
    with _config_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    return public_group_status(config)


def public_group_status(config: dict[str, object] | None = None) -> dict[str, object]:
    current = config or load_group_config()
    with _history_lock:
        active_group_count = sum(1 for history in _histories.values() if history)
        context_message_count = sum(len(history) for history in _histories.values())
    return {
        **current,
        "active_group_count": active_group_count,
        "context_message_count": context_message_count,
        "storage": "memory_only",
        "sync_to_agent": False,
    }


def group_is_allowed(group_id: object) -> bool:
    config = load_group_config()
    return bool(config["enabled"]) and str(group_id or "").strip() in set(config["group_ids"])


def group_mention_required() -> bool:
    return bool(load_group_config()["mention_required"])


def get_group_history(group_id: object) -> list[dict[str, str]]:
    key = str(group_id or "").strip()
    with _history_lock:
        return [dict(item) for item in _histories.get(key, ())]


def append_group_exchange(
    group_id: object,
    sender_name: str,
    user_message: str,
    replies: list[str],
) -> None:
    key = str(group_id or "").strip()
    if not key:
        return
    limit = int(load_group_config()["context_messages"])
    with _history_lock:
        history = _histories.setdefault(key, deque(maxlen=limit))
        if history.maxlen != limit:
            history = deque(history, maxlen=limit)
            _histories[key] = history
        display_name = sender_name.strip() or "群成员"
        if user_message.strip():
            history.append({"role": "user", "content": f"{display_name}：{user_message.strip()}"})
        for reply in replies:
            if reply.strip():
                history.append({"role": "assistant", "content": reply.strip()})


def clear_group_histories() -> dict[str, object]:
    with _history_lock:
        _histories.clear()
    return public_group_status()


__all__ = [
    "append_group_exchange",
    "clear_group_histories",
    "get_group_history",
    "group_is_allowed",
    "group_mention_required",
    "load_group_config",
    "public_group_status",
    "save_group_config",
]
