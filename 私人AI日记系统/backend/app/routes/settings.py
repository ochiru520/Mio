from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .. import companion_service
from ..config import load_runtime_settings, save_runtime_settings, settings
from ..documents import load_manual_statuses
from ..llm import config_status
from ..mio_profile import load_mio_profile, save_mio_profile_from_settings
from .onebot import active_websocket_count
from ..web import templates
from ..web_search_service import perform_web_lookup, should_use_web_lookup


router = APIRouter()


class ProfileAvatarRequest(BaseModel):
    data_url: str = Field(min_length=20, max_length=18_000_000)


class WebSearchTestRequest(BaseModel):
    query: str = Field(default="DeepSeek 最新消息", min_length=1, max_length=160)


@router.get("/api/settings/runtime")
async def runtime_settings_api():
    return {"settings": await asyncio.to_thread(load_runtime_settings)}


@router.patch("/api/settings/runtime")
async def update_runtime_settings_api(changes: dict[str, object]):
    try:
        values = await asyncio.to_thread(save_runtime_settings, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"运行设置保存失败：{exc}") from exc
    return {"settings": values}


@router.post("/api/settings/web-search/test")
async def test_web_search_api(payload: WebSearchTestRequest):
    query = payload.query.strip()
    triggered = should_use_web_lookup(query)
    if not settings.web_search_enabled:
        return {
            "ok": False,
            "enabled": False,
            "triggered": False,
            "query": query,
            "message": "联网搜索当前未开启，请先打开并保存联网搜索。",
            "sources": [],
            "attempts": [],
        }
    lookup = await perform_web_lookup(query)
    if lookup is None:
        return {
            "ok": False,
            "enabled": True,
            "triggered": triggered,
            "query": query,
            "message": "这句话没有触发联网规则；测试时可加入“查一下”“最新”或“现在”。",
            "sources": [],
            "attempts": [],
        }
    sources = [
        {"title": source.title, "url": source.url, "snippet": source.snippet[:240]}
        for source in lookup.sources
    ]
    return {
        "ok": bool(sources),
        "enabled": True,
        "triggered": True,
        "query": lookup.query,
        "engine": lookup.engine,
        "message": (
            f"联网正常，{lookup.engine or '实时数据源'}返回 {len(sources)} 条结果。"
            if sources
            else f"联网请求已触发，但没有拿到结果：{lookup.error or '未知原因'}"
        ),
        "sources": sources,
        "attempts": list(lookup.attempts),
    }


@router.get("/api/settings/profile")
async def editable_profile_api():
    profile = await asyncio.to_thread(load_mio_profile)
    return {
        "profile": profile,
        "avatar": {
            "url": "/api/settings/avatar",
            "custom": settings.mio_avatar_path.is_file(),
        },
        "user_avatar": {
            "url": "/api/settings/user-avatar",
            "custom": settings.user_avatar_path.is_file(),
        },
        "chat_background": {
            "url": "/api/settings/chat-background",
            "custom": settings.chat_background_path.is_file(),
        },
    }


@router.patch("/api/settings/profile")
async def update_editable_profile_api(payload: dict[str, object]):
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail="缺少有效的 Mio 属性对象。")
    try:
        saved = await asyncio.to_thread(save_mio_profile_from_settings, profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Mio 属性保存失败：{exc}") from exc
    return {"profile": saved}


@router.get("/api/settings/avatar")
async def profile_avatar_api():
    path = companion_service.profile_avatar_path()
    if path is None:
        raise HTTPException(status_code=404, detail="还没有可用的 Mio 头像。")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/api/settings/avatar")
async def update_profile_avatar_api(payload: ProfileAvatarRequest):
    try:
        path = await asyncio.to_thread(companion_service.save_profile_avatar_data_url, payload.data_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": True, "url": "/api/settings/avatar", "path": str(path)}


@router.delete("/api/settings/avatar")
async def reset_profile_avatar_api():
    await asyncio.to_thread(settings.mio_avatar_path.unlink, missing_ok=True)
    return {"reset": True, "url": "/api/settings/avatar"}


@router.get("/api/settings/user-avatar")
async def user_avatar_api():
    if not settings.user_avatar_path.is_file():
        raise HTTPException(status_code=404, detail="还没有设置用户头像。")
    return FileResponse(settings.user_avatar_path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/api/settings/user-avatar")
async def update_user_avatar_api(payload: ProfileAvatarRequest):
    try:
        path = await asyncio.to_thread(companion_service.save_user_avatar_data_url, payload.data_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": True, "url": "/api/settings/user-avatar", "path": str(path)}


@router.delete("/api/settings/user-avatar")
async def reset_user_avatar_api():
    await asyncio.to_thread(settings.user_avatar_path.unlink, missing_ok=True)
    return {"reset": True, "url": "/api/settings/user-avatar"}


@router.get("/api/settings/chat-background")
async def chat_background_api():
    if not settings.chat_background_path.is_file():
        raise HTTPException(status_code=404, detail="还没有设置对话背景。")
    return FileResponse(settings.chat_background_path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/settings/chat-background")
async def update_chat_background_api(payload: ProfileAvatarRequest):
    try:
        path = await asyncio.to_thread(companion_service.save_chat_background_data_url, payload.data_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": True, "url": "/api/settings/chat-background", "path": str(path)}


@router.delete("/api/settings/chat-background")
async def reset_chat_background_api():
    await asyncio.to_thread(settings.chat_background_path.unlink, missing_ok=True)
    return {"reset": True, "url": "/api/settings/chat-background"}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    profile = load_mio_profile()
    return templates.TemplateResponse(
        "设置.html",
        {
            "request": request,
            "llm_status": config_status(),
            "web_search_status": {
                "enabled": settings.web_search_enabled,
                "max_results": settings.web_search_max_results,
                "timeout_seconds": settings.web_search_timeout_seconds,
                "page_max_chars": settings.web_page_max_chars,
            },
            "daily_review_status": {
                "enabled": settings.daily_review_auto_enabled,
                "hour": settings.daily_review_auto_hour,
                "minute": settings.daily_review_auto_minute,
                "notify_qq": settings.daily_review_auto_notify_qq,
                "check_seconds": settings.daily_review_check_seconds,
            },
            "daily_diary_status": {
                "enabled": settings.daily_diary_auto_enabled,
                "check_seconds": settings.daily_diary_check_seconds,
                "day_boundary_hour": settings.day_boundary_hour,
            },
            "memory_status": {
                "chat_history_limit": settings.chat_history_limit,
                "chat_raw_history_limit": settings.chat_raw_history_limit,
                "chat_context_max_chars": settings.chat_context_max_chars,
                "chat_context_max_tokens": settings.chat_context_max_tokens,
                "chat_context_warning_ratio": settings.chat_context_warning_ratio,
                "chat_context_compress_ratio": settings.chat_context_compress_ratio,
                "chat_recent_keep_messages": settings.chat_recent_keep_messages,
                "memory_context_days": settings.memory_context_days,
                "memory_context_max_chars": settings.memory_context_max_chars,
                "memory_context_messages_per_day": settings.memory_context_messages_per_day,
                "day_boundary_hour": settings.day_boundary_hour,
            },
            "qq_status": {
                "enabled": settings.qq_bot_enabled,
                "token_set": bool(settings.qq_onebot_token),
                "allowed_user_count": len(settings.qq_allowed_user_ids),
                "allowed_group_count": len(settings.qq_allowed_group_ids),
                "group_mention_required": settings.qq_group_mention_required,
                "image_enabled": settings.qq_image_enabled,
                "image_max_count": settings.qq_image_max_count,
                "image_max_bytes": settings.qq_image_max_bytes,
                "image_detail": settings.qq_image_detail,
                "image_send_to_model": settings.qq_image_send_to_model,
                "message_debounce_seconds": settings.qq_message_debounce_seconds,
                "message_incomplete_debounce_seconds": settings.qq_message_incomplete_debounce_seconds,
                "reply_initial_delay_seconds": settings.qq_reply_initial_delay_seconds,
                "reply_delay_seconds": settings.qq_reply_delay_seconds,
                "proactive_enabled": settings.qq_proactive_enabled,
                "proactive_min_idle_minutes": settings.qq_proactive_min_idle_minutes,
                "proactive_max_idle_minutes": settings.qq_proactive_max_idle_minutes,
                "proactive_day_start_hour": settings.qq_proactive_day_start_hour,
                "proactive_day_end_hour": settings.qq_proactive_day_end_hour,
                "proactive_check_seconds": settings.qq_proactive_check_seconds,
                "websocket_connections": active_websocket_count(),
                "websocket_url": f"ws://127.0.0.1:{settings.app_port}/onebot/ws",
            },
            "manuals": load_manual_statuses(),
            "mio_profile": {
                "path": str(settings.mio_profile_path),
                "exists": settings.mio_profile_path.exists(),
                "updated_at": str(profile.get("updated_at") or ""),
                "name": str((profile.get("identity") or {}).get("name") or "Mio"),
            },
            "env_files": [
                {
                    "name": "后端配置",
                    "path": str(settings.backend_dir / ".env"),
                    "exists": (settings.backend_dir / ".env").exists(),
                },
                {
                    "name": "项目配置",
                    "path": str(settings.project_root / ".env"),
                    "exists": (settings.project_root / ".env").exists(),
                },
            ],
            "data_paths": [
                {"name": "项目目录", "path": str(settings.project_root)},
                {"name": "数据目录", "path": str(settings.data_dir)},
                {"name": "日记目录", "path": str(settings.diary_dir)},
                {"name": "Mio 属性", "path": str(settings.mio_profile_path)},
                {"name": "数据库", "path": str(settings.db_path)},
            ],
        },
    )
