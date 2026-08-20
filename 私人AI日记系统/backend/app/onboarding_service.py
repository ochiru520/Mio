from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import db
from .config import save_runtime_settings, settings
from .mio_profile import DEFAULT_PROFILE, load_mio_profile, save_mio_profile_from_settings
from .model_registry import get_model_profile


ONBOARDING_VERSION = 2


def _state_path() -> Path:
    return settings.data_dir / "首次启动.json"


def _write_state(state: dict[str, Any]) -> dict[str, Any]:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return state


def _existing_user_evidence() -> list[str]:
    evidence: list[str] = []
    if settings.db_path.exists():
        with db.get_conn() as conn:
            for table, label in (
                ("messages", "聊天记录"),
                ("diaries", "日记"),
                ("structured_memories", "结构化记忆"),
            ):
                try:
                    count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.OperationalError:
                    count = 0
                if count:
                    evidence.append(label)
            try:
                manual_free_count = int(
                    conn.execute("SELECT COUNT(*) FROM memories WHERE type != 'manual'").fetchone()[0]
                )
            except sqlite3.OperationalError:
                manual_free_count = 0
            if manual_free_count:
                evidence.append("记忆")
    if settings.mio_profile_path.is_file():
        profile = load_mio_profile()
        comparable = deepcopy(profile)
        default = deepcopy(DEFAULT_PROFILE)
        comparable["updated_at"] = ""
        default["updated_at"] = ""
        if comparable != default:
            evidence.append("角色属性")
    if settings.model_profiles_path.is_file():
        evidence.append("模型供应商")
    if any(settings.diary_dir.glob("*.md")):
        evidence.append("日记文件")
    return list(dict.fromkeys(evidence))


def _load_saved_state() -> dict[str, Any] | None:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def onboarding_status() -> dict[str, Any]:
    saved = _load_saved_state()
    if saved is not None:
        return saved
    evidence = _existing_user_evidence()
    if evidence:
        return _write_state(
            {
                "version": ONBOARDING_VERSION,
                "completed": True,
                "mode": "legacy_existing_user",
                "completed_at": db.now_iso(),
                "existing_data": evidence,
            }
        )
    return _write_state({
        "version": ONBOARDING_VERSION,
        "completed": False,
        "mode": "new_user",
        "completed_at": "",
        "existing_data": [],
        "model_verification": {
            "verified": False,
            "model_id": "",
            "verified_at": "",
        },
    })


def record_model_verification(model_id: str) -> dict[str, Any]:
    profile_id = str(model_id or "").strip()
    if not profile_id:
        raise ValueError("缺少已测试的模型 ID。")
    profile = get_model_profile(profile_id)
    state = onboarding_status()
    if state.get("completed"):
        return state
    state["model_verification"] = {
        "verified": True,
        "model_id": profile.id,
        "provider_id": profile.provider_id,
        "provider_name": profile.provider_name,
        "model": profile.model,
        "verified_at": db.now_iso(),
    }
    return _write_state(state)


def _require_verified_model() -> dict[str, Any]:
    state = _load_saved_state() or {}
    verification = state.get("model_verification")
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        raise ValueError("请先在模型服务步骤完成一次真实聊天测试。")
    model_id = str(verification.get("model_id") or "").strip()
    try:
        profile = get_model_profile(model_id)
    except ValueError as exc:
        raise ValueError("已测试的模型配置不存在，请重新保存并测试模型。") from exc
    if profile.id != model_id:
        raise ValueError("已测试的模型身份不一致，请重新测试模型。")
    return verification


def prepare_first_launch_defaults() -> None:
    """Disable sensing defaults only for a genuinely new, unconfigured installation."""
    if settings.companion_config_path.exists() or onboarding_status()["completed"]:
        return
    from . import companion_service

    companion_service.save_config(
        {
            "screen_ai_enabled": False,
            "screen_audio_enabled": False,
            "screen_direct_voice_enabled": False,
            "speak_screen_observations": False,
            "speak_game_observations": False,
        }
    )


def complete_onboarding(payload: dict[str, Any]) -> dict[str, Any]:
    # 模型供应商可以跳过：第一次进入不强制配置，之后在设置中随时可配。
    try:
        model_verification = _require_verified_model()
    except ValueError:
        model_verification = {
            "verified": False,
            "model_id": "",
            "provider_id": "",
            "provider_name": "",
            "model": "",
            "verified_at": "",
        }
    name = str(payload.get("assistant_name") or "Mio").strip()
    user_address = str(payload.get("user_address") or "你").strip()
    if not name or len(name) > 80:
        raise ValueError("角色名字需要是 1 至 80 个字符。")
    if not user_address or len(user_address) > 80:
        raise ValueError("用户称呼需要是 1 至 80 个字符。")

    profile = deepcopy(load_mio_profile())
    profile.setdefault("identity", {})["name"] = name
    profile.setdefault("preferences", {})["user_address"] = user_address
    save_mio_profile_from_settings(profile)

    runtime_changes = {
        "web_search_enabled": bool(payload.get("web_search_enabled", False)),
        "qq_proactive_enabled": bool(payload.get("proactive_enabled", False)),
        "daily_diary_auto_enabled": bool(payload.get("daily_diary_auto_enabled", False)),
        "qq_bot_enabled": bool(payload.get("qq_enabled", False)),
        "qq_image_send_to_model": False,
        "daily_review_auto_enabled": False,
        "weekly_review_enabled": False,
    }
    save_runtime_settings(runtime_changes)

    state = {
        "version": ONBOARDING_VERSION,
        "completed": True,
        "mode": "new_user_completed",
        "completed_at": db.now_iso(),
        "existing_data": [],
        "model_verification": model_verification,
        "choices": runtime_changes,
    }
    return _write_state(state)
