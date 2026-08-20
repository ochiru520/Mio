from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..llm import LLMConfigError
from ..monthly_review_service import generate_monthly_review, month_bounds, normalize_month


router = APIRouter()


@router.get("/api/monthly")
async def api_monthly_list():
    items = []
    for row in db.list_monthly_reviews():
        month = str(row["month"])
        month_start, month_end = month_bounds(month)
        items.append({**dict(row), "month_start": month_start, "month_end": month_end})
    return items


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
    }
