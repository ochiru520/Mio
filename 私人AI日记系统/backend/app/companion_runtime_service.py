"""Call audio/observer resource ownership shared by HTTP and passive recovery.

Importing the module does not start workers or inspect user data.
"""
from __future__ import annotations
import asyncio
import threading
from contextlib import contextmanager
from . import call_session_service, companion_service, pet_event_service, screen_observation_service, system_audio_service


_pet_call_started_observer = False

_pet_call_previous_audio_model = ""

_pet_call_previous_audio_engine = ""

_pet_call_resource_owner = ""

_observation_start_lock = threading.RLock()
_observation_start_generation = 0


class ObservationStartCancelled(RuntimeError):
    """An observation start was superseded or stopped while preparing resources."""


def begin_observation_start() -> int:
    """Reserve a start before awaiting model readiness, without starting capture."""
    global _observation_start_generation
    with _observation_start_lock:
        _observation_start_generation += 1
        return _observation_start_generation


def observation_ticket() -> int:
    """Read the current session without superseding a running or pending start."""
    with _observation_start_lock:
        return _observation_start_generation


@contextmanager
def observation_operation(ticket: int):
    """Keep manual capture setup/cleanup atomic with observation stop."""
    with _observation_start_lock:
        if ticket != _observation_start_generation:
            raise ObservationStartCancelled("本次陪看操作已取消，请重新操作。")
        yield

CALL_ASR_MODEL = "large-v3-turbo"

def call_runtime_status() -> dict[str, object]:
    return {
        **call_session_service.manager.status(),
        "started_observer": _pet_call_started_observer,
        "previous_audio_model": _pet_call_previous_audio_model,
        "asr": system_audio_service.status(),
    }

def ensure_call_asr_ready() -> dict[str, object]:
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
    if (not config.get("screen_audio_enabled", True)
            or not companion_service.window_observer.status().get("running")):
        current = system_audio_service.start(forced, transcription_only=True)
    else:
        current = system_audio_service.start(forced)
    return current

def release_call_resources_sync(call_session_id: str = "") -> None:
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

async def release_call_resources(call_session_id: str = "") -> None:
    await asyncio.to_thread(release_call_resources_sync, call_session_id)

def start_observation_runtime(
    *,
    interval_ms: int,
    capture_only: bool,
    hwnd: int | None = None,
    screen_scope: str = "",
    start_ticket: int | None = None,
) -> dict[str, object]:
    with _observation_start_lock:
        if start_ticket is not None and start_ticket != _observation_start_generation:
            raise ObservationStartCancelled("本次陪看启动已取消，请重新点击开始。")
        screen_observation_service.set_capture_only(capture_only)
        observer = companion_service.window_observer
        config = companion_service.load_config()
        observer.configure_watch(str(config.get("screen_watch_mode") or "single"),
                                 str(config.get("screen_watch_pace") or "normal"))
        if screen_scope:
            observer.select_screen(screen_scope)
        elif hwnd:
            observer.select(hwnd)
        result = observer.start(interval_ms)
        if not capture_only:
            if _pet_call_resource_owner:
                ensure_call_asr_ready()
            elif config.get("screen_audio_enabled", True):
                system_audio_service.start(config)
        return result

def stop_observation_runtime() -> dict[str, object]:
    global _observation_start_generation
    with _observation_start_lock:
        _observation_start_generation += 1
        result = companion_service.window_observer.stop()
        screen_observation_service.end_session()
        sources = {"screen", "game", "desktop_pet_wake"}
        companion_service.cancel_speech_playback(sources=sources)
        pet_event_service.publish("speech_interrupt", {"sources": sorted(sources), "reason": "observation_stopped"})
        if not _pet_call_resource_owner:
            system_audio_service.stop()
        else:
            ensure_call_asr_ready()
        return result

def prepare_call_runtime(call_session_id: str) -> dict[str, object]:
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
        config = companion_service.load_config()
        companion_service.window_observer.configure_watch(str(config.get("screen_watch_mode") or "single"),
                                                         str(config.get("screen_watch_pace") or "normal"))
        companion_service.window_observer.select_screen("primary")
        companion_service.window_observer.start(1000)
        screen_observation_service.set_capture_only(False)
    return audio_status
