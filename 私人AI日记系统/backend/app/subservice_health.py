from __future__ import annotations

import time
import threading
from collections import deque
from typing import Any


RECOVERY_WINDOW_SECONDS = 600.0
RECOVERY_MAX_ATTEMPTS = 2
_recovery_lock = threading.Lock()
_recovery_attempts: dict[str, deque[float]] = {}


def _service(
    service_id: str,
    state: str,
    *,
    enabled: bool,
    running: bool,
    ready: bool,
    last_error: str = "",
    recovery_scope: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "service_id": service_id,
        "state": state,
        "enabled": bool(enabled),
        "running": bool(running),
        "ready": bool(ready),
        "last_error": str(last_error or "")[:500],
        "recovery_scope": recovery_scope,
        "details": details or {},
    }


def snapshot() -> dict[str, Any]:
    """Collect a passive, non-network health snapshot for isolated runtimes."""
    from . import companion_service, local_vision_service, screen_observation_service, system_audio_service
    from .routes import companion, onebot

    call = companion.call_runtime_status()
    audio = dict(call["asr"])
    tts = companion_service.voice_runtime_health()
    vision = local_vision_service.passive_status()
    observation = screen_observation_service.runtime_health_status()
    capture = dict(observation["capture"])
    qq = onebot.runtime_health_status()

    audio_running = bool(audio.get("running"))
    audio_ready = bool(audio.get("ready"))
    audio_desired = bool(audio.get("desired_running") or call.get("active"))
    audio_phase = str(audio.get("phase") or "stopped")
    audio_state = (
        "ready"
        if audio_ready
        else "starting"
        if audio_running
        else "failed"
        if audio_phase == "error" or audio_desired
        else "stopped"
    )

    call_active = bool(call.get("active"))
    call_state = "ready" if call_active and audio_ready else "degraded" if call_active else "stopped"

    tts_running = bool(tts.get("managed_running") or tts.get("observed_running") is True)
    tts_desired = bool(tts.get("desired_running"))
    tts_warmup = str(tts.get("warmup_state") or "idle")
    tts_error = str(tts.get("last_error") or tts.get("warmup_error") or "")
    tts_state = (
        "ready"
        if tts_running and not tts_error
        else "starting"
        if tts_warmup in {"scheduled", "running"}
        else "failed"
        if tts_error or tts_desired
        else "unknown"
    )

    capture_running = bool(capture.get("running"))
    capture_desired = bool(capture.get("desired_running"))
    capture_error = str(capture.get("error") or capture.get("capture_backend_error") or "")
    frame_age = capture.get("last_frame_age_seconds")
    capture_ready = bool(
        capture_running
        and capture.get("preview_available")
        and (frame_age is None or float(frame_age) <= 5)
    )
    capture_state = (
        "ready"
        if capture_ready
        else "degraded"
        if capture_running
        else "failed"
        if capture_error or capture_desired
        else "stopped"
    )

    observed_vision = vision.get("observed_running")
    vision_running = bool(vision.get("owned_server") or observed_vision is True)
    vision_desired = bool(vision.get("desired_running"))
    vision_error = str(vision.get("last_error") or "")
    # A manifest/process is not enough: require a successful real inference
    # probe when the field is present. Older test doubles omit it.
    inference_verified = vision.get("inference_ready")
    vision_ready = bool(
        vision_running
        and vision.get("model_installed")
        and (inference_verified if inference_verified is not None else True)
    )
    vision_state = (
        "ready"
        if vision_ready
        else "starting"
        if vision.get("pulling")
        else "failed"
        if vision_error or vision_desired or str(vision.get("inference_state") or "") in {"oom", "failed"}
        else "degraded"
        if vision.get("model_installed") and str(vision.get("inference_state") or "") == "unverified"
        else "unknown"
        if observed_vision is None
        else "stopped"
    )

    analysis_enabled = bool(observation.get("enabled", True))
    analysis_message = str(observation.get("last_error") or "")
    analysis_error = analysis_message if analysis_enabled else ""
    analysis_running = bool(analysis_enabled and observation.get("analysis_in_progress"))
    analysis_state = (
        "disabled"
        if not analysis_enabled
        else "running"
        if analysis_running
        else "degraded"
        if analysis_error
        else "idle"
    )

    qq_enabled = bool(qq.get("enabled"))
    qq_connections = int(qq.get("websocket_connections") or 0)
    qq_state = "ready" if qq_connections else "offline" if qq_enabled else "disabled"

    services = {
        "phone": _service(
            "phone",
            call_state,
            enabled=call_active,
            running=call_active,
            ready=call_active and audio_ready,
            last_error=str(audio.get("last_error") or "") if call_active else "",
            recovery_scope="phone_session",
            details={"started_observer": bool(call.get("started_observer"))},
        ),
        "asr_system_audio": _service(
            "asr_system_audio",
            audio_state,
            enabled=audio_desired,
            running=audio_running,
            ready=audio_ready,
            last_error=str(audio.get("last_error") or ""),
            recovery_scope="system_audio_worker",
            details={
                "phase": audio_phase,
                "model": str(audio.get("model") or ""),
                "quality_requests": dict(audio.get("quality_requests") or {}),
            },
        ),
        "tts": _service(
            "tts",
            tts_state,
            enabled=tts_desired,
            running=tts_running,
            ready=tts_state == "ready",
            last_error=tts_error,
            recovery_scope="gpt_sovits_process",
            details=tts,
        ),
        "screen_capture": _service(
            "screen_capture",
            capture_state,
            enabled=capture_desired,
            running=capture_running,
            ready=capture_ready,
            last_error=capture_error,
            recovery_scope="screen_capture_thread",
            details={
                "mode": str(capture.get("mode") or ""),
                "backend": str(capture.get("capture_backend") or ""),
                "frame_id": int(capture.get("frame_id") or 0),
                "last_frame_age_seconds": frame_age,
                "desired_running": capture_desired,
                "screen_scope": str(capture.get("screen_scope") or "primary"),
                "hwnd": int(capture.get("hwnd") or 0),
                "interval_ms": int(capture.get("interval_ms") or 1000),
            },
        ),
        "screen_analysis": _service(
            "screen_analysis",
            analysis_state,
            enabled=analysis_enabled,
            running=analysis_running,
            ready=analysis_enabled and not analysis_running and not analysis_error,
            last_error=analysis_error,
            recovery_scope="screen_analysis_task",
            details={
                "status_message": analysis_message if not analysis_enabled else "",
                "last_analyzed_at": str(observation.get("last_analyzed_at") or ""),
                "last_model": str(observation.get("last_model") or ""),
                "pipeline_timings": dict(observation.get("last_pipeline_timings") or {}),
            },
        ),
        "local_vision": _service(
            "local_vision",
            vision_state,
            enabled=vision_desired,
            running=vision_running,
            ready=vision_ready,
            last_error=vision_error,
            recovery_scope="ollama_process",
            details=vision,
        ),
        "qq": _service(
            "qq",
            qq_state,
            enabled=qq_enabled,
            running=qq_connections > 0,
            ready=qq_connections > 0,
            recovery_scope="napcat_onebot_connection",
            details=qq,
        ),
    }
    degraded = [
        service_id
        for service_id, item in services.items()
        if item["state"] in {"failed", "degraded"}
    ]
    return {
        "sampled_at_epoch": time.time(),
        "passive": True,
        "overall": "degraded" if degraded else "ok",
        "degraded_services": degraded,
        "services": services,
    }


def _claim_recovery(service_id: str, now: float) -> tuple[bool, int]:
    with _recovery_lock:
        history = _recovery_attempts.setdefault(service_id, deque())
        cutoff = now - RECOVERY_WINDOW_SECONDS
        while history and history[0] < cutoff:
            history.popleft()
        if len(history) >= RECOVERY_MAX_ATTEMPTS:
            return False, len(history)
        history.append(now)
        return True, len(history)


def reset_recovery_state_for_tests() -> None:
    with _recovery_lock:
        _recovery_attempts.clear()


def recover_failed(health: dict[str, Any] | None = None) -> dict[str, Any]:
    """Recover only explicitly desired child services that are clearly failed."""
    from . import companion_service, local_vision_service, screen_observation_service, system_audio_service

    current = health if isinstance(health, dict) else snapshot()
    services = current.get("services") if isinstance(current, dict) else None
    services = services if isinstance(services, dict) else {}
    attempts: list[dict[str, Any]] = []
    fused: list[str] = []
    now = time.monotonic()

    for service_id in ("asr_system_audio", "tts", "local_vision", "screen_capture"):
        item = services.get(service_id)
        if not isinstance(item, dict) or item.get("state") != "failed" or not item.get("enabled"):
            continue
        allowed, attempts_in_window = _claim_recovery(service_id, now)
        if not allowed:
            fused.append(service_id)
            continue
        try:
            if service_id == "asr_system_audio":
                system_audio_service.start(companion_service.load_config())
            elif service_id == "tts":
                companion_service.restart_voice_service()
            elif service_id == "local_vision":
                local_vision_service.restart_server()
            else:
                details = dict(item.get("details") or {})
                observer = companion_service.window_observer
                observer.stop()
                if str(details.get("mode") or "screen") == "window" and int(details.get("hwnd") or 0):
                    observer.select(int(details["hwnd"]))
                else:
                    observer.select_screen(str(details.get("screen_scope") or "primary"))
                observer.start(int(details.get("interval_ms") or 1000))
        except Exception as exc:
            attempts.append(
                {
                    "service_id": service_id,
                    "success": False,
                    "error": str(exc)[:500],
                    "attempts_in_window": attempts_in_window,
                }
            )
        else:
            attempts.append(
                {
                    "service_id": service_id,
                    "success": True,
                    "error": "",
                    "attempts_in_window": attempts_in_window,
                }
            )

    return {
        "attempted": bool(attempts),
        "recovered": [item["service_id"] for item in attempts if item["success"]],
        "failed": [item for item in attempts if not item["success"]],
        "fused": fused,
        "attempts": attempts,
    }
