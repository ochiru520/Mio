from __future__ import annotations

import re
from datetime import date as date_cls, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import db
from ..llm import LLMConfigError
from ..markdown_rendering import render_safe_markdown
from ..web import templates
from ..weekly_review_service import generate_weekly_review, week_end_for
from ..review_sources import metadata, period_payload


router = APIRouter()

WEEK_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validated_week_start(week_start: str) -> str:
    if not WEEK_START_RE.match(week_start):
        raise HTTPException(status_code=400, detail="周起始日期格式应为 YYYY-MM-DD。")
    try:
        parsed = date_cls.fromisoformat(week_start)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="周起始日期无效。") from exc
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


@router.get("/weekly", response_class=HTMLResponse)
async def weekly_list_page(request: Request):
    reviews = []
    for row in db.list_weekly_reviews():
        start = str(row["week_start"])
        end = week_end_for(start)
        reviews.append({**dict(row), "week_end": end, **metadata("weekly", start, period_payload(start, end))})
    return templates.TemplateResponse(
        "周报列表.html",
        {"request": request, "reviews": reviews},
    )


@router.get("/api/weekly")
async def api_weekly_list():
    result = []
    for row in db.list_weekly_reviews():
        start = str(row["week_start"])
        end = week_end_for(start)
        result.append({**dict(row), "week_end": end, **metadata("weekly", start, period_payload(start, end))})
    return result


@router.get("/weekly/{week_start}", response_class=HTMLResponse)
async def weekly_detail_page(request: Request, week_start: str):
    normalized = _validated_week_start(week_start)
    review = db.get_weekly_review(normalized)
    review_metadata = metadata("weekly", normalized, period_payload(normalized, week_end_for(normalized))) if review else None
    prev_week = (date_cls.fromisoformat(normalized) - timedelta(days=7)).isoformat()
    next_week = (date_cls.fromisoformat(normalized) + timedelta(days=7)).isoformat()
    return templates.TemplateResponse(
        "周报详情.html",
        {
            "request": request,
            "week_start": normalized,
            "week_end": week_end_for(normalized),
            "review": review,
            "review_metadata": review_metadata,
            "body_html": render_safe_markdown(review["markdown_content"]) if review else "",
            "prev_week": prev_week,
            "next_week": next_week,
        },
    )


@router.post("/api/weekly/{week_start}/generate")
async def api_generate_weekly_review(week_start: str):
    normalized = _validated_week_start(week_start)
    try:
        result = await generate_weekly_review(normalized, overwrite=True)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"周复盘生成失败：{exc}") from exc

    return {
        "week_start": result.week_start,
        "week_end": result.week_end,
        "markdown": result.markdown_content,
        "review_url": f"/weekly/{result.week_start}",
        **metadata("weekly", result.week_start, period_payload(result.week_start, result.week_end)),
    }
