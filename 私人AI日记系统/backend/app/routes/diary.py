from __future__ import annotations
from ..repositories import diary_export_repository as repository

import html
import io
import zipfile
import difflib

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .. import db
from ..config import settings
from ..life_loop_service import diary_lifecycle
from ..markdown_rendering import render_safe_markdown
from ..web import templates
from ..diary_dates import normalize_diary_date
from ..diary_operations_service import (
    DAILY_THIRTY_STATUS_OPTIONS, generate_today_diary_payload, save_diary_markdown,
)
from ..http_service_errors import service_errors_to_http, validated_diary_date


router = APIRouter()

MAX_DIARY_EXPORT_BYTES = 100 * 1024 * 1024


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


class DiaryUpdateRequest(BaseModel):
    markdown_content: str
    mood_tags: str = ""
    daily_thirty_status: str = "unknown"
    expected_revision: int | None = None
    change_reason: str = ""


class DiaryConfirmRequest(BaseModel):
    confirmed: bool = True


class DiaryRestoreRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)


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
    date = validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    body_html = render_safe_markdown(diary["markdown_content"])
    versions = db.list_diary_versions(date)
    return templates.TemplateResponse(
        "日记详情.html",
        {
            "request": request,
            "diary": diary,
            "body_html": body_html,
            "photos": _photos_for_date(date),
            "versions": versions,
        },
    )


@router.get("/diaries/{date}/download")
async def download_diary(date: str):
    date = validated_diary_date(date)
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
    date = validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    return templates.TemplateResponse(
        "日记编辑.html",
        {
            "request": request,
            "diary": diary,
            "revision": (db.list_diary_versions(date)[0]["revision"] if db.list_diary_versions(date) else 0),
            "status_options": [
                ("unknown", "未判定"),
                ("done", "完成"),
                ("partial", "部分完成"),
                ("missed", "未完成"),
            ],
        },
    )


@router.post("/api/diary/generate-today")
@service_errors_to_http
async def generate_today_diary():
    return await generate_today_diary_payload()


@router.get("/api/diaries")
async def api_list_diaries(q: str = Query(default="")):
    return [dict(row) for row in db.search_diaries(q)]


@router.get("/api/diary/automatic")
async def automatic_diary_status():
    from ..daily_diary_service import get_daily_diary_status
    return get_daily_diary_status()


@router.post("/api/diary/automatic/check")
async def retry_automatic_diary():
    from ..daily_diary_service import request_check
    return request_check()


@router.put("/api/diaries/{date}")
@service_errors_to_http
async def api_update_diary(date: str, payload: DiaryUpdateRequest):
    date = validated_diary_date(date)
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
        expected_revision=payload.expected_revision,
        change_reason=payload.change_reason,
    )


@router.get("/api/diaries/{date}")
async def api_get_diary(date: str):
    date = validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")
    result = dict(diary)
    result["markdown_content_html_escaped"] = html.escape(result["markdown_content"])
    result["life_loop"] = diary_lifecycle(date)
    versions = db.list_diary_versions(date)
    result["revision"] = int(versions[0]["revision"]) if versions else 0
    result["versions"] = [dict(item) for item in versions]
    return result


@router.get("/api/diaries/{date}/versions")
async def api_list_diary_versions(date: str):
    date = validated_diary_date(date)
    if db.get_diary(date) is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")
    return {"date": date, "versions": [dict(item) for item in db.list_diary_versions(date)]}


@router.get("/api/diaries/{date}/versions/{revision}/diff")
async def api_diff_diary_version(date: str, revision: int):
    date = validated_diary_date(date)
    version = db.get_diary_version(date, revision)
    current = db.get_diary(date)
    if version is None or current is None:
        raise HTTPException(status_code=404, detail="没有找到指定日记版本。")
    diff = "".join(difflib.unified_diff(
        str(version["markdown_content"] or "").splitlines(keepends=True),
        str(current["markdown_content"] or "").splitlines(keepends=True),
        fromfile=f"revision-{revision}", tofile="current",
    ))
    return {"date": date, "revision": revision, "current_revision": max((int(row["revision"]) for row in db.list_diary_versions(date)), default=0), "diff": diff}


@router.post("/api/diaries/{date}/versions/{revision}/restore")
@service_errors_to_http
async def api_restore_diary_version(date: str, revision: int, payload: DiaryRestoreRequest = DiaryRestoreRequest()):
    date = validated_diary_date(date)
    if revision < 1:
        raise HTTPException(status_code=400, detail="日记版本无效。")
    version = db.get_diary_version(date, revision)
    current = db.get_diary(date)
    if version is None or current is None:
        raise HTTPException(status_code=404, detail="没有找到指定日记版本。")
    result = save_diary_markdown(
        date, version["markdown_content"], version["mood_tags"], version["daily_thirty_status"],
        version["confirmed_at"], change_reason=f"从 revision {revision} 恢复",
        expected_revision=payload.expected_revision, restored_title=version["title"],
    )
    return {**result, "restored_from_revision": revision}


@router.post("/api/diaries/{date}/confirm")
async def api_confirm_diary(date: str, payload: DiaryConfirmRequest):
    date = validated_diary_date(date)
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
    date = validated_diary_date(date)
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
    with repository.transaction() as conn:
        diaries = conn.month_diaries((date_pattern,)).fetchall()

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
