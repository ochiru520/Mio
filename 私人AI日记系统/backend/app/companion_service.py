from __future__ import annotations

import asyncio
import base64
import binascii
import ctypes
import functools
import hashlib
import zipfile
from ctypes import wintypes
from array import array
from datetime import datetime
from difflib import SequenceMatcher
import json
import logging
import math
import os
import re
import shutil
import subprocess
import struct
import sys
import threading
import time
import uuid
import wave
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

import httpx
import yaml
from PIL import Image

from . import cloud_tts, speech_translation_service, system_audio_service
from .config import settings
from .companion_observation_service import GameObserver, WindowObserver, cleanup_legacy_preview


logger = logging.getLogger(__name__)


SHOW_EVENT_NAME = "Local\\MioAgentDesktopShow-7C53C273"
PET_CHAT_EVENT_NAME = "Local\\MioAgentDesktopPetChat-7C53C273"
PET_CHAT_ANCHOR_PATH = settings.data_dir / "桌宠聊天位置请求.json"


def pet_chat_anchor() -> dict[str, object]:
    try:
        payload = json.loads(PET_CHAT_ANCHOR_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    try:
        return {
            "anchor_x": int(payload["anchor_x"]),
            "anchor_y": int(payload["anchor_y"]),
            "recorded_at": str(payload.get("recorded_at") or ""),
        }
    except (KeyError, TypeError, ValueError):
        return {}
MIO_VOICE_ENGINE = "gpt_sovits"
SO_VITS_SVC_ENGINE = "so_vits_svc"
GENIE_VOICE_RUNTIME = "genie"
LEGACY_GPT_SOVITS_RUNTIME = "gpt_sovits"
DEFAULT_VOICE_PROFILE_ID = "mio"
VOICE_PROFILE_FIELDS = (
    "engine",
    "gpt_sovits_ref_audio",
    "gpt_sovits_prompt_text",
    "gpt_sovits_prompt_language",
    "gpt_sovits_text_language",
    "gpt_sovits_translate_to_japanese",
    "gpt_sovits_gpt_weights",
    "gpt_sovits_sovits_weights",
    "so_vits_svc_model_path",
    "so_vits_svc_config_path",
    "so_vits_svc_speaker",
    "so_vits_svc_pitch",
    "so_vits_svc_auto_predict_f0",
    "so_vits_svc_noise_scale",
    "so_vits_svc_base_profile_id",
    "source_package_name",
    "source_license",
)
GPT_SOVITS_NLTK_RESOURCES = (
    ("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng"),
)
GPT_SOVITS_TAGGER_FILES = (
    "averaged_perceptron_tagger_eng.weights.json",
    "averaged_perceptron_tagger_eng.tagdict.json",
    "averaged_perceptron_tagger_eng.classes.json",
)


DEFAULT_CONFIG: dict[str, Any] = {
    "config_schema_version": 2,
    "voice_enabled": True,
    "voice_startup_enabled": False,
    "voice_idle_timeout_seconds": 180,
    "voice_engine": MIO_VOICE_ENGINE,
    "local_voice_runtime": GENIE_VOICE_RUNTIME,
    "cloud_tts_api_key": "",
    "cloud_tts_app_id": "",
    "cloud_tts_speaker": "zh_female_vv_uranus_bigtts",
    "cloud_tts_speech_rate": 0,
    "default_voice_profile_id": DEFAULT_VOICE_PROFILE_ID,
    "voice_profiles": {},
    "chat_model_id": "auto",
    "chat_reasoning_level": "auto",
    "pet_chat_model_id": "auto",
    "pet_chat_reasoning_level": "auto",
    "pet_call_asr_engine": "auto",
    "pet_call_input_language": "zh",
    "speech_translation_model_id": "deepseek-v4-flash",
    "pet_call_silence_ms": 650,
    "pet_call_voice_threshold": 0.018,
    "pet_call_min_speech_ms": 280,
    "pet_call_max_turn_seconds": 18,
    "voice_volume": 85,
    "voice_streaming_enabled": True,
    "pet_speech_language": "zh",
    "startup_greeting_enabled": True,
    "qq_startup_enabled": False,
    "speak_proactive": False,
    "speak_screen_observations": True,
    "speak_game_observations": True,
    "qq_voice_mode": "adaptive",
    "gpt_sovits_url": "http://127.0.0.1:9880",
    "gpt_sovits_ref_audio": "",
    "gpt_sovits_prompt_text": "",
    "gpt_sovits_prompt_language": "ja",
    "gpt_sovits_text_language": "auto",
    "gpt_sovits_translate_to_japanese": False,
    "gpt_sovits_gpt_weights": "",
    "gpt_sovits_sovits_weights": "",
    "screen_ai_enabled": True,
    "screen_audio_enabled": True,
    "screen_audio_model": "base",
    "screen_audio_language": "auto",
    "screen_audio_chunk_seconds": 5,
    "screen_vision_route": "local",
    "screen_vision_model_id": "auto-fast",
    "screen_direct_voice_enabled": True,
    "screen_change_threshold": 4.0,
    "screen_analysis_interval_seconds": 5,
    "screen_request_timeout_seconds": 25,
    "screen_voice_cooldown_seconds": 5,
    "screen_minimum_importance": 0.62,
    "screen_daily_cost_limit_yuan": 5.0,
    "bubble_seconds": 9,
    "pet_size_percent": 150,
    "pet_renderer": "live2d",
    "live2d_model_id": "hiyori",
    "live2d_scale": 1.0,
    "live2d_vertical_offset": 0.0,
    "live2d_follow_cursor": True,
    "live2d_idle_motion": True,
    "live2d_click_motion": True,
    "live2d_smart_passthrough": True,
    "live2d_click_through_locked": False,
    "live2d_speech_bubble_enabled": True,
    "live2d_keep_visible": False,
    "live2d_always_on_top": True,
    "live2d_disable_gpu": False,
    "live2d_motion_slots": {},
    "live2d_expression_slots": {},
    "position_x": 80,
    "position_y": 420,
}


LIVE2D_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "hiyori",
        "name": "Hiyori Momose",
        "description": "Live2D 官方免费示例，包含完整待机与点击动作",
        "model_path": "models/hiyori/Hiyori.model3.json",
        "preview_path": "models/hiyori/preview.png",
        "license": "Live2D Free Material License",
        "source": "Live2D/CubismWebSamples",
        "source_url": "https://github.com/Live2D/CubismWebSamples",
        "motion_count": 10,
    },
)


LIVE2D_MOTION_SLOT_IDS = frozenset(
    {"idle", "touch", "think", "speak", "observe", "cheerful", "concerned", "alert", "attention", "shy"}
)
LIVE2D_EXPRESSION_SLOT_IDS = frozenset(
    {"neutral", "gentle", "cheerful", "concerned", "serious", "shy"}
)


_pet_process: subprocess.Popen | None = None
_pet_runtime_kind = ""
_pet_lock = threading.Lock()
_pet_activity_lock = threading.Lock()
_pet_activity_revision = 0
_pet_activity: dict[str, Any] = {
    "state": "idle",
    "emotion": "neutral",
    "source": "",
    "updated_at": 0.0,
    "expires_at": 0.0,
}
_speech_lock = threading.Lock()
_speech_synthesis_lock = threading.Lock()
# Starlette may advance or close a streaming response generator on a different
# worker thread. A plain Lock can be released there; RLock cannot.
_voice_synthesis_lock = threading.Lock()
_gpt_sovits_process: subprocess.Popen | None = None
_gpt_sovits_lock = threading.Lock()
_gpt_sovits_last_error = ""
_frontend_ready_event: asyncio.Event | None = None
_voice_quality_lock = threading.Lock()
_voice_quality_last: dict[str, Any] = {
    "checked_at": "",
    "passed": None,
    "reasons": [],
    "semantic_check": "not_run",
}
_gpt_sovits_probe_at = 0.0
_gpt_sovits_probe_result = False
_gpt_sovits_desired_running = False
_gpt_sovits_applied_gpt_weights = ""
_gpt_sovits_applied_sovits_weights = ""
_voice_runtime_metrics_lock = threading.Lock()
_voice_runtime_metrics: dict[str, Any] = {
    "service_started_at": "",
    "load_seconds": None,
    "warmup_state": "idle",
    "warmup_seconds": None,
    "warmup_error": "",
    "last_first_audio_ms": None,
    "reference_leak_blocks": 0,
    "last_reference_leak": {},
}
_speech_generation = 0
_speech_owner_generation = 0
_speech_owner_priority = 0
_speech_owner_source = ""
_speech_translation_last_error = ""
_speech_translation_last_model = ""
_voice_language_warmup_lock = threading.Lock()
_voice_language_warmup_active = False

SPEECH_SOURCE_PRIORITIES = {
    "screen": 20,
    "proactive": 30,
    "qq": 50,
    "chat": 80,
    "phone": 100,
}

PET_SPRITE_FILES: dict[str, str] = {
    "idle": "待机.png",
    "blink": "眨眼.png",
    "speaking": "说话.png",
    "cheerful": "开心.png",
    "concerned": "担心.png",
    "shy": "害羞.png",
}
PET_ACTIVITY_LABELS: dict[str, str] = {
    "idle": "安静待机",
    "listening": "在听你说",
    "thinking": "正在想",
    "working": "正在处理",
    "responding": "准备回应",
    "speaking": "正在说话",
    "observing": "专注观察",
}
MAX_SPRITE_SHEET_BYTES = 24 * 1024 * 1024
MAX_SPRITE_SHEET_PIXELS = 40_000_000


def _electron_pet_command() -> list[str] | None:
    configured = os.getenv("MIO_LIVE2D_PET_EXE", "").strip()
    if configured:
        executable = Path(configured)
        return [str(executable)] if executable.is_file() else None
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", settings.agent_frontend_dir.parent))
        candidates = [
            bundle_root / "live2d_desktop" / "MioLive2D桌宠.exe",
            bundle_root / "live2d_desktop" / "澪Live2D桌宠.exe",
        ]
        executable = next((path for path in candidates if path.is_file()), None)
        return [str(executable)] if executable else None
    app_root = settings.source_workspace_root / "澪Agent应用" / "live2d-desktop"
    executable = app_root / "node_modules" / "electron" / "dist" / "electron.exe"
    if executable.is_file() and (app_root / "main.js").is_file():
        return [str(executable), str(app_root)]
    return None


def _pet_command() -> tuple[list[str], str]:
    if str(load_config().get("pet_renderer") or "classic") == "live2d":
        electron_command = _electron_pet_command()
        if electron_command:
            return electron_command, "electron_live2d"
    if getattr(sys, "frozen", False):
        return [sys.executable, "--desktop-pet"], "pywebview_fallback"
    return [sys.executable, "-m", "app.desktop_pet"], "python_fallback"


def _external_pet_renderer_connected() -> bool:
    try:
        from . import pet_event_service

        return pet_event_service.has_desktop_renderer()
    except (ImportError, RuntimeError):
        return False


def pet_running() -> bool:
    global _pet_process, _pet_runtime_kind
    with _pet_lock:
        if _pet_process is not None:
            if _pet_process.poll() is None:
                return True
            _pet_process = None
            _pet_runtime_kind = ""
    return str(load_config().get("pet_renderer") or "classic") == "live2d" and _external_pet_renderer_connected()


def start_pet() -> dict[str, Any]:
    global _pet_process, _pet_runtime_kind
    with _pet_lock:
        if _pet_process is not None and _pet_process.poll() is None:
            already_running = True
        elif str(load_config().get("pet_renderer") or "classic") == "live2d" and _external_pet_renderer_connected():
            already_running = True
        else:
            already_running = False
            env = os.environ.copy()
            env["MIO_RUNTIME_ROOT"] = str(settings.project_root)
            env["MIO_PET_API_BASE"] = os.getenv(
                "MIO_PET_API_BASE",
                f"http://127.0.0.1:{settings.app_port}",
            ).rstrip("/")
            configured_state_root = os.getenv("MIO_DESKTOP_STATE_DIR", "").strip()
            state_root = Path(configured_state_root) if configured_state_root else (
                Path("D:/Mio数据")
                if Path("D:/").exists()
                else Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "MioAgent"
            )
            env["MIO_PET_STATE_DIR"] = str(state_root / "Live2D桌宠")
            env["MIO_AGENT_PARENT_PID"] = str(os.getpid())
            env["MIO_PET_DISABLE_GPU"] = (
                "1" if load_config().get("live2d_disable_gpu", False) else "0"
            )
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            command, _pet_runtime_kind = _pet_command()
            _pet_process = subprocess.Popen(
                command,
                env=env,
                cwd=str(settings.backend_dir),
                creationflags=flags,
            )
    if already_running:
        return pet_status()
    time.sleep(0.35)
    return pet_status()


def stop_pet() -> dict[str, Any]:
    global _pet_process, _pet_runtime_kind
    external_renderer = _external_pet_renderer_connected()
    if external_renderer:
        from . import pet_event_service

        pet_event_service.publish("shutdown", {"reason": "stop_requested"})
    with _pet_lock:
        process = _pet_process
        _pet_process = None
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    _pet_runtime_kind = ""
    return pet_status()


def restart_pet() -> dict[str, Any]:
    stop_pet()
    return start_pet()


def _signal_desktop_event(name: str) -> bool:
    try:
        event_handle = ctypes.windll.kernel32.OpenEventW(0x0002, False, name)
    except (AttributeError, OSError):
        return False
    if not event_handle:
        return False
    try:
        return bool(ctypes.windll.kernel32.SetEvent(event_handle))
    finally:
        ctypes.windll.kernel32.CloseHandle(event_handle)


def signal_pet_chat_window(anchor: dict[str, object] | None = None) -> bool:
    if isinstance(anchor, dict):
        try:
            payload = {
                "anchor_x": int(float(anchor.get("anchor_x") or 0)),
                "anchor_y": int(float(anchor.get("anchor_y") or 0)),
                "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            PET_CHAT_ANCHOR_PATH.parent.mkdir(parents=True, exist_ok=True)
            PET_CHAT_ANCHOR_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError):
            pass
    return _signal_desktop_event(PET_CHAT_EVENT_NAME) or _signal_desktop_event(SHOW_EVENT_NAME)


def signal_agent_window() -> bool:
    return _signal_desktop_event(SHOW_EVENT_NAME)


def show_agent_window() -> dict[str, Any]:
    if signal_agent_window():
        return {"ok": True, "method": "event"}

    configured = str(os.getenv("MIO_AGENT_EXE") or "").strip()
    candidates = [Path(configured)] if configured else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable))
    candidates.extend([Path("D:/Mio/Mio.exe"), Path("D:/澪Agent/澪.exe")])
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        return {"ok": False, "method": "unavailable"}

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
        close_fds=True,
        creationflags=creationflags,
    )
    return {"ok": True, "method": "launch", "path": str(executable)}


def set_pet_activity(
    state: str,
    *,
    emotion: str = "neutral",
    source: str = "",
    ttl_seconds: float = 12,
) -> dict[str, Any]:
    global _pet_activity_revision
    normalized_state = str(state or "idle").strip().lower()
    if normalized_state not in PET_ACTIVITY_LABELS:
        normalized_state = "idle"
    normalized_emotion = str(emotion or "neutral").strip().lower()
    if normalized_emotion not in SPEECH_EMOTION_LABELS:
        normalized_emotion = "neutral"
    now = time.monotonic()
    ttl = max(0.0, min(180.0, float(ttl_seconds or 0)))
    with _pet_activity_lock:
        _pet_activity_revision += 1
        _pet_activity.update(
            {
                "state": normalized_state,
                "emotion": normalized_emotion,
                "source": str(source or "")[:40],
                "updated_at": now,
                "expires_at": now + ttl if normalized_state != "idle" and ttl > 0 else 0.0,
            }
        )
    result = pet_activity_status()
    from . import pet_event_service

    pet_event_service.publish("activity", result)
    return result


def pet_activity_status() -> dict[str, Any]:
    now = time.monotonic()
    with _pet_activity_lock:
        state = str(_pet_activity.get("state") or "idle")
        expires_at = float(_pet_activity.get("expires_at") or 0.0)
        if state != "idle" and expires_at and now >= expires_at:
            state = "idle"
            emotion = "neutral"
            source = ""
            remaining_ms = 0
        else:
            emotion = str(_pet_activity.get("emotion") or "neutral")
            source = str(_pet_activity.get("source") or "")
            remaining_ms = max(0, round((expires_at - now) * 1000)) if expires_at else 0
        return {
            "state": state,
            "label": PET_ACTIVITY_LABELS.get(state, PET_ACTIVITY_LABELS["idle"]),
            "emotion": emotion,
            "emotion_label": SPEECH_EMOTION_LABELS.get(emotion, SPEECH_EMOTION_LABELS["neutral"]),
            "source": source,
            "remaining_ms": remaining_ms,
            "revision": _pet_activity_revision,
        }


def pet_status() -> dict[str, Any]:
    from . import pet_event_service

    running = pet_running()
    sprites = pet_sprite_manifest()
    external_renderer = running and _pet_process is None
    return {
        "running": running,
        "pid": _pet_process.pid if running and _pet_process is not None else None,
        "runtime_kind": _pet_runtime_kind if _pet_process is not None else ("external_renderer" if external_renderer else ""),
        "electron_available": _electron_pet_command() is not None,
        "avatar_available": default_avatar_path() is not None,
        "sprite_states": sprites["states"],
        "sprite_set_ready": sprites["ready"],
        "sprite_expected_count": sprites["expected_count"],
        "sprite_version": sprites["version"],
        "activity": pet_activity_status(),
        "settings": load_config(),
        "live2d": {
            "available": (settings.agent_frontend_dir / "live2d-pet" / "index.html").is_file(),
            "models": available_live2d_models(),
            "notices_url": "/agent-app/live2d-pet/THIRD_PARTY_NOTICES.html",
            "runtime": pet_event_service.status(),
        },
    }


SPEECH_EMOTION_LABELS: dict[str, str] = {
    "neutral": "自然",
    "gentle": "轻柔",
    "cheerful": "开心",
    "concerned": "担心",
    "serious": "认真",
    "shy": "害羞",
}

# GPT-SoVITS 的情绪主要来自对应的原始参考音；这些参数只做轻微辅助。
GPT_SOVITS_EMOTION_STYLES: dict[str, dict[str, float | int]] = {
    "neutral": {"speed_factor": 1.00, "temperature": 0.70, "top_k": 10, "top_p": 0.90, "fragment_interval": 0.18},
    "gentle": {"speed_factor": 0.94, "temperature": 0.64, "top_k": 8, "top_p": 0.86, "fragment_interval": 0.24},
    "cheerful": {"speed_factor": 1.06, "temperature": 0.78, "top_k": 12, "top_p": 0.94, "fragment_interval": 0.14},
    "concerned": {"speed_factor": 0.93, "temperature": 0.66, "top_k": 8, "top_p": 0.86, "fragment_interval": 0.22},
    "serious": {"speed_factor": 0.96, "temperature": 0.60, "top_k": 8, "top_p": 0.82, "fragment_interval": 0.18},
    "shy": {"speed_factor": 0.92, "temperature": 0.68, "top_k": 9, "top_p": 0.88, "fragment_interval": 0.26},
}

GPT_SOVITS_CHINESE_EMOTION_STYLES: dict[str, dict[str, float | int | bool]] = {
    "neutral": {
        "speed_factor": 1.00, "temperature": 0.68, "top_k": 10, "top_p": 0.88,
        "fragment_interval": 0.16, "seed": 3101, "repetition_penalty": 1.35,
    },
    "gentle": {
        "speed_factor": 0.91, "temperature": 0.60, "top_k": 7, "top_p": 0.82,
        "fragment_interval": 0.26, "seed": 3102, "repetition_penalty": 1.30,
    },
    "cheerful": {
        "speed_factor": 1.09, "temperature": 0.80, "top_k": 14, "top_p": 0.94,
        "fragment_interval": 0.11, "seed": 3103, "repetition_penalty": 1.32,
    },
    "concerned": {
        "speed_factor": 0.92, "temperature": 0.64, "top_k": 8, "top_p": 0.84,
        "fragment_interval": 0.24, "seed": 3104, "repetition_penalty": 1.32,
    },
    "serious": {
        "speed_factor": 0.96, "temperature": 0.56, "top_k": 6, "top_p": 0.78,
        "fragment_interval": 0.18, "seed": 3105, "repetition_penalty": 1.38,
    },
    "shy": {
        "speed_factor": 0.89, "temperature": 0.63, "top_k": 8, "top_p": 0.84,
        "fragment_interval": 0.28, "seed": 3106, "repetition_penalty": 1.30,
    },
}

SPEECH_EMOTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "serious": (
        r"我不喜欢你这样", r"别再否定自己", r"不许", r"必须", r"认真听", r"停一下",
        r"先停下", r"不能再", r"我还是要提醒", r"别骗自己", r"说真的", r"不可以",
    ),
    "concerned": (
        r"难受", r"疼", r"痛", r"害怕", r"焦虑", r"担心", r"别硬撑", r"别撑着",
        r"不舒服", r"还好吗", r"没睡", r"失眠", r"生病", r"受伤", r"是不是累了",
        r"怎么了", r"没事吧", r"吃药", r"休息一会",
    ),
    "cheerful": (
        r"太好了", r"太棒了", r"真棒", r"终于", r"做到了", r"好厉害", r"开心",
        r"恭喜", r"哈哈", r"嘿嘿", r"真好", r"好耶", r"喜欢", r"成功了", r"完成了",
    ),
    "shy": (
        r"害羞", r"别这样说", r"这个问题.{0,8}计算", r"不告诉你", r"先不说",
        r"不好意思", r"突然这么说", r"才没有", r"笨蛋", r"……",
    ),
    "gentle": (
        r"晚安", r"早点休息", r"睡吧", r"慢慢来", r"别急", r"陪着你", r"抱一下",
        r"抱抱", r"安心睡", r"轻一点", r"辛苦了", r"休息一下", r"今天很累", r"乖",
        r"没关系", r"我在", r"先缓一缓", r"照顾好自己",
    ),
}
SPEECH_EMOTION_PRIORITY = ("serious", "concerned", "cheerful", "shy", "gentle")

SPEECH_REQUESTED_EMOTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "neutral": (
        r"(?:自然|正常|平常|普通)(?:一点|些|地|的)?(?:说|语气|口吻|声音|回复|回答)",
    ),
    "gentle": (
        r"(?:温柔|轻柔|柔和|轻一点|慢一点)(?:地|的|一点|些)?(?:说|语气|口吻|声音|回复|回答)?",
        r"哄(?:哄)?我",
    ),
    "cheerful": (
        r"(?:开心|高兴|活泼|兴奋|元气)(?:地|的|一点|些)?(?:说|语气|口吻|声音|回复|回答)?",
    ),
    "concerned": (
        r"(?:担心|关心|安慰|心疼)(?:地|的|一点|些)?(?:说|语气|口吻|声音|回复|回答)?",
    ),
    "serious": (
        r"(?:认真|严肃|郑重|生气|不满)(?:地|的|一点|些)?(?:说|语气|口吻|声音|回复|回答)?",
    ),
    "shy": (
        r"(?:害羞|羞涩|不好意思)(?:地|的|一点|些)?(?:说|语气|口吻|声音|回复|回答)?",
    ),
}

ADAPTIVE_QQ_VOICE_RE = re.compile(
    r"(?:想听(?:你|Mio|澪)(?:说话|的声音)|哄哄我|陪我说两句|念给我听|叫我起床|睡前说|晚安|我(?:有点)?难受|我(?:有点)?害怕)",
    re.IGNORECASE,
)
SPEECH_PREFIX_RE = re.compile(
    r"^\s*(?:(?:语音|音频)(?:消息)?"
    r"(?:[（(]\s*(?:约|大约)?\s*\d+(?:\.\d+)?\s*秒\s*[）)])?"
    r"(?:里)?(?:说|回复)?|(?:然后)?(?:轻轻|小声|认真|开心地|慢慢地)?"
    r"(?:笑了?(?:一声|一下)?|叹了?(?:一口气|一声)?|停顿了?(?:一下)?))\s*[：:]\s*"
)
SPEECH_STAGE_DIRECTION_RE = re.compile(
    r"[（(](?=[^（）()]{0,80}(?:声音|声线|语气|音量|音调|语速|停顿|沉默|轻轻|小声|轻声|低声|笑|叹气|深吸|呼吸|说到|听不见|看着|转开|抬头|摇头|点头|脸红|害羞|犹豫|眨眼))"
    r"[^（）()]{0,100}[）)]"
)
SPEECH_STAGE_DIRECTION_LINE_RE = re.compile(
    r"^\s*(?:声音|声线|语气|音量|语速|停顿|沉默|轻轻地?|小声地?|轻声地?|低声地?|笑了?|叹气|深吸一口气|呼吸|说到最后|几乎听不见).{0,80}\s*$"
)
SPEECH_META_RE = re.compile(
    r"^\s*[（(].*(?:这次|语音|声音|听到|听见|发出来|没问题).*[）)]\s*$"
)
SPEECH_CONTENT_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
SPEECH_LATIN_RE = re.compile(r"[A-Za-z]+")
SPEECH_JAPANESE_RE = re.compile(r"[\u3040-\u30ff]")
SPEECH_HAN_RE = re.compile(r"[\u3400-\u9fff]")
SPEECH_TERM_PRONUNCIATIONS = {
    "ai": "人工智能",
    "qq": "扣扣",
    "gpt": "吉皮提",
    "api": "接口",
    "pdf": "批迪艾弗",
    "ui": "优艾",
    "unity": "尤尼蒂",
    "steam": "斯提姆",
    "deepseek": "迪普西克",
}
SPEECH_LETTER_PRONUNCIATIONS = {
    "a": "诶", "b": "比", "c": "西", "d": "迪", "e": "伊", "f": "艾弗", "g": "吉",
    "h": "艾尺", "i": "爱", "j": "杰", "k": "开", "l": "艾勒", "m": "艾姆", "n": "恩",
    "o": "欧", "p": "批", "q": "丘", "r": "阿尔", "s": "艾丝", "t": "提", "u": "优",
    "v": "维", "w": "达不溜", "x": "艾克斯", "y": "歪", "z": "贼德",
}


SPEECH_JAPANESE_QUICK_TRANSLATIONS = speech_translation_service.QUICK_JAPANESE_TRANSLATIONS
SPEECH_JAPANESE_AUTO_MODEL_ID = speech_translation_service.DEFAULT_TRANSLATION_MODEL_ID
SPEECH_JAPANESE_TRANSLATION_FAILURE_TEXT = (
    "今は日本語への翻訳がうまくできなかったよ。画面の文章を読んでね。"
)


def _speech_translation_model(config: dict[str, Any]) -> str:
    return (
        str(config.get("speech_translation_model_id") or SPEECH_JAPANESE_AUTO_MODEL_ID).strip()
        or SPEECH_JAPANESE_AUTO_MODEL_ID
    )


def _translate_speech_to_japanese(text: str, config: dict[str, Any]) -> str:
    global _speech_translation_last_error, _speech_translation_last_model

    try:
        result = speech_translation_service.translate(
            text,
            target_language="ja",
            model_id=_speech_translation_model(config),
            timeout_seconds=4.0,
        )
        _speech_translation_last_error = ""
        _speech_translation_last_model = result.model
        return result.text
    except speech_translation_service.SpeechTranslationError as exc:
        _speech_translation_last_model = ""
        _speech_translation_last_error = (
            f"{exc}（{exc.category}）；已停止本次日语朗读，不会回退为中文"
        )[:500]
        return ""


def _translate_speech_to_chinese(text: str, config: dict[str, Any]) -> str:
    global _speech_translation_last_error, _speech_translation_last_model

    try:
        result = speech_translation_service.translate(
            text,
            target_language="zh",
            model_id=_speech_translation_model(config),
            timeout_seconds=4.0,
        )
        _speech_translation_last_error = ""
        _speech_translation_last_model = result.model
        return result.text
    except speech_translation_service.SpeechTranslationError as exc:
        _speech_translation_last_model = ""
        _speech_translation_last_error = (
            f"{exc}（{exc.category}）；本次保留日语原文朗读"
        )[:500]
        return ""


def _prepare_speech_input(text: str, config: dict[str, Any]) -> tuple[str, str, bool]:
    configured_language = str(config.get("gpt_sovits_text_language") or "auto")
    detected_language = speech_text_language(text, "auto")
    if bool(config.get("gpt_sovits_translate_to_chinese", False)) and detected_language == "ja":
        translated = _translate_speech_to_chinese(text, config)
        if translated:
            return _naturalize_short_speech_text(translated, "zh"), "zh", True
        return _naturalize_short_speech_text(text, "ja"), "ja", False
    if bool(config.get("gpt_sovits_translate_to_japanese", False)) and detected_language == "zh":
        translated = _translate_speech_to_japanese(text, config)
        if translated:
            return _naturalize_short_speech_text(translated, "ja"), "ja", True
        detail = _speech_translation_last_error or "没有得到可用的日语译文"
        raise ValueError(f"日语朗读准备失败：{detail}")
    language = speech_text_language(text, configured_language)
    return _naturalize_short_speech_text(text, language), language, False


def _reference_audio_path(config: dict[str, Any]) -> Path:
    raw = str(config.get("gpt_sovits_ref_audio") or "").strip()
    if not raw:
        raise ValueError("还没有导入 Mio 的参考音频。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = settings.companion_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError("Mio 的参考音频不存在，请重新导入。")
    return path


def _emotion_reference(
    config: dict[str, Any],
    emotion: str,
    text_language: str = "",
) -> tuple[Path, str, str]:
    fallback: tuple[Path, str, str] | None = None
    try:
        fallback = (
            _reference_audio_path(config),
            str(config.get("gpt_sovits_prompt_text") or "").strip(),
            str(config.get("gpt_sovits_prompt_language") or "ja"),
        )
    except ValueError:
        # 已整理好的情绪参考音频不需要再手动导入一条默认参考音。
        pass
    if not bool(config.get("use_emotion_references", True)):
        if fallback is not None:
            return fallback
        raise ValueError("这个音色还没有可用的参考音频。")
    mapping_path = settings.voice_training_dir / "emotion-references.json"
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        language_group = None
        if text_language in {"zh", "all_zh"}:
            language_group = mapping.get("zh")
        elif text_language in {"ja", "all_ja"}:
            language_group = mapping.get("ja")
        original_reference = mapping.get(emotion) or mapping.get("neutral")
        language_reference = None
        if isinstance(language_group, dict):
            language_reference = language_group.get(emotion) or language_group.get("neutral")

        # 中文情绪参考音是由模型二次生成的，继续拿它做参考会逐代压平原素材的
        # 情绪。中文跨语种合成优先使用原始日语片段，保留真实的语气和韵律。
        # Very short requests can reproduce a cross-language reference
        # verbatim. Prefer a reference matching the requested language.
        selected = language_reference if isinstance(language_reference, dict) else original_reference
        if not isinstance(selected, dict):
            if fallback is not None:
                return fallback
            raise ValueError("还没有可用的 Mio 参考音频。")
        audio_path = Path(str(selected.get("audio") or "")).expanduser()
        if not audio_path.is_absolute():
            audio_path = mapping_path.parent / audio_path
        audio_path = audio_path.resolve()
        prompt_text = str(selected.get("text") or "").strip()
        prompt_language = str(selected.get("language") or "ja").strip().lower()
        if audio_path.is_file() and prompt_text:
            return audio_path, prompt_text, prompt_language
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    if fallback is not None:
        return fallback
    raise ValueError("当前音色还没有可用的参考音频。")


def _auxiliary_reference_paths(config: dict[str, Any], primary: Path, text_language: str) -> list[str]:
    if text_language not in {"zh", "all_zh"}:
        return []
    # 中文需要保留情绪参考音本身的韵律。再混入一条普通参考音会让
    # GPT-SoVITS 更偏向平均音色，实际试听中会明显压平中文情绪。
    # 日语链路仍使用单条原有参考音，不受此分支影响。
    return []


def _gpt_sovits_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:500]
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("detail") or "").strip()
        exception = str(payload.get("Exception") or payload.get("exception") or "").strip()
        return "：".join(part for part in (message, exception) if part)[:500] or str(payload)[:500]
    return str(payload)[:500]


def _apply_gpt_sovits_weights(client: httpx.Client, config: dict[str, Any]) -> None:
    global _gpt_sovits_applied_gpt_weights, _gpt_sovits_applied_sovits_weights
    url = str(config["gpt_sovits_url"])
    requested = (
        ("gpt", str(config.get("gpt_sovits_gpt_weights") or "").strip()),
        ("sovits", str(config.get("gpt_sovits_sovits_weights") or "").strip()),
    )
    for kind, raw_path in requested:
        if not raw_path:
            continue
        if not _voice_weight_path(raw_path, kind):
            raise ValueError(f"{kind.upper()} 音色模型格式不正确。")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"选择的 {kind.upper()} 音色模型不存在。")
        applied = _gpt_sovits_applied_gpt_weights if kind == "gpt" else _gpt_sovits_applied_sovits_weights
        if str(path) == applied:
            continue
        endpoint = "/set_gpt_weights" if kind == "gpt" else "/set_sovits_weights"
        response = client.get(f"{url}{endpoint}", params={"weights_path": str(path)})
        if not response.is_success:
            raise OSError(f"GPT-SoVITS 加载音色模型失败：{_gpt_sovits_error(response)}")
        if kind == "gpt":
            _gpt_sovits_applied_gpt_weights = str(path)
        else:
            _gpt_sovits_applied_sovits_weights = str(path)


def _synthesize_gpt_sovits_wav(
    text: str,
    config: dict[str, Any],
    *,
    emotion: str | None = None,
    context: str = "",
) -> bytes:
    url, payload = _gpt_sovits_request_payload(
        text,
        config,
        emotion=emotion,
        context=context,
        streaming_mode=False,
    )
    with _voice_synthesis_lock:
        with httpx.Client(timeout=120, trust_env=False) as client:
            _apply_gpt_sovits_weights(client, config)
            response = client.post(f"{url}/tts", json=payload)
    if not response.is_success:
        raise OSError(f"GPT-SoVITS 生成失败：{_gpt_sovits_error(response)}")
    content = bytes(response.content)
    if not content.startswith(b"RIFF") or len(content) < 44:
        raise OSError("GPT-SoVITS 没有返回有效的 WAV 音频。")
    return content


def _record_reference_audio_block(
    *,
    score: float | None,
    reference: Path | None,
    expected_text: str,
) -> None:
    with _voice_runtime_metrics_lock:
        _voice_runtime_metrics["reference_leak_blocks"] = int(
            _voice_runtime_metrics.get("reference_leak_blocks") or 0
        ) + 1
        _voice_runtime_metrics["last_reference_leak"] = {
            "blocked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "score": None if score is None else round(float(score), 4),
            "reference": reference.name if reference is not None else "",
            "expected_text": str(expected_text or "")[:80],
        }


def iter_speech_wav_stream(
    text: str,
    *,
    context: str = "",
    emotion: str | None = None,
    model_id: str = "",
    language: str = "",
) -> Iterator[bytes]:
    """Yield GPT-SoVITS WAV bytes as they arrive, with complete-WAV fallback."""
    global _gpt_sovits_last_error

    clean = clean_speech_text(text)
    if not clean:
        raise ValueError("这条消息没有可朗读的正文。")
    clean = clean[:600]
    if load_config().get("voice_engine") == "cloud":
        # 云端引擎一次合成完整 WAV，兼容流式接口的消费方。
        yield synthesize_speech_wav(clean, context=context, emotion=emotion)
        return
    _, config = resolve_voice_profile(model_id, speech_language=language)
    if config.get("engine") == SO_VITS_SVC_ENGINE:
        yield synthesize_speech_wav(clean, context=context, emotion=emotion, model_id=model_id, language=language)
        return
    if _uses_genie_runtime(config):
        # Genie 的单次调用会先生成完整 WAV。把长回复切成短段，再把各段
        # PCM 接到同一个流式 WAV 中，避免首段超过桌宠首音门槛后整条被取消。
        try:
            prepared_text, prepared_language, _ = _prepare_speech_input(clean, config)
        except ValueError as exc:
            if (
                bool(config.get("gpt_sovits_translate_to_japanese", False))
                and speech_text_language(clean, "auto") == "zh"
            ):
                # Translation is a single whole-reply operation. If it fails,
                # speak a truthful Japanese status sentence instead of falling
                # back to Chinese or leaving the user with a silent response.
                logger.warning("整条日语朗读翻译失败，改播日语故障提示：%s", exc)
                prepared_text = SPEECH_JAPANESE_TRANSLATION_FAILURE_TEXT
                prepared_language = "ja"
            else:
                raise
        yield _streaming_pcm_wav_header()
        successful_segments = 0
        last_segment_error: Exception | None = None
        for segment, is_line_break in _split_genie_stream_segments(prepared_text):
            try:
                content = synthesize_speech_wav(
                    segment,
                    context=context,
                    emotion=emotion,
                    model_id=model_id,
                    language=language,
                    _prepared_language=prepared_language,
                )
            except Exception as exc:
                # A malformed short segment must not discard already generated
                # audio or prevent later dialogue lines from being spoken.
                last_segment_error = exc
                logger.warning("Genie 语音片段失败，继续后续片段：%s", exc)
                continue
            yield _wav_pcm_payload(content)
            successful_segments += 1
            if is_line_break:
                # Explicit source line breaks are meaningful dialogue boundaries.
                # Keep them audible even when the TTS model trims trailing silence.
                yield b"\x00\x00" * int(32000 * 0.30)
        if successful_segments == 0 and last_segment_error is not None:
            raise OSError(f"Genie 没有生成任何可播放片段：{last_segment_error}") from last_segment_error
        return
    selected_emotion = emotion if emotion in SPEECH_EMOTION_LABELS else infer_speech_emotion(clean, context)
    yielded_any = False
    started_at = time.monotonic()
    try:
        _ensure_gpt_sovits_service()
        if not bool(config.get("voice_streaming_enabled", True)):
            yield synthesize_speech_wav(
                clean,
                context=context,
                emotion=selected_emotion,
                model_id=model_id,
                language=language,
            )
            return

        url, payload = _gpt_sovits_request_payload(
            clean,
            config,
            emotion=selected_emotion,
            context=context,
            streaming_mode=2,
        )
        with _voice_synthesis_lock:
            with httpx.Client(timeout=120, trust_env=False) as client:
                _apply_gpt_sovits_weights(client, config)
                with client.stream("POST", f"{url}/tts", json=payload) as response:
                    if not response.is_success:
                        response.read()
                        raise OSError(f"GPT-SoVITS 流式生成失败：{_gpt_sovits_error(response)}")
                    for chunk in response.iter_bytes(chunk_size=32 * 1024):
                        if not chunk:
                            continue
                        if not yielded_any:
                            with _voice_runtime_metrics_lock:
                                _voice_runtime_metrics["last_first_audio_ms"] = round(
                                    (time.monotonic() - started_at) * 1000,
                                    1,
                                )
                        yielded_any = True
                        yield bytes(chunk)
        if not yielded_any:
            raise OSError("GPT-SoVITS 流式响应没有音频数据。")
        _gpt_sovits_last_error = ""
    except httpx.RemoteProtocolError as exc:
        if yielded_any and "incomplete chunked read" in str(exc).lower():
            _gpt_sovits_last_error = ""
            return
        if yielded_any:
            _gpt_sovits_last_error = str(exc)
            raise
        content = synthesize_speech_wav(
            clean,
            context=context,
            emotion=selected_emotion,
            model_id=model_id,
            language=language,
        )
        _gpt_sovits_last_error = ""
        yield content
    except Exception as exc:
        if yielded_any:
            _gpt_sovits_last_error = str(exc)
            raise
        # Older GPT-SoVITS builds may reject streaming_mode=2. Keep Electron
        # usable by returning the established complete WAV through the same endpoint.
        content = synthesize_speech_wav(
            clean,
            context=context,
            emotion=selected_emotion,
            model_id=model_id,
            language=language,
        )
        _gpt_sovits_last_error = ""
        yield content


def _gpt_sovits_request_payload(
    text: str,
    config: dict[str, Any],
    *,
    emotion: str | None = None,
    context: str = "",
    streaming_mode: bool | int = False,
) -> tuple[str, dict[str, Any]]:
    emotion = emotion if emotion in SPEECH_EMOTION_LABELS else infer_speech_emotion(text, context)
    prepared_text, text_language, translated = _prepare_speech_input(text, config)
    reference, prompt_text, prompt_language = _emotion_reference(config, emotion, text_language)
    if not prompt_text:
        raise ValueError("请填写参考音频的准确原文。")
    url = str(config.get("gpt_sovits_url") or DEFAULT_CONFIG["gpt_sovits_url"]).rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("GPT-SoVITS 服务地址必须以 http:// 或 https:// 开头。")
    speech_text = prepare_speech_prosody(prepared_text, emotion, text_language)
    style = (
        GPT_SOVITS_CHINESE_EMOTION_STYLES[emotion]
        if text_language in {"zh", "all_zh"}
        else GPT_SOVITS_EMOTION_STYLES[emotion]
    )
    payload = {
        "text": speech_text,
        "text_lang": text_language,
        "ref_audio_path": str(reference),
        "aux_ref_audio_paths": _auxiliary_reference_paths(config, reference, text_language),
        "prompt_lang": prompt_language,
        "prompt_text": prompt_text,
        "text_split_method": "cut5" if text_language in {"zh", "all_zh"} else "cut1",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": streaming_mode,
        **style,
    }
    if translated and text_language == "ja":
        payload["text_split_method"] = "cut1"
    return url, payload


class _WaveFormatEx(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WaveHeader(ctypes.Structure):
    pass


_WaveHeader._fields_ = [
    ("lpData", ctypes.c_void_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", ctypes.c_size_t),
    ("dwFlags", wintypes.DWORD),
    ("dwLoops", wintypes.DWORD),
    ("lpNext", ctypes.POINTER(_WaveHeader)),
    ("reserved", ctypes.c_size_t),
]


def _stream_wav_header(data: bytearray) -> tuple[_WaveFormatEx, int] | None:
    if len(data) < 12:
        return None
    if bytes(data[:4]) != b"RIFF" or bytes(data[8:12]) != b"WAVE":
        raise OSError("GPT-SoVITS 流式响应不是 WAV 音频。")
    position = 12
    format_values: tuple[int, int, int, int, int, int] | None = None
    while position + 8 <= len(data):
        chunk_id = bytes(data[position : position + 4])
        chunk_size = struct.unpack_from("<I", data, position + 4)[0]
        chunk_start = position + 8
        if chunk_id == b"fmt ":
            if len(data) < chunk_start + min(chunk_size, 16):
                return None
            format_values = struct.unpack_from("<HHIIHH", data, chunk_start)
        elif chunk_id == b"data":
            if format_values is None:
                raise OSError("GPT-SoVITS 流式 WAV 缺少音频格式。")
            return _WaveFormatEx(*format_values, 0), chunk_start
        next_position = chunk_start + chunk_size + (chunk_size % 2)
        if next_position > len(data):
            return None
        position = next_position
    return None


def _wave_out_play_chunk(
    winmm: Any,
    handle: wintypes.HANDLE,
    content: bytes,
    generation: int,
) -> bool:
    if not content:
        return True
    buffer = ctypes.create_string_buffer(content)
    header = _WaveHeader(
        ctypes.cast(buffer, ctypes.c_void_p),
        len(content),
        0,
        0,
        0,
        0,
        None,
        0,
    )
    if winmm.waveOutPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)) != 0:
        raise OSError("Windows 无法准备流式语音缓冲区。")
    try:
        if winmm.waveOutWrite(handle, ctypes.byref(header), ctypes.sizeof(header)) != 0:
            raise OSError("Windows 无法播放流式语音。")
        while not (header.dwFlags & 0x00000001):
            with _speech_lock:
                if generation != _speech_generation:
                    winmm.waveOutReset(handle)
                    return False
            time.sleep(0.01)
        return True
    finally:
        winmm.waveOutUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))


def _play_gpt_sovits_stream(
    text: str,
    config: dict[str, Any],
    generation: int,
    *,
    emotion: str | None = None,
    context: str = "",
    on_audio_started: Callable[[float], None] | None = None,
    started_at: float = 0.0,
) -> bool:
    if os.name != "nt":
        raise OSError("流式本地语音当前只支持 Windows。")
    url, payload = _gpt_sovits_request_payload(
        text,
        config,
        emotion=emotion,
        context=context,
        streaming_mode=2,
    )
    winmm = ctypes.WinDLL("winmm")
    audio_handle = wintypes.HANDLE()
    pending = bytearray()
    wav_format: _WaveFormatEx | None = None
    played_any = False
    with _voice_synthesis_lock:
        with httpx.Client(timeout=120, trust_env=False) as client:
            _apply_gpt_sovits_weights(client, config)
            with client.stream("POST", f"{url}/tts", json=payload) as response:
                if not response.is_success:
                    response.read()
                    raise OSError(f"GPT-SoVITS 流式生成失败：{_gpt_sovits_error(response)}")
                try:
                    for chunk in response.iter_bytes(chunk_size=32 * 1024):
                        if not chunk:
                            continue
                        with _speech_lock:
                            if generation != _speech_generation:
                                return False
                        pending.extend(chunk)
                        if wav_format is None:
                            parsed = _stream_wav_header(pending)
                            if parsed is None:
                                continue
                            wav_format, audio_start = parsed
                            if winmm.waveOutOpen(
                                ctypes.byref(audio_handle),
                                0xFFFFFFFF,
                                ctypes.byref(wav_format),
                                0,
                                0,
                                0,
                            ) != 0:
                                raise OSError("Windows 无法打开流式语音播放设备。")
                            volume = max(0, min(100, int(config.get("voice_volume", 85))))
                            channel_volume = round(0xFFFF * volume / 100)
                            winmm.waveOutSetVolume(
                                audio_handle,
                                channel_volume | (channel_volume << 16),
                            )
                            del pending[:audio_start]
                        block_align = max(1, int(wav_format.nBlockAlign))
                        playable_size = len(pending) - (len(pending) % block_align)
                        if playable_size <= 0:
                            continue
                        playable = bytes(pending[:playable_size])
                        del pending[:playable_size]
                        if not played_any:
                            played_any = True
                            if on_audio_started is not None:
                                try:
                                    on_audio_started(max(0.0, time.monotonic() - started_at))
                                except Exception:
                                    pass
                        if not _wave_out_play_chunk(winmm, audio_handle, playable, generation):
                            return False
                except httpx.RemoteProtocolError as exc:
                    if not played_any or "incomplete chunked read" not in str(exc).lower():
                        raise
                finally:
                    if wav_format is not None and pending:
                        _wave_out_play_chunk(winmm, audio_handle, bytes(pending), generation)
                    if audio_handle:
                        winmm.waveOutReset(audio_handle)
                        winmm.waveOutClose(audio_handle)
    if not played_any:
        raise OSError("GPT-SoVITS 流式响应没有音频数据。")
    return True


def inspect_speech_wav_quality(
    content: bytes,
    expected_text: str,
    *,
    language: str = "auto",
    use_local_asr: bool = True,
) -> dict[str, Any]:
    global _voice_quality_last
    reasons: list[str] = []
    try:
        metrics = _wav_quality_metrics(content)
    except ValueError as exc:
        metrics = {}
        reasons.append(str(exc))

    if metrics:
        duration = float(metrics["duration_seconds"])
        if duration < 0.12:
            reasons.append("语音时长过短")
        if float(metrics["rms"]) < 0.003:
            reasons.append("语音整体音量过低")
        if float(metrics["silence_ratio"]) > 0.96:
            reasons.append("语音静音占比过高")
        if float(metrics["clipping_ratio"]) > 0.03:
            reasons.append("语音削波失真过多")

    diagnostic: dict[str, Any] = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "passed": not reasons,
        "reasons": reasons,
        "metrics": metrics,
        "semantic_check": "not_available" if use_local_asr else "disabled",
        "transcript": "",
        "transcript_language": "",
        "similarity": None,
    }

    normalized_expected = _speech_text_for_comparison(expected_text)
    duration = float(metrics.get("duration_seconds") or 0)
    if not reasons and use_local_asr and len(normalized_expected) >= 4 and duration >= 0.55:
        asr_language = "zh" if language in {"zh", "all_zh"} else "ja" if language in {"ja", "all_ja"} else "auto"
        transcript_result = system_audio_service.transcribe_wav_for_quality(
            content,
            language=asr_language,
        )
        if transcript_result is not None:
            transcript = str(transcript_result.get("text") or "").strip()
            diagnostic["transcript"] = transcript
            diagnostic["transcript_language"] = str(transcript_result.get("language") or "")
            if transcript_result.get("error"):
                diagnostic["semantic_check"] = "worker_error"
                diagnostic["semantic_error"] = str(transcript_result["error"])
            elif not transcript:
                diagnostic["semantic_check"] = "failed"
                reasons.append("本地语音识别没有听清生成内容")
            else:
                similarity = _speech_text_similarity(expected_text, transcript)
                diagnostic["similarity"] = similarity
                diagnostic["semantic_check"] = "passed" if similarity >= 0.28 else "failed"
                if similarity < 0.28:
                    reasons.append("生成语音与准备朗读的文字差异过大")

    diagnostic["passed"] = not reasons
    diagnostic["reasons"] = reasons
    with _voice_quality_lock:
        _voice_quality_last = diagnostic
    return dict(diagnostic)


def _speak_wav_worker_impl(
    text: str,
    generation: int,
    context: str,
    result: dict[str, Any] | None = None,
    emotion: str | None = None,
    streaming: bool = True,
    on_audio_started: Callable[[float], None] | None = None,
    model_id: str = "",
    language: str = "",
) -> None:
    global _gpt_sovits_last_error
    started_at = time.monotonic()
    try:
        def notify_audio_started(latency: float) -> None:
            with _voice_runtime_metrics_lock:
                _voice_runtime_metrics["last_first_audio_ms"] = round(latency * 1000, 1)
            if result is not None:
                result["audio_started"] = True
            if on_audio_started is not None:
                on_audio_started(latency)

        if load_config().get("voice_engine") == "cloud":
            content = synthesize_speech_wav(
                text,
                context=context,
                emotion=emotion,
                model_id=model_id,
                language=language,
            )
            with _speech_lock:
                if generation != _speech_generation:
                    if result is not None:
                        result["canceled"] = True
                    return
            import winsound

            notify_audio_started(max(0.0, time.monotonic() - started_at))
            winsound.PlaySound(content, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            if result is not None:
                result["played"] = True
            _gpt_sovits_last_error = ""
            return

        _, config = resolve_voice_profile(model_id, speech_language=language)
        if streaming and config.get("engine") == MIO_VOICE_ENGINE and not _uses_genie_runtime(config):
            try:
                _ensure_gpt_sovits_service()
                played = _play_gpt_sovits_stream(
                    text,
                    config,
                    generation,
                    emotion=emotion,
                    context=context,
                    on_audio_started=notify_audio_started,
                    started_at=started_at,
                )
            except Exception:
                if result is not None and result.get("audio_started"):
                    raise
                # Older GPT-SoVITS builds may not implement streaming_mode=2.
                # Retry once with the existing complete-WAV path before reporting failure.
                content = synthesize_speech_wav(
                    text,
                    context=context,
                    emotion=emotion,
                    model_id=model_id,
                    language=language,
                )
                with _speech_lock:
                    if generation != _speech_generation:
                        if result is not None:
                            result["canceled"] = True
                        return
                import winsound

                notify_audio_started(max(0.0, time.monotonic() - started_at))
                winsound.PlaySound(content, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                played = True
            if result is not None:
                result["played"] = played
                result["canceled"] = not played
            if played:
                _gpt_sovits_last_error = ""
            return
        content = synthesize_speech_wav(
            text,
            context=context,
            emotion=emotion,
            model_id=model_id,
            language=language,
        )
        with _speech_lock:
            if generation != _speech_generation:
                if result is not None:
                    result["canceled"] = True
                return
        import winsound

        notify_audio_started(max(0.0, time.monotonic() - started_at))
        winsound.PlaySound(content, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        if result is not None:
            result["played"] = True
    except Exception as exc:
        message = f"本地播放失败：{exc}"
        _gpt_sovits_last_error = message
        if result is not None:
            result["error"] = message


def _speak_wav_worker(
    text: str,
    generation: int,
    context: str,
    result: dict[str, Any] | None = None,
    emotion: str | None = None,
    streaming: bool = True,
    on_audio_started: Callable[[float], None] | None = None,
    model_id: str = "",
    language: str = "",
) -> None:
    global _speech_owner_generation, _speech_owner_priority, _speech_owner_source
    # Only one expensive TTS synthesis may run at once. A newer utterance
    # supersedes queued work before it can enter Genie/ONNX.
    try:
        with _speech_synthesis_lock:
            with _speech_lock:
                if generation != _speech_generation:
                    if result is not None:
                        result["canceled"] = True
                    return
            _speak_wav_worker_impl(
                text,
                generation,
                context,
                result,
                emotion,
                streaming,
                on_audio_started,
                model_id,
                language,
            )
    finally:
        with _speech_lock:
            if _speech_owner_generation == generation:
                _speech_owner_generation = 0
                _speech_owner_priority = 0
                _speech_owner_source = ""


_realtime_text_bridge_lock = threading.Lock()
_realtime_text_bridge: Callable[[str], None] | None = None


def register_realtime_text_bridge(callback: Callable[[str], None] | None) -> None:
    """注册/注销实时语音桥接：活跃的豆包实时会话接管朗读文本。"""
    global _realtime_text_bridge
    with _realtime_text_bridge_lock:
        _realtime_text_bridge = callback


def _bridged_speak(text: str) -> bool:
    """实时语音会话活跃时，把朗读文本交给豆包实时会话出声。"""
    with _realtime_text_bridge_lock:
        callback = _realtime_text_bridge
    if callback is None:
        return False
    try:
        callback(text)
        return True
    except Exception:
        return False


def speak_text(
    text: str,
    *,
    context: str = "",
    wait: bool = False,
    emotion: str | None = None,
    streaming: bool | None = None,
    on_audio_started: Callable[[float], None] | None = None,
    model_id: str = "",
    language: str = "",
    source: str = "chat",
    priority: int | None = None,
) -> bool:
    global _speech_generation, _gpt_sovits_last_error
    global _speech_owner_generation, _speech_owner_priority, _speech_owner_source
    clean = clean_speech_text(text)
    config = load_config()
    if not clean or not config["voice_enabled"]:
        return False
    if _bridged_speak(clean[:600]):
        return True
    clean = clean[:600]
    use_streaming = bool(config.get("voice_streaming_enabled", True)) if streaming is None else bool(streaming)
    normalized_source = str(source or "chat").strip().lower()
    requested_priority = int(
        SPEECH_SOURCE_PRIORITIES.get(normalized_source, SPEECH_SOURCE_PRIORITIES["chat"])
        if priority is None
        else priority
    )
    with _speech_lock:
        if _speech_owner_generation and requested_priority < _speech_owner_priority:
            return False
        _speech_generation += 1
        generation = _speech_generation
        _speech_owner_generation = generation
        _speech_owner_priority = requested_priority
        _speech_owner_source = normalized_source
    result: dict[str, Any] = {"played": False, "canceled": False, "error": ""}
    worker = threading.Thread(
        target=_speak_wav_worker,
        args=(clean, generation, context, result, emotion, use_streaming, on_audio_started, model_id, language),
        name="mio-gpt-sovits-playback",
        daemon=True,
    )
    worker.start()
    if wait:
        worker.join(timeout=150)
        if worker.is_alive():
            _gpt_sovits_last_error = "本地语音播放超时"
            return False
        return bool(result["played"]) and not bool(result["error"])
    return True


def synthesize_speech_wav(
    text: str,
    *,
    context: str = "",
    emotion: str | None = None,
    require_configured_engine: bool = False,
    model_id: str = "",
    language: str = "",
    _prepared_language: str = "",
) -> bytes:
    global _gpt_sovits_last_error
    clean = clean_speech_text(text)
    if not clean:
        raise ValueError("这条消息没有可朗读的正文。")
    clean = clean[:600]
    engine_config = load_config()
    if engine_config.get("voice_engine") == "cloud":
        try:
            content = cloud_tts.synthesize_wav(clean, engine_config)
            content = _postprocess_speech_wav(content, int(engine_config.get("voice_volume", 85)))
            _gpt_sovits_last_error = ""
            return content
        except (ValueError, OSError, httpx.HTTPError) as exc:
            _gpt_sovits_last_error = str(exc)
            raise OSError(f"云端语音暂时不可用：{exc}") from exc
    profile_id, config = resolve_voice_profile(model_id, speech_language=language)
    try:
        selected_emotion = emotion if emotion in SPEECH_EMOTION_LABELS else infer_speech_emotion(clean, context)
        synthesis_config = config
        if config.get("engine") == SO_VITS_SVC_ENGINE:
            _, synthesis_config = _resolve_base_voice_profile(config, profile_id)
        if _prepared_language in {"zh", "ja", "all_zh", "all_ja"}:
            expected_speech_text = clean
            expected_language = _prepared_language
        else:
            expected_speech_text, expected_language, _ = _prepare_speech_input(clean, synthesis_config)
        if _uses_genie_runtime(synthesis_config) and config.get("engine") == MIO_VOICE_ENGINE:
            from . import genie_tts_service

            reference, prompt_text, prompt_language = _emotion_reference(
                synthesis_config,
                selected_emotion,
                expected_language,
            )
            genie_config = dict(synthesis_config)
            genie_config.update({
                "gpt_sovits_ref_audio": str(reference),
                "gpt_sovits_prompt_text": prompt_text,
                "gpt_sovits_prompt_language": prompt_language,
                "gpt_sovits_text_language": expected_language,
            })
            speech_text = prepare_speech_prosody(expected_speech_text, selected_emotion, expected_language)
            content = genie_tts_service.synthesize_wav(speech_text, genie_config)
            reference_candidates = _reference_audio_candidates(synthesis_config, reference)
            duration_limit = _short_speech_duration_limit(expected_speech_text)
            duration_seconds = _wav_duration_seconds(content)
            leaked_reference, leak_score, leaked_path = _looks_like_any_reference_audio(
                content,
                reference_candidates,
            )
            duration_abnormal = bool(
                duration_limit is not None and duration_seconds > duration_limit
            )
            if duration_abnormal or leaked_reference:
                if leaked_reference:
                    _record_reference_audio_block(
                        score=leak_score,
                        reference=leaked_path,
                        expected_text=expected_speech_text,
                    )
                # A short result with implausible duration is safer to replace
                # with a known natural sentence. For a longer, otherwise valid
                # result that copied a conditioning clip, retry the exact text
                # so the user's requested content is not silently changed.
                retry_source_text = (
                    _short_speech_recovery_text(
                        expected_speech_text,
                        expected_language,
                    )
                    if duration_abnormal
                    else expected_speech_text
                )
                logger.warning(
                    "Genie 音频异常（%.2fs / 上限 %s / 全参考最高相似度 %s / 命中 %s），重试一次",
                    duration_seconds,
                    "不适用" if duration_limit is None else f"{duration_limit:.2f}s",
                    "无法检测" if leak_score is None else f"{leak_score:.3f}",
                    leaked_path.name if leaked_path is not None else "无",
                )
                retry_text = prepare_speech_prosody(
                    retry_source_text,
                    selected_emotion,
                    expected_language,
                )
                content = genie_tts_service.synthesize_wav(retry_text, genie_config)
                retry_duration = _wav_duration_seconds(content)
                retry_limit = (
                    _recovery_speech_duration_limit(retry_source_text)
                    if duration_abnormal
                    else duration_limit
                )
                retry_leaked, retry_score, retry_path = _looks_like_any_reference_audio(
                    content,
                    reference_candidates,
                )
                retry_duration_abnormal = bool(
                    retry_limit is not None and retry_duration > retry_limit
                )
                if retry_leaked:
                    _record_reference_audio_block(
                        score=retry_score,
                        reference=retry_path,
                        expected_text=retry_source_text,
                    )
                if retry_duration_abnormal or retry_leaked:
                    raise OSError(
                        "Genie 重试音频仍异常"
                        f"（{retry_duration:.2f} 秒 / 上限 "
                        f"{'不适用' if retry_limit is None else f'{retry_limit:.2f} 秒'} / "
                        f"全参考最高相似度 "
                        f"{'无法检测' if retry_score is None else f'{retry_score:.3f}'} / "
                        f"命中 {retry_path.name if retry_path is not None else '无'}），"
                        "已阻止播放参考音频（训练参考）"
                    )
            with _voice_runtime_metrics_lock:
                metrics = genie_tts_service.runtime_status().get("last_metrics") or {}
                _voice_runtime_metrics["last_first_audio_ms"] = metrics.get("first_audio_ms")
        else:
            _ensure_gpt_sovits_service()
            content = _synthesize_gpt_sovits_wav(clean, synthesis_config, emotion=selected_emotion, context=context)
        if config.get("engine") == SO_VITS_SVC_ENGINE:
            from . import so_vits_svc_service

            content = so_vits_svc_service.convert_wav(content, config)
        content = _postprocess_speech_wav(content, int(config.get("voice_volume", 85)))
        if not (_uses_genie_runtime(synthesis_config) and config.get("engine") == MIO_VOICE_ENGINE):
            quality = inspect_speech_wav_quality(
                content,
                expected_speech_text,
                language=expected_language,
            )
            if not quality["passed"]:
                raise OSError("语音质量检查未通过：" + "；".join(quality["reasons"]))
        _gpt_sovits_last_error = ""
        return content
    except (ValueError, OSError, TimeoutError, httpx.HTTPError) as exc:
        _gpt_sovits_last_error = str(exc)
        profile_name = str(config.get("voice_profile_name") or profile_id)
        raise OSError(f"当前角色音色暂时不可用（{profile_name}）：{exc}") from exc


VOICE_PACKAGE_FORMAT = "mio-voice-package"
VOICE_PACKAGE_MAX_ENTRIES = 64
VOICE_PACKAGE_MAX_MANIFEST_BYTES = 1024 * 1024
VOICE_PACKAGE_MAX_COMPRESSION_RATIO = 1000
VOICE_PACKAGE_DISK_RESERVE_BYTES = 128 * 1024 * 1024
VOICE_REFERENCE_ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"}
VoiceImportProgress = Callable[[dict[str, object]], None]


def _terminate_process_tree(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()


def _gpt_sovits_process_running() -> bool:
    global _gpt_sovits_process
    with _gpt_sovits_lock:
        if _gpt_sovits_process is None:
            return False
        if _gpt_sovits_process.poll() is None:
            return True
        _gpt_sovits_process = None
        return False


def _probe_gpt_sovits(*, force: bool = False) -> bool:
    global _gpt_sovits_probe_at, _gpt_sovits_probe_result
    now = time.monotonic()
    if not force and now - _gpt_sovits_probe_at < 30:
        return _gpt_sovits_probe_result
    url = str(load_config().get("gpt_sovits_url") or DEFAULT_CONFIG["gpt_sovits_url"]).rstrip("/")
    try:
        response = httpx.get(f"{url}/docs", timeout=0.4, trust_env=False)
        available = response.status_code < 500
    except httpx.HTTPError:
        available = False
    _gpt_sovits_probe_at = now
    _gpt_sovits_probe_result = available
    return available


def voice_runtime_status(*, force_probe: bool = False) -> dict[str, Any]:
    config = load_config()
    reference = str(config.get("gpt_sovits_ref_audio") or "")
    reference_path = Path(reference) if reference else None
    if config.get("voice_engine") == "cloud":
        with _voice_runtime_metrics_lock:
            runtime_metrics = dict(_voice_runtime_metrics)
        return {
            "engine": "cloud",
            "engine_label": "云端语音（豆包）",
            "default_profile_id": config["default_voice_profile_id"],
            "profiles": config["voice_profiles"],
            "service_url": "",
            "service_running": True,
            "service_loading": False,
            "managed_running": False,
            "reference_ready": False,
            "reference_name": "",
            "prompt_ready": False,
            "emotion_reference_ready": False,
            "emotion_reference_count": 0,
            "emotion_reference_emotions": [],
            "translation": {
                "enabled": False,
                "target_language": "",
                "configured_model": "",
                "last_model": "",
                "last_error": "",
                "last_error_category": "",
                "cache_size": 0,
                "last_cache_hit": False,
            },
            "active_weights": {"gpt": "", "sovits": ""},
            "last_error": _gpt_sovits_last_error,
            "quality_gate": {},
            "load_seconds": None,
            "warmup_state": "idle",
            "warmup_seconds": None,
            "warmup_error": "",
            "last_first_audio_ms": runtime_metrics["last_first_audio_ms"],
            "fallback_engine": "",
            "weights": {"gpt": [], "sovits": []},
            "cloud_tts": {
                "configured": cloud_tts.cloud_tts_configured(config),
                "speaker": config.get("cloud_tts_speaker") or cloud_tts.CLOUD_TTS_DEFAULT_SPEAKER,
                "speech_rate": int(config.get("cloud_tts_speech_rate") or 0),
            },
        }
    if _uses_genie_runtime(config):
        from . import genie_tts_service

        genie_status = genie_tts_service.runtime_status()
        emotion_references = _emotion_reference_status()
        runtime_weights = _runtime_voice_weights()
        translation_status = speech_translation_service.status()
        with _voice_runtime_metrics_lock:
            runtime_metrics = dict(_voice_runtime_metrics)
        return {
            "engine": "gpt_sovits",
            "engine_label": "Genie ONNX CPU",
            "local_voice_runtime": GENIE_VOICE_RUNTIME,
            "default_profile_id": config["default_voice_profile_id"],
            "profiles": config["voice_profiles"],
            "service_url": "",
            "service_running": bool(genie_status["running"]),
            "service_ready": bool(genie_status["ready"]),
            "service_loading": False,
            "managed_running": bool(genie_status["running"]),
            "reference_ready": bool(reference_path and reference_path.is_file()),
            "reference_name": reference_path.name if reference_path and reference_path.is_file() else "",
            "prompt_ready": bool(str(config.get("gpt_sovits_prompt_text") or "").strip()),
            "emotion_reference_ready": emotion_references["ready"],
            "emotion_reference_count": emotion_references["count"],
            "emotion_reference_emotions": emotion_references["emotions"],
            "translation": {
                "enabled": bool(config.get("gpt_sovits_translate_to_japanese", False)),
                "target_language": "ja",
                "configured_model": config.get("speech_translation_model_id"),
                "last_model": translation_status["last_model"],
                "last_error": translation_status["last_error"],
                "last_error_category": translation_status["last_error_category"],
                "cache_size": translation_status["cache_size"],
                "last_cache_hit": translation_status["last_cache_hit"],
            },
            "active_weights": {
                "gpt": str(config.get("gpt_sovits_gpt_weights") or runtime_weights["gpt"]),
                "sovits": str(config.get("gpt_sovits_sovits_weights") or runtime_weights["sovits"]),
            },
            "last_error": genie_status["last_error"] or _gpt_sovits_last_error,
            "quality_gate": {},
            "load_seconds": runtime_metrics["load_seconds"],
            "warmup_state": runtime_metrics["warmup_state"],
            "warmup_seconds": runtime_metrics["warmup_seconds"],
            "warmup_error": runtime_metrics["warmup_error"],
            "last_first_audio_ms": (genie_status.get("last_metrics") or {}).get("first_audio_ms"),
            "fallback_engine": "",
            "weights": {"gpt": [], "sovits": []},
            "legacy_weights_available": bool(any(_voice_weight_options().values())),
            "runtime_dir": genie_status["runtime_dir"],
            "model_root": genie_status["model_root"],
            "model_dir": genie_status["model_dir"],
            "model_ready": bool(genie_status["model_ready"]),
            "model_source": genie_status["model_source"],
            "missing": genie_status["missing"],
        }
    service_running = _probe_gpt_sovits(force=force_probe)
    managed_running = _gpt_sovits_process_running()
    emotion_references = _emotion_reference_status()
    runtime_weights = _runtime_voice_weights()
    with _voice_quality_lock:
        quality_diagnostic = dict(_voice_quality_last)
    with _voice_runtime_metrics_lock:
        runtime_metrics = dict(_voice_runtime_metrics)
    translation_status = speech_translation_service.status()
    return {
        "engine": "gpt_sovits",
        "engine_label": "GPT-SoVITS",
        "local_voice_runtime": LEGACY_GPT_SOVITS_RUNTIME,
        "default_profile_id": config["default_voice_profile_id"],
        "profiles": config["voice_profiles"],
        "service_url": config["gpt_sovits_url"],
        "service_running": service_running,
        "service_loading": False,
        "managed_running": managed_running,
        "reference_ready": bool(reference_path and reference_path.is_file()),
        "reference_name": reference_path.name if reference_path and reference_path.is_file() else "",
        "prompt_ready": bool(str(config.get("gpt_sovits_prompt_text") or "").strip()),
        "emotion_reference_ready": emotion_references["ready"],
        "emotion_reference_count": emotion_references["count"],
        "emotion_reference_emotions": emotion_references["emotions"],
        "translation": {
            "enabled": bool(config.get("gpt_sovits_translate_to_japanese", False)),
            "target_language": "ja",
            "configured_model": config.get("speech_translation_model_id"),
            "last_model": translation_status["last_model"],
            "last_error": translation_status["last_error"],
            "last_error_category": translation_status["last_error_category"],
            "cache_size": translation_status["cache_size"],
            "last_cache_hit": translation_status["last_cache_hit"],
        },
        "active_weights": {
            "gpt": str(config.get("gpt_sovits_gpt_weights") or runtime_weights["gpt"]),
            "sovits": str(config.get("gpt_sovits_sovits_weights") or runtime_weights["sovits"]),
        },
        "last_error": _gpt_sovits_last_error,
        "quality_gate": quality_diagnostic,
        "load_seconds": runtime_metrics["load_seconds"],
        "warmup_state": runtime_metrics["warmup_state"],
        "warmup_seconds": runtime_metrics["warmup_seconds"],
        "warmup_error": runtime_metrics["warmup_error"],
        "last_first_audio_ms": runtime_metrics["last_first_audio_ms"],
        "fallback_engine": "",
        "weights": _voice_weight_options(),
    }


def voice_runtime_health() -> dict[str, Any]:
    """Return cached/process-only TTS health without issuing an HTTP probe."""
    if _uses_genie_runtime():
        from . import genie_tts_service

        status = genie_tts_service.runtime_status()
        with _voice_runtime_metrics_lock:
            runtime_metrics = dict(_voice_runtime_metrics)
        translation_status = speech_translation_service.status()
        return {
            "runtime": GENIE_VOICE_RUNTIME,
            "managed_running": bool(status["running"]),
            "desired_running": _gpt_sovits_desired_running,
            "observed_running": bool(status["running"]),
            "probe_age_seconds": 0.0,
            "probe_stale": False,
            "last_error": status["last_error"],
            "translation_last_error": translation_status["last_error"],
            "translation_last_error_category": translation_status["last_error_category"],
            "translation_last_model": translation_status["last_model"],
            "translation_retry_after_seconds": translation_status["retry_after_seconds"],
            "warmup_state": runtime_metrics["warmup_state"],
            "warmup_error": runtime_metrics["warmup_error"],
            "last_first_audio_ms": (status.get("last_metrics") or {}).get("first_audio_ms"),
        }
    now = time.monotonic()
    probe_age = max(0.0, now - _gpt_sovits_probe_at) if _gpt_sovits_probe_at else None
    observed_running = (
        bool(_gpt_sovits_probe_result)
        if probe_age is not None and probe_age <= 30
        else None
    )
    with _voice_runtime_metrics_lock:
        runtime_metrics = dict(_voice_runtime_metrics)
    translation_status = speech_translation_service.status()
    return {
        "managed_running": _gpt_sovits_process_running(),
        "desired_running": _gpt_sovits_desired_running,
        "observed_running": observed_running,
        "probe_age_seconds": round(probe_age, 3) if probe_age is not None else None,
        "probe_stale": observed_running is None,
        "last_error": _gpt_sovits_last_error,
        "translation_last_error": translation_status["last_error"],
        "translation_last_error_category": translation_status["last_error_category"],
        "translation_last_model": translation_status["last_model"],
        "translation_retry_after_seconds": translation_status["retry_after_seconds"],
        "warmup_state": runtime_metrics["warmup_state"],
        "warmup_error": runtime_metrics["warmup_error"],
        "last_first_audio_ms": runtime_metrics["last_first_audio_ms"],
    }


def start_voice_service() -> dict[str, Any]:
    status = start_gpt_sovits_service()
    warm_voice_runtime_async()
    return status


async def start_voice_on_app_startup() -> bool:
    """Start and warm the local voice runtime only when the saved switch permits it."""
    global _gpt_sovits_last_error
    config = load_config()
    if not bool(config.get("voice_startup_enabled", False)):
        return False
    if not bool(config.get("voice_enabled", True)):
        return False
    if str(config.get("voice_engine") or MIO_VOICE_ENGINE) != MIO_VOICE_ENGINE:
        return False
    if os.getenv("MIO_DESKTOP_APP", "").strip() == "1":
        event = _frontend_ready_event
        if event is None:
            event = asyncio.Event()
            globals()["_frontend_ready_event"] = event
        await event.wait()
    try:
        await asyncio.to_thread(start_voice_service)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _gpt_sovits_last_error = f"随应用启动音色服务失败：{exc}"
        logger.exception("随应用启动音色服务失败")
        return False
    return True


def reset_frontend_ready() -> None:
    """Create a fresh UI-ready gate for the current backend lifespan."""
    global _frontend_ready_event
    _frontend_ready_event = asyncio.Event()


def signal_frontend_ready() -> bool:
    """Release deferred desktop work after the interactive UI has mounted."""
    global _frontend_ready_event
    if _frontend_ready_event is None:
        _frontend_ready_event = asyncio.Event()
    was_ready = _frontend_ready_event.is_set()
    _frontend_ready_event.set()
    return not was_ready


def stop_voice_service() -> dict[str, Any]:
    return stop_gpt_sovits_service()


def restart_voice_service() -> dict[str, Any]:
    return restart_gpt_sovits_service()


def _prepare_gpt_sovits_nltk_data(root: Path, python: Path) -> Path:
    nltk_data_dir = root / "cache" / "nltk_data"
    nltk_data_dir.mkdir(parents=True, exist_ok=True)

    def resource_ready(resource: str) -> bool:
        if (nltk_data_dir / f"{resource}.zip").is_file():
            return True
        target = nltk_data_dir / resource
        if resource.endswith("averaged_perceptron_tagger_eng"):
            return all((target / filename).is_file() for filename in GPT_SOVITS_TAGGER_FILES)
        return target.exists()

    missing_packages = [
        package
        for resource, package in GPT_SOVITS_NLTK_RESOURCES
        if not resource_ready(resource)
    ]
    if not missing_packages:
        return nltk_data_dir

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NLTK_DATA"] = str(nltk_data_dir)
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        completed = subprocess.run(
            [
                str(python),
                "-m",
                "nltk.downloader",
                "--quiet",
                "-d",
                str(nltk_data_dir),
                *missing_packages,
            ],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        pass

    unresolved = [
        package
        for resource, package in GPT_SOVITS_NLTK_RESOURCES
        if not resource_ready(resource)
    ]
    if unresolved:
        tagger_dir = nltk_data_dir / "taggers" / "averaged_perceptron_tagger_eng"
        tagger_dir.mkdir(parents=True, exist_ok=True)
        fallback_files = dict(zip(GPT_SOVITS_TAGGER_FILES, ({}, {}, ["NN"]), strict=True))
        for filename, payload in fallback_files.items():
            target = tagger_dir / filename
            temporary = target.with_suffix(f"{target.suffix}.tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(target)
        (tagger_dir / ".mio-offline-fallback").write_text(
            "NLTK download was unavailable; use a neutral POS tag so GPT-SoVITS stays usable offline.\n",
            encoding="ascii",
        )
    return nltk_data_dir


def start_gpt_sovits_service() -> dict[str, Any]:
    global _gpt_sovits_process, _gpt_sovits_probe_at, _gpt_sovits_last_error
    global _gpt_sovits_desired_running
    _gpt_sovits_desired_running = True
    if _uses_genie_runtime():
        from . import genie_tts_service

        service_started = time.monotonic()
        genie_tts_service.start_worker()
        with _voice_runtime_metrics_lock:
            _voice_runtime_metrics.update({
                "service_started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "load_seconds": round(time.monotonic() - service_started, 3),
                "warmup_state": "idle",
                "warmup_seconds": None,
                "warmup_error": "",
            })
        return voice_runtime_status()
    if _probe_gpt_sovits(force=True):
        return voice_runtime_status()
    service_started = time.monotonic()
    config = load_config()
    parsed = urlparse(str(config["gpt_sovits_url"]))
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("只能由 Agent 启动本机 GPT-SoVITS 服务。")
    port = parsed.port or 9880
    root = settings.voice_training_dir
    source = root / "GPT-SoVITS"
    python = root / ".voice-env" / "Scripts" / "python.exe"
    api = source / "api_v2.py"
    tts_config = source / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
    for required in (python, api, tts_config):
        if not required.is_file():
            raise FileNotFoundError(f"找不到 GPT-SoVITS 运行文件：{required}")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HOME"] = str(root / "cache" / "huggingface")
    env["MODELSCOPE_CACHE"] = str(root / "cache" / "modelscope")
    env["NLTK_DATA"] = str(_prepare_gpt_sovits_nltk_data(root, python))
    fast_langdetect_dir = source / "GPT_SoVITS" / "pretrained_models" / "fast_langdetect"
    fast_langdetect_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_dir = root / "cache" / "bin"
    if (ffmpeg_dir / "ffmpeg.exe").is_file():
        env["PATH"] = f"{ffmpeg_dir}{os.pathsep}{env.get('PATH', '')}"
    with _gpt_sovits_lock:
        if _gpt_sovits_process is None or _gpt_sovits_process.poll() is not None:
            _gpt_sovits_process = subprocess.Popen(
                [
                    str(python),
                    str(api),
                    "-a",
                    parsed.hostname or "127.0.0.1",
                    "-p",
                    str(port),
                    "-c",
                    str(tts_config),
                ],
                cwd=str(source),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    _gpt_sovits_last_error = ""
    _gpt_sovits_probe_at = 0.0
    with _voice_runtime_metrics_lock:
        _voice_runtime_metrics.update({
            "service_started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "load_seconds": None,
            "warmup_state": "idle",
            "warmup_seconds": None,
            "warmup_error": "",
        })
    with _voice_runtime_metrics_lock:
        _voice_runtime_metrics["load_seconds"] = round(time.monotonic() - service_started, 3)
    status = voice_runtime_status()
    status["starting"] = True
    return status


def _warm_genie_language(language: str) -> None:
    selected = str(language or "zh").strip().lower()
    if selected not in {"zh", "ja"}:
        selected = "zh"
    from . import genie_tts_service

    if genie_tts_service.runtime_status().get("hot"):
        return
    # This calls the synthesis API directly and discards the returned WAV. It
    # never enters speak_text or the playback queue, so no warmup phrase can be
    # heard by the user while the first real ONNX inference is paid up front.
    warm_text = "こんにちは" if selected == "ja" else "你好呀"
    synthesize_speech_wav(
        warm_text,
        context="Mio 内部语音预热",
        emotion="gentle",
        language=selected,
    )


def warm_voice_runtime() -> dict[str, Any]:
    uses_genie = _uses_genie_runtime()
    genie_hot = False
    if uses_genie:
        from . import genie_tts_service

        genie_hot = bool(genie_tts_service.runtime_status().get("hot"))
    with _voice_runtime_metrics_lock:
        if _voice_runtime_metrics["warmup_state"] == "running":
            return dict(_voice_runtime_metrics)
        if _voice_runtime_metrics["warmup_state"] == "ready" and (
            not uses_genie or genie_hot
        ):
            return dict(_voice_runtime_metrics)
        _voice_runtime_metrics.update({
            "warmup_state": "running",
            "warmup_seconds": None,
            "warmup_error": "",
        })
    started_at = time.monotonic()
    try:
        config = load_config()
        if uses_genie:
            warm_language = str(config.get("pet_speech_language") or "zh").strip().lower()
            _warm_genie_language(warm_language)
        else:
            warm_language = str(config.get("pet_speech_language") or "zh").strip().lower()
            if warm_language not in {"zh", "ja"}:
                warm_language = "zh"
            warm_text = "うん" if warm_language == "ja" else "嗯"
            _ensure_gpt_sovits_service()
            _, selected = resolve_voice_profile("", config=config, speech_language=warm_language)
            _synthesize_gpt_sovits_wav(
                warm_text,
                selected,
                emotion="gentle",
                context="语音预热",
            )
    except Exception as exc:
        with _voice_runtime_metrics_lock:
            _voice_runtime_metrics.update({
                "warmup_state": "failed",
                "warmup_seconds": round(time.monotonic() - started_at, 3),
                "warmup_error": str(exc)[:500],
            })
    else:
        with _voice_runtime_metrics_lock:
            _voice_runtime_metrics.update({
                "warmup_state": "ready",
                "warmup_seconds": round(time.monotonic() - started_at, 3),
                "warmup_error": "",
            })
    with _voice_runtime_metrics_lock:
        return dict(_voice_runtime_metrics)


def warm_voice_runtime_async() -> bool:
    uses_genie = _uses_genie_runtime()
    genie_cold = False
    if uses_genie:
        from . import genie_tts_service

        genie_cold = not bool(genie_tts_service.runtime_status().get("hot"))
    with _voice_runtime_metrics_lock:
        state = _voice_runtime_metrics["warmup_state"]
        if state in {"scheduled", "running"}:
            return False
        if state == "ready" and not genie_cold:
            return False
        _voice_runtime_metrics["warmup_state"] = "scheduled"
    threading.Thread(
        target=warm_voice_runtime,
        name="mio-gpt-sovits-warmup",
        daemon=True,
    ).start()
    return True


def warm_voice_language_async(language: str) -> bool:
    global _voice_language_warmup_active
    selected = str(language or "").strip().lower()
    if selected not in {"zh", "ja"}:
        return False

    if _uses_genie_runtime():
        from . import genie_tts_service

        if genie_tts_service.runtime_status().get("hot"):
            return False
    with _voice_language_warmup_lock:
        if _voice_language_warmup_active:
            return False
        _voice_language_warmup_active = True

    def worker() -> None:
        global _voice_language_warmup_active
        try:
            if _uses_genie_runtime():
                _warm_genie_language(selected)
            else:
                _ensure_gpt_sovits_service()
        except Exception:
            logger.warning("Mio %s 语音切换预热失败", selected, exc_info=True)
        finally:
            with _voice_language_warmup_lock:
                _voice_language_warmup_active = False

    threading.Thread(
        target=worker,
        name=f"mio-voice-language-warmup-{selected}",
        daemon=True,
    ).start()
    return True


def _ensure_gpt_sovits_service(*, timeout_seconds: float = 90.0, poll_seconds: float = 0.5) -> None:
    if _uses_genie_runtime():
        from . import genie_tts_service

        genie_tts_service.start_worker()
        return
    if _probe_gpt_sovits(force=True):
        return
    start_gpt_sovits_service()
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        if _probe_gpt_sovits(force=True):
            return
        process = _gpt_sovits_process
        if process is not None and process.poll() is not None:
            raise OSError(f"GPT-SoVITS 启动失败，进程退出码：{process.returncode}")
        time.sleep(max(0.05, poll_seconds))
    raise TimeoutError("GPT-SoVITS 启动超时，请在桌宠页面检查音色服务。")


def stop_gpt_sovits_service() -> dict[str, Any]:
    global _gpt_sovits_process, _gpt_sovits_probe_at, _gpt_sovits_desired_running
    _gpt_sovits_desired_running = False
    if _uses_genie_runtime():
        from . import genie_tts_service

        genie_tts_service.stop_worker()
        with _voice_runtime_metrics_lock:
            _voice_runtime_metrics.update({
                "load_seconds": None,
                "warmup_state": "idle",
                "warmup_seconds": None,
                "warmup_error": "",
                "last_first_audio_ms": None,
            })
        return voice_runtime_status(force_probe=True)
    with _gpt_sovits_lock:
        process = _gpt_sovits_process
        _gpt_sovits_process = None
    _terminate_process_tree(process)
    _gpt_sovits_probe_at = 0.0
    with _voice_runtime_metrics_lock:
        _voice_runtime_metrics.update({
            "load_seconds": None,
            "warmup_state": "idle",
            "warmup_seconds": None,
            "warmup_error": "",
            "last_first_audio_ms": None,
        })
    return voice_runtime_status(force_probe=True)


def restart_gpt_sovits_service() -> dict[str, Any]:
    stop_gpt_sovits_service()
    return start_gpt_sovits_service()




# Domain composition. Legacy imports remain supported; new code belongs in the domains.
from .companion.configuration import ConfigurationService
_configuration_service = ConfigurationService(
    dep_DEFAULT_CONFIG=lambda: DEFAULT_CONFIG,
    dep_DEFAULT_VOICE_PROFILE_ID=lambda: DEFAULT_VOICE_PROFILE_ID,
    dep_GENIE_VOICE_RUNTIME=lambda: GENIE_VOICE_RUNTIME,
    dep_LEGACY_GPT_SOVITS_RUNTIME=lambda: LEGACY_GPT_SOVITS_RUNTIME,
    dep_LIVE2D_EXPRESSION_SLOT_IDS=lambda: LIVE2D_EXPRESSION_SLOT_IDS,
    dep_LIVE2D_MOTION_SLOT_IDS=lambda: LIVE2D_MOTION_SLOT_IDS,
    dep_MIO_VOICE_ENGINE=lambda: MIO_VOICE_ENGINE,
    dep_SO_VITS_SVC_ENGINE=lambda: SO_VITS_SVC_ENGINE,
    dep_VOICE_PROFILE_FIELDS=lambda: VOICE_PROFILE_FIELDS,
    dep__live2d_model_ids=lambda *args, **kwargs: _live2d_model_ids(*args, **kwargs),
    dep_settings=lambda: settings,
)
_voice_weight_path = _configuration_service._voice_weight_path
_migrate_config = _configuration_service._migrate_config
_normalize_live2d_motion_slots = _configuration_service._normalize_live2d_motion_slots
_normalize_live2d_expression_slots = _configuration_service._normalize_live2d_expression_slots
_legacy_voice_profile = _configuration_service._legacy_voice_profile
_uses_genie_runtime = _configuration_service._uses_genie_runtime
_normalize_voice_profiles = _configuration_service._normalize_voice_profiles
resolve_voice_profile = _configuration_service.resolve_voice_profile
_resolve_base_voice_profile = _configuration_service._resolve_base_voice_profile
_apply_voice_profile_config = _configuration_service._apply_voice_profile_config
load_config = _configuration_service.load_config
save_config = _configuration_service.save_config
load_normalized_config = _configuration_service.load_normalized_config
save_pet_position = _configuration_service.save_pet_position
save_pet_size = _configuration_service.save_pet_size

from .companion.live2d_assets import Live2DAssetsService
_live2d_assets_service = Live2DAssetsService(
    dep_LIVE2D_MODELS=lambda: LIVE2D_MODELS,
    dep__decode_image_data_url=lambda *args, **kwargs: _decode_image_data_url(*args, **kwargs),
    dep_load_config=lambda *args, **kwargs: load_config(*args, **kwargs),
    dep_save_config=lambda *args, **kwargs: save_config(*args, **kwargs),
)
live2d_state_dir = _live2d_assets_service.live2d_state_dir
_live2d_models_dir = _live2d_assets_service._live2d_models_dir
_live2d_runtime_path = _live2d_assets_service._live2d_runtime_path
_read_json_object = _live2d_assets_service._read_json_object
_safe_child = _live2d_assets_service._safe_child
_live2d_capabilities = _live2d_assets_service._live2d_capabilities
_register_unlisted_live2d_expressions = _live2d_assets_service._register_unlisted_live2d_expressions
_live2d_preview_candidate = _live2d_assets_service._live2d_preview_candidate
_custom_live2d_models = _live2d_assets_service._custom_live2d_models
available_live2d_models = _live2d_assets_service.available_live2d_models
_live2d_model_ids = _live2d_assets_service._live2d_model_ids
_write_live2d_runtime_selected = _live2d_assets_service._write_live2d_runtime_selected
select_live2d_model = _live2d_assets_service.select_live2d_model
import_live2d_model_directory = _live2d_assets_service.import_live2d_model_directory
delete_live2d_model = _live2d_assets_service.delete_live2d_model
live2d_model_preview_path = _live2d_assets_service.live2d_model_preview_path
save_live2d_model_preview_data_url = _live2d_assets_service.save_live2d_model_preview_data_url

from .companion.appearance import AppearanceService
_appearance_service = AppearanceService(
    dep_MAX_SPRITE_SHEET_BYTES=lambda: MAX_SPRITE_SHEET_BYTES,
    dep_MAX_SPRITE_SHEET_PIXELS=lambda: MAX_SPRITE_SHEET_PIXELS,
    dep_PET_SPRITE_FILES=lambda: PET_SPRITE_FILES,
    dep_settings=lambda: settings,
)
default_avatar_path = _appearance_service.default_avatar_path
profile_avatar_path = _appearance_service.profile_avatar_path
pet_sprite_path = _appearance_service.pet_sprite_path
pet_sprite_manifest = _appearance_service.pet_sprite_manifest
_decode_image_data_url = _appearance_service._decode_image_data_url
save_avatar_data_url = _appearance_service.save_avatar_data_url
save_profile_avatar_data_url = _appearance_service.save_profile_avatar_data_url
save_user_avatar_data_url = _appearance_service.save_user_avatar_data_url
save_chat_background_data_url = _appearance_service.save_chat_background_data_url
save_sprite_sheet_data_url = _appearance_service.save_sprite_sheet_data_url

from .companion.speech_text import SpeechTextService
_speech_text_service = SpeechTextService(
    dep_ADAPTIVE_QQ_VOICE_RE=lambda: ADAPTIVE_QQ_VOICE_RE,
    dep_SPEECH_CONTENT_RE=lambda: SPEECH_CONTENT_RE,
    dep_SPEECH_EMOTION_LABELS=lambda: SPEECH_EMOTION_LABELS,
    dep_SPEECH_EMOTION_PATTERNS=lambda: SPEECH_EMOTION_PATTERNS,
    dep_SPEECH_EMOTION_PRIORITY=lambda: SPEECH_EMOTION_PRIORITY,
    dep_SPEECH_HAN_RE=lambda: SPEECH_HAN_RE,
    dep_SPEECH_JAPANESE_RE=lambda: SPEECH_JAPANESE_RE,
    dep_SPEECH_LATIN_RE=lambda: SPEECH_LATIN_RE,
    dep_SPEECH_LETTER_PRONUNCIATIONS=lambda: SPEECH_LETTER_PRONUNCIATIONS,
    dep_SPEECH_META_RE=lambda: SPEECH_META_RE,
    dep_SPEECH_PREFIX_RE=lambda: SPEECH_PREFIX_RE,
    dep_SPEECH_REQUESTED_EMOTION_PATTERNS=lambda: SPEECH_REQUESTED_EMOTION_PATTERNS,
    dep_SPEECH_STAGE_DIRECTION_LINE_RE=lambda: SPEECH_STAGE_DIRECTION_LINE_RE,
    dep_SPEECH_STAGE_DIRECTION_RE=lambda: SPEECH_STAGE_DIRECTION_RE,
    dep_SPEECH_TERM_PRONUNCIATIONS=lambda: SPEECH_TERM_PRONUNCIATIONS,
    dep_load_config=lambda *args, **kwargs: load_config(*args, **kwargs),
)
_speech_latin_pronunciation = _speech_text_service._speech_latin_pronunciation
clean_speech_text = _speech_text_service.clean_speech_text
speech_text_language = _speech_text_service.speech_text_language
_naturalize_short_speech_text = _speech_text_service._naturalize_short_speech_text
_requested_speech_emotion = _speech_text_service._requested_speech_emotion
infer_speech_emotion = _speech_text_service.infer_speech_emotion
prepare_speech_prosody = _speech_text_service.prepare_speech_prosody
speech_emotion_info = _speech_text_service.speech_emotion_info
should_use_qq_voice = _speech_text_service.should_use_qq_voice

from .companion.audio_processing import AudioProcessingService
_audio_processing_service = AudioProcessingService(
    dep_settings=lambda: settings,
)
_split_genie_stream_text = _audio_processing_service._split_genie_stream_text
_split_genie_stream_segments = _audio_processing_service._split_genie_stream_segments
_streaming_pcm_wav_header = _audio_processing_service._streaming_pcm_wav_header
_wav_pcm_payload = _audio_processing_service._wav_pcm_payload
_wav_duration_seconds = _audio_processing_service._wav_duration_seconds
_short_speech_duration_limit = _audio_processing_service._short_speech_duration_limit
_short_speech_recovery_text = _audio_processing_service._short_speech_recovery_text
_recovery_speech_duration_limit = _audio_processing_service._recovery_speech_duration_limit
_wav_acoustic_features = _audio_processing_service._wav_acoustic_features
_reference_audio_leak_score_from_features = _audio_processing_service._reference_audio_leak_score_from_features
_cached_reference_audio_features = _audio_processing_service._cached_reference_audio_features
_reference_audio_features = _audio_processing_service._reference_audio_features
_reference_audio_leak_score = _audio_processing_service._reference_audio_leak_score
_looks_like_reference_audio = _audio_processing_service._looks_like_reference_audio
_reference_audio_candidates = _audio_processing_service._reference_audio_candidates
_looks_like_any_reference_audio = _audio_processing_service._looks_like_any_reference_audio
_postprocess_speech_wav = _audio_processing_service._postprocess_speech_wav
_speech_text_for_comparison = _audio_processing_service._speech_text_for_comparison
_speech_text_similarity = _audio_processing_service._speech_text_similarity
_wav_quality_metrics = _audio_processing_service._wav_quality_metrics

from .companion.voice_packages import VoicePackagesService
_voice_packages_service = VoicePackagesService(
    dep_DEFAULT_VOICE_PROFILE_ID=lambda: DEFAULT_VOICE_PROFILE_ID,
    dep_MIO_VOICE_ENGINE=lambda: MIO_VOICE_ENGINE,
    dep_SO_VITS_SVC_ENGINE=lambda: SO_VITS_SVC_ENGINE,
    dep_SPEECH_EMOTION_LABELS=lambda: SPEECH_EMOTION_LABELS,
    dep_VOICE_PACKAGE_DISK_RESERVE_BYTES=lambda: VOICE_PACKAGE_DISK_RESERVE_BYTES,
    dep_VOICE_PACKAGE_FORMAT=lambda: VOICE_PACKAGE_FORMAT,
    dep_VOICE_PACKAGE_MAX_COMPRESSION_RATIO=lambda: VOICE_PACKAGE_MAX_COMPRESSION_RATIO,
    dep_VOICE_PACKAGE_MAX_ENTRIES=lambda: VOICE_PACKAGE_MAX_ENTRIES,
    dep_VOICE_PACKAGE_MAX_MANIFEST_BYTES=lambda: VOICE_PACKAGE_MAX_MANIFEST_BYTES,
    dep_VOICE_REFERENCE_ALLOWED_EXTENSIONS=lambda: VOICE_REFERENCE_ALLOWED_EXTENSIONS,
    dep__voice_weight_path=lambda *args, **kwargs: _voice_weight_path(*args, **kwargs),
    dep_load_config=lambda *args, **kwargs: load_config(*args, **kwargs),
    dep_save_config=lambda *args, **kwargs: save_config(*args, **kwargs),
    dep_settings=lambda: settings,
)
save_voice_reference_data_url = _voice_packages_service.save_voice_reference_data_url
_report_voice_import_progress = _voice_packages_service._report_voice_import_progress
export_voice_package = _voice_packages_service.export_voice_package
_import_so_vits_svc_archive = _voice_packages_service._import_so_vits_svc_archive
_import_voice_package_source = _voice_packages_service._import_voice_package_source
import_voice_package = _voice_packages_service.import_voice_package
import_voice_package_file = _voice_packages_service.import_voice_package_file
_new_voice_profile_id = _voice_packages_service._new_voice_profile_id
_voice_weight_options = _voice_packages_service._voice_weight_options
_runtime_voice_weights = _voice_packages_service._runtime_voice_weights
_emotion_reference_status = _voice_packages_service._emotion_reference_status

from .companion.training import TrainingService
_training_service = TrainingService(
    dep__runtime_voice_weights=lambda *args, **kwargs: _runtime_voice_weights(*args, **kwargs),
    dep_settings=lambda: settings,
)
voice_training_status = _training_service.voice_training_status
launch_voice_training = _training_service.launch_voice_training


def _create_window_observer():
    use_process = os.getenv("MIO_SCREEN_OBSERVER_PROCESS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    is_worker = os.getenv("MIO_SCREEN_OBSERVER_WORKER", "").strip() == "1"
    if use_process and not is_worker:
        from .screen_observer_process import ScreenObserverProcess

        return ScreenObserverProcess()
    return WindowObserver()


window_observer = _create_window_observer()
game_observer = window_observer


def shutdown() -> None:
    def run(label: str, callback) -> None:
        try:
            callback()
        except Exception:
            logger.exception("关闭 Mio 运行资源失败：%s", label)

    run("屏幕观察", window_observer.stop)
    close_observer = getattr(window_observer, "close", None)
    if callable(close_observer):
        run("屏幕观察进程", close_observer)
    run("系统声音", system_audio_service.stop)
    run("临时预览", cleanup_legacy_preview)
    run("Live2D 桌宠", stop_pet)
    run("GPT-SoVITS", stop_gpt_sovits_service)
    try:
        from . import local_vision_service
    except Exception:
        logger.exception("加载本地视觉清理模块失败")
    else:
        run("本地视觉", local_vision_service.stop_server)
