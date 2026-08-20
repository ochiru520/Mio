from __future__ import annotations

import html
import io
import re
import zipfile
from datetime import date as date_cls

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..llm import LLMConfigError, call_chat_completion
from ..life_loop_service import diary_lifecycle
from ..markdown_rendering import render_safe_markdown
from ..model_registry import list_model_profiles
from ..prompts import build_diary_edit_messages, build_diary_messages
from ..web import templates


router = APIRouter()

DAILY_THIRTY_STATUS_OPTIONS = {"done", "partial", "missed", "unknown"}
DIARY_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_DIARY_EXPORT_BYTES = 100 * 1024 * 1024


def normalize_diary_date(value: str) -> str:
    if not DIARY_DATE_RE.fullmatch(str(value or "")):
        raise ValueError("日记日期格式应为 YYYY-MM-DD。")
    try:
        return date_cls.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("日记日期无效。") from exc


def _validated_diary_date(value: str) -> str:
    try:
        return normalize_diary_date(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _diary_export_entries(rows) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    total_bytes = 0
    for row in rows:
        try:
            normalized = normalize_diary_date(str(row["date"]))
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="日记数据包含无效日期，已停止导出。") from exc
        content = str(row["markdown_content"] or "")
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > MAX_DIARY_EXPORT_BYTES:
            raise HTTPException(status_code=413, detail="日记导出内容超过 100 MB，请缩小导出范围。")
        entries.append((f"{normalized}.md", content))
    return entries


def _diary_model_id() -> str:
    """Resolve the configured default profile instead of sending an empty ID."""
    profiles = [profile for profile in list_model_profiles() if profile.base_urls and profile.api_key]
    selected = next((profile for profile in profiles if profile.is_default), None)
    if selected is None and profiles:
        selected = profiles[0]
    if selected is None:
        raise LLMConfigError("没有可用的默认模型，请先在模型设置中配置一个模型。")
    return selected.id


class DiaryUpdateRequest(BaseModel):
    markdown_content: str
    mood_tags: str = ""
    daily_thirty_status: str = "unknown"


class DiaryConfirmRequest(BaseModel):
    confirmed: bool = True


def _chat_log_for_rows(rows) -> str:
    parts: list[str] = []
    for row in rows:
        speaker = "用户" if row["role"] == "user" else "AI"
        parts.append(f"[{row['created_at']}] {speaker}：{row['content']}")
    return "\n\n".join(parts)


def _material_log_for_rows(rows) -> str:
    return "\n".join(f"- {row['content']}" for row in rows)


TAGS_LINE_RE = re.compile(r"^\s*标签[：:]\s*(.+?)\s*$", re.M)


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
) -> dict[str, str]:
    date = normalize_diary_date(date)
    markdown_content = markdown_content.strip()
    if not markdown_content:
        raise HTTPException(status_code=400, detail="日记内容不能为空。")

    effective_status = daily_thirty_status or _resolve_daily_thirty_status(date, markdown_content)
    if effective_status not in DAILY_THIRTY_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="每日三十状态无效。")

    title = _extract_title(date, markdown_content)
    diary_path = settings.diary_dir / f"{date}.md"
    diary_path.write_text(markdown_content, encoding="utf-8")
    db.upsert_diary(
        date,
        title,
        markdown_content,
        mood_tags.strip(),
        effective_status,
        confirmed_at,
    )
    return {
        "date": date,
        "title": title,
        "path": str(diary_path),
        "daily_thirty_status": effective_status,
    }


async def edit_diary_with_instruction(date: str, instruction: str) -> dict[str, str]:
    date = normalize_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    messages = build_diary_edit_messages(date, diary["markdown_content"], instruction.strip())
    try:
        markdown_content = await call_chat_completion(messages, temperature=0.25)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    markdown_content, edited_tags = _extract_tags(markdown_content)
    result = save_diary_markdown(
        date,
        markdown_content,
        edited_tags or diary["mood_tags"],
        _resolve_daily_thirty_status(date, markdown_content),
        diary["confirmed_at"],
    )
    result["markdown"] = markdown_content
    return result


@router.get("/diaries", response_class=HTMLResponse)
async def diary_list_page(request: Request, q: str = Query(default="")):
    query = q.strip()
    diaries = db.search_diaries(query)
    return templates.TemplateResponse(
        "日记列表.html",
        {"request": request, "diaries": diaries, "query": query},
    )


@router.get("/diaries/export/all.zip")
async def export_all_diaries():
    diaries = db.list_diary_exports()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in _diary_export_entries(diaries):
            archive.writestr(name, content)

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="all-diaries.zip"'},
    )


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _photos_for_date(date: str) -> list[str]:
    date = normalize_diary_date(date)
    folder = settings.photo_dir / date
    if not folder.is_dir():
        return []
    return sorted(
        item.name
        for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() in PHOTO_EXTENSIONS
    )


@router.get("/diaries/{date}", response_class=HTMLResponse)
async def diary_detail_page(request: Request, date: str):
    date = _validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    body_html = render_safe_markdown(diary["markdown_content"])
    return templates.TemplateResponse(
        "日记详情.html",
        {
            "request": request,
            "diary": diary,
            "body_html": body_html,
            "photos": _photos_for_date(date),
        },
    )


@router.get("/diaries/{date}/download")
async def download_diary(date: str):
    date = _validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    return Response(
        content=diary["markdown_content"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{date}.md"'},
    )


@router.get("/diaries/{date}/edit", response_class=HTMLResponse)
async def diary_edit_page(request: Request, date: str):
    date = _validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    return templates.TemplateResponse(
        "日记编辑.html",
        {
            "request": request,
            "diary": diary,
            "status_options": [
                ("unknown", "未判定"),
                ("done", "完成"),
                ("partial", "部分完成"),
                ("missed", "未完成"),
            ],
        },
    )


@router.post("/api/diary/generate-today")
async def generate_today_diary():
    return await generate_today_diary_payload()


async def generate_today_diary_payload() -> dict[str, str]:
    return await generate_diary_for_date_payload(db.today_string(), overwrite=True)


async def generate_diary_for_date_payload(date: str, overwrite: bool = True) -> dict[str, str]:
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
        raise HTTPException(status_code=400, detail=f"{date} 还没有聊天记录或日记素材。")

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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    markdown_content, auto_tags = _extract_tags(markdown_content)
    result = save_diary_markdown(
        date,
        markdown_content,
        auto_tags,
        _resolve_daily_thirty_status(date, markdown_content),
        "",
    )
    db.mark_materials_used(date)
    result["overwritten"] = existing is not None
    result["skipped"] = False
    result["markdown"] = markdown_content

    return result


@router.get("/api/diaries")
async def api_list_diaries(q: str = Query(default="")):
    return [dict(row) for row in db.search_diaries(q)]


@router.put("/api/diaries/{date}")
async def api_update_diary(date: str, payload: DiaryUpdateRequest):
    date = _validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    markdown_content = payload.markdown_content.strip()
    if not markdown_content:
        raise HTTPException(status_code=400, detail="日记内容不能为空。")
    if payload.daily_thirty_status not in DAILY_THIRTY_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="每日三十状态无效。")

    return save_diary_markdown(
        date,
        markdown_content,
        payload.mood_tags.strip(),
        payload.daily_thirty_status,
    )


@router.get("/api/diaries/{date}")
async def api_get_diary(date: str):
    date = _validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")
    result = dict(diary)
    result["markdown_content_html_escaped"] = html.escape(result["markdown_content"])
    result["life_loop"] = diary_lifecycle(date)
    return result


@router.post("/api/diaries/{date}/confirm")
async def api_confirm_diary(date: str, payload: DiaryConfirmRequest):
    date = _validated_diary_date(date)
    confirmed_at = db.set_diary_confirmed(date, payload.confirmed)
    if confirmed_at is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")
    return {
        "date": date,
        "confirmed": payload.confirmed,
        "confirmed_at": confirmed_at,
    }


@router.delete("/api/diaries/{date}")
async def api_delete_diary(date: str):
    date = _validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    diary_path = settings.diary_dir / f"{date}.md"
    deleted_file_content: bytes | None = None
    if diary_path.exists():
        try:
            deleted_file_content = diary_path.read_bytes()
            diary_path.unlink()
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"日记文件正在使用或无法删除：{exc}") from exc
    try:
        deleted_from_database = db.delete_diary(date)
    except Exception as database_error:
        if deleted_file_content is not None:
            try:
                diary_path.write_bytes(deleted_file_content)
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"日记数据库删除失败，恢复日记文件也失败：{database_error}；{exc}",
                ) from database_error
        raise HTTPException(status_code=500, detail=f"日记数据库删除失败，文件已恢复：{database_error}") from database_error
    if not deleted_from_database:
        if deleted_file_content is not None:
            try:
                diary_path.write_bytes(deleted_file_content)
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"日记数据库删除失败，恢复日记文件也失败：{exc}",
                ) from exc
        raise HTTPException(status_code=409, detail="日记数据已发生变化，请刷新后重试。")

    return {"date": date, "deleted": True}


@router.get("/diaries/export/month/{year}/{month}.zip")
async def export_month_diaries(year: int, month: int):
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="导出月份无效。")
    # 构造日期模式 YYYY-MM-%
    date_pattern = f"{year:04d}-{month:02d}-%"

    # 查询该月所有日记
    with db.get_conn() as conn:
        diaries = conn.execute(
            """
            SELECT date, markdown_content
            FROM diaries
            WHERE date LIKE ?
            ORDER BY date
            """,
            (date_pattern,),
        ).fetchall()

    if not diaries:
        raise HTTPException(status_code=404, detail="该月没有找到任何日记。")

    # 打包成ZIP
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in _diary_export_entries(diaries):
            archive.writestr(name, content)

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{year}-{month:02d}-diaries.zip"'},
    )
