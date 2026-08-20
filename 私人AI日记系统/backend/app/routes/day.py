from __future__ import annotations

from fastapi import APIRouter

from .chat import analyze_today_state
from .diary import generate_today_diary_payload


router = APIRouter()


@router.post("/api/day/end-today")
async def end_today():
    state = await analyze_today_state()
    diary = await generate_today_diary_payload()
    return {
        "date": diary["date"],
        "state": state,
        "diary": diary,
        "diary_url": f"/diaries/{diary['date']}",
    }
