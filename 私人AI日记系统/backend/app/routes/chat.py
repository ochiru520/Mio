from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .. import db
from ..chat_service import chat_with_ai
from ..llm import LLMConfigError
from .. import daily_state_analysis_service
from ..http_service_errors import service_errors_to_http


router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: str = "default"


class MaterialRequest(BaseModel):
    content: str


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return RedirectResponse(url="/diaries", status_code=303)


@router.post("/api/chat")
async def api_chat(payload: ChatRequest):
    user_message = payload.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="消息不能为空。")

    try:
        result = await chat_with_ai(
            user_message,
            conversation_id=payload.conversation_id,
            source="web",
            capture_follow_ups=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "reply": result.reply,
        "replies": result.replies,
        "agent_run_id": result.agent_run_id,
        "agent_run_status": result.agent_run_status,
        "tool_receipts": list(result.tool_receipts),
    }


@router.post("/api/materials")
async def api_add_material(payload: MaterialRequest):
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="日记素材不能为空。")
    material_id = db.add_diary_material(content)
    return {"id": material_id, "content": content}


@router.get("/api/materials/today")
async def api_today_materials():
    return [dict(row) for row in db.list_diary_materials()]


@router.post("/api/state/analyze-today")
@service_errors_to_http
async def api_analyze_today_state():
    return await daily_state_analysis_service.analyze_today_state()
