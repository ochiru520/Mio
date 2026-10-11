from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..llm import LLMConfigError
from ..monthly_review_service import generate_monthly_review, month_bounds, normalize_month
from ..review_sources import metadata, period_payload


router = APIRouter()


@router.get("/api/monthly")
async def api_monthly_list():
    items = {}
    for row in db.list_monthly_reviews():
        month = str(row["month"])
        items[month] = {**dict(row), "source_diaries": []}
    for row in db.list_monthly_diary_index():
        month = str(row["date"])[:7]
        item = items.setdefault(month, {"month": month, "markdown_content": "", "source_diaries": []})
        item["source_diaries"].append(dict(row))
    for month, item in items.items():
        month_start, month_end = month_bounds(month)
        item.update(month_start=month_start, month_end=month_end, diary_count=len(item["source_diaries"]))
        item.update(metadata("monthly", month, period_payload(month_start, month_end)))
    return sorted(items.values(), key=lambda item: item["month"], reverse=True)


@router.post("/api/monthly/{month}/generate")
async def api_generate_monthly_review(month: str):
    try:
        normalized = normalize_month(month)
        result = await generate_monthly_review(normalized, overwrite=True)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"月记生成失败：{exc}") from exc

    return {
        "month": result.month,
        "month_start": result.month_start,
        "month_end": result.month_end,
        "markdown": result.markdown_content,
        **metadata("monthly", result.month, period_payload(result.month_start, result.month_end)),
    }
