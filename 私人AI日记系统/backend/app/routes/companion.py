from __future__ import annotations

import asyncio
import base64
from difflib import SequenceMatcher
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import (
    call_session_service,
    companion_service,
    db,
    local_vision_service,
    pet_event_service,
    route_observation_service,
    screen_observation_service,
    system_audio_service,
    window_topology_service,
)
from ..auto_router import AutoRoute, build_task_profile, select_auto_route
from ..chat_idempotency import (
    claim_request,
    content_fingerprint,
    normalize_error_detail,
    pending_error,
    request_fingerprint,
)
from ..agent_loop_service import abandon_deferred_final_response
from ..chat_service import chat_with_ai, persist_generated_chat_result
from ..conversation_runtime import ChatRunCancelledError, chat_run_coordinator
from ..config import settings
from ..llm import ModelRequestError, resolve_model_id
from ..image_service import image_attachment_from_data_url


router = APIRouter(prefix="/api/companion")
DESKTOP_PET_CONVERSATION_ID = "desktop_pet"
_pet_call_started_observer = False
_pet_call_previous_audio_model = ""
_pet_call_previous_audio_engine = ""
_pet_call_resource_owner = ""
CALL_ASR_MODEL = "large-v3-turbo"
PHONE_TOOL_INTENT_RE = re.compile(
    r"(查一下|搜索|联网|提醒|记住|写进|创建|删除|修改|打开|关闭|设置|"
    r"看(?:看|一下)?(?:屏幕|画面|窗口)|(?:看到|看见).*(?:什么|啥)|现在.*(?:状态|正常|能力)|"
    r"检查.*(?:状态|服务|语音|模型)|做一件|帮我做|替我)",
    re.IGNORECASE,
)


def _phone_turn_policy(transcript: str, configured_reasoning: str) -> tuple[bool, str]:
    needs_tools = bool(PHONE_TOOL_INTENT_RE.search(str(transcript or "")))
    if needs_tools:
        return True, configured_reasoning
    return False, "off"


def _route_metadata(route: AutoRoute | None) -> tuple[str, dict[str, object], tuple[dict[str, object], ...]]:
    if route is None:
        return "conversation", {}, ()
    task_profile = dict(getattr(route, "task_profile", {}) or {})
    candidates = tuple(getattr(route, "candidates", ()) or ())
    return str(task_profile.get("task_type") or "conversation"), task_profile, candidates


def _record_chat_route_failure(
    route: AutoRoute | None,
    *,
    source: str,
    request_id: str,
    selected_model_id: str,
    selected_reasoning_level: str,
    error_code: str,
) -> None:
    task_type, task_profile, candidates = _route_metadata(route)
    route_observation_service.record_failed_route(
        source=source,
        mode="automatic" if route is not None else "manual",
        request_id=request_id,
        selected_model_id=selected_model_id,
        selected_reasoning_level=selected_reasoning_level,
        actual_model_id=selected_model_id,
        difficulty=str(getattr(route, "difficulty", "")) if route is not None else "",
        reason=(
            str(getattr(route, "reason", "自动路由"))
            if route is not None
            else "桌宠或电话设置指定模型与思考档位"
        ),
        latency_budget_ms=int(getattr(route, "latency_budget_ms", 0) or 0) if route is not None else 0,
        error_code=error_code,
        task_type=task_type,
        task_profile=task_profile,
        candidates=candidates,
    )


def _record_chat_route_success(
    route: AutoRoute | None,
    *,
    source: str,
    request_id: str,
    selected_model_id: str,
    selected_reasoning_level: str,
    result: object,
) -> None:
    task_type, task_profile, candidates = _route_metadata(route)
    escalated_from = str(getattr(result, "route_escalated_from_model_id", "") or "")
    if route is not None and escalated_from:
        _record_chat_route_failure(
            route,
            source=source,
            request_id=request_id,
            selected_model_id=escalated_from,
            selected_reasoning_level=selected_reasoning_level,
            error_code="primary_model_failed_before_final_reply",
        )
    route_observation_service.record_completed_route(
        source=source,
        mode="automatic" if route is not None else "manual",
        request_id=request_id,
        selected_model_id=selected_model_id,
        selected_reasoning_level=str(getattr(result, "reasoning_level", selected_reasoning_level) or selected_reasoning_level),
        actual_model_id=str(getattr(result, "model_id", selected_model_id) or selected_model_id),
        connection_route=str(getattr(result, "route", "") or ""),
        difficulty=str(getattr(route, "difficulty", "")) if route is not None else "",
        reason=(
            str(getattr(route, "reason", "自动路由"))
            if route is not None
            else "桌宠或电话设置指定模型与思考档位"
        ),
        latency_budget_ms=int(getattr(route, "latency_budget_ms", 0) or 0) if route is not None else 0,
        first_token_latency_ms=getattr(result, "first_token_latency_ms", None),
        total_latency_ms=getattr(result, "total_latency_ms", None),
        request_cost_yuan=getattr(result, "request_cost_yuan", None),
        request_cost_source=str(getattr(result, "request_cost_source", "") or ""),
        task_type=task_type,
        task_profile=task_profile,
        candidates=candidates,
        escalated_from_model_id=escalated_from,
    )


@router.websocket("/ws")
async def companion_websocket(websocket: WebSocket):
    await pet_event_service.serve(websocket)


class CompanionSettingsRequest(BaseModel):
    voice_enabled: bool = True
    voice_startup_enabled: bool = False
    voice_idle_timeout_seconds: int = Field(default=180, ge=0, le=1800)
    voice_engine: str = Field(default="gpt_sovits", pattern="^(gpt_sovits|cloud)$")
    local_voice_runtime: str = Field(default="genie", pattern="^(genie|gpt_sovits)$")
    cloud_tts_api_key: str = Field(default="", max_length=2000)
    cloud_tts_app_id: str = Field(default="", max_length=200)
    cloud_tts_speaker: str = Field(default="zh_female_vv_uranus_bigtts", max_length=200)
    cloud_tts_speech_rate: int = Field(default=0, ge=-50, le=100)
    default_voice_profile_id: str = Field(default="mio", max_length=80)
    voice_profiles: dict[str, dict[str, object]] = Field(default_factory=dict)
    chat_model_id: str = Field(default="auto", max_length=200)
    chat_reasoning_level: str = Field(default="auto", max_length=50)
    pet_chat_model_id: str = Field(default="auto", max_length=200)
    pet_chat_reasoning_level: str = Field(default="auto", max_length=50)
    pet_call_asr_engine: str = Field(
        default="auto",
        pattern="^(auto|whisper|sensevoice|paraformer)$",
    )
    pet_call_input_language: str = Field(default="zh", pattern="^(auto|zh|ja)$")
    speech_translation_model_id: str = Field(default="deepseek-v4-flash", max_length=200)
    pet_call_silence_ms: int = Field(default=650, ge=350, le=1800)
    pet_call_voice_threshold: float = Field(default=0.018, ge=0.004, le=0.12)
    pet_call_min_speech_ms: int = Field(default=280, ge=150, le=1500)
    pet_call_max_turn_seconds: int = Field(default=18, ge=5, le=45)
    voice_volume: int = Field(default=85, ge=0, le=100)
    voice_streaming_enabled: bool = True
    pet_speech_language: str = Field(default="zh", pattern="^(zh|ja)$")
    speak_proactive: bool = False
    speak_screen_observations: bool = True
    speak_game_observations: bool = True
    qq_voice_mode: str = Field(default="adaptive", pattern="^(explicit|adaptive|always)$")
    gpt_sovits_url: str = "http://127.0.0.1:9880"
    gpt_sovits_ref_audio: str = ""
    gpt_sovits_prompt_text: str = Field(default="", max_length=1000)
    gpt_sovits_prompt_language: str = "ja"
    gpt_sovits_text_language: str = "auto"
    gpt_sovits_translate_to_japanese: bool = False
    gpt_sovits_gpt_weights: str = ""
    gpt_sovits_sovits_weights: str = ""
    screen_ai_enabled: bool = True
    screen_audio_enabled: bool = True
    screen_audio_model: str = Field(default="base", pattern="^(tiny|base|small)$")
    screen_audio_language: str = Field(default="auto", pattern="^(auto|zh|ja|en)$")
    screen_audio_chunk_seconds: int = Field(default=5, ge=4, le=15)
    screen_vision_route: str = Field(default="local", pattern="^(local|cloud)$")
    screen_vision_model_id: str = Field(default="auto-fast", max_length=200)
    screen_direct_voice_enabled: bool = True
    screen_change_threshold: float = Field(default=8.0, ge=1.0, le=50.0)
    screen_analysis_interval_seconds: int = Field(default=5, ge=5, le=600)
    screen_request_timeout_seconds: int = Field(default=25, ge=5, le=60)
    screen_voice_cooldown_seconds: int = Field(default=5, ge=5, le=600)
    screen_minimum_importance: float = Field(default=0.62, ge=0.0, le=1.0)
    screen_daily_cost_limit_yuan: float = Field(default=5.0, ge=0.1, le=1000.0)
    bubble_seconds: int = Field(default=9, ge=3, le=30)
    pet_size_percent: int = Field(default=150, ge=80, le=240)
    pet_renderer: str = Field(default="live2d", pattern="^(classic|live2d)$")
    live2d_model_id: str = Field(default="hiyori", min_length=1, max_length=100)
    live2d_scale: float = Field(default=1.0, ge=0.65, le=1.55)
    live2d_vertical_offset: float = Field(default=0.0, ge=-0.35, le=0.35)
    live2d_follow_cursor: bool = True
    live2d_idle_motion: bool = True
    live2d_click_motion: bool = True
    live2d_smart_passthrough: bool = True
    live2d_click_through_locked: bool = False
    live2d_speech_bubble_enabled: bool = True
    live2d_keep_visible: bool = False
    live2d_always_on_top: bool = True
    live2d_disable_gpu: bool = False
    live2d_motion_slots: dict[str, dict[str, str]] = Field(default_factory=dict)
    live2d_expression_slots: dict[str, dict[str, str]] = Field(default_factory=dict)


class StartupGreetingSettingsRequest(BaseModel):
    enabled: bool = True


class QqStartupSettingsRequest(BaseModel):
    enabled: bool = False


class CompanionChatSettingsRequest(BaseModel):
    model_id: str = Field(default="auto", max_length=200)
    reasoning_level: str = Field(default="auto", max_length=50)
    voice_language: str | None = Field(default=None, pattern="^(auto|zh|ja)$")


class AvatarRequest(BaseModel):
    data_url: str


class SpriteSheetRequest(BaseModel):
    data_url: str


class Live2DModelImportRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=2000)
    display_name: str = Field(default="", max_length=100)


class Live2DPreviewRequest(BaseModel):
    data_url: str


class VoiceReferenceRequest(BaseModel):
    name: str = "参考音频.wav"
    data_url: str
    profile_id: str = Field(default="", max_length=80)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=1000)
    emotion: str = Field(default="", pattern="^(|neutral|gentle|cheerful|concerned|serious|shy)$")
    model_id: str = Field(default="", max_length=200)
    language: str = Field(default="", pattern="^(|zh|ja)$")


class CompanionChatImageRequest(BaseModel):
    name: str = Field(default="粘贴的图片.png", max_length=120)
    data_url: str


class CompanionChatRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    model_id: str = Field(default="auto", max_length=200)
    reasoning_level: str = Field(default="auto", max_length=50)
    images: list[CompanionChatImageRequest] = Field(default_factory=list, max_length=5)
    client_request_id: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9._:-]*$")


class CompanionChatCancelRequest(BaseModel):
    client_request_id: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9._:-]*$")


class CompanionCallTurnRequest(BaseModel):
    wav_base64: str = Field(min_length=20, max_length=8_000_000)
    language: str = Field(default="zh", pattern="^(auto|zh|ja)$")
    call_session_id: str = Field(default="", max_length=80)
    turn_id: int = Field(default=0, ge=0, le=1_000_000)


class CompanionCallControlRequest(BaseModel):
    call_session_id: str = Field(default="", max_length=80)
    response_id: str = Field(default="", max_length=160)


class CompanionCallDeviceRequest(BaseModel):
    call_session_id: str = Field(min_length=1, max_length=80)
    device_id: str = Field(default="", max_length=500)
    label: str = Field(default="", max_length=500)
    sample_rate: int = Field(default=0, ge=0, le=384000)
    channel_count: int = Field(default=0, ge=0, le=32)
    echo_cancellation: bool | None = None
    noise_suppression: bool | None = None
    auto_gain_control: bool | None = None


class CompanionPositionRequest(BaseModel):
    x: int = Field(ge=-10000, le=10000)
    y: int = Field(ge=-10000, le=10000)


class CompanionSizeRequest(BaseModel):
    percent: int = Field(ge=80, le=240)


class CompanionChatWindowStateRequest(BaseModel):
    open: bool


class WindowTopologyEventRequest(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    runtime: str = Field(min_length=1, max_length=80)
    window_id: str = Field(min_length=1, max_length=100)
    pid: int = Field(default=0, ge=0)
    action: str = Field(min_length=1, max_length=80)
    correlation_id: str = Field(min_length=1, max_length=120)
    visible: bool = False
    focused: bool = False
    bounds: dict[str, int] = Field(default_factory=dict)


class Live2DMotionPreviewRequest(BaseModel):
    group: str = Field(min_length=1, max_length=120)
    index: int | None = Field(default=None, ge=0, le=100)


class Live2DExpressionPreviewRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=120)


class GameWindowRequest(BaseModel):
    hwnd: int


class WindowObservationRequest(BaseModel):
    interval_ms: int = Field(default=1000, ge=500, le=5000)
    capture_only: bool = False


class ScreenObservationRequest(WindowObservationRequest):
    scope: str = Field(default="primary", pattern="^(primary|all)$")


def _status_payload() -> dict[str, object]:
    window_status = companion_service.window_observer.status()
    return {
        "pet": companion_service.pet_status(),
        "screen": window_status,
        "screen_analysis": screen_observation_service.status(),
        "system_audio": system_audio_service.status(),
        "window": window_status,
        "game": window_status,
        "voice_training": companion_service.voice_training_status(),
        "voice_runtime": companion_service.voice_runtime_status(),
        "call": call_runtime_status(),
        "window_topology": window_topology_service.snapshot(),
    }


def call_runtime_status() -> dict[str, object]:
    return {
        **call_session_service.manager.status(),
        "started_observer": _pet_call_started_observer,
        "previous_audio_model": _pet_call_previous_audio_model,
        "asr": system_audio_service.status(),
    }


def _primary_conversation_id() -> str:
    if settings.qq_allowed_user_ids:
        return f"qq_private_{settings.qq_allowed_user_ids[0]}"
    return "default"


def _desktop_pet_conversation_id() -> str:
    return DESKTOP_PET_CONVERSATION_ID


def _pet_call_screen_context() -> str:
    observation = screen_observation_service.status()
    foreground = pet_event_service.status().get("foreground") or {}
    parts = [
        "【电话模式的当前电脑上下文】",
        "这是系统私下提供的环境信息，不要复述标签，也不要声称看到了这里没有的信息。",
    ]
    title = str(foreground.get("title") or "").strip()
    process_name = str(foreground.get("process_name") or "").strip()
    if title or process_name:
        parts.append(f"当前前台窗口：{title or '标题未知'}（{process_name or '程序未知'}）")
    summary = str(observation.get("last_event_summary") or "").strip()
    if summary:
        parts.append(f"最近画面摘要：{summary}")
    game_state = observation.get("game_state")
    if isinstance(game_state, dict) and game_state:
        compact_state = ", ".join(
            f"{key}={value}" for key, value in list(game_state.items())[:8]
            if value is not None and value != "" and value != [] and value != {}
        )
        if compact_state:
            parts.append(f"最近画面状态：{compact_state}")
    last_analyzed_at = str(observation.get("last_analyzed_at") or "").strip()
    if last_analyzed_at:
        parts.append(f"画面摘要时间：{last_analyzed_at}")
    parts.append("像电话聊天一样直接回答，优先一到三句自然短句。只有与当前话题有关时才提到屏幕内容。")
    return "\n".join(parts)


def _ensure_call_asr_ready() -> dict[str, object]:
    config = companion_service.load_config()
    requested_engine = str(config.get("pet_call_asr_engine") or "auto").strip().lower()
    resolved_engine = "whisper" if requested_engine == "auto" else requested_engine
    if resolved_engine == "paraformer" and config.get("pet_call_input_language") == "ja":
        return {
            **system_audio_service.status(),
            "ready": False,
            "last_error": "Paraformer 中文模型不支持日语电话识别，请改用自动、Faster-Whisper 或 SenseVoice。",
        }
    forced = dict(config)
    forced["screen_audio_enabled"] = True
    forced["asr_engine"] = resolved_engine
    if resolved_engine == "whisper":
        forced["asr_model"] = CALL_ASR_MODEL
    current = system_audio_service.start(forced)
    return current


def _release_call_resources_sync(call_session_id: str = "") -> None:
    global _pet_call_started_observer, _pet_call_previous_audio_model
    global _pet_call_previous_audio_engine, _pet_call_resource_owner

    if (
        call_session_id
        and _pet_call_resource_owner
        and call_session_id != _pet_call_resource_owner
    ):
        return

    if _pet_call_started_observer:
        companion_service.window_observer.stop()
        screen_observation_service.end_session()
    _pet_call_started_observer = False

    config = companion_service.load_config()
    if not companion_service.window_observer.status().get("running") or not config.get("screen_audio_enabled", True):
        system_audio_service.stop()
    else:
        restored = dict(config)
        restored["asr_engine"] = _pet_call_previous_audio_engine or "whisper"
        if _pet_call_previous_audio_model in {"tiny", "base", "small"}:
            restored["screen_audio_model"] = _pet_call_previous_audio_model
        system_audio_service.start(restored)
    _pet_call_previous_audio_model = ""
    _pet_call_previous_audio_engine = ""
    _pet_call_resource_owner = ""


async def _release_call_resources(call_session_id: str = "") -> None:
    await asyncio.to_thread(_release_call_resources_sync, call_session_id)


def _preview_response(not_found_detail: str) -> Response:
    content = companion_service.window_observer.take_preview()
    if content is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/status")
async def companion_status():
    return await asyncio.to_thread(_status_payload)


@router.get("/chat-anchor")
async def companion_chat_anchor():
    return {"anchor": companion_service.pet_chat_anchor()}


@router.post("/chat-window/state")
async def companion_chat_window_state(payload: CompanionChatWindowStateRequest):
    pet_event_service.publish("chat_window_state", {"open": payload.open})
    return {"ok": True, "open": payload.open}


@router.get("/window-topology")
async def companion_window_topology():
    return window_topology_service.snapshot()


@router.post("/window-topology/events")
async def companion_window_topology_event(payload: WindowTopologyEventRequest):
    try:
        event = window_topology_service.record(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"recorded": True, "event": event}


@router.post("/agent/show")
async def companion_agent_show():
    result = companion_service.show_agent_window()
    if not result["ok"]:
        raise HTTPException(status_code=503, detail="没有找到正在运行或已安装的 Mio。")
    return result


async def _wake_pet_from_screen() -> None:
    try:
        await screen_observation_service.analyze_once(force=True, wake=True)
    finally:
        if companion_service.pet_running():
            companion_service.set_pet_activity(
                "idle",
                source="desktop_pet_wake",
                ttl_seconds=0,
            )


def _start_pet_with_screen_observation() -> bool:
    screen_observation_service.set_capture_only(False)
    companion_service.window_observer.select_screen("primary")
    companion_service.window_observer.start(1000)
    companion_service.save_config({"screen_audio_enabled": True})
    system_audio_service.start(companion_service.load_config())
    companion_service.set_pet_activity(
        "thinking",
        emotion="gentle",
        source="desktop_pet_wake",
        ttl_seconds=120,
    )
    return bool(companion_service.load_config().get("voice_enabled", True))


def _start_companion_runtime() -> tuple[dict[str, object], bool, bool]:
    was_running = companion_service.pet_running()
    companion_service.start_pet()
    should_wake = not was_running
    should_warm = _start_pet_with_screen_observation() if should_wake else False
    return _status_payload(), should_wake, should_warm


def _stop_companion_runtime() -> None:
    companion_service.stop_pet()
    companion_service.window_observer.stop()
    screen_observation_service.end_session()
    companion_service.save_config({"screen_audio_enabled": False})
    system_audio_service.stop()


def _restart_companion_runtime() -> tuple[dict[str, object], bool]:
    _stop_companion_runtime()
    companion_service.start_pet()
    should_warm = _start_pet_with_screen_observation()
    return _status_payload(), should_warm


def _apply_companion_settings(changes: dict[str, object]) -> None:
    was_running = companion_service.pet_running()
    if "screen_audio_enabled" in changes:
        # 系统声音观察是视觉观察的一部分，不再接受独立开关状态。
        changes["screen_audio_enabled"] = bool(
            companion_service.window_observer.status().get("running")
        )
    previous_model_id = str(companion_service.load_config().get("live2d_model_id") or "hiyori")
    selected_model_id = str(changes.get("live2d_model_id") or "")
    if selected_model_id:
        companion_service.select_live2d_model(selected_model_id)
    companion_service.save_config(changes)
    if was_running and selected_model_id and selected_model_id != previous_model_id:
        companion_service.restart_pet()
    audio_settings_changed = bool(
        {"screen_audio_enabled", "screen_audio_model", "screen_audio_language", "screen_audio_chunk_seconds"}
        & changes.keys()
    )
    if audio_settings_changed and companion_service.window_observer.status().get("running"):
        system_audio_service.stop()
        saved_config = companion_service.load_config()
        if saved_config["screen_audio_enabled"]:
            system_audio_service.start(saved_config)


def _start_observation_runtime(
    *,
    interval_ms: int,
    capture_only: bool,
    hwnd: int | None = None,
    screen_scope: str = "",
) -> dict[str, object]:
    screen_observation_service.set_capture_only(capture_only)
    observer = companion_service.window_observer
    if screen_scope:
        observer.select_screen(screen_scope)
    elif hwnd:
        observer.select(hwnd)
    result = observer.start(interval_ms)
    if not capture_only:
        companion_service.save_config({"screen_audio_enabled": True})
        if not _pet_call_resource_owner:
            system_audio_service.start(companion_service.load_config())
    return result


def _stop_observation_runtime() -> dict[str, object]:
    result = companion_service.window_observer.stop()
    screen_observation_service.end_session()
    companion_service.save_config({"screen_audio_enabled": False})
    if not _pet_call_resource_owner:
        system_audio_service.stop()
    return result


def _prepare_call_runtime(call_session_id: str) -> dict[str, object]:
    global _pet_call_started_observer, _pet_call_previous_audio_model
    global _pet_call_previous_audio_engine, _pet_call_resource_owner
    audio_status = system_audio_service.status()
    _pet_call_resource_owner = call_session_id
    _pet_call_previous_audio_model = (
        str(audio_status.get("model") or "") if audio_status.get("running") else ""
    )
    previous_engine = str(audio_status.get("requested_engine") or audio_status.get("engine") or "")
    _pet_call_previous_audio_engine = {
        "faster-whisper": "whisper",
        "sensevoice-small": "sensevoice",
        "paraformer-zh": "paraformer",
    }.get(previous_engine, previous_engine)
    observer_was_running = bool(companion_service.window_observer.status().get("running"))
    _pet_call_started_observer = not observer_was_running
    if _pet_call_started_observer:
        companion_service.window_observer.select_screen("primary")
        companion_service.window_observer.start(1000)
        screen_observation_service.set_capture_only(False)
    return audio_status


def _normalized_echo_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+", "", str(text or "")).lower()


def _is_recent_playback_echo(transcript: str, reference: str) -> tuple[bool, float]:
    heard = _normalized_echo_text(transcript)
    spoken = _normalized_echo_text(reference)
    if len(heard) < 3 or len(spoken) < 3:
        return False, 0.0
    similarity = SequenceMatcher(None, heard, spoken).ratio()
    contained = len(heard) >= 4 and heard in spoken
    coverage = len(heard) / max(1, len(spoken))
    is_echo = contained and coverage >= 0.18 or similarity >= 0.72
    return is_echo, round(similarity, 4)


@router.post("/start")
async def companion_start(background_tasks: BackgroundTasks):
    try:
        status, should_wake, should_warm = await asyncio.to_thread(_start_companion_runtime)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"桌宠启动失败：{exc}") from exc
    except (ValueError, RuntimeError) as exc:
        await asyncio.to_thread(companion_service.stop_pet)
        raise HTTPException(status_code=500, detail=f"桌宠启动后无法观察屏幕：{exc}") from exc
    if should_warm:
        background_tasks.add_task(companion_service.warm_voice_runtime_async)
    if should_wake:
        background_tasks.add_task(_wake_pet_from_screen)
    return status


@router.post("/stop")
async def companion_stop():
    await asyncio.to_thread(_stop_companion_runtime)
    await asyncio.to_thread(local_vision_service.unload_model)
    return await asyncio.to_thread(_status_payload)


@router.post("/restart")
async def companion_restart(background_tasks: BackgroundTasks):
    try:
        status, should_warm = await asyncio.to_thread(_restart_companion_runtime)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"桌宠重启失败：{exc}") from exc
    except (ValueError, RuntimeError) as exc:
        await asyncio.to_thread(companion_service.stop_pet)
        raise HTTPException(status_code=500, detail=f"桌宠重启后无法观察屏幕：{exc}") from exc
    if should_warm:
        background_tasks.add_task(companion_service.warm_voice_runtime_async)
    background_tasks.add_task(_wake_pet_from_screen)
    return status


@router.patch("/settings")
async def companion_settings(
    payload: CompanionSettingsRequest,
    background_tasks: BackgroundTasks = None,
):
    changes = payload.model_dump(exclude_unset=True)
    try:
        await asyncio.to_thread(_apply_companion_settings, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if changes.get("screen_vision_route") == "cloud":
        await asyncio.to_thread(local_vision_service.unload_model)
    if background_tasks is not None and changes.get("pet_speech_language") in {"zh", "ja"}:
        background_tasks.add_task(
            companion_service.warm_voice_language_async,
            str(changes["pet_speech_language"]),
        )
    return await asyncio.to_thread(_status_payload)


@router.get("/live2d/models")
async def companion_live2d_models():
    return {"models": await asyncio.to_thread(companion_service.available_live2d_models)}


@router.post("/live2d/models/import")
async def companion_live2d_model_import(payload: Live2DModelImportRequest):
    try:
        model = await asyncio.to_thread(
            companion_service.import_live2d_model_directory,
            payload.source_path,
            payload.display_name,
        )
        await asyncio.to_thread(
            companion_service.save_config,
            {"pet_renderer": "live2d", "live2d_model_id": model["id"]},
        )
        if await asyncio.to_thread(companion_service.pet_running):
            await asyncio.to_thread(companion_service.restart_pet)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": True, "model": model, **(await asyncio.to_thread(_status_payload))}


@router.delete("/live2d/models/{model_id}")
async def companion_live2d_model_delete(model_id: str):
    was_running = await asyncio.to_thread(companion_service.pet_running)
    try:
        deleted = await asyncio.to_thread(companion_service.delete_live2d_model, model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="没有找到这个自定义 Live2D 模型。")
    if was_running:
        await asyncio.to_thread(companion_service.restart_pet)
    return {"deleted": True, **(await asyncio.to_thread(_status_payload))}


@router.get("/live2d/models/{model_id}/preview")
async def companion_live2d_model_preview(model_id: str):
    path = companion_service.live2d_model_preview_path(model_id)
    if path is None:
        raise HTTPException(status_code=404, detail="这个模型没有预览图。")
    media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.post("/live2d/models/{model_id}/preview")
async def companion_live2d_model_preview_save(model_id: str, payload: Live2DPreviewRequest):
    try:
        path = await asyncio.to_thread(
            companion_service.save_live2d_model_preview_data_url,
            model_id,
            payload.data_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "saved": True,
        "preview_url": f"/api/companion/live2d/models/{model_id}/preview?v={path.stat().st_mtime_ns}",
        **(await asyncio.to_thread(_status_payload)),
    }


@router.post("/live2d/motion/preview")
async def companion_live2d_motion_preview(payload: Live2DMotionPreviewRequest):
    if not pet_event_service.has_desktop_renderer():
        raise HTTPException(status_code=409, detail="请先启动 Live2D 桌宠。")
    event = {"group": payload.group.strip(), "index": payload.index}
    pet_event_service.publish("motion_preview", event)
    return {"ok": True, **event}


@router.post("/live2d/expression/preview")
async def companion_live2d_expression_preview(payload: Live2DExpressionPreviewRequest):
    if not pet_event_service.has_desktop_renderer():
        raise HTTPException(status_code=409, detail="请先启动 Live2D 桌宠。")
    event = {"expression": payload.expression.strip()}
    pet_event_service.publish("expression_preview", event)
    return {"ok": True, **event}


@router.post("/app-ready")
async def companion_app_ready():
    return {"ok": True, "released": companion_service.signal_frontend_ready()}


@router.get("/startup-greeting")
async def startup_greeting_settings():
    saved = await asyncio.to_thread(companion_service.load_config)
    return {"enabled": bool(saved.get("startup_greeting_enabled", True))}


@router.patch("/startup-greeting")
async def update_startup_greeting_settings(payload: StartupGreetingSettingsRequest):
    saved = await asyncio.to_thread(
        companion_service.save_config,
        {"startup_greeting_enabled": payload.enabled},
    )
    return {"enabled": bool(saved["startup_greeting_enabled"])}


@router.get("/qq-startup")
async def qq_startup_settings():
    saved = await asyncio.to_thread(companion_service.load_config)
    return {"enabled": bool(saved.get("qq_startup_enabled", False))}


@router.patch("/qq-startup")
async def update_qq_startup_settings(payload: QqStartupSettingsRequest):
    saved = await asyncio.to_thread(companion_service.save_config, {"qq_startup_enabled": payload.enabled})
    return {"enabled": bool(saved["qq_startup_enabled"])}


@router.get("/chat-settings")
async def get_companion_chat_settings():
    saved = await asyncio.to_thread(companion_service.load_config)
    return {
        "model_id": saved["chat_model_id"],
        "reasoning_level": saved["chat_reasoning_level"],
        "voice_language": saved["gpt_sovits_text_language"],
    }


@router.patch("/chat-settings")
async def companion_chat_settings(
    payload: CompanionChatSettingsRequest,
    background_tasks: BackgroundTasks = None,
):
    changes = {
        "chat_model_id": payload.model_id,
        "chat_reasoning_level": payload.reasoning_level,
    }
    if payload.voice_language is not None:
        changes["gpt_sovits_text_language"] = payload.voice_language
    saved = await asyncio.to_thread(companion_service.save_config, changes)
    if background_tasks is not None and payload.voice_language in {"zh", "ja"}:
        background_tasks.add_task(
            companion_service.warm_voice_language_async,
            payload.voice_language,
        )
    return {
        "model_id": saved["chat_model_id"],
        "reasoning_level": saved["chat_reasoning_level"],
        "voice_language": saved["gpt_sovits_text_language"],
    }


@router.get("/avatar")
async def companion_avatar():
    path = companion_service.default_avatar_path()
    if path is None:
        raise HTTPException(status_code=404, detail="还没有可用的桌宠头像。")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/sprite/{state}")
async def companion_sprite(state: str):
    path = companion_service.pet_sprite_path(state)
    if path is None:
        raise HTTPException(status_code=404, detail="这个桌宠动作还没有素材。")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/avatar")
async def companion_avatar_save(payload: AvatarRequest):
    try:
        await asyncio.to_thread(companion_service.save_avatar_data_url, payload.data_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if await asyncio.to_thread(companion_service.pet_running):
        await asyncio.to_thread(companion_service.restart_pet)
    return await asyncio.to_thread(_status_payload)


@router.post("/spritesheet")
async def companion_sprite_sheet_save(payload: SpriteSheetRequest):
    try:
        await asyncio.to_thread(companion_service.save_sprite_sheet_data_url, payload.data_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await asyncio.to_thread(_status_payload)


@router.post("/voice/speak")
async def companion_voice_speak(payload: SpeechRequest):
    try:
        spoken = await asyncio.to_thread(
            companion_service.speak_text,
            payload.text,
            context=payload.context,
            emotion=payload.emotion or None,
            model_id=payload.model_id,
            language=payload.language,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Mio 的语音启动失败：{exc}") from exc
    return {"spoken": spoken}


@router.post("/chat")
async def companion_chat(payload: CompanionChatRequest):
    message = payload.message.strip()
    conversation_id = _desktop_pet_conversation_id()
    try:
        claim = claim_request(
            payload.client_request_id,
            request_fingerprint({
                "channel": "companion_chat",
                "conversation_id": conversation_id,
                "message": message,
                "model_id": payload.model_id,
                "reasoning_level": payload.reasoning_level,
                "images": [
                    {"name": item.name, "content_hash": content_fingerprint(item.data_url)}
                    for item in payload.images
                ],
            }),
            conversation_id=conversation_id,
            source="desktop_pet",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not claim.created:
        if claim.status == "succeeded":
            return claim.response
        if claim.status == "failed":
            raise HTTPException(status_code=claim.http_status or 500, detail=claim.error)
        raise HTTPException(status_code=409, detail=pending_error(claim))

    try:
        images = [
            image_attachment_from_data_url(item.data_url, source=item.name)
            for item in payload.images
        ]
    except (RuntimeError, ValueError) as exc:
        detail = normalize_error_detail(
            f"图片无法读取：{exc}",
            code="invalid_chat_image",
            request_id=claim.client_request_id,
        )
        db.fail_chat_request(claim.client_request_id, http_status=400, error=detail)
        raise HTTPException(status_code=400, detail=detail) from exc
    if not message and not images:
        detail = normalize_error_detail(
            "消息或图片不能为空。",
            code="invalid_chat_request",
            request_id=claim.client_request_id,
        )
        db.fail_chat_request(claim.client_request_id, http_status=400, error=detail)
        raise HTTPException(status_code=400, detail=detail)
    config = companion_service.load_config()
    if (
        config.get("voice_enabled", True)
        and config.get("local_voice_runtime") == companion_service.GENIE_VOICE_RUNTIME
    ):
        # Hide a reclaimed Genie's first real ONNX inference behind the model
        # response time. The discarded warmup WAV never enters playback.
        companion_service.warm_voice_language_async(
            str(config.get("pet_speech_language") or "zh")
        )
    routing_history = list(
        db.get_recent_messages(limit=12, conversation_id=conversation_id)
    )
    screen_follow_up = bool(
        not images
        and screen_observation_service.is_screen_chat_follow_up(message, routing_history)
    )
    requested_model = payload.model_id.strip()
    requested_reasoning = payload.reasoning_level.strip()
    route = None
    try:
        if screen_follow_up:
            result = await screen_observation_service.analyze_screen_chat_follow_up(
                message,
                conversation_id=conversation_id,
                request_id=claim.client_request_id,
                source="desktop_pet",
            )
            selected_model = result.model_id
            selected_reasoning = result.reasoning_level
        else:
            task_profile = build_task_profile(
                message,
                history_rows=routing_history,
                image_count=len(images),
            )
            if not requested_model or requested_model == "auto":
                requested_model = str(config.get("pet_chat_model_id") or "auto").strip() or "auto"
                requested_reasoning = (
                    str(config.get("pet_chat_reasoning_level") or "auto").strip() or "auto"
                )
            if requested_model == "auto":
                route = select_auto_route(
                    message,
                    history_rows=routing_history,
                    image_count=len(images),
                )
                selected_model = route.model_id
                selected_reasoning = route.reasoning_level
            else:
                selected_model = resolve_model_id(requested_model)
                selected_reasoning = requested_reasoning
            use_fast_chat = bool(
                not task_profile.requires_tools
                and task_profile.task_type in {"conversation", "analysis"}
                and not images
            )
            if use_fast_chat and selected_reasoning in {"", "auto", "off", "low"}:
                selected_reasoning = "off"
            result = await chat_with_ai(
                message,
                conversation_id=conversation_id,
                source="desktop_pet",
                image_attachments=images,
                reasoning_level=selected_reasoning,
                model_id=selected_model,
                fallback_model_id=str(getattr(route, "fallback_model_id", "") or ""),
                fallback_reasoning_level=str(getattr(route, "fallback_reasoning_level", "") or ""),
                capture_follow_ups=True,
                request_id=claim.client_request_id,
                agent_tools_enabled=not use_fast_chat,
                fast_path=use_fast_chat,
            )
    except ValueError as exc:
        detail = normalize_error_detail(
            str(exc),
            code="invalid_chat_request",
            request_id=claim.client_request_id,
        )
        db.fail_chat_request(claim.client_request_id, http_status=400, error=detail)
        raise HTTPException(status_code=400, detail=detail) from exc
    except ChatRunCancelledError as exc:
        detail = normalize_error_detail(
            f"回复已取消：{exc}",
            code="request_cancelled",
            request_id=claim.client_request_id,
        )
        db.fail_chat_request(claim.client_request_id, http_status=409, error=detail)
        raise HTTPException(status_code=409, detail=detail) from exc
    except ModelRequestError as exc:
        _record_chat_route_failure(
            route,
            source="desktop_pet",
            request_id=claim.client_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            error_code="model_request_failed",
        )
        detail = exc.public_detail()
        detail["request_id"] = claim.client_request_id
        db.fail_chat_request(claim.client_request_id, http_status=502, error=detail)
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        _record_chat_route_failure(
            route,
            source="desktop_pet",
            request_id=claim.client_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            error_code="model_request_failed",
        )
        detail = normalize_error_detail(
            f"Mio 暂时没有回复：{exc}",
            code="model_request_failed",
            request_id=claim.client_request_id,
        )
        db.fail_chat_request(claim.client_request_id, http_status=502, error=detail)
        raise HTTPException(status_code=502, detail=detail) from exc
    # Agent 面板的桌宠会话由后端直接触发本机语音，关闭桌宠窗口时也能听到。
    # 桌宠进程的消息轮询会跳过同一 source，避免重复播放。
    voice_attempted = bool(
        result.replies
        and companion_service.load_config().get("voice_enabled", True)
    )
    spoken = False
    voice_delegated = bool(voice_attempted and pet_event_service.has_clients())
    voice_error = ""
    if voice_attempted and not voice_delegated:
        speech_text = "\n".join(result.replies)
        speech_emotion = getattr(result, "speech_emotion", "") or companion_service.infer_speech_emotion(
            speech_text,
            message,
        )
        companion_service.set_pet_activity(
            "speaking",
            emotion=speech_emotion,
            source="desktop_pet",
            ttl_seconds=max(15, min(90, len(speech_text) / 3 + 12)),
        )
        spoken = await asyncio.to_thread(
            companion_service.speak_text,
            speech_text,
            context=message,
            emotion=speech_emotion,
            wait=False,
            model_id=result.model_id or selected_model,
            language=str(companion_service.load_config().get("pet_speech_language") or "zh"),
        )
        companion_service.set_pet_activity(
            "responding",
            emotion=speech_emotion if spoken else "concerned",
            source="desktop_pet",
            ttl_seconds=5,
        )
        if not spoken:
            voice_status = await asyncio.to_thread(companion_service.voice_runtime_status)
            voice_error = str(voice_status.get("last_error") or "语音没有完成播放")
    elif voice_delegated:
        speech_text = "\n".join(result.replies)
        speech_emotion = getattr(result, "speech_emotion", "") or companion_service.infer_speech_emotion(
            speech_text,
            message,
        )
        companion_service.set_pet_activity(
            "responding",
            emotion=speech_emotion,
            source="desktop_pet",
            ttl_seconds=max(8, min(45, len(speech_text) / 4 + 8)),
        )
    response = {
        "reply": result.reply,
        "replies": result.replies,
        "voice_attempted": voice_attempted,
        "spoken": spoken,
        "voice_delegated": voice_delegated,
        "voice_error": voice_error,
        "speech_emotion": getattr(result, "speech_emotion", "") or (
            speech_emotion if voice_attempted else ""
        ),
        "request_id": result.request_id,
        "client_request_id": claim.client_request_id,
        "model_id": result.model_id,
        "provider_id": getattr(result, "provider_id", ""),
        "provider_name": getattr(result, "provider_name", ""),
        "provider_model": getattr(result, "provider_model", ""),
        "provider_request_id": getattr(result, "provider_request_id", ""),
        "route": getattr(result, "route", ""),
        "http_status": getattr(result, "http_status", 0),
        "reasoning_level": result.reasoning_level,
        "agent_run_id": getattr(result, "agent_run_id", ""),
        "agent_run_status": getattr(result, "agent_run_status", ""),
        "tool_receipts": list(getattr(result, "tool_receipts", ()) or ()),
        "route_candidate_model_ids": list(getattr(result, "route_candidate_model_ids", ()) or ()),
        "route_escalated_from_model_id": str(getattr(result, "route_escalated_from_model_id", "") or ""),
    }
    db.complete_chat_request(claim.client_request_id, response)
    if not screen_follow_up:
        _record_chat_route_success(
            route,
            source="desktop_pet",
            request_id=claim.client_request_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            result=result,
        )
    return response


@router.post("/chat/cancel")
async def cancel_companion_chat(payload: CompanionChatCancelRequest):
    cancelled = await chat_run_coordinator.cancel(
        DESKTOP_PET_CONVERSATION_ID,
        source="desktop_pet",
        reason=f"user_cancelled:{payload.client_request_id or 'unknown'}",
    )
    return {
        "cancelled": cancelled,
        "conversation_id": DESKTOP_PET_CONVERSATION_ID,
        "client_request_id": payload.client_request_id,
    }


@router.post("/call/start")
async def companion_call_start():
    previous = call_session_service.manager.status()
    if previous.get("active"):
        previous_response_id = str(previous.get("current_response_id") or "")
        call_session_service.manager.stop(
            str(previous.get("call_session_id") or ""),
            reason="call_replaced",
        )
        await chat_run_coordinator.cancel(
            DESKTOP_PET_CONVERSATION_ID,
            source="desktop_pet_call",
            reason="call_replaced",
        )
        if previous_response_id:
            pet_event_service.publish(
                "speech_interrupt",
                {"reason": "call_ended", "response_id": previous_response_id},
            )
        await _release_call_resources(str(previous.get("call_session_id") or ""))
    session = call_session_service.manager.start()
    call_session_id = str(session["call_session_id"])
    await asyncio.to_thread(_prepare_call_runtime, call_session_id)
    if not call_session_service.manager.is_active_session(call_session_id):
        raise HTTPException(status_code=409, detail="电话会话在启动期间已被新会话替换。")
    asyncio.create_task(screen_observation_service.analyze_once(force=True))
    asr = await asyncio.to_thread(_ensure_call_asr_ready)
    deadline = time.monotonic() + 25
    while not asr.get("ready") and asr.get("running") and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        asr = system_audio_service.status()
    if not call_session_service.manager.is_active_session(call_session_id):
        raise HTTPException(status_code=409, detail="电话会话在语音识别加载期间已被新会话替换。")
    if not asr.get("ready"):
        reason = str(asr.get("last_error") or "本地语音识别加载超时")
        if call_session_service.manager.fail_start(call_session_id, reason):
            await _release_call_resources(call_session_id)
        raise HTTPException(status_code=503, detail=f"语音识别启动失败：{reason}")
    call_config = await asyncio.to_thread(companion_service.load_config)
    if call_config.get("voice_enabled", True):
        companion_service.warm_voice_runtime_async()
    if not call_session_service.manager.mark_listening(call_session_id):
        raise HTTPException(status_code=409, detail="电话会话在接通前已被替换。")
    return {
        "active": True,
        "call_session_id": call_session_id,
        "next_turn_id": 1,
        "asr": asr,
        "settings": {
            key: call_config[key]
            for key in (
                "pet_call_input_language",
                "pet_call_asr_engine",
                "pet_call_silence_ms",
                "pet_call_voice_threshold",
                "pet_call_min_speech_ms",
                "pet_call_max_turn_seconds",
            )
        },
    }


@router.post("/call/stop")
async def companion_call_stop(payload: CompanionCallControlRequest | None = None):
    current = call_session_service.manager.status()
    requested_session_id = str(payload.call_session_id if payload else "")
    response_id = str(current.get("current_response_id") or "")
    stopped = call_session_service.manager.stop(requested_session_id, reason="call_ended")
    if not stopped:
        return {
            "active": bool(current.get("active")),
            "stopped": False,
            "call_session_id": str(current.get("call_session_id") or ""),
        }
    await chat_run_coordinator.cancel(
        DESKTOP_PET_CONVERSATION_ID,
        source="desktop_pet_call",
        reason="call_ended",
    )
    if response_id:
        pet_event_service.publish(
            "speech_interrupt",
            {"reason": "call_ended", "response_id": response_id},
        )
    await _release_call_resources(str(current.get("call_session_id") or ""))
    return {
        "active": False,
        "stopped": True,
        "call_session_id": str(current.get("call_session_id") or ""),
    }


@router.post("/call/interrupt")
async def companion_call_interrupt(payload: CompanionCallControlRequest | None = None):
    response_id = call_session_service.manager.interrupt(
        str(payload.call_session_id if payload else ""),
        str(payload.response_id if payload else ""),
    )
    if response_id:
        pet_event_service.publish(
            "speech_interrupt",
            {"reason": "user_started_speaking", "response_id": response_id},
        )
    return {"interrupted": bool(response_id), "response_id": response_id}


@router.post("/call/device")
async def companion_call_device(payload: CompanionCallDeviceRequest):
    saved = call_session_service.manager.set_device(
        payload.call_session_id,
        {
            "device_id": payload.device_id,
            "label": payload.label,
            "sample_rate": payload.sample_rate,
            "channel_count": payload.channel_count,
            "echo_cancellation": payload.echo_cancellation,
            "noise_suppression": payload.noise_suppression,
            "auto_gain_control": payload.auto_gain_control,
            "actual_gain": "browser_not_exposed",
        },
    )
    if not saved:
        raise HTTPException(status_code=409, detail="电话会话已经变化，设备信息未写入。")
    return {"saved": True, "call_session_id": payload.call_session_id}


@router.get("/call/status")
async def companion_call_status():
    return call_runtime_status()


@router.post("/call/turn")
async def companion_call_turn(payload: CompanionCallTurnRequest):
    current_call = call_session_service.manager.status()
    if not current_call.get("active"):
        raise HTTPException(status_code=409, detail="电话尚未接通。")
    if payload.call_session_id and payload.call_session_id != current_call.get("call_session_id"):
        raise HTTPException(status_code=409, detail="电话会话已经变化，请忽略这段旧录音。")
    request_started = time.monotonic()
    try:
        wav_content = base64.b64decode(payload.wav_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="麦克风音频无法读取。") from exc
    if len(wav_content) < 48 or not wav_content.startswith(b"RIFF") or wav_content[8:12] != b"WAVE":
        raise HTTPException(status_code=400, detail="麦克风音频不是有效的 WAV。")
    try:
        turn = call_session_service.manager.begin_turn(payload.call_session_id, payload.turn_id)
    except call_session_service.CallSessionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    asr_started = time.monotonic()
    asr_status = system_audio_service.status()
    if not asr_status.get("ready"):
        await asyncio.to_thread(_ensure_call_asr_ready)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not system_audio_service.status().get("ready"):
            await asyncio.sleep(0.1)
        asr_status = system_audio_service.status()
    # Phone chat is predominantly Chinese. Keep the system-audio observer's
    # language setting independent so short microphone turns cannot drift into
    # unrelated languages when Whisper auto-detection has too little context.
    call_language = payload.language if payload.language in {"zh", "ja"} else "zh"
    transcript_result = await asyncio.to_thread(
        system_audio_service.transcribe_wav_for_quality,
        wav_content,
        language=call_language,
        purpose="phone",
        timeout_seconds=18.0,
    )
    asr_ms = (time.monotonic() - asr_started) * 1000
    try:
        call_session_service.manager.require_current(turn)
    except call_session_service.CallSessionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not transcript_result:
        raise HTTPException(status_code=503, detail="本地语音识别尚未准备好。")
    if transcript_result.get("error"):
        raise HTTPException(status_code=502, detail=f"语音识别失败：{transcript_result['error']}")
    if transcript_result.get("accepted") is False:
        asr_diagnostics = {
            "audio": dict(transcript_result.get("audio") or {}),
            "asr": dict(transcript_result.get("asr") or {}),
        }
        call_session_service.manager.update_turn(
            turn,
            "listening",
            asr=asr_diagnostics,
        )
        return {
            "heard": False,
            "active": True,
            "call_session_id": turn.call_session_id,
            "turn_id": turn.turn_id,
            "response_id": turn.response_id,
            "rejection_reason": str(transcript_result.get("rejection_reason") or "unreliable_transcript"),
            "diagnostics": asr_diagnostics,
            "timings": {"asr_ms": round(asr_ms, 1)},
        }
    transcript = " ".join(str(transcript_result.get("text") or "").split()).strip()
    if not transcript:
        call_session_service.manager.update_turn(turn, "listening")
        return {
            "heard": False,
            "active": True,
            "call_session_id": turn.call_session_id,
            "turn_id": turn.turn_id,
            "response_id": turn.response_id,
            "timings": {"asr_ms": round(asr_ms, 1)},
        }

    echo_reference = call_session_service.manager.echo_reference(turn)
    is_echo, echo_similarity = _is_recent_playback_echo(transcript, echo_reference)
    if is_echo:
        diagnostics = {
            "audio": dict(transcript_result.get("audio") or {}),
            "asr": dict(transcript_result.get("asr") or {}),
            "echo_similarity": echo_similarity,
        }
        call_session_service.manager.update_turn(turn, "listening", asr=diagnostics)
        return {
            "heard": False,
            "active": True,
            "call_session_id": turn.call_session_id,
            "turn_id": turn.turn_id,
            "response_id": turn.response_id,
            "rejection_reason": "playback_echo",
            "diagnostics": diagnostics,
            "timings": {"asr_ms": round(asr_ms, 1)},
        }

    call_session_service.manager.update_turn(
        turn,
        "model",
        transcript=transcript,
        asr={
            "audio": dict(transcript_result.get("audio") or {}),
            "asr": dict(transcript_result.get("asr") or {}),
            "engine": str(transcript_result.get("engine") or asr_status.get("engine") or ""),
            "language": str(transcript_result.get("language") or ""),
        },
    )

    config = companion_service.load_config()
    requested_model = str(config.get("pet_chat_model_id") or "auto").strip() or "auto"
    requested_reasoning = str(config.get("pet_chat_reasoning_level") or "auto").strip() or "auto"
    route = None
    if requested_model == "auto":
        route = select_auto_route(
            transcript,
            history_rows=db.get_recent_messages(limit=12, conversation_id=DESKTOP_PET_CONVERSATION_ID),
            image_count=0,
        )
        selected_model = route.model_id
        selected_reasoning = route.reasoning_level
    else:
        selected_model = resolve_model_id(requested_model)
        selected_reasoning = requested_reasoning
    agent_tools_enabled, selected_reasoning = _phone_turn_policy(
        transcript,
        selected_reasoning,
    )

    model_started = time.monotonic()
    try:
        result = await chat_with_ai(
            transcript,
            conversation_id=DESKTOP_PET_CONVERSATION_ID,
            source="desktop_pet_call",
            reasoning_level=selected_reasoning,
            model_id=selected_model,
            fallback_model_id=str(getattr(route, "fallback_model_id", "") or ""),
            fallback_reasoning_level=str(getattr(route, "fallback_reasoning_level", "") or ""),
            voice_reply_requested=True,
            capture_follow_ups=False,
            extra_system_context=_pet_call_screen_context(),
            request_id=turn.response_id,
            persist=False,
            agent_tools_enabled=agent_tools_enabled,
        )
    except ChatRunCancelledError as exc:
        raise HTTPException(status_code=409, detail=f"电话会话已取消：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _record_chat_route_failure(
            route,
            source="desktop_pet_call",
            request_id=turn.response_id,
            selected_model_id=selected_model,
            selected_reasoning_level=selected_reasoning,
            error_code="model_request_failed",
        )
        raise HTTPException(status_code=502, detail=f"Mio 暂时没有回复：{exc}") from exc
    model_ms = (time.monotonic() - model_started) * 1000
    try:
        call_session_service.manager.update_turn(turn, "committing", reply=result.reply)
        call_session_service.manager.commit_if_current(
            turn,
            lambda: persist_generated_chat_result(
                transcript,
                result,
                conversation_id=DESKTOP_PET_CONVERSATION_ID,
                source="desktop_pet_call",
                voice_reply_requested=True,
            ),
        )
    except call_session_service.CallSessionConflict as exc:
        abandon_deferred_final_response(
            str(getattr(result, "agent_run_id", "") or ""),
            error="电话会话已结束，暂存回复未提交。",
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _record_chat_route_success(
        route,
        source="desktop_pet_call",
        request_id=turn.response_id,
        selected_model_id=selected_model,
        selected_reasoning_level=selected_reasoning,
        result=result,
    )
    return {
        "active": True,
        "heard": True,
        "call_session_id": turn.call_session_id,
        "turn_id": turn.turn_id,
        "response_id": turn.response_id,
        "voice_state": "awaiting_voice",
        "transcript": transcript,
        "reply": result.reply,
        "replies": result.replies,
        "request_id": result.request_id,
        "model_id": result.model_id,
        "reasoning_level": result.reasoning_level,
        "timings": {
            "asr_ms": round(asr_ms, 1),
            "model_ms": round(model_ms, 1),
            "model_first_token_ms": result.first_token_latency_ms,
            "server_total_ms": round((time.monotonic() - request_started) * 1000, 1),
        },
    }


@router.get("/chat/history")
async def companion_chat_history(limit: int = Query(default=120, ge=1, le=500)):
    conversation_id = _desktop_pet_conversation_id()
    rows = db.get_recent_messages(limit=limit, conversation_id=conversation_id)
    return {
        "conversation_id": conversation_id,
        "messages": [
            {
                "id": int(row["id"]),
                "role": str(row["role"] or ""),
                "content": str(row["content"] or ""),
                "source": str(row["source"] or ""),
                "created_at": str(row["created_at"] or ""),
                "request_id": str(row["request_id"] or ""),
                "model_id": str(row["model_id"] or ""),
                "provider_model": str(row["provider_model"] or ""),
                "reasoning_level": str(row["reasoning_level"] or ""),
            }
            for row in rows
        ],
    }


@router.post("/voice/test")
async def companion_voice_test():
    config = await asyncio.to_thread(companion_service.load_config)
    profile_id = str(config.get("default_voice_profile_id") or "mio")
    profile = (config.get("voice_profiles") or {}).get(profile_id) or {}
    profile_name = str(profile.get("name") or "当前角色")
    language = str(config.get("pet_speech_language") or "zh")
    test_text = (
        "こんにちは。日本語の音声を確認しています。聞こえますか？"
        if language == "ja"
        else f"你好，我是{profile_name}。这样听得清楚吗？"
    )
    try:
        spoken = await asyncio.to_thread(
            companion_service.speak_text,
            test_text,
            wait=True,
            language=language,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"语音测试失败：{exc}") from exc
    if not spoken:
        error = str(companion_service.voice_runtime_status().get("last_error") or "没有完成播放")
        raise HTTPException(status_code=500, detail=f"语音测试失败：{error}")
    return {"spoken": spoken}


@router.post("/voice/reference")
async def companion_voice_reference(payload: VoiceReferenceRequest):
    try:
        await asyncio.to_thread(
            companion_service.save_voice_reference_data_url,
            payload.data_url,
            payload.name,
            profile_id=payload.profile_id,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await asyncio.to_thread(_status_payload)


class VoiceProfileExportRequest(BaseModel):
    profile_id: str = Field(default="", max_length=80)


@router.post("/voice/profiles/export")
async def companion_voice_profile_export(payload: VoiceProfileExportRequest):
    profile_id = str(payload.profile_id or "").strip()
    if not profile_id:
        raise HTTPException(status_code=400, detail="缺少音色 ID。")
    try:
        content = await asyncio.to_thread(companion_service.export_voice_package, profile_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", profile_id).strip("-") or "voice"
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"voice-package-{safe_name}.zip\"; "
                f"filename*=UTF-8''{quote(f'音色包-{profile_id}.zip')}"
            )
        },
    )


@router.post("/voice/profiles/import-package")
async def companion_voice_profile_import(request: Request):
    temporary_path: Path | None = None
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".mio-voice.zip",
            prefix="voice-import-",
            dir=settings.data_dir,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            async for chunk in request.stream():
                if chunk:
                    temporary.write(chunk)
        await asyncio.to_thread(companion_service.import_voice_package_file, temporary_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return await asyncio.to_thread(_status_payload)


@router.post("/voice/runtime/{action}")
async def companion_voice_runtime(action: str):
    actions = {
        "start": companion_service.start_voice_service,
        "stop": companion_service.stop_voice_service,
        "restart": companion_service.restart_voice_service,
        "warmup": companion_service.warm_voice_runtime,
    }
    selected = actions.get(action)
    if selected is None:
        raise HTTPException(status_code=404, detail="不支持的音色服务操作。")
    try:
        return await asyncio.to_thread(selected)
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/voice/audio")
async def companion_voice_audio(payload: SpeechRequest):
    emotion = payload.emotion or str(
        companion_service.speech_emotion_info(payload.text, payload.context)["id"]
    )
    try:
        content = await asyncio.to_thread(
            companion_service.synthesize_speech_wav,
            payload.text,
            context=payload.context,
            emotion=emotion,
            model_id=payload.model_id,
            language=payload.language,
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=f"角色语音生成失败：{exc}") from exc
    return Response(
        content=content,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Mio-Emotion": emotion,
        },
    )


@router.post("/voice/stream")
async def companion_voice_stream(payload: SpeechRequest):
    emotion = payload.emotion or str(
        companion_service.speech_emotion_info(payload.text, payload.context)["id"]
    )
    if not companion_service.clean_speech_text(payload.text):
        raise HTTPException(status_code=400, detail="这条消息没有可朗读的正文。")
    return StreamingResponse(
        companion_service.iter_speech_wav_stream(
            payload.text,
            context=payload.context,
            emotion=emotion,
            model_id=payload.model_id,
            language=payload.language,
        ),
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Mio-Emotion": emotion,
            "X-Mio-Streaming": "1",
        },
    )



@router.post("/voice/training/{action}")
async def companion_voice_training(action: str):
    try:
        return await asyncio.to_thread(companion_service.launch_voice_training, action)
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/position")
async def companion_position(payload: CompanionPositionRequest):
    await asyncio.to_thread(companion_service.save_pet_position, payload.x, payload.y)
    return await asyncio.to_thread(_status_payload)


@router.patch("/size")
async def companion_size(payload: CompanionSizeRequest):
    await asyncio.to_thread(companion_service.save_pet_size, payload.percent)
    return await asyncio.to_thread(_status_payload)


@router.get("/feed")
async def companion_feed(after_id: int | None = Query(default=None, ge=0)):
    conversation_id = _desktop_pet_conversation_id()
    latest = db.get_latest_message_id(role="assistant", conversation_id=conversation_id)
    if after_id is None:
        return {"latest_id": latest, "conversation_id": conversation_id, "messages": []}
    rows = db.get_messages_after_id(
        after_id,
        role="assistant",
        limit=20,
        conversation_id=conversation_id,
    )
    return {
        "latest_id": int(rows[-1]["id"]) if rows else latest,
        "conversation_id": conversation_id,
        "messages": [
            {
                "id": int(row["id"]),
                "content": str(row["content"] or ""),
                "source": str(row["source"] or ""),
                "created_at": str(row["created_at"] or ""),
                "request_id": str(row["request_id"] or ""),
                "emotion": str(
                    row["emotion"]
                    or companion_service.speech_emotion_info(str(row["content"] or ""))["id"]
                ),
            }
            for row in rows
        ],
    }


@router.get("/windows")
async def companion_windows():
    try:
        return await asyncio.to_thread(companion_service.window_observer.list_windows)
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=500, detail=f"读取窗口列表失败：{exc}") from exc


@router.post("/game/select")
async def companion_game_select(payload: GameWindowRequest):
    try:
        await asyncio.to_thread(local_vision_service.unload_model)
        return await asyncio.to_thread(companion_service.game_observer.select, payload.hwnd)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/window/select")
async def companion_window_select(payload: GameWindowRequest):
    try:
        return await asyncio.to_thread(companion_service.window_observer.select, payload.hwnd)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/game/capture")
async def companion_game_capture():
    try:
        return await asyncio.to_thread(companion_service.game_observer.capture)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/window/capture")
async def companion_window_capture():
    try:
        return await asyncio.to_thread(companion_service.window_observer.capture)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/game/start")
async def companion_game_start(payload: WindowObservationRequest):
    try:
        await asyncio.to_thread(local_vision_service.unload_model)
        status = companion_service.game_observer.status()
        return await asyncio.to_thread(
            _start_observation_runtime,
            interval_ms=payload.interval_ms,
            capture_only=payload.capture_only,
            hwnd=int(status.get("hwnd") or 0),
        )
    except ValueError as exc:
        screen_observation_service.set_capture_only(False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/window/start")
async def companion_window_start(payload: WindowObservationRequest):
    try:
        status = companion_service.window_observer.status()
        return await asyncio.to_thread(
            _start_observation_runtime,
            interval_ms=payload.interval_ms,
            capture_only=payload.capture_only,
            hwnd=int(status.get("hwnd") or 0),
        )
    except ValueError as exc:
        screen_observation_service.set_capture_only(False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/game/stop")
async def companion_game_stop():
    result = await asyncio.to_thread(_stop_observation_runtime)
    await asyncio.to_thread(local_vision_service.unload_model)
    return result


@router.post("/window/stop")
async def companion_window_stop():
    result = await asyncio.to_thread(_stop_observation_runtime)
    await asyncio.to_thread(local_vision_service.unload_model)
    return result


@router.get("/game/preview")
async def companion_game_preview():
    return _preview_response("还没有画面预览。")


@router.post("/game/analyze")
async def companion_game_analyze():
    status = companion_service.game_observer.status()
    if status.get("mode") != "window" or not status.get("hwnd"):
        raise HTTPException(status_code=400, detail="请先选择要观察的游戏窗口。")
    was_running = bool(status.get("running"))
    try:
        replied = await screen_observation_service.analyze_once(force=True)
    finally:
        if not was_running:
            await asyncio.to_thread(companion_service.game_observer.stop)
            await asyncio.to_thread(screen_observation_service.end_session)
    return {"replied": replied, "analysis": await asyncio.to_thread(screen_observation_service.status)}


@router.get("/window/preview")
async def companion_window_preview():
    return _preview_response("还没有窗口画面预览。")


@router.post("/screen/start")
async def companion_screen_start(payload: ScreenObservationRequest):
    try:
        if not payload.capture_only:
            route = str(companion_service.load_config().get("screen_vision_route") or "local")
            if route == "local":
                await local_vision_service.ensure_ready()
            else:
                await asyncio.to_thread(local_vision_service.unload_model)
        return await asyncio.to_thread(
            _start_observation_runtime,
            interval_ms=payload.interval_ms,
            capture_only=payload.capture_only,
            screen_scope=payload.scope,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        screen_observation_service.set_capture_only(False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/screen/capture")
async def companion_screen_capture(payload: ScreenObservationRequest):
    try:
        return await asyncio.to_thread(companion_service.window_observer.select_screen, payload.scope)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/screen/analyze")
async def companion_screen_analyze(payload: ScreenObservationRequest):
    was_running = bool(companion_service.window_observer.status().get("running"))
    previous_capture_only = bool(
        (await asyncio.to_thread(screen_observation_service.status)).get("capture_only")
    )
    try:
        if not bool(companion_service.load_config().get("screen_ai_enabled", True)) and not payload.capture_only:
            raise HTTPException(status_code=409, detail="屏幕 AI 已关闭；请先在桌宠设置中启用后再分析。")
        if not payload.capture_only:
            route = str(companion_service.load_config().get("screen_vision_route") or "local")
            if route == "local":
                await local_vision_service.ensure_ready()
            else:
                await asyncio.to_thread(local_vision_service.unload_model)
        await asyncio.to_thread(screen_observation_service.set_capture_only, payload.capture_only)
        await asyncio.to_thread(companion_service.window_observer.select_screen, payload.scope)
        replied = await screen_observation_service.analyze_once(force=True)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if not was_running:
            await asyncio.to_thread(companion_service.window_observer.stop)
            await asyncio.to_thread(screen_observation_service.end_session)
        else:
            await asyncio.to_thread(screen_observation_service.set_capture_only, previous_capture_only)
    return {"replied": replied, "analysis": await asyncio.to_thread(screen_observation_service.status)}


@router.post("/screen/stop")
async def companion_screen_stop():
    result = await asyncio.to_thread(_stop_observation_runtime)
    await asyncio.to_thread(local_vision_service.unload_model)
    return result


@router.get("/screen/preview")
async def companion_screen_preview():
    return _preview_response("还没有屏幕画面预览。")


@router.post("/local-vision/runtime/{action}")
async def companion_local_vision_runtime(action: str):
    handlers = {
        "start": local_vision_service.start_server,
        "stop": local_vision_service.stop_server,
        "restart": local_vision_service.restart_server,
        "unload": local_vision_service.unload_model,
    }
    handler = handlers.get(action)
    if handler is None:
        raise HTTPException(status_code=404, detail="不支持的本地视觉操作。")
    try:
        return await asyncio.to_thread(handler)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/local-vision/model/pull")
async def companion_local_vision_model_pull():
    try:
        return await asyncio.to_thread(local_vision_service.start_model_pull)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
