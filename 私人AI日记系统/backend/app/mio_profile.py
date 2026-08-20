from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from . import db
from .config import settings
from .llm import LLMConfigError, call_chat_completion


PROFILE_VERSION = 1

def _load_distribution_default_profile() -> dict[str, Any]:
    try:
        from .public_distribution_defaults import PUBLIC_DEFAULT_PROFILE

        profile = PUBLIC_DEFAULT_PROFILE
    except ImportError:
        from .private_distribution_defaults import PRIVATE_DEFAULT_PROFILE

        profile = PRIVATE_DEFAULT_PROFILE
    if not isinstance(profile, dict):
        raise RuntimeError("默认人格配置必须是 JSON 对象。")
    return deepcopy(profile)


DEFAULT_PROFILE = _load_distribution_default_profile()
DEFAULT_PROFILE["version"] = PROFILE_VERSION

ALLOWED_TOP_KEYS = {"version", "updated_at", "identity", "speaking_style", "behavior", "preferences"}
EDITABLE_PROFILE_SECTIONS = ("identity", "speaking_style", "behavior", "preferences")
FORBIDDEN_CONTENT_RE = re.compile(
    r"(api\s*key|apikey|secret|token|密码|密钥|账号|系统命令|删除文件|执行命令|绕过安全)",
    re.IGNORECASE,
)


def _profile_path():
    settings.ensure_directories()
    return settings.mio_profile_path


def _merge_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_PROFILE)
    for key, value in profile.items():
        if key not in ALLOWED_TOP_KEYS:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    merged["version"] = PROFILE_VERSION
    return merged


def load_mio_profile() -> dict[str, Any]:
    path = _profile_path()
    if not path.exists():
        profile = deepcopy(DEFAULT_PROFILE)
        profile["updated_at"] = db.now_iso()
        save_mio_profile(profile)
        return profile

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return _merge_defaults(raw)


def save_mio_profile(profile: dict[str, Any]) -> dict[str, Any]:
    merged = _merge_defaults(profile)
    merged["updated_at"] = db.now_iso()
    path = _profile_path()
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return merged


def _validate_editable_profile_value(value: Any, *, path: str, depth: int = 0) -> None:
    if depth > 3:
        raise ValueError(f"{path} 的嵌套层级过深。")
    if isinstance(value, str):
        if len(value) > 2000:
            raise ValueError(f"{path} 不能超过 2000 个字符。")
        if FORBIDDEN_CONTENT_RE.search(value):
            raise ValueError(f"{path} 包含不能写入 Mio 属性的敏感内容。")
        return
    if isinstance(value, list):
        if len(value) > 30:
            raise ValueError(f"{path} 最多保留 30 项。")
        for index, item in enumerate(value):
            _validate_editable_profile_value(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 50:
            raise ValueError(f"{path} 的字段过多。")
        for key, item in value.items():
            clean_key = str(key).strip()
            if not clean_key or len(clean_key) > 80 or FORBIDDEN_CONTENT_RE.search(clean_key):
                raise ValueError(f"{path} 包含不允许的字段名。")
            _validate_editable_profile_value(item, path=f"{path}.{clean_key}", depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise ValueError(f"{path} 包含不支持的数据类型。")


def save_mio_profile_from_settings(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("Mio 属性必须是 JSON 对象。")
    encoded = json.dumps(profile, ensure_ascii=False)
    if len(encoded) > 30000:
        raise ValueError("Mio 属性内容过长。")

    current = load_mio_profile()
    updated = deepcopy(current)
    for section in EDITABLE_PROFILE_SECTIONS:
        if section not in profile:
            continue
        value = profile[section]
        if not isinstance(value, dict):
            raise ValueError(f"{section} 必须是对象。")
        _validate_editable_profile_value(value, path=section)
        updated[section] = deepcopy(value)
    identity = updated.setdefault("identity", {})
    display_name = str(identity.get("name") or "").strip()
    if not display_name:
        raise ValueError("应用显示名字不能为空。")
    if len(display_name) > 80:
        raise ValueError("应用显示名字不能超过 80 个字符。")
    identity["name"] = display_name
    return save_mio_profile(updated)


def render_mio_profile_for_prompt(profile: dict[str, Any] | None = None) -> str:
    data = profile or load_mio_profile()
    return json.dumps(data, ensure_ascii=False, indent=2)


def _profile_without_timestamp(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(_merge_defaults(profile))
    normalized["updated_at"] = ""
    return normalized


def _fallback_note(instruction: str, context_messages: list[str] | None = None) -> str:
    context = "\n".join(context_messages or [])
    if FORBIDDEN_CONTENT_RE.search(f"{instruction}\n{context}"):
        raise ValueError("这类内容不能写入 Mio 属性。")
    recent_context = "；".join(
        line.removeprefix("user: ").strip()
        for line in (context_messages or [])
        if line.startswith("user: ") and line.removeprefix("user: ").strip() != instruction.strip()
    )
    note = f"用户要求写入 Mio 属性：{instruction.strip()}"
    if recent_context:
        note += f"；相关上下文：{recent_context[-400:]}"
    return note[:800]


def _append_custom_note(profile: dict[str, Any], note: str) -> dict[str, Any]:
    updated = _merge_defaults(profile)
    preferences = updated.setdefault("preferences", {})
    notes = preferences.get("custom_notes")
    if not isinstance(notes, list):
        notes = []
    if note not in notes:
        notes.append(note)
    preferences["custom_notes"] = notes[-30:]
    return updated


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("模型没有返回 JSON 对象。")
    return data


def build_profile_update_messages(
    current_profile: dict[str, Any],
    instruction: str,
    context_messages: list[str] | None = None,
) -> list[dict[str, str]]:
    system = """你是 Mio 的属性配置编辑器。

你的任务是根据用户明确要求，更新 Mio 的本地属性 JSON。
只允许修改人格、说话方式、行为偏好、称呼偏好、日记偏好这类低风险属性。
不要加入任何 API key、账号、密码、系统命令、代码片段、文件删除、网络执行或绕过安全限制的内容。
不要修改 version。
如果用户说“它”“这个”“刚刚那个”“上面那句”，要结合最近对话判断指代内容。
如果用户要求“加入/写入/放到属性或底层设定”，但没有指定现有字段，优先追加到 preferences.custom_notes。
必须输出完整 JSON 对象，不要 Markdown，不要解释。"""
    context_text = "\n".join(context_messages or [])
    user = f"""用户要求：
{instruction}

最近对话上下文：
{context_text or "无"}

当前 Mio 属性 JSON：
{json.dumps(current_profile, ensure_ascii=False, indent=2)}

请输出更新后的完整 JSON。"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def update_mio_profile_with_instruction(
    instruction: str,
    context_messages: list[str] | None = None,
) -> dict[str, Any]:
    current = load_mio_profile()
    try:
        raw = await call_chat_completion(
            build_profile_update_messages(current, instruction, context_messages),
            temperature=0.2,
        )
        updated = _extract_json_object(raw)
        if _profile_without_timestamp(updated) == _profile_without_timestamp(current):
            updated = _append_custom_note(current, _fallback_note(instruction, context_messages))
    except LLMConfigError:
        raise
    except Exception as exc:
        raise ValueError(f"属性更新失败：{exc}") from exc
    return save_mio_profile(updated)
