from __future__ import annotations

import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import db
from ..web import templates

router = APIRouter()


def _mood_trend(days: int = 30) -> list[dict]:
    start = (datetime.date.fromisoformat(db.today_string()) - datetime.timedelta(days=days - 1)).isoformat()
    return [
        {
            "date": row["date"],
            "mood": row["mood"] or "",
            "mood_score": int(row["mood_score"] or 0),
            "daily_thirty_status": row["daily_thirty_status"],
        }
        for row in db.list_daily_states_since(start)
    ]


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(
    request: Request,
    year: int = Query(default=0),
    month: int = Query(default=0),
):
    today = datetime.date.fromisoformat(db.today_string())
    if not year or not month:
        year, month = today.year, today.month
    # clamp to valid range
    month = max(1, min(12, month))
    stats = db.get_diary_stats()
    calendar = db.get_calendar_data(year, month)
    return templates.TemplateResponse(
        "统计.html",
        {
            "request": request,
            "stats": stats,
            "calendar": calendar,
            "year": year,
            "month": month,
            "mood_trend": _mood_trend(30),
        },
    )


@router.get("/api/mood-trend")
async def api_mood_trend(days: int = Query(default=30, ge=7, le=120)):
    return _mood_trend(days)


@router.get("/api/stats")
async def api_stats():
    return db.get_diary_stats()


@router.get("/api/calendar/{year}/{month}")
async def api_calendar(year: int, month: int):
    return db.get_calendar_data(year, month)
