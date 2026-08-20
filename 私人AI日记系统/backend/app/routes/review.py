from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import db
from ..llm import LLMConfigError
from ..markdown_rendering import render_safe_markdown
from ..review_service import generate_review_for_date
from ..web import templates
from .diary import _validated_diary_date


router = APIRouter()


@router.get("/reviews", response_class=HTMLResponse)
async def reviews_list_page(request: Request):
    reviews = db.list_reviews()
    return templates.TemplateResponse(
        "回顾列表.html",
        {
            "request": request,
            "reviews": reviews,
        },
    )


@router.get("/api/reviews")
async def api_reviews_list():
    return [dict(row) for row in db.list_reviews()]


@router.get("/reviews/{date}", response_class=HTMLResponse)
async def review_page(request: Request, date: str):
    date = _validated_diary_date(date)
    diary = db.get_diary(date)
    if diary is None:
        raise HTTPException(status_code=404, detail="没有找到这一天的日记。")

    review = db.get_daily_review(date)
    return templates.TemplateResponse(
        "每日回顾.html",
        {
            "request": request,
            "date": date,
            "diary": diary,
            "review": review,
            "body_html": render_safe_markdown(review["markdown_content"]) if review else "",
        },
    )


@router.post("/api/reviews/{date}/generate")
async def api_generate_review(date: str):
    date = _validated_diary_date(date)
    try:
        result = await generate_review_for_date(date, overwrite=True)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"每日回顾生成失败：{exc}") from exc

    return {
        "date": date,
        "markdown": result.markdown_content,
        "review_url": f"/reviews/{date}",
    }
