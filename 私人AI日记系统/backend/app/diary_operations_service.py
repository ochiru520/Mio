"""Shared diary generation, editing and version-aware persistence.

HTTP, QQ and background schedulers share these operations; transport error
mapping remains at their adapters.
"""
from __future__ import annotations

import re

from . import db
from .config import settings
from .diary_dates import normalize_diary_date
from .llm import LLMConfigError, call_chat_completion
from .model_runtime import operation
from .model_registry import list_model_profiles
from .provider_compat import model_connection_configured
from .prompts import build_diary_edit_messages, build_diary_messages
from .service_errors import ServiceOperationError

DAILY_THIRTY_STATUS_OPTIONS = {"done", "partial", "missed", "unknown"}
TAGS_LINE_RE = re.compile(r"^\s*标签[：:]\s*(.+?)\s*$", re.M)


def _diary_model_id() -> str:
    """Resolve the configured default profile instead of sending an empty ID."""
    profiles = [profile for profile in list_model_profiles() if model_connection_configured(profile)]
    from .model_runtime import model_selection
    requested, _ = model_selection("record")
    if requested and requested != "auto":
        selected = next((profile for profile in profiles if profile.id == requested), None)
        if selected is None:
            raise LLMConfigError("日记专用模型不可用，请在日记设置中重新选择模型并检查服务地址、启动状态或鉴权。")
        return selected.id
    selected = next((profile for profile in profiles if profile.is_default), None)
    if selected is None and profiles:
        selected = profiles[0]
    if selected is None:
        raise LLMConfigError("没有可用的默认模型，请先在模型设置中配置一个模型。")
    return selected.id


def _chat_log_for_rows(rows) -> str:
    parts: list[str] = []
    for row in rows:
        speaker = "用户" if row["role"] == "user" else "AI"
        parts.append(f"[{row['created_at']}] {speaker}：{row['content']}")
    return "\n\n".join(parts)


def _material_log_for_rows(rows) -> str:
    return "\n".join(f"- {row['content']}" for row in rows)


def _extract_tags(markdown_content: str) -> tuple[str, str]:
    """把日记末尾的“标签：xxx、yyy”行提取成 mood_tags，并从正文中移除。"""
    match = TAGS_LINE_RE.search(markdown_content)
    if not match:
        return markdown_content, ""
    raw = match.group(1)
    normalized = raw.replace("，", "、").replace(",", "、").replace(" ", "")
    tags = [tag for tag in normalized.split("、") if tag][:4]
    cleaned = TAGS_LINE_RE.sub("", markdown_content).strip()
    return cleaned, "、".join(tags)


def _extract_title(date: str, markdown_content: str) -> str:
    for line in markdown_content.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return match.group(1).strip()
    return f"{date} 日记"


def _extract_daily_thirty_status(markdown_content: str) -> str:
    text = markdown_content.strip()
    patterns = [
        r"(?:每日三十|今日成长)[\s\S]{0,160}?状态[：:]\s*([^\n\r。；;，,]+)",
        r"状态[：:]\s*(完成|已完成|部分完成|部分|未完成|没完成|未确认|未判定|done|partial|missed|unknown)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        raw_value = match.group(1).strip().lower()
        if any(label in raw_value for label in ("未完成", "没完成", "missed")):
            return "missed"
        if any(label in raw_value for label in ("部分完成", "部分", "partial")):
            return "partial"
        if any(label in raw_value for label in ("未确认", "未判定", "unknown")):
            return "unknown"
        if any(label in raw_value for label in ("已完成", "完成", "done")):
            return "done"
    return "unknown"


def _resolve_daily_thirty_status(date: str, markdown_content: str) -> str:
    state = db.get_daily_state(date)
    if state and state["daily_thirty_status"] in DAILY_THIRTY_STATUS_OPTIONS - {"unknown"}:
        return state["daily_thirty_status"]

    markdown_status = _extract_daily_thirty_status(markdown_content)
    if markdown_status != "unknown":
        return markdown_status

    if state and state["daily_thirty_status"] in DAILY_THIRTY_STATUS_OPTIONS:
        return state["daily_thirty_status"]
    return "unknown"


def save_diary_markdown(
    date: str,
    markdown_content: str,
    mood_tags: str = "",
    daily_thirty_status: str | None = None,
    confirmed_at: str | None = None,
    expected_revision: int | None = None,
    change_reason: str = "",
    restored_title: str | None = None,
) -> dict[str, str]:
    date = normalize_diary_date(date)
    markdown_content = markdown_content.strip()
    if not markdown_content:
        raise ServiceOperationError(status_code=400, detail="日记内容不能为空。")

    effective_status = daily_thirty_status or _resolve_daily_thirty_status(date, markdown_content)
    if effective_status not in DAILY_THIRTY_STATUS_OPTIONS:
        raise ServiceOperationError(status_code=400, detail="每日三十状态无效。")

    title = restored_title if restored_title is not None else _extract_title(date, markdown_content)
    try:
        revision = db.upsert_diary(
            date, title, markdown_content, mood_tags.strip(), effective_status,
            confirmed_at, expected_revision=expected_revision, change_reason=change_reason,
        )
    except ValueError as exc:
        raise ServiceOperationError(status_code=409, detail=str(exc)) from exc
    # A diary revision changes the evidence behind reminders and facts. Mark
    # linked follow-ups stale so the next interaction must re-check the text.
    try:
        db.mark_pending_thread_source_stale("diary", date, revision)
    except Exception:
        pass
    diary_path = settings.diary_dir / f"{date}.md"
    try:
        diary_path.write_text(markdown_content, encoding="utf-8")
    except OSError as exc:
        raise ServiceOperationError(status_code=500, detail=f"日记文件保存失败：{exc}") from exc
    return {
        "date": date,
        "title": title,
        "path": str(diary_path),
        "daily_thirty_status": effective_status,
        "revision": revision,
    }


@operation("record", automatic=True)
async def edit_diary_with_instruction(date: str, instruction: str) -> dict[str, str]:
    date = normalize_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise ServiceOperationError(status_code=404, detail="没有找到这一天的日记。")

    messages = build_diary_edit_messages(date, diary["markdown_content"], instruction.strip())
    try:
        markdown_content = await call_chat_completion(messages, temperature=0.25)
    except LLMConfigError as exc:
        raise ServiceOperationError(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise ServiceOperationError(status_code=502, detail=str(exc)) from exc

    markdown_content, edited_tags = _extract_tags(markdown_content)
    result = save_diary_markdown(
        date,
        markdown_content,
        edited_tags or diary["mood_tags"],
        _resolve_daily_thirty_status(date, markdown_content),
        diary["confirmed_at"],
        change_reason="AI 按指令编辑",
    )
    result["markdown"] = markdown_content
    return result


async def generate_today_diary_payload() -> dict[str, str]:
    return await generate_diary_for_date_payload(db.today_string(), overwrite=True)


@operation("record", automatic=True)
async def generate_diary_for_date_payload(date: str, overwrite: bool = True) -> dict[str, str]:
    from .agent_task_service import automatic_work_paused
    if automatic_work_paused():
        raise ServiceOperationError(status_code=409, detail="隐私暂停中，日记生成已停止。")
    date = normalize_diary_date(date)
    existing = db.get_diary(date)
    if existing is not None and not overwrite:
        return {
            "date": date,
            "title": existing["title"],
            "path": str(settings.diary_dir / f"{date}.md"),
            "daily_thirty_status": existing["daily_thirty_status"],
            "overwritten": False,
            "skipped": True,
        }

    rows = db.get_today_messages(date)
    materials = db.list_diary_materials(date)
    if not rows and not materials:
        raise ServiceOperationError(status_code=400, detail=f"{date} 还没有聊天记录或日记素材。")

    chat_log = _chat_log_for_rows(rows)
    material_log = _material_log_for_rows(materials)
    if material_log:
        chat_log = f"{chat_log}\n\n[日记素材暂存箱]\n{material_log}" if chat_log else f"[日记素材暂存箱]\n{material_log}"
    state_row = db.get_daily_state(date)
    daily_state = dict(state_row) if state_row is not None else None
    messages = build_diary_messages(date, chat_log, daily_state)

    try:
        markdown_content = await call_chat_completion(
            messages,
            temperature=0.4,
            model_id=_diary_model_id(),
            reasoning_level="medium",
        )
    except LLMConfigError as exc:
        raise ServiceOperationError(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise ServiceOperationError(status_code=502, detail=str(exc)) from exc

    if automatic_work_paused():
        raise ServiceOperationError(status_code=409, detail="隐私暂停中，日记生成已停止。")
    # A manual save may have completed while the model request was awaiting.
    # No await is allowed between this check and the synchronous save below.
    if not overwrite and db.get_diary(date) is not None:
        return {"date": date, "skipped": True, "overwritten": False}
    markdown_content, auto_tags = _extract_tags(markdown_content)
    result = save_diary_markdown(
        date,
        markdown_content,
        auto_tags,
        _resolve_daily_thirty_status(date, markdown_content),
        "",
        change_reason="AI 自动生成",
    )
    db.mark_materials_used(date)
    result["overwritten"] = existing is not None
    result["skipped"] = False
    result["markdown"] = markdown_content

    return result
