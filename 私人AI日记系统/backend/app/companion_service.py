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


def _voice_weight_path(value: object, kind: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    expected_suffix = ".ckpt" if kind == "gpt" else ".pth"
    if path.suffix.lower() != expected_suffix:
        return ""
    return raw


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


def _migrate_config(data: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(data)
    try:
        schema_version = int(migrated.get("config_schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 1
    if schema_version < 2:
        try:
            legacy_screen_timeout = int(migrated.get("screen_request_timeout_seconds", 12))
        except (TypeError, ValueError):
            legacy_screen_timeout = 12
        if legacy_screen_timeout == 12:
            migrated["screen_request_timeout_seconds"] = 25
    migrated["config_schema_version"] = 2
    return migrated

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


def live2d_state_dir() -> Path:
    configured_state_root = os.getenv("MIO_DESKTOP_STATE_DIR", "").strip()
    state_root = Path(configured_state_root) if configured_state_root else (
        Path("D:/Mio数据")
        if Path("D:/").exists()
        else Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "MioAgent"
    )
    return (state_root / "Live2D桌宠").resolve()


def _live2d_models_dir() -> Path:
    path = live2d_state_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _live2d_runtime_path() -> Path:
    return live2d_state_dir() / "runtime.json"


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 {path.name}。") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 必须是 JSON 对象。")
    return value


def _safe_child(root: Path, relative: object) -> Path:
    text = str(relative or "").replace("\\", "/").strip()
    if not text:
        raise ValueError("Live2D 模型引用了空文件路径。")
    candidate = (root / text).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Live2D 模型包含越界文件路径。") from exc
    return candidate


def _live2d_capabilities(document: dict[str, Any]) -> dict[str, Any]:
    references = document.get("FileReferences")
    references = references if isinstance(references, dict) else {}
    raw_motions = references.get("Motions")
    raw_motions = raw_motions if isinstance(raw_motions, dict) else {}
    motions = [
        {"name": str(name), "count": len(entries) if isinstance(entries, list) else 0}
        for name, entries in raw_motions.items()
    ]
    names = [str(item["name"]) for item in motions]

    def match(*patterns: str) -> str:
        return next((name for name in names if any(re.search(pattern, name, re.I) for pattern in patterns)), "")

    idle = match(r"^idle$", r"idle|wait|stand|breath") or (names[0] if names else "")
    touch = match(r"^tapbody$", r"tap|touch|click|body") or next((name for name in names if name != idle), idle)
    slots = {
        "idle": idle,
        "touch": touch,
        "think": match(r"think|ponder|question|wonder|confus") or idle,
        "speak": match(r"speak|talk|voice|mouth|chat") or idle,
        "observe": match(r"observe|watch|look|search|scan") or idle,
        "cheerful": match(r"happy|joy|cheer|smile|laugh|win|victory|success") or touch or idle,
        "concerned": match(r"sad|worry|concern|trouble|down|lose|defeat") or idle,
        "alert": match(r"alert|angry|serious|surprise|shock|danger") or touch or idle,
        "attention": match(r"attention|curious|notice|question|look") or idle,
        "shy": match(r"shy|blush|embarrass") or idle,
    }
    slots = {key: value for key, value in slots.items() if value}
    expressions = references.get("Expressions")
    return {
        "format": str(document.get("Version") or "Cubism 4"),
        "motions": motions,
        "expressions": expressions if isinstance(expressions, list) else [],
        "physics": bool(references.get("Physics")),
        "pose": bool(references.get("Pose")),
        "idleGroup": idle,
        "tapGroup": touch,
        "motionSlots": slots,
        "unassignedMotions": [name for name in names if name not in set(slots.values())],
    }


def _register_unlisted_live2d_expressions(
    document: dict[str, Any],
    model_parent: Path,
    files: list[Path],
) -> bool:
    references = document.get("FileReferences")
    if not isinstance(references, dict):
        return False
    raw_expressions = references.get("Expressions")
    expressions = list(raw_expressions) if isinstance(raw_expressions, list) else []
    known_files = {
        str(item.get("File") or "").replace("\\", "/").lower()
        for item in expressions
        if isinstance(item, dict)
    }
    known_names = {
        str(item.get("Name") or "").strip().lower()
        for item in expressions
        if isinstance(item, dict)
    }
    changed = False
    for expression_path in sorted(
        (item for item in files if item.name.lower().endswith(".exp3.json")),
        key=lambda item: str(item).lower(),
    ):
        try:
            relative = str(expression_path.relative_to(model_parent)).replace("\\", "/")
        except ValueError:
            continue
        name = expression_path.name[:-len(".exp3.json")].strip() or "Expression"
        if relative.lower() in known_files:
            continue
        unique_name = name
        suffix = 2
        while unique_name.lower() in known_names:
            unique_name = f"{name}-{suffix}"
            suffix += 1
        expressions.append({"Name": unique_name, "File": relative})
        known_files.add(relative.lower())
        known_names.add(unique_name.lower())
        changed = True
    if changed or (expressions and not isinstance(raw_expressions, list)):
        references["Expressions"] = expressions
    return changed


def _live2d_preview_candidate(source_root: Path, files: list[Path]) -> Path | None:
    image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    candidates = []
    for path in files:
        if path.suffix.lower() not in image_suffixes:
            continue
        relative_parts = [part.lower() for part in path.relative_to(source_root).parts[:-1]]
        if any("texture" in part or "贴图" in part for part in relative_parts):
            continue
        name = path.stem.lower()
        exact_preview = path.name.lower() in {"preview.png", "preview.jpg", "preview.jpeg", "preview.webp"}
        keyword = any(word in name for word in ("preview", "cover", "icon", "avatar", "thumbnail", "立绘", "头像", "封面"))
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        candidates.append((0 if exact_preview else 1 if keyword else 2, -size, len(path.parts), path))
    return min(candidates, default=(0, 0, 0, None))[-1]


def _custom_live2d_models() -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    for model_root in sorted(_live2d_models_dir().iterdir(), key=lambda item: item.name.lower()):
        if not model_root.is_dir():
            continue
        metadata_path = model_root / "mio-model.json"
        if not metadata_path.is_file():
            continue
        try:
            metadata = _read_json_object(metadata_path)
            model_path = _safe_child(model_root, metadata.get("modelPath"))
            if not model_path.is_file():
                continue
            document = _read_json_object(model_path)
            model_files = [item for item in model_root.rglob("*") if item.is_file()]
            expressions_changed = _register_unlisted_live2d_expressions(
                document,
                model_path.parent,
                model_files,
            )
            refreshed_capabilities = _live2d_capabilities(document)
            metadata_changed = expressions_changed or metadata.get("capabilities") != refreshed_capabilities
            if expressions_changed:
                model_path.write_text(
                    json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            metadata["capabilities"] = refreshed_capabilities
            if not str(metadata.get("previewPath") or "").strip():
                preview_candidate = _live2d_preview_candidate(model_root, model_files)
                if preview_candidate is not None:
                    metadata["previewPath"] = str(
                        preview_candidate.relative_to(model_root)
                    ).replace("\\", "/")
                    metadata_changed = True
            if metadata_changed:
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        except ValueError:
            continue
        preview_relative = str(metadata.get("previewPath") or "").replace("\\", "/").strip()
        preview_path = None
        if preview_relative:
            try:
                candidate = _safe_child(model_root, preview_relative)
                preview_path = candidate if candidate.is_file() else None
            except ValueError:
                preview_path = None
        models.append({
            "id": model_root.name,
            "name": str(metadata.get("name") or model_root.name),
            "description": str(metadata.get("sourceLabel") or "本地导入的 Live2D 模型"),
            "model_path": str(model_path.relative_to(model_root)).replace("\\", "/"),
            "preview_path": preview_relative if preview_path else "",
            "preview_url": f"/api/companion/live2d/models/{model_root.name}/preview" if preview_path else "",
            "license": str((metadata.get("authorization") or {}).get("notice") or "授权状态未确认"),
            "source": "imported",
            "motion_count": sum(int(item.get("count") or 0) for item in (metadata.get("capabilities") or {}).get("motions", [])),
            "capabilities": metadata.get("capabilities") if isinstance(metadata.get("capabilities"), dict) else {},
            "imported": True,
        })
    return models


def available_live2d_models() -> list[dict[str, Any]]:
    return [dict(model, imported=False) for model in LIVE2D_MODELS] + _custom_live2d_models()


def _live2d_model_ids() -> set[str]:
    return {str(model["id"]) for model in available_live2d_models()}


def _write_live2d_runtime_selected(model_id: str) -> None:
    runtime_path = _live2d_runtime_path()
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(runtime_path.read_text(encoding="utf-8")) if runtime_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current["selectedModelId"] = model_id if model_id in {str(model["id"]) for model in _custom_live2d_models()} else ""
    temporary = runtime_path.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(runtime_path)


def select_live2d_model(model_id: str) -> str:
    clean = str(model_id or "").strip()
    if clean not in _live2d_model_ids():
        raise ValueError("没有找到这个 Live2D 模型。")
    _write_live2d_runtime_selected(clean)
    return clean


def import_live2d_model_directory(source: str | Path, display_name: str = "") -> dict[str, Any]:
    source_root = Path(source).expanduser().resolve()
    if not source_root.is_dir():
        raise ValueError("请选择一个有效的 Live2D 模型目录。")
    files = [item for item in source_root.rglob("*") if item.is_file()]
    if len(files) > 5000:
        raise ValueError("模型文件超过 5000 个，无法导入。")
    total_bytes = sum(item.stat().st_size for item in files)
    if total_bytes > 500 * 1024 * 1024:
        raise ValueError("模型目录不能超过 500MB。")
    model_files = sorted((item for item in files if item.name.lower().endswith(".model3.json")), key=str)
    if not model_files:
        raise ValueError("所选目录中没有 .model3.json 文件。")
    source_model = model_files[0]
    document = _read_json_object(source_model)
    references = document.get("FileReferences")
    if not isinstance(references, dict):
        raise ValueError("model3.json 缺少 FileReferences。")
    model_parent = source_model.parent
    moc_path = _safe_child(model_parent, references.get("Moc"))
    if not moc_path.is_file() or moc_path.suffix.lower() != ".moc3":
        raise ValueError("模型缺少可用的 .moc3 文件。")
    textures = references.get("Textures")
    if not isinstance(textures, list) or not textures:
        raise ValueError("模型没有纹理文件。")
    for texture in textures:
        if not _safe_child(model_parent, texture).is_file():
            raise ValueError(f"模型纹理不存在：{texture}")

    clean_base = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", source_root.name).strip("-")[:48] or "model"
    model_id = f"{clean_base}-{int(time.time() * 1000):x}"
    target_root = _live2d_models_dir() / model_id
    staging = _live2d_models_dir() / f".{model_id}.importing"
    expressions_changed = _register_unlisted_live2d_expressions(document, model_parent, files)
    preview = _live2d_preview_candidate(source_root, files)
    license_files = [
        str(item.relative_to(source_root)).replace("\\", "/")
        for item in files
        if re.search(r"(^|[\\/])(licen[sc]e|copying|notice)(\.|$|[-_])", str(item.relative_to(source_root)), re.I)
    ][:20]
    try:
        shutil.copytree(source_root, staging)
        if expressions_changed:
            staged_model = staging / source_model.relative_to(source_root)
            staged_model.write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        metadata = {
            "name": (display_name.strip() or source_root.name)[:100],
            "modelPath": str(source_model.relative_to(source_root)).replace("\\", "/"),
            "previewPath": str(preview.relative_to(source_root)).replace("\\", "/") if preview else "",
            "sourceLabel": str(source_root),
            "importedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "capabilities": _live2d_capabilities(document),
            "authorization": {
                "status": "files_found" if license_files else "unverified",
                "licenseFiles": license_files,
                "notice": "已发现授权文件，使用和分发前请核对。" if license_files else "未发现授权文件；只允许本机使用，禁止随安装包分发。",
                "distributionAllowed": False,
            },
        }
        (staging / "mio-model.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staging.replace(target_root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _write_live2d_runtime_selected(model_id)
    return next(model for model in available_live2d_models() if model["id"] == model_id)


def delete_live2d_model(model_id: str) -> bool:
    clean = str(model_id or "").strip()
    if clean == "hiyori":
        raise ValueError("内置 Live2D 模型不能删除。")
    target = (_live2d_models_dir() / clean).resolve()
    try:
        target.relative_to(_live2d_models_dir())
    except ValueError as exc:
        raise ValueError("模型编号无效。") from exc
    if not target.is_dir() or not (target / "mio-model.json").is_file():
        return False
    shutil.rmtree(target)
    config = load_config()
    if str(config.get("live2d_model_id") or "") == clean:
        save_config({"live2d_model_id": "hiyori"})
    _write_live2d_runtime_selected("")
    return True


def live2d_model_preview_path(model_id: str) -> Path | None:
    clean = str(model_id or "").strip()
    model = next((item for item in _custom_live2d_models() if item["id"] == clean), None)
    if model is None or not model.get("preview_path"):
        return None
    path = _safe_child(_live2d_models_dir() / clean, model["preview_path"])
    return path if path.is_file() else None


def save_live2d_model_preview_data_url(model_id: str, data_url: str) -> Path:
    clean = str(model_id or "").strip()
    model_root = (_live2d_models_dir() / clean).resolve()
    try:
        model_root.relative_to(_live2d_models_dir())
    except ValueError as exc:
        raise ValueError("模型编号无效。") from exc
    metadata_path = model_root / "mio-model.json"
    if not metadata_path.is_file():
        raise ValueError("只有本地导入的 Live2D 模型可以更换封面。")
    raw = _decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="Live2D 封面")
    target = model_root / "mio-preview.png"
    temporary = model_root / "mio-preview.tmp.png"
    try:
        with Image.open(BytesIO(raw)) as source:
            source.load()
            image = source.convert("RGBA")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            image.save(temporary, format="PNG", optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("这个文件不是可用的图片。") from exc
    temporary.replace(target)
    metadata = _read_json_object(metadata_path)
    metadata["previewPath"] = target.name
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target

LIVE2D_MOTION_SLOT_IDS = frozenset(
    {"idle", "touch", "think", "speak", "observe", "cheerful", "concerned", "alert", "attention", "shy"}
)
LIVE2D_EXPRESSION_SLOT_IDS = frozenset(
    {"neutral", "gentle", "cheerful", "concerned", "serious", "shy"}
)


def _normalize_live2d_motion_slots(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for raw_model_id, raw_slots in list(value.items())[:30]:
        model_id = str(raw_model_id or "").strip()[:100]
        if not model_id or not isinstance(raw_slots, dict):
            continue
        slots: dict[str, str] = {}
        for raw_slot, raw_group in raw_slots.items():
            slot = str(raw_slot or "").strip().lower()
            group = str(raw_group or "").strip()[:120]
            if slot in LIVE2D_MOTION_SLOT_IDS and group:
                slots[slot] = group
        if slots:
            normalized[model_id] = slots
    return normalized


def _normalize_live2d_expression_slots(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for raw_model_id, raw_slots in list(value.items())[:30]:
        model_id = str(raw_model_id or "").strip()[:100]
        if not model_id or not isinstance(raw_slots, dict):
            continue
        slots: dict[str, str] = {}
        for raw_slot, raw_expression in raw_slots.items():
            slot = str(raw_slot or "").strip().lower()
            expression = str(raw_expression or "").strip()[:120]
            if slot in LIVE2D_EXPRESSION_SLOT_IDS and expression:
                slots[slot] = expression
        if slots:
            normalized[model_id] = slots
    return normalized

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


def _legacy_voice_profile(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "默认角色音色",
        "engine": MIO_VOICE_ENGINE,
        "gpt_sovits_ref_audio": str(config.get("gpt_sovits_ref_audio") or "").strip(),
        "gpt_sovits_prompt_text": str(config.get("gpt_sovits_prompt_text") or "").strip()[:1000],
        "gpt_sovits_prompt_language": str(config.get("gpt_sovits_prompt_language") or "ja").strip().lower(),
        "gpt_sovits_text_language": str(config.get("gpt_sovits_text_language") or "auto").strip().lower(),
        "gpt_sovits_translate_to_japanese": bool(config.get("gpt_sovits_translate_to_japanese", False)),
        "gpt_sovits_gpt_weights": _voice_weight_path(config.get("gpt_sovits_gpt_weights"), "gpt"),
        "gpt_sovits_sovits_weights": _voice_weight_path(config.get("gpt_sovits_sovits_weights"), "sovits"),
        "use_emotion_references": True,
        "so_vits_svc_model_path": "",
        "so_vits_svc_config_path": "",
        "so_vits_svc_speaker": "",
        "so_vits_svc_pitch": 0,
        "so_vits_svc_auto_predict_f0": True,
        "so_vits_svc_noise_scale": 0.4,
        "so_vits_svc_base_profile_id": "",
        "source_package_name": "",
        "source_license": "",
    }


def _uses_genie_runtime(config: dict[str, Any] | None = None) -> bool:
    selected = config or load_config()
    return str(selected.get("local_voice_runtime") or GENIE_VOICE_RUNTIME).strip().lower() == GENIE_VOICE_RUNTIME


def _normalize_voice_profiles(value: object, legacy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    languages = {"zh", "ja", "en", "yue", "ko", "all_zh", "all_ja", "all_en", "auto"}
    source = value if isinstance(value, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_profile_id, candidate in list(source.items())[:20]:
        profile_id = str(raw_profile_id or "").strip()[:80]
        if not re.fullmatch(r"[A-Za-z0-9._-]+", profile_id) or not isinstance(candidate, dict):
            continue
        prompt_language = str(candidate.get("gpt_sovits_prompt_language") or "ja").strip().lower()
        text_language = str(candidate.get("gpt_sovits_text_language") or "auto").strip().lower()
        engine = str(candidate.get("engine") or MIO_VOICE_ENGINE).strip().lower()
        if engine not in {MIO_VOICE_ENGINE, SO_VITS_SVC_ENGINE}:
            engine = MIO_VOICE_ENGINE
        normalized[profile_id] = {
            "name": str(candidate.get("name") or profile_id).strip()[:80] or profile_id,
            "engine": engine,
            "gpt_sovits_ref_audio": str(candidate.get("gpt_sovits_ref_audio") or "").strip(),
            "gpt_sovits_prompt_text": str(candidate.get("gpt_sovits_prompt_text") or "").strip()[:1000],
            "gpt_sovits_prompt_language": prompt_language if prompt_language in languages else "ja",
            "gpt_sovits_text_language": text_language if text_language in languages else "auto",
            "gpt_sovits_translate_to_japanese": bool(candidate.get("gpt_sovits_translate_to_japanese", False)),
            "gpt_sovits_gpt_weights": _voice_weight_path(candidate.get("gpt_sovits_gpt_weights"), "gpt"),
            "gpt_sovits_sovits_weights": _voice_weight_path(candidate.get("gpt_sovits_sovits_weights"), "sovits"),
            "use_emotion_references": bool(candidate.get("use_emotion_references", True)),
            "so_vits_svc_model_path": str(candidate.get("so_vits_svc_model_path") or "").strip(),
            "so_vits_svc_config_path": str(candidate.get("so_vits_svc_config_path") or "").strip(),
            "so_vits_svc_speaker": str(candidate.get("so_vits_svc_speaker") or "").strip()[:80],
            "so_vits_svc_pitch": max(-24, min(24, int(candidate.get("so_vits_svc_pitch") or 0))),
            "so_vits_svc_auto_predict_f0": bool(candidate.get("so_vits_svc_auto_predict_f0", True)),
            "so_vits_svc_noise_scale": max(0.0, min(1.0, float(candidate.get("so_vits_svc_noise_scale") or 0.4))),
            "so_vits_svc_base_profile_id": str(candidate.get("so_vits_svc_base_profile_id") or "").strip()[:80],
            "source_package_name": str(candidate.get("source_package_name") or "").strip()[:260],
            "source_license": str(candidate.get("source_license") or "").strip()[:200],
        }
    if not normalized:
        fallback = dict(legacy)
        fallback["name"] = str(fallback.get("name") or "默认角色音色")
        normalized[DEFAULT_VOICE_PROFILE_ID] = fallback
    return normalized


def resolve_voice_profile(
    model_id: str = "",
    config: dict[str, Any] | None = None,
    speech_language: str = "",
) -> tuple[str, dict[str, Any]]:
    base = config or load_config()
    profiles = base.get("voice_profiles") if isinstance(base.get("voice_profiles"), dict) else {}
    default_profile_id = str(base.get("default_voice_profile_id") or DEFAULT_VOICE_PROFILE_ID)
    # 音色属于角色，不再随聊天模型变化；所有入口统一使用当前默认音色。
    profile_id = default_profile_id
    if profile_id not in profiles:
        profile_id = next(iter(profiles), DEFAULT_VOICE_PROFILE_ID)
    profile = profiles.get(profile_id) or _legacy_voice_profile(base)
    selected = dict(base)
    selected.update(profile)
    selected["voice_profile_id"] = profile_id
    selected["voice_profile_name"] = str(profile.get("name") or profile_id)
    language = str(speech_language or base.get("pet_speech_language") or "zh").strip().lower()
    if language == "zh":
        selected["gpt_sovits_text_language"] = "zh"
        selected["gpt_sovits_translate_to_japanese"] = False
        selected["gpt_sovits_translate_to_chinese"] = True
    elif language == "ja":
        selected["gpt_sovits_text_language"] = "ja"
        selected["gpt_sovits_translate_to_japanese"] = True
        selected["gpt_sovits_translate_to_chinese"] = False
    return profile_id, selected


def _resolve_base_voice_profile(config: dict[str, Any], selected_profile_id: str) -> tuple[str, dict[str, Any]]:
    profiles = config.get("voice_profiles") if isinstance(config.get("voice_profiles"), dict) else {}
    selected = profiles.get(selected_profile_id) if isinstance(profiles.get(selected_profile_id), dict) else {}
    requested_id = str(selected.get("so_vits_svc_base_profile_id") or "").strip()
    candidates = [requested_id]
    candidates.extend(
        profile_id
        for profile_id, profile in profiles.items()
        if profile_id != selected_profile_id and isinstance(profile, dict) and profile.get("engine") == MIO_VOICE_ENGINE
    )
    for profile_id in candidates:
        profile = profiles.get(profile_id)
        if not profile_id or not isinstance(profile, dict) or profile.get("engine") != MIO_VOICE_ENGINE:
            continue
        base = dict(config)
        base.update(profile)
        for key in ("gpt_sovits_text_language", "gpt_sovits_translate_to_japanese", "gpt_sovits_translate_to_chinese"):
            if key in config:
                base[key] = config[key]
        base["voice_profile_id"] = profile_id
        base["voice_profile_name"] = str(profile.get("name") or profile_id)
        return profile_id, base
    raise ValueError("这个第三方音色缺少基础 TTS 音色，请先保留或导入一个 GPT-SoVITS 音色。")


def _apply_voice_profile_config(data: dict[str, Any]) -> None:
    profiles = _normalize_voice_profiles(data.get("voice_profiles"), _legacy_voice_profile(data))
    requested_default = str(data.get("default_voice_profile_id") or DEFAULT_VOICE_PROFILE_ID).strip()[:80]
    default_profile_id = requested_default if requested_default in profiles else next(iter(profiles))
    data["default_voice_profile_id"] = default_profile_id
    data["voice_profiles"] = profiles
    data.pop("model_voice_bindings", None)
    default_profile = profiles[default_profile_id]
    for key in VOICE_PROFILE_FIELDS:
        data[key] = default_profile.get(key, "")


def load_config() -> dict[str, Any]:
    data = dict(DEFAULT_CONFIG)
    try:
        saved = json.loads(settings.companion_config_path.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            data.update(_migrate_config(saved))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    data["voice_enabled"] = bool(data.get("voice_enabled", True))
    data["voice_startup_enabled"] = bool(data.get("voice_startup_enabled", False))
    data["voice_idle_timeout_seconds"] = max(
        0,
        min(1800, int(data.get("voice_idle_timeout_seconds", 180))),
    )
    voice_engine = str(data.get("voice_engine") or MIO_VOICE_ENGINE).strip().lower()
    data["voice_engine"] = voice_engine if voice_engine in {"gpt_sovits", "cloud"} else MIO_VOICE_ENGINE
    local_runtime = str(data.get("local_voice_runtime") or GENIE_VOICE_RUNTIME).strip().lower()
    data["local_voice_runtime"] = local_runtime if local_runtime in {GENIE_VOICE_RUNTIME, LEGACY_GPT_SOVITS_RUNTIME} else GENIE_VOICE_RUNTIME
    data["cloud_tts_api_key"] = str(data.get("cloud_tts_api_key") or "").strip()
    data["cloud_tts_app_id"] = str(data.get("cloud_tts_app_id") or "").strip()[:200]
    cloud_speaker = str(data.get("cloud_tts_speaker") or DEFAULT_CONFIG["cloud_tts_speaker"]).strip()
    data["cloud_tts_speaker"] = cloud_speaker[:200] or DEFAULT_CONFIG["cloud_tts_speaker"]
    data["cloud_tts_speech_rate"] = max(
        -50,
        min(100, int(data.get("cloud_tts_speech_rate") or 0)),
    )
    data["chat_model_id"] = str(data.get("chat_model_id") or "auto").strip()[:200] or "auto"
    data["chat_reasoning_level"] = str(data.get("chat_reasoning_level") or "auto").strip()[:50] or "auto"
    data["pet_chat_model_id"] = str(data.get("pet_chat_model_id") or "auto").strip()[:200] or "auto"
    data["pet_chat_reasoning_level"] = str(
        data.get("pet_chat_reasoning_level") or "auto"
    ).strip()[:50] or "auto"
    call_asr_engine = str(data.get("pet_call_asr_engine") or "auto").strip().lower()
    data["pet_call_asr_engine"] = (
        call_asr_engine
        if call_asr_engine in {"auto", "whisper", "sensevoice", "paraformer"}
        else "auto"
    )
    call_language = str(data.get("pet_call_input_language") or "zh").strip().lower()
    data["pet_call_input_language"] = "ja" if call_language == "ja" else "zh"
    data["speech_translation_model_id"] = (
        str(data.get("speech_translation_model_id") or "deepseek-v4-flash").strip()[:200]
        or "deepseek-v4-flash"
    )
    data["pet_call_silence_ms"] = max(350, min(1800, int(data.get("pet_call_silence_ms", 650))))
    data["pet_call_voice_threshold"] = max(0.004, min(0.12, float(data.get("pet_call_voice_threshold", 0.018))))
    data["pet_call_min_speech_ms"] = max(150, min(1500, int(data.get("pet_call_min_speech_ms", 280))))
    data["pet_call_max_turn_seconds"] = max(5, min(45, int(data.get("pet_call_max_turn_seconds", 18))))
    data["startup_greeting_enabled"] = bool(data.get("startup_greeting_enabled", True))
    data["qq_startup_enabled"] = bool(data.get("qq_startup_enabled", False))
    data["speak_proactive"] = bool(data.get("speak_proactive", False))
    data["speak_screen_observations"] = bool(data.get("speak_screen_observations", True))
    data["speak_game_observations"] = bool(data.get("speak_game_observations", True))
    data["screen_ai_enabled"] = bool(data.get("screen_ai_enabled", True))
    data["screen_audio_enabled"] = bool(data.get("screen_audio_enabled", True))
    screen_audio_model = str(data.get("screen_audio_model") or "base").strip().lower()
    data["screen_audio_model"] = screen_audio_model if screen_audio_model in {"tiny", "base", "small"} else "base"
    screen_audio_language = str(data.get("screen_audio_language") or "auto").strip().lower()
    data["screen_audio_language"] = screen_audio_language if screen_audio_language in {"auto", "zh", "ja", "en"} else "auto"
    data["screen_audio_chunk_seconds"] = max(4, min(15, int(data.get("screen_audio_chunk_seconds", 5))))
    screen_vision_route = str(data.get("screen_vision_route") or "local").strip().lower()
    data["screen_vision_route"] = screen_vision_route if screen_vision_route in {"local", "cloud"} else "local"
    data["qq_voice_mode"] = str(data.get("qq_voice_mode") or "adaptive")
    data["gpt_sovits_url"] = str(data.get("gpt_sovits_url") or DEFAULT_CONFIG["gpt_sovits_url"]).rstrip("/")
    data["gpt_sovits_ref_audio"] = str(data.get("gpt_sovits_ref_audio") or "")
    data["gpt_sovits_prompt_text"] = str(data.get("gpt_sovits_prompt_text") or "")[:1000]
    data["gpt_sovits_prompt_language"] = str(data.get("gpt_sovits_prompt_language") or "ja")
    data["gpt_sovits_text_language"] = str(data.get("gpt_sovits_text_language") or "auto")
    data["gpt_sovits_translate_to_japanese"] = bool(
        data.get("gpt_sovits_translate_to_japanese", False)
    )
    data["gpt_sovits_gpt_weights"] = str(data.get("gpt_sovits_gpt_weights") or "")
    data["gpt_sovits_sovits_weights"] = str(data.get("gpt_sovits_sovits_weights") or "")
    _apply_voice_profile_config(data)
    data["voice_volume"] = max(0, min(100, int(data.get("voice_volume", 85))))
    data["voice_streaming_enabled"] = bool(data.get("voice_streaming_enabled", True))
    pet_speech_language = str(data.get("pet_speech_language") or "zh").strip().lower()
    data["pet_speech_language"] = pet_speech_language if pet_speech_language in {"zh", "ja"} else "zh"
    data["screen_direct_voice_enabled"] = bool(data.get("screen_direct_voice_enabled", True))
    data["screen_vision_model_id"] = (
        str(data.get("screen_vision_model_id") or "auto-fast").strip()[:200] or "auto-fast"
    )
    data["bubble_seconds"] = max(3, min(30, int(data.get("bubble_seconds", 9))))
    data["pet_size_percent"] = max(80, min(240, int(data.get("pet_size_percent", 150))))
    # 公开版只保留 Live2D 桌宠形象：历史数据里的静态立绘模式统一迁移。
    pet_renderer = str(data.get("pet_renderer") or "live2d").strip().lower()
    data["pet_renderer"] = "live2d" if pet_renderer in {"classic", "live2d"} else "live2d"
    live2d_model_id = str(data.get("live2d_model_id") or "hiyori").strip().lower()
    available_model_ids = _live2d_model_ids()
    data["live2d_model_id"] = live2d_model_id if live2d_model_id in available_model_ids else "hiyori"
    data["live2d_scale"] = max(0.65, min(1.55, float(data.get("live2d_scale", 1.0))))
    data["live2d_vertical_offset"] = max(
        -0.35,
        min(0.35, float(data.get("live2d_vertical_offset", 0.0))),
    )
    data["live2d_follow_cursor"] = bool(data.get("live2d_follow_cursor", True))
    data["live2d_idle_motion"] = bool(data.get("live2d_idle_motion", True))
    data["live2d_click_motion"] = bool(data.get("live2d_click_motion", True))
    data["live2d_smart_passthrough"] = bool(data.get("live2d_smart_passthrough", True))
    data["live2d_click_through_locked"] = bool(data.get("live2d_click_through_locked", False))
    data["live2d_speech_bubble_enabled"] = bool(data.get("live2d_speech_bubble_enabled", True))
    data["live2d_keep_visible"] = bool(data.get("live2d_keep_visible", False))
    data["live2d_always_on_top"] = bool(data.get("live2d_always_on_top", True))
    data["live2d_disable_gpu"] = bool(data.get("live2d_disable_gpu", False))
    data["live2d_motion_slots"] = _normalize_live2d_motion_slots(
        data.get("live2d_motion_slots")
    )
    data["live2d_expression_slots"] = _normalize_live2d_expression_slots(
        data.get("live2d_expression_slots")
    )
    data["screen_change_threshold"] = max(1.0, min(50.0, float(data.get("screen_change_threshold", 4.0))))
    data["screen_analysis_interval_seconds"] = max(
        5,
        min(600, int(data.get("screen_analysis_interval_seconds", 5))),
    )
    data["screen_request_timeout_seconds"] = max(
        5,
        min(60, int(data.get("screen_request_timeout_seconds", 25))),
    )
    data["screen_voice_cooldown_seconds"] = max(
        5,
        min(600, int(data.get("screen_voice_cooldown_seconds", 5))),
    )
    data["screen_minimum_importance"] = max(
        0.1,
        min(1.0, float(data.get("screen_minimum_importance", 0.62))),
    )
    data["screen_daily_cost_limit_yuan"] = max(
        0.1,
        min(1000.0, float(data.get("screen_daily_cost_limit_yuan", 5.0))),
    )
    return data


def save_config(changes: dict[str, Any]) -> dict[str, Any]:
    data = load_config()
    previous_key = str(data.get("cloud_tts_api_key") or "")
    for key in DEFAULT_CONFIG:
        if key in changes:
            data[key] = changes[key]
    if "cloud_tts_api_key" in changes:
        key_value = str(changes.get("cloud_tts_api_key") or "").strip()
        if key_value == "__clear__":
            data["cloud_tts_api_key"] = ""
        elif key_value:
            from .secret_store import protect_secret

            data["cloud_tts_api_key"] = protect_secret(key_value)
        else:
            # 留空表示保持原值不变（前端不回显 Key，无法填"原样"）
            data["cloud_tts_api_key"] = previous_key
    if any(key in changes for key in VOICE_PROFILE_FIELDS):
        profiles = dict(data.get("voice_profiles") or {})
        default_id = str(data.get("default_voice_profile_id") or DEFAULT_VOICE_PROFILE_ID)
        profile = dict(profiles.get(default_id) or _legacy_voice_profile(data))
        for key in VOICE_PROFILE_FIELDS:
            if key in changes:
                profile[key] = changes[key]
        profiles[default_id] = profile
        data["voice_profiles"] = profiles
    data = {key: value for key, value in load_normalized_config(data).items() if key in DEFAULT_CONFIG}
    settings.companion_dir.mkdir(parents=True, exist_ok=True)
    temporary = settings.companion_config_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(settings.companion_config_path)
    from . import pet_event_service

    pet_event_service.publish("settings_changed", data)
    return data


def load_normalized_config(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_CONFIG)
    normalized.update(_migrate_config(data))
    normalized["voice_enabled"] = bool(normalized.get("voice_enabled", True))
    normalized["voice_startup_enabled"] = bool(normalized.get("voice_startup_enabled", False))
    normalized["voice_idle_timeout_seconds"] = max(
        0,
        min(1800, int(normalized.get("voice_idle_timeout_seconds", 180))),
    )
    voice_engine = str(normalized.get("voice_engine") or MIO_VOICE_ENGINE).strip().lower()
    normalized["voice_engine"] = voice_engine if voice_engine in {"gpt_sovits", "cloud"} else MIO_VOICE_ENGINE
    local_runtime = str(normalized.get("local_voice_runtime") or GENIE_VOICE_RUNTIME).strip().lower()
    normalized["local_voice_runtime"] = local_runtime if local_runtime in {GENIE_VOICE_RUNTIME, LEGACY_GPT_SOVITS_RUNTIME} else GENIE_VOICE_RUNTIME
    normalized["cloud_tts_api_key"] = str(normalized.get("cloud_tts_api_key") or "").strip()
    normalized["cloud_tts_app_id"] = str(normalized.get("cloud_tts_app_id") or "").strip()[:200]
    cloud_speaker = str(normalized.get("cloud_tts_speaker") or DEFAULT_CONFIG["cloud_tts_speaker"]).strip()
    normalized["cloud_tts_speaker"] = cloud_speaker[:200] or DEFAULT_CONFIG["cloud_tts_speaker"]
    normalized["cloud_tts_speech_rate"] = max(
        -50,
        min(100, int(normalized.get("cloud_tts_speech_rate") or 0)),
    )
    normalized["chat_model_id"] = str(normalized.get("chat_model_id") or "auto").strip()[:200] or "auto"
    normalized["chat_reasoning_level"] = str(
        normalized.get("chat_reasoning_level") or "auto"
    ).strip()[:50] or "auto"
    normalized["pet_chat_model_id"] = str(
        normalized.get("pet_chat_model_id") or "auto"
    ).strip()[:200] or "auto"
    normalized["pet_chat_reasoning_level"] = str(
        normalized.get("pet_chat_reasoning_level") or "auto"
    ).strip()[:50] or "auto"
    call_asr_engine = str(normalized.get("pet_call_asr_engine") or "auto").strip().lower()
    normalized["pet_call_asr_engine"] = (
        call_asr_engine
        if call_asr_engine in {"auto", "whisper", "sensevoice", "paraformer"}
        else "auto"
    )
    call_language = str(normalized.get("pet_call_input_language") or "zh").strip().lower()
    normalized["pet_call_input_language"] = "ja" if call_language == "ja" else "zh"
    normalized["speech_translation_model_id"] = (
        str(normalized.get("speech_translation_model_id") or "deepseek-v4-flash").strip()[:200]
        or "deepseek-v4-flash"
    )
    normalized["pet_call_silence_ms"] = max(350, min(1800, int(normalized.get("pet_call_silence_ms", 650))))
    normalized["pet_call_voice_threshold"] = max(0.004, min(0.12, float(normalized.get("pet_call_voice_threshold", 0.018))))
    normalized["pet_call_min_speech_ms"] = max(150, min(1500, int(normalized.get("pet_call_min_speech_ms", 280))))
    normalized["pet_call_max_turn_seconds"] = max(5, min(45, int(normalized.get("pet_call_max_turn_seconds", 18))))
    normalized["startup_greeting_enabled"] = bool(normalized.get("startup_greeting_enabled", True))
    normalized["qq_startup_enabled"] = bool(normalized.get("qq_startup_enabled", False))
    normalized["speak_proactive"] = bool(normalized.get("speak_proactive", False))
    normalized["speak_screen_observations"] = bool(normalized.get("speak_screen_observations", True))
    normalized["speak_game_observations"] = bool(normalized.get("speak_game_observations", True))
    normalized["screen_ai_enabled"] = bool(normalized.get("screen_ai_enabled", True))
    normalized["screen_audio_enabled"] = bool(normalized.get("screen_audio_enabled", True))
    screen_audio_model = str(normalized.get("screen_audio_model") or "base").strip().lower()
    normalized["screen_audio_model"] = screen_audio_model if screen_audio_model in {"tiny", "base", "small"} else "base"
    screen_audio_language = str(normalized.get("screen_audio_language") or "auto").strip().lower()
    normalized["screen_audio_language"] = screen_audio_language if screen_audio_language in {"auto", "zh", "ja", "en"} else "auto"
    normalized["screen_audio_chunk_seconds"] = max(
        4,
        min(15, int(normalized.get("screen_audio_chunk_seconds", 5))),
    )
    screen_vision_route = str(normalized.get("screen_vision_route") or "local").strip().lower()
    normalized["screen_vision_route"] = (
        screen_vision_route if screen_vision_route in {"local", "cloud"} else "local"
    )
    voice_mode = str(normalized.get("qq_voice_mode") or "adaptive").strip().lower()
    normalized["qq_voice_mode"] = voice_mode if voice_mode in {"explicit", "adaptive", "always"} else "adaptive"
    normalized["gpt_sovits_url"] = str(
        normalized.get("gpt_sovits_url") or DEFAULT_CONFIG["gpt_sovits_url"]
    ).strip().rstrip("/")
    normalized["gpt_sovits_ref_audio"] = str(normalized.get("gpt_sovits_ref_audio") or "").strip()
    normalized["gpt_sovits_prompt_text"] = str(normalized.get("gpt_sovits_prompt_text") or "").strip()[:1000]
    languages = {"zh", "ja", "en", "yue", "ko", "all_zh", "all_ja", "all_en", "auto"}
    prompt_language = str(normalized.get("gpt_sovits_prompt_language") or "ja").strip().lower()
    text_language = str(normalized.get("gpt_sovits_text_language") or "auto").strip().lower()
    normalized["gpt_sovits_prompt_language"] = prompt_language if prompt_language in languages else "ja"
    normalized["gpt_sovits_text_language"] = text_language if text_language in languages else "auto"
    normalized["gpt_sovits_translate_to_japanese"] = bool(
        normalized.get("gpt_sovits_translate_to_japanese", False)
    )
    normalized["gpt_sovits_gpt_weights"] = str(normalized.get("gpt_sovits_gpt_weights") or "").strip()
    normalized["gpt_sovits_sovits_weights"] = str(normalized.get("gpt_sovits_sovits_weights") or "").strip()
    _apply_voice_profile_config(normalized)
    normalized["voice_volume"] = max(0, min(100, int(normalized.get("voice_volume", 85))))
    normalized["voice_streaming_enabled"] = bool(normalized.get("voice_streaming_enabled", True))
    pet_speech_language = str(normalized.get("pet_speech_language") or "zh").strip().lower()
    normalized["pet_speech_language"] = pet_speech_language if pet_speech_language in {"zh", "ja"} else "zh"
    normalized["screen_direct_voice_enabled"] = bool(normalized.get("screen_direct_voice_enabled", True))
    normalized["screen_vision_model_id"] = (
        str(normalized.get("screen_vision_model_id") or "auto-fast").strip()[:200] or "auto-fast"
    )
    normalized["bubble_seconds"] = max(3, min(30, int(normalized.get("bubble_seconds", 9))))
    normalized["pet_size_percent"] = max(80, min(240, int(normalized.get("pet_size_percent", 150))))
    pet_renderer = str(normalized.get("pet_renderer") or "live2d").strip().lower()
    normalized["pet_renderer"] = "live2d" if pet_renderer in {"classic", "live2d"} else "live2d"
    live2d_model_id = str(normalized.get("live2d_model_id") or "hiyori").strip().lower()
    available_model_ids = _live2d_model_ids()
    normalized["live2d_model_id"] = live2d_model_id if live2d_model_id in available_model_ids else "hiyori"
    normalized["live2d_scale"] = max(
        0.65,
        min(1.55, float(normalized.get("live2d_scale", 1.0))),
    )
    normalized["live2d_vertical_offset"] = max(
        -0.35,
        min(0.35, float(normalized.get("live2d_vertical_offset", 0.0))),
    )
    normalized["live2d_follow_cursor"] = bool(normalized.get("live2d_follow_cursor", True))
    normalized["live2d_idle_motion"] = bool(normalized.get("live2d_idle_motion", True))
    normalized["live2d_click_motion"] = bool(normalized.get("live2d_click_motion", True))
    normalized["live2d_smart_passthrough"] = bool(
        normalized.get("live2d_smart_passthrough", True)
    )
    normalized["live2d_click_through_locked"] = bool(
        normalized.get("live2d_click_through_locked", False)
    )
    normalized["live2d_speech_bubble_enabled"] = bool(
        normalized.get("live2d_speech_bubble_enabled", True)
    )
    normalized["live2d_keep_visible"] = bool(normalized.get("live2d_keep_visible", False))
    normalized["live2d_always_on_top"] = bool(normalized.get("live2d_always_on_top", True))
    normalized["live2d_disable_gpu"] = bool(normalized.get("live2d_disable_gpu", False))
    normalized["live2d_motion_slots"] = _normalize_live2d_motion_slots(
        normalized.get("live2d_motion_slots")
    )
    normalized["live2d_expression_slots"] = _normalize_live2d_expression_slots(
        normalized.get("live2d_expression_slots")
    )
    normalized["screen_change_threshold"] = max(
        1.0,
        min(50.0, float(normalized.get("screen_change_threshold", 4.0))),
    )
    normalized["screen_analysis_interval_seconds"] = max(
        5,
        min(600, int(normalized.get("screen_analysis_interval_seconds", 5))),
    )
    normalized["screen_request_timeout_seconds"] = max(
        5,
        min(60, int(normalized.get("screen_request_timeout_seconds", 25))),
    )
    normalized["screen_voice_cooldown_seconds"] = max(
        5,
        min(600, int(normalized.get("screen_voice_cooldown_seconds", 5))),
    )
    normalized["screen_minimum_importance"] = max(
        0.1,
        min(1.0, float(normalized.get("screen_minimum_importance", 0.62))),
    )
    normalized["screen_daily_cost_limit_yuan"] = max(
        0.1,
        min(1000.0, float(normalized.get("screen_daily_cost_limit_yuan", 5.0))),
    )
    normalized["position_x"] = max(-10000, min(10000, int(normalized.get("position_x", 80))))
    normalized["position_y"] = max(-10000, min(10000, int(normalized.get("position_y", 420))))
    return normalized


def save_pet_position(x: int, y: int) -> dict[str, Any]:
    return save_config({"position_x": x, "position_y": y})


def save_pet_size(percent: int) -> dict[str, Any]:
    return save_config({"pet_size_percent": percent})


def default_avatar_path() -> Path | None:
    candidates = [
        settings.companion_avatar_path,
        settings.agent_frontend_dir / "mio-avatar.png",
        settings.workspace_root / "澪Agent应用" / "public" / "mio-avatar.png",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def profile_avatar_path() -> Path | None:
    candidates = [
        settings.mio_avatar_path,
        settings.agent_frontend_dir / "mio-avatar.png",
        settings.workspace_root / "澪Agent应用" / "public" / "mio-avatar.png",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def pet_sprite_path(state: str) -> Path | None:
    filename = PET_SPRITE_FILES.get(str(state).strip().lower())
    if filename is None:
        return None
    path = settings.companion_sprite_dir / filename
    return path if path.is_file() else None


def pet_sprite_manifest() -> dict[str, Any]:
    states: list[str] = []
    latest_mtime_ns = 0
    for state in PET_SPRITE_FILES:
        path = pet_sprite_path(state)
        if path is None:
            continue
        states.append(state)
        try:
            latest_mtime_ns = max(latest_mtime_ns, path.stat().st_mtime_ns)
        except OSError:
            pass
    return {
        "states": states,
        "ready": len(states) == len(PET_SPRITE_FILES),
        "expected_count": len(PET_SPRITE_FILES),
        "version": str(latest_mtime_ns) if latest_mtime_ns else "",
    }


def _decode_image_data_url(data_url: str, *, max_bytes: int, label: str) -> bytes:
    if "," not in data_url:
        raise ValueError(f"{label}数据格式不正确。")
    header, encoded = data_url.split(",", 1)
    if not header.startswith("data:image/") or ";base64" not in header:
        raise ValueError(f"请选择图片文件作为{label}。")
    if len(encoded) > (max_bytes * 4 // 3) + 8:
        size_mb = max_bytes // (1024 * 1024)
        raise ValueError(f"{label}图片不能超过 {size_mb}MB。")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError(f"{label}图片无法读取。") from exc
    if not raw:
        raise ValueError(f"{label}图片不能为空。")
    if len(raw) > max_bytes:
        size_mb = max_bytes // (1024 * 1024)
        raise ValueError(f"{label}图片不能超过 {size_mb}MB。")
    return raw


def save_avatar_data_url(data_url: str) -> Path:
    raw = _decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="桌宠头像")
    settings.companion_dir.mkdir(parents=True, exist_ok=True)
    temporary = settings.companion_avatar_path.with_suffix(".tmp.png")
    try:
        from io import BytesIO

        with Image.open(BytesIO(raw)) as image:
            image.convert("RGBA").save(temporary, format="PNG", optimize=True)
    except (OSError, ValueError) as exc:
        raise ValueError("这个文件不是可用的图片。") from exc
    temporary.replace(settings.companion_avatar_path)
    return settings.companion_avatar_path


def save_profile_avatar_data_url(data_url: str) -> Path:
    raw = _decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="Mio 头像")
    settings.mio_avatar_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.mio_avatar_path.with_suffix(".tmp.png")
    try:
        with Image.open(BytesIO(raw)) as image:
            image.convert("RGBA").save(temporary, format="PNG", optimize=True)
    except (OSError, ValueError) as exc:
        raise ValueError("这个文件不是可用的图片。") from exc
    temporary.replace(settings.mio_avatar_path)
    return settings.mio_avatar_path


def save_user_avatar_data_url(data_url: str) -> Path:
    raw = _decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="用户头像")
    settings.user_avatar_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.user_avatar_path.with_suffix(".tmp.png")
    try:
        with Image.open(BytesIO(raw)) as source:
            source.load()
            image = source.convert("RGBA")
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            image.save(temporary, format="PNG", optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("这个文件不是可用的图片。") from exc
    temporary.replace(settings.user_avatar_path)
    return settings.user_avatar_path


def save_chat_background_data_url(data_url: str) -> Path:
    raw = _decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="对话背景")
    settings.chat_background_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.chat_background_path.with_suffix(".tmp.jpg")
    try:
        with Image.open(BytesIO(raw)) as source:
            source.load()
            image = source.convert("RGB")
            image.thumbnail((3840, 2160), Image.Resampling.LANCZOS)
            image.save(temporary, format="JPEG", quality=90, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("这个文件不是可用的图片。") from exc
    temporary.replace(settings.chat_background_path)
    return settings.chat_background_path


def save_sprite_sheet_data_url(data_url: str) -> list[Path]:
    raw = _decode_image_data_url(data_url, max_bytes=MAX_SPRITE_SHEET_BYTES, label="桌宠动作表")
    try:
        with Image.open(BytesIO(raw)) as source:
            columns, rows = (3, 2) if source.width >= source.height else (2, 3)
            if source.width < columns * 32 or source.height < rows * 32:
                raise ValueError("动作表尺寸太小，无法按 3x2 或 2x3 网格切分。")
            if source.width * source.height > MAX_SPRITE_SHEET_PIXELS:
                raise ValueError("动作表总像素过大，请先缩小图片。")
            source.load()
            image = source.convert("RGBA")
    except ValueError:
        raise
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("这个文件不是可用的动作表图片。") from exc

    columns, rows = (3, 2) if image.width >= image.height else (2, 3)
    cell_width = image.width // columns
    cell_height = image.height // rows
    settings.companion_sprite_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = settings.companion_sprite_dir / f".上传-{time.time_ns()}"
    staging_dir.mkdir()
    staged: list[tuple[Path, Path]] = []
    try:
        for index, filename in enumerate(PET_SPRITE_FILES.values()):
            column = index % columns
            row = index // columns
            left = column * cell_width
            top = row * cell_height
            right = image.width if column == columns - 1 else (column + 1) * cell_width
            bottom = image.height if row == rows - 1 else (row + 1) * cell_height
            temporary = staging_dir / filename
            image.crop((left, top, right, bottom)).save(temporary, format="PNG", optimize=True)
            staged.append((temporary, settings.companion_sprite_dir / filename))
        for temporary, target in staged:
            temporary.replace(target)
    except (OSError, ValueError) as exc:
        raise ValueError("动作表切分保存失败。") from exc
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
        try:
            staging_dir.rmdir()
        except OSError:
            pass
    return [target for _, target in staged]


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


def _speech_latin_pronunciation(match: re.Match[str]) -> str:
    word = match.group(0).lower()
    known = SPEECH_TERM_PRONUNCIATIONS.get(word)
    if known:
        return known
    return "".join(SPEECH_LETTER_PRONUNCIATIONS[letter] for letter in word)


def clean_speech_text(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""
    lines: list[str] = []
    for raw_line in raw.split("\n"):
        clean = re.sub(r"[^\S\n]+", " ", raw_line).strip()
        if not clean or SPEECH_META_RE.fullmatch(clean):
            continue
        clean = SPEECH_STAGE_DIRECTION_RE.sub("", clean)
        if SPEECH_STAGE_DIRECTION_LINE_RE.fullmatch(clean):
            continue
        previous = None
        while previous != clean:
            previous = clean
            clean = SPEECH_PREFIX_RE.sub("", clean).strip()
        clean = clean.strip(" \t\r\n“”\"‘’'（）()")
        clean = clean.replace("——", "，").replace("—", "，").replace("……", "…")
        clean = re.sub(r"[，,]{2,}", "，", clean)
        clean = SPEECH_LATIN_RE.sub(_speech_latin_pronunciation, clean)
        if SPEECH_CONTENT_RE.search(clean):
            lines.append(clean)
    return "\n".join(lines)


def speech_text_language(text: str, configured_language: str = "auto") -> str:
    configured = str(configured_language or "auto").strip().lower()
    if configured != "auto":
        return configured
    kana_count = len(SPEECH_JAPANESE_RE.findall(text))
    han_count = len(SPEECH_HAN_RE.findall(text))
    if not kana_count and han_count:
        return "zh"
    if not kana_count:
        return "auto"
    chinese_markers = len(re.findall(r"[这那的了我你请说听给今晚今天明天可以现在]", text))
    if han_count and chinese_markers >= 2:
        return "auto"
    return "ja"


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


def _naturalize_short_speech_text(text: str, language: str) -> str:
    """Expand isolated interjections before TTS so Genie cannot echo a reference clip."""
    raw = str(text or "").strip()
    if language in {"zh", "all_zh"}:
        replacements = {
            "好": "好的",
            "好啊": "好啊，我知道了",
            "好的": "好的，我明白了",
            "好的呀": "好的呀，我知道了",
            "好呀": "好呀，我知道了",
            "好吧": "好吧，我知道了",
            "好嘛": "好嘛，我知道了",
            "嗯": "嗯嗯",
            "嗯好": "嗯嗯，好的",
            "嗯嗯": "嗯嗯，我在听",
            "哦": "哦哦",
            "啊": "啊，我明白了",
            "行": "可以",
            "行啊": "可以啊，我知道了",
            "可以": "可以，我知道了",
            "可以啊": "可以啊，我知道了",
            "知道了": "知道了，我会记住的",
        }
    elif language in {"ja", "all_ja"}:
        replacements = {
            "はい": "うん、わかったよ",
            "うん": "うんうん、わかったよ",
            "うんうん": "うんうん、わかったよ",
            "そう": "そうだね、わかったよ",
            "そうだね": "そうだね、わかったよ",
            "いい": "いいよ、わかったよ",
            "いいよ": "いいよ、わかったよ",
            "あ": "あ、そうなんだ",
            "わかった": "うん、わかったよ",
            "了解": "うん、わかったよ",
            "大丈夫": "うん、大丈夫だよ",
        }
    else:
        return raw
    choices = "|".join(re.escape(item) for item in sorted(replacements, key=len, reverse=True))
    match = re.fullmatch(rf"({choices})([。.!！…~～]*)", raw)
    if match is None:
        return raw
    return replacements[match.group(1)] + match.group(2)


def _requested_speech_emotion(context: str) -> str | None:
    clean = " ".join(str(context or "").split()).strip()
    latest: tuple[int, str] | None = None
    for emotion, patterns in SPEECH_REQUESTED_EMOTION_PATTERNS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, clean):
                prefix = clean[max(0, match.start() - 4):match.start()]
                if re.search(r"(?:不要|别|不用|不必)\s*$", prefix):
                    continue
                candidate = (match.start(), emotion)
                if latest is None or candidate[0] >= latest[0]:
                    latest = candidate
    return latest[1] if latest else None


def infer_speech_emotion(text: str, context: str = "") -> str:
    clean = " ".join(str(text or "").split()).strip()
    context_clean = " ".join(str(context or "").split()).strip()
    requested = _requested_speech_emotion(context_clean)
    if requested:
        return requested
    if not clean and not context_clean:
        return "neutral"
    scores = {
        emotion: (
            2 * sum(1 for pattern in patterns if re.search(pattern, clean))
            + sum(1 for pattern in patterns if re.search(pattern, context_clean))
        )
        for emotion, patterns in SPEECH_EMOTION_PATTERNS.items()
    }
    if re.search(r"[！!]{1,}", clean):
        scores["cheerful"] += 1
    if re.search(r"[？?]$", clean) and re.search(r"(?:还好|怎么|是不是|要不要|可以吗)", clean):
        scores["concerned"] += 1
    if clean.startswith(("……", "...")):
        scores["shy"] += 1
    if re.search(r"(?:终于|成功|完成|做到了|太好了|开心)", context_clean):
        scores["cheerful"] += 1
    if re.search(r"(?:难受|疼|痛|害怕|焦虑|失眠|生病|受伤|撑不住)", context_clean):
        scores["concerned"] += 1
    highest = max(scores.values(), default=0)
    if highest <= 0:
        return "neutral"
    return next(emotion for emotion in SPEECH_EMOTION_PRIORITY if scores[emotion] == highest)


def prepare_speech_prosody(text: str, emotion: str, language: str) -> str:
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if language not in {"zh", "all_zh"} or not clean:
        return clean
    output: list[str] = []
    for line in clean.split("\n"):
        line = re.sub(r"\s*([，。！？；：、…])\s*", r"\1", line.strip())
        if not line:
            continue
        line = re.sub(r"([，。！？；：、…])\1+", r"\1", line)
        # 只强化本身就是语气起句的短语，不把任意第一个逗号改成长停顿。
        if emotion == "cheerful":
            line = re.sub(r"^(太好了|太棒了|好耶|终于)[，,]", r"\1！", line, count=1)
        elif emotion == "serious":
            line = re.sub(r"^(先停一下|等一下|听我说|说真的)[，,]", r"\1。", line, count=1)
        elif emotion == "shy":
            line = re.sub(r"^[.…]+", "…", line)
        if line[-1] not in "。！？!?…":
            line += "！" if emotion == "cheerful" else "。"
        elif emotion == "cheerful" and line.endswith("。"):
            line = line[:-1] + "！"
        elif emotion == "shy" and line.endswith(("。", "！", "!")):
            line = line[:-1] + "……"
        output.append(line)
    return "\n".join(output)


def speech_emotion_info(text: str, context: str = "") -> dict[str, int | str]:
    emotion = infer_speech_emotion(text, context)
    return {"id": emotion, "label": SPEECH_EMOTION_LABELS[emotion]}


def should_use_qq_voice(user_message: str, *, explicitly_requested: bool = False) -> bool:
    mode = str(load_config().get("qq_voice_mode") or "adaptive")
    if mode == "always":
        return True
    if explicitly_requested:
        return True
    if mode == "explicit":
        return False
    return bool(ADAPTIVE_QQ_VOICE_RE.search(str(user_message or "")))


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


def _split_genie_stream_text(text: str, *, max_chars: int = 18) -> list[str]:
    return [segment for segment, _ in _split_genie_stream_segments(text, max_chars=max_chars)]


def _split_genie_stream_segments(
    text: str, *, max_chars: int = 18
) -> list[tuple[str, bool]]:
    """Split speech into synthesis chunks and retain explicit line boundaries."""
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean:
        return []
    lines = [line.strip() for line in clean.split("\n") if line.strip()]
    result: list[tuple[str, bool]] = []
    for line_index, line in enumerate(lines):
        pieces = re.findall(r"[^。！？!?；;，,、…]+[。！？!?；;，,、…]*", line)
        segments: list[str] = []
        pending = ""
        for piece in pieces or [line]:
            piece = piece.strip()
            if not piece:
                continue
            if pending and len(pending) + len(piece) <= max_chars:
                pending += piece
                continue
            if pending:
                segments.append(pending)
                pending = ""
            while len(piece) > max_chars:
                segments.append(piece[:max_chars])
                piece = piece[max_chars:]
            pending = piece
        if pending:
            segments.append(pending)
        if not segments:
            segments = [line]
        # Unsupported symbols can leave a punctuation-only tail (for example
        # an emoji followed by ``。``). Keep that tail with the preceding
        # spoken text instead of sending an empty segment to the TTS worker.
        spoken_segments: list[str] = []
        for segment in segments:
            if re.search(r"[0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]", segment):
                spoken_segments.append(segment)
            elif spoken_segments:
                spoken_segments[-1] += segment
        segments = spoken_segments or [line]
        for segment_index, segment in enumerate(segments):
            is_line_break = line_index < len(lines) - 1 and segment_index == len(segments) - 1
            result.append((segment, is_line_break))
    return result


def _streaming_pcm_wav_header() -> bytes:
    data_size = 0x7FFFFFF0
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        32000,
        64000,
        2,
        16,
        b"data",
        data_size,
    )


def _wav_pcm_payload(content: bytes) -> bytes:
    with wave.open(BytesIO(content), "rb") as stream:
        if (
            stream.getnchannels() != 1
            or stream.getsampwidth() != 2
            or stream.getframerate() != 32000
        ):
            raise OSError("Genie 流式音频不是 32 kHz / 16-bit / 单声道。")
        frames = stream.readframes(stream.getnframes())
    if not frames:
        raise OSError("Genie 流式音频没有 PCM 数据。")
    return frames


def _wav_duration_seconds(content: bytes) -> float:
    with wave.open(BytesIO(content), "rb") as stream:
        frame_rate = stream.getframerate()
        if frame_rate <= 0:
            raise OSError("Genie WAV 采样率无效。")
        return stream.getnframes() / frame_rate


def _short_speech_duration_limit(text: str) -> float | None:
    spoken = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]", "", str(text or ""))
    if not spoken or len(spoken) > 10:
        return None
    # Genie occasionally expands a short acknowledgement into a complete
    # reference clip. Naturalized two/three-word replies also need protection.
    # Formal Mio-Genie measurements put normal 5-10 character Japanese
    # acknowledgements around 1.4-3.0 s, while leaked references cluster near
    # 4.5-4.9 s. Keep the first-pass gate below that observed failure band.
    if len(spoken) <= 3:
        return 2.4
    if len(spoken) <= 6:
        return 3.0
    return 3.4


def _short_speech_recovery_text(text: str, language: str) -> str:
    if language in {"ja", "all_ja"}:
        return "うん、わかったよ。続けて話してね。"
    return "好的，我明白了，你继续说吧。"


def _recovery_speech_duration_limit(text: str) -> float:
    """Allow a natural recovery sentence without accepting a full reference clip."""
    spoken = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]", "", str(text or ""))
    # The old fixed four-second gate rejected valid Japanese recovery phrases
    # around five seconds long and turned the whole reply silent.
    return min(10.0, max(4.0, 1.2 + 0.48 * len(spoken)))


def _wav_acoustic_features(source: bytes | Path, *, buckets: int = 96) -> tuple[list[list[float]], float] | None:
    """Return dependency-free rhythm features for conservative reference-leak checks."""
    try:
        handle = BytesIO(source) if isinstance(source, bytes) else source.open("rb")
        with handle:
            with wave.open(handle, "rb") as stream:
                channels = stream.getnchannels()
                sample_width = stream.getsampwidth()
                sample_rate = stream.getframerate()
                frames = stream.getnframes()
                if channels <= 0 or sample_width != 2 or sample_rate <= 0 or frames <= 0:
                    return None
                pcm = array("h")
                pcm.frombytes(stream.readframes(frames))
    except (OSError, EOFError, wave.Error, ValueError):
        return None
    if sys.byteorder != "little":
        pcm.byteswap()
    if channels > 1:
        mono = [
            sum(int(pcm[index + channel]) for channel in range(channels)) / channels
            for index in range(0, len(pcm) - channels + 1, channels)
        ]
    else:
        mono = [float(value) for value in pcm]
    if len(mono) < max(64, sample_rate // 20):
        return None
    peak = max(abs(value) for value in mono)
    if peak < 32:
        return None
    threshold = max(96.0, peak * 0.035)
    voiced = [index for index, value in enumerate(mono) if abs(value) >= threshold]
    if not voiced:
        return None
    context = max(1, sample_rate // 20)
    start = max(0, voiced[0] - context)
    end = min(len(mono), voiced[-1] + context + 1)
    mono = mono[start:end]
    duration = len(mono) / sample_rate
    if len(mono) < buckets:
        return None
    energy: list[float] = []
    movement: list[float] = []
    crossings: list[float] = []
    for bucket in range(buckets):
        left = bucket * len(mono) // buckets
        right = max(left + 1, (bucket + 1) * len(mono) // buckets)
        chunk = mono[left:right]
        energy.append(math.sqrt(sum(value * value for value in chunk) / len(chunk)))
        if len(chunk) > 1:
            movement.append(sum(abs(chunk[index] - chunk[index - 1]) for index in range(1, len(chunk))) / (len(chunk) - 1))
            crossings.append(sum(1 for index in range(1, len(chunk)) if (chunk[index] >= 0) != (chunk[index - 1] >= 0)) / (len(chunk) - 1))
        else:
            movement.append(0.0)
            crossings.append(0.0)

    def normalize(values: list[float]) -> list[float]:
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        scale = math.sqrt(variance)
        if scale <= 1e-9:
            return [0.0 for _ in values]
        return [(value - mean) / scale for value in values]

    return [normalize(energy), normalize(movement), normalize(crossings)], duration


def _reference_audio_leak_score_from_features(
    generated: tuple[list[list[float]], float] | None,
    original: tuple[list[list[float]], float] | None,
) -> float | None:
    if generated is None or original is None:
        return None
    generated_features, generated_duration = generated
    original_features, original_duration = original
    duration_ratio = generated_duration / max(0.001, original_duration)
    if not 0.76 <= duration_ratio <= 1.32:
        return 0.0

    def correlation(left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right))
        denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
        if denominator <= 1e-9:
            return 1.0 if left == right else 0.0
        return max(-1.0, min(1.0, numerator / denominator))

    correlations = [
        correlation(generated_feature, original_feature)
        for generated_feature, original_feature in zip(generated_features, original_features)
    ]
    return max(0.0, 0.60 * correlations[0] + 0.25 * correlations[1] + 0.15 * correlations[2])


@functools.lru_cache(maxsize=64)
def _cached_reference_audio_features(
    path_text: str,
    size: int,
    modified_ns: int,
) -> tuple[list[list[float]], float] | None:
    del size, modified_ns
    return _wav_acoustic_features(Path(path_text))


def _reference_audio_features(reference: Path) -> tuple[list[list[float]], float] | None:
    try:
        resolved = reference.resolve()
        stat = resolved.stat()
    except OSError:
        return None
    return _cached_reference_audio_features(str(resolved), stat.st_size, stat.st_mtime_ns)


def _reference_audio_leak_score(content: bytes, reference: Path) -> float | None:
    """Score near-copying of a reference clip without importing a heavy ASR stack."""
    return _reference_audio_leak_score_from_features(
        _wav_acoustic_features(content),
        _reference_audio_features(reference),
    )


def _looks_like_reference_audio(content: bytes, reference: Path) -> tuple[bool, float | None]:
    score = _reference_audio_leak_score(content, reference)
    # Real leaked clips from the ONNX runtime are not byte-identical to the
    # conditioning WAV; their dependency-free rhythm score measured 0.86-0.89.
    # A short result must stay clearly below that band to be accepted.
    return bool(score is not None and score >= 0.84), score


def _reference_audio_candidates(config: dict[str, Any], primary: Path) -> list[Path]:
    """Return every runtime/training reference that Genie must never reproduce."""
    candidates: dict[str, Path] = {}

    def add_path(value: object, *, relative_to: Path | None = None) -> None:
        raw = str(value or "").strip()
        if not raw:
            return
        path = Path(raw).expanduser()
        if not path.is_absolute() and relative_to is not None:
            path = relative_to / path
        try:
            path = path.resolve()
            if path.is_file() and path.suffix.lower() == ".wav":
                candidates[str(path).casefold()] = path
        except OSError:
            return

    add_path(primary)
    add_path(config.get("gpt_sovits_ref_audio"), relative_to=settings.companion_dir)

    mapping_path = settings.voice_training_dir / "emotion-references.json"
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        mapping = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            add_path(value.get("audio"), relative_to=mapping_path.parent)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(mapping)
    for directory in (
        settings.voice_training_dir / "materials" / "prepared" / "wav32k_v2",
        settings.voice_training_dir / "materials" / "emotion-references-zh",
        settings.companion_dir / "默认参考音频",
    ):
        try:
            for path in directory.glob("*.wav"):
                add_path(path)
        except OSError:
            continue
    return list(candidates.values())


def _looks_like_any_reference_audio(
    content: bytes,
    references: list[Path],
) -> tuple[bool, float | None, Path | None]:
    generated = _wav_acoustic_features(content)
    best_score: float | None = None
    best_reference: Path | None = None
    for reference in references:
        score = _reference_audio_leak_score_from_features(
            generated,
            _reference_audio_features(reference),
        )
        if score is not None and (best_score is None or score > best_score):
            best_score = score
            best_reference = reference
    return bool(best_score is not None and best_score >= 0.84), best_score, best_reference


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


def _postprocess_speech_wav(content: bytes, volume: int) -> bytes:
    try:
        with wave.open(BytesIO(content), "rb") as source:
            params = source.getparams()
            if params.sampwidth != 2 or params.nchannels not in {1, 2}:
                return content
            frames = source.readframes(params.nframes)
    except (EOFError, OSError, wave.Error):
        return content

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return content

    peak = max(abs(sample) for sample in samples)
    if peak <= 0:
        return content
    threshold = max(96, int(peak * 0.012))
    active = [index for index, sample in enumerate(samples) if abs(sample) >= threshold]
    if active:
        padding = int(params.framerate * 0.06) * params.nchannels
        start = max(0, active[0] - padding)
        end = min(len(samples), active[-1] + 1 + padding)
        start -= start % params.nchannels
        remainder = end % params.nchannels
        if remainder:
            end = min(len(samples), end + params.nchannels - remainder)
        minimum = int(params.framerate * 0.12) * params.nchannels
        if end - start >= minimum:
            samples = samples[start:end]

    gain = max(0.0, min(1.0, volume / 100.0))
    if gain < 0.999:
        for index, sample in enumerate(samples):
            samples[index] = max(-32768, min(32767, round(sample * gain)))

    if sys.byteorder != "little":
        samples.byteswap()
    output = BytesIO()
    try:
        with wave.open(output, "wb") as target:
            target.setparams(params)
            target.writeframes(samples.tobytes())
    except (OSError, wave.Error):
        return content
    return output.getvalue()


def _speech_text_for_comparison(text: str) -> str:
    return "".join(
        character.casefold()
        for character in str(text or "")
        if character.isalnum() or "\u3040" <= character <= "\u30ff" or "\u3400" <= character <= "\u9fff"
    )


def _speech_text_similarity(expected: str, actual: str) -> float:
    left = _speech_text_for_comparison(expected)
    right = _speech_text_for_comparison(actual)
    if not left or not right:
        return 0.0
    sequence_score = SequenceMatcher(None, left, right).ratio()
    left_chars = set(left)
    overlap_score = len(left_chars.intersection(right)) / max(1, len(left_chars))
    return round(max(sequence_score, overlap_score * 0.8), 4)


def _wav_quality_metrics(content: bytes) -> dict[str, float | int]:
    try:
        with wave.open(BytesIO(content), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frame_count = source.getnframes()
            frames = source.readframes(frame_count)
    except (EOFError, OSError, wave.Error) as exc:
        raise ValueError(f"音频不是有效的 WAV：{exc}") from exc
    if sample_width != 2 or channels not in {1, 2} or sample_rate <= 0:
        raise ValueError("语音质量检查只支持 16 位单声道或双声道 WAV。")

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples or frame_count <= 0:
        raise ValueError("生成的 WAV 没有可播放采样。")

    normalized_square_sum = sum(float(sample) * float(sample) for sample in samples)
    rms = math.sqrt(normalized_square_sum / len(samples)) / 32768.0
    clipping_ratio = sum(1 for sample in samples if abs(sample) >= 32700) / len(samples)

    window_frames = max(1, round(sample_rate * 0.02))
    silent_windows = 0
    total_windows = 0
    for start_frame in range(0, frame_count, window_frames):
        start = start_frame * channels
        end = min(len(samples), (start_frame + window_frames) * channels)
        window = samples[start:end]
        if not window:
            continue
        window_rms = math.sqrt(sum(float(sample) * float(sample) for sample in window) / len(window)) / 32768.0
        total_windows += 1
        if window_rms < 0.0035:
            silent_windows += 1

    return {
        "duration_seconds": round(frame_count / sample_rate, 3),
        "sample_rate": sample_rate,
        "channels": channels,
        "rms": round(rms, 6),
        "silence_ratio": round(silent_windows / max(1, total_windows), 4),
        "clipping_ratio": round(clipping_ratio, 6),
    }


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


def save_voice_reference_data_url(data_url: str, filename: str, *, profile_id: str = "") -> Path:
    if "," not in data_url:
        raise ValueError("参考音频数据格式不正确。")
    header, encoded = data_url.split(",", 1)
    if ";base64" not in header or not header.startswith(("data:audio/", "data:video/", "data:application/")):
        raise ValueError("请选择音频文件作为参考音色。")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("参考音频无法读取。") from exc
    if not raw:
        raise ValueError("参考音频不能为空。")
    if len(raw) > 40 * 1024 * 1024:
        raise ValueError("参考音频不能超过 40MB。")
    extension = Path(filename).suffix.lower()
    allowed = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"}
    if extension not in allowed:
        extension = ".wav" if "wav" in header.lower() else ".mp3"
    config = load_config()
    selected_profile_id = str(profile_id or config.get("default_voice_profile_id") or DEFAULT_VOICE_PROFILE_ID)
    if selected_profile_id not in config["voice_profiles"]:
        raise ValueError("要更新的音色配置不存在。")
    safe_profile_id = re.sub(r"[^A-Za-z0-9._-]+", "-", selected_profile_id).strip("-.") or "voice"
    settings.companion_dir.mkdir(parents=True, exist_ok=True)
    target = settings.companion_dir / f"音色参考-{safe_profile_id}{extension}"
    temporary = settings.companion_dir / f"音色参考-{safe_profile_id}.tmp{extension}"
    temporary.write_bytes(raw)
    temporary.replace(target)
    for existing in settings.companion_dir.glob(f"音色参考-{safe_profile_id}.*"):
        if existing != target:
            existing.unlink(missing_ok=True)
    profiles = dict(config.get("voice_profiles") or {})
    profile = dict(profiles[selected_profile_id])
    profile["gpt_sovits_ref_audio"] = str(target.resolve())
    profiles[selected_profile_id] = profile
    changes: dict[str, Any] = {"voice_profiles": profiles}
    if selected_profile_id == config.get("default_voice_profile_id"):
        changes["gpt_sovits_ref_audio"] = str(target.resolve())
    save_config(changes)
    return target


VOICE_PACKAGE_FORMAT = "mio-voice-package"
VOICE_PACKAGE_MAX_ENTRIES = 64
VOICE_PACKAGE_MAX_MANIFEST_BYTES = 1024 * 1024
VOICE_PACKAGE_MAX_COMPRESSION_RATIO = 1000
VOICE_PACKAGE_DISK_RESERVE_BYTES = 128 * 1024 * 1024
VOICE_REFERENCE_ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"}
VoiceImportProgress = Callable[[dict[str, object]], None]


def _report_voice_import_progress(
    progress: VoiceImportProgress | None,
    *,
    phase: str,
    message: str,
    processed_bytes: int = 0,
    total_bytes: int = 0,
) -> None:
    if progress is None:
        return
    percent = int(processed_bytes * 100 / total_bytes) if total_bytes > 0 else 0
    progress({
        "phase": phase,
        "message": message,
        "processed_bytes": max(0, int(processed_bytes)),
        "total_bytes": max(0, int(total_bytes)),
        "percent": max(0, min(99, percent)),
    })


def export_voice_package(profile_id: str) -> bytes:
    """把单个音色导出为只含数据的 ZIP 音色包（manifest + 可选参考音频）。"""
    config = load_config()
    profiles = config.get("voice_profiles") or {}
    if profile_id not in profiles:
        raise ValueError("要导出的音色不存在。")
    profile = dict(profiles[profile_id])
    manifest: dict[str, Any] = {
        "format": VOICE_PACKAGE_FORMAT,
        "format_version": 1,
        "name": str(profile.get("name") or profile_id).strip()[:80],
        "engine": str(profile.get("engine") or MIO_VOICE_ENGINE),
        "prompt_text": str(profile.get("gpt_sovits_prompt_text") or ""),
        "prompt_language": str(profile.get("gpt_sovits_prompt_language") or "zh"),
        "text_language": str(profile.get("gpt_sovits_text_language") or "auto"),
        "translate_to_japanese": bool(profile.get("gpt_sovits_translate_to_japanese", False)),
        "use_emotion_references": bool(profile.get("use_emotion_references", True)),
        "gpt_weights_name": Path(str(profile.get("gpt_sovits_gpt_weights") or "")).name,
        "sovits_weights_name": Path(str(profile.get("gpt_sovits_sovits_weights") or "")).name,
        "reference_audio": "",
        "license": "",
        "creator": "",
        "description": "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sha256": "",
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        ref_audio = str(profile.get("gpt_sovits_ref_audio") or "")
        if ref_audio:
            path = Path(ref_audio)
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                manifest["reference_audio"] = path.name
                manifest["sha256"] = digest.hexdigest()
                archive.write(path, f"reference_audio/{path.name}")
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def _import_so_vits_svc_archive(
    archive: zipfile.ZipFile,
    normalized_infos: dict[str, zipfile.ZipInfo],
    *,
    filename: str,
    progress: VoiceImportProgress | None = None,
) -> dict[str, Any]:
    from . import so_vits_svc_service

    config_candidates = [name for name in normalized_infos if Path(name).name.lower() == "config.json"]
    model_candidates = [
        name for name in normalized_infos
        if re.fullmatch(r"G_[^/\\]+\.pth", Path(name).name, flags=re.IGNORECASE)
    ]
    if not config_candidates or not model_candidates:
        raise ValueError("这不是 Mio 交换音色包，也没有识别到可用的第三方音色模型（当前支持 So-VITS-SVC 4.1）。")
    config_entry = min(config_candidates, key=lambda name: len(Path(name).parts))
    config_parent = str(Path(config_entry).parent).replace("\\", "/").strip(".")
    same_parent_models = [
        name for name in model_candidates
        if str(Path(name).parent).replace("\\", "/").strip(".") == config_parent
    ]
    model_entry = max(same_parent_models or model_candidates, key=lambda name: normalized_infos[name].file_size)
    config_info = normalized_infos[config_entry]
    model_info = normalized_infos[model_entry]
    if config_info.file_size > VOICE_PACKAGE_MAX_MANIFEST_BYTES:
        raise ValueError("第三方音色 config.json 异常过大，已拦截。")
    if model_info.file_size < 1024 * 1024:
        raise ValueError("第三方音色主模型异常过小，文件可能不完整。")
    ratio = model_info.file_size / max(1, model_info.compress_size)
    if ratio > VOICE_PACKAGE_MAX_COMPRESSION_RATIO:
        raise ValueError("第三方音色主模型压缩比异常，疑似 ZIP 炸弹。")
    try:
        model_config = json.loads(archive.read(config_info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("第三方音色 config.json 无法读取。") from None
    if not isinstance(model_config, dict):
        raise ValueError("第三方音色 config.json 格式不正确。")
    model_section = model_config.get("model") if isinstance(model_config.get("model"), dict) else {}
    speakers = model_config.get("spk") if isinstance(model_config.get("spk"), dict) else {}
    speech_encoder = str(model_section.get("speech_encoder") or "").strip().lower()
    if speech_encoder not in {"vec768l12", "vec256l9"} or not speakers:
        raise ValueError("第三方音色不是当前可兼容的 So-VITS-SVC 4.1 模型。")
    speaker = str(next(iter(speakers))).strip()[:80]
    if not speaker:
        raise ValueError("第三方音色没有有效的说话人名称。")

    config = load_config()
    profiles = dict(config.get("voice_profiles") or {})
    if len(profiles) >= 20:
        raise ValueError("音色数量已达上限（20 个），请先删除一个再导入。")
    base_profile_id = ""
    current_default = str(config.get("default_voice_profile_id") or "")
    if isinstance(profiles.get(current_default), dict) and profiles[current_default].get("engine") == MIO_VOICE_ENGINE:
        base_profile_id = current_default
    if not base_profile_id:
        base_profile_id = next(
            (profile_id for profile_id, profile in profiles.items() if isinstance(profile, dict) and profile.get("engine") == MIO_VOICE_ENGINE),
            "",
        )
    if not base_profile_id:
        raise ValueError("导入 So-VITS-SVC 前请先保留一个 GPT-SoVITS 基础音色，转换文字时需要它先生成声源。")

    new_id = _new_voice_profile_id(profiles)
    package_stem = Path(filename or "").stem.strip()
    display_name = (speaker if speaker else package_stem or "第三方音色")[:80]
    third_party_root = settings.companion_dir / "第三方音色"
    target_dir = third_party_root / new_id
    staging_dir = third_party_root / f".{new_id}.{uuid.uuid4().hex}.importing"
    settings.companion_dir.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(settings.companion_dir).free
    required_bytes = int(model_info.file_size + config_info.file_size)
    if required_bytes > max(0, free_bytes - VOICE_PACKAGE_DISK_RESERVE_BYTES):
        raise ValueError("磁盘空间不足，无法导入第三方音色主模型。")
    third_party_root.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=False, exist_ok=False)
    total_copy_bytes = int(config_info.file_size + model_info.file_size)
    copied_bytes = 0
    try:
        target_config = staging_dir / "config.json"
        target_model = staging_dir / Path(model_entry).name
        with archive.open(config_info, "r") as source_config, target_config.open("wb") as output:
            while True:
                chunk = source_config.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                copied_bytes += len(chunk)
                _report_voice_import_progress(
                    progress,
                    phase="extracting",
                    message="正在复制音色模型",
                    processed_bytes=copied_bytes,
                    total_bytes=total_copy_bytes,
                )
        with archive.open(model_info, "r") as source_model, target_model.open("wb") as output:
            while True:
                chunk = source_model.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                copied_bytes += len(chunk)
                _report_voice_import_progress(
                    progress,
                    phase="extracting",
                    message="正在复制音色模型",
                    processed_bytes=copied_bytes,
                    total_bytes=total_copy_bytes,
                )
        staging_dir.replace(target_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    profile: dict[str, Any] = {
        "name": display_name,
        "engine": SO_VITS_SVC_ENGINE,
        "gpt_sovits_ref_audio": "",
        "gpt_sovits_prompt_text": "",
        "gpt_sovits_prompt_language": "zh",
        "gpt_sovits_text_language": "auto",
        "gpt_sovits_translate_to_japanese": False,
        "gpt_sovits_gpt_weights": "",
        "gpt_sovits_sovits_weights": "",
        "use_emotion_references": False,
        "so_vits_svc_model_path": str((target_dir / Path(model_entry).name).resolve()),
        "so_vits_svc_config_path": str((target_dir / "config.json").resolve()),
        "so_vits_svc_speaker": speaker,
        "so_vits_svc_pitch": 0,
        "so_vits_svc_auto_predict_f0": bool(model_section.get("use_automatic_f0_prediction", True)),
        "so_vits_svc_noise_scale": 0.4,
        "so_vits_svc_base_profile_id": base_profile_id,
        "source_package_name": Path(filename or "第三方音色.zip").name[:260],
        "source_license": "未声明（仅导入可信且有权使用的模型）",
    }
    runtime = so_vits_svc_service.runtime_status()
    activated = bool(runtime["ready"])
    if activated:
        try:
            _report_voice_import_progress(
                progress,
                phase="validating",
                message="正在独立进程中用 CPU 加载验证音色模型",
                processed_bytes=99,
                total_bytes=100,
            )
            so_vits_svc_service.probe_profile(profile, device="cpu")
        except (ValueError, OSError, TimeoutError) as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise ValueError(f"第三方音色模型加载验证失败：{exc}") from exc
    profiles[new_id] = profile
    try:
        _report_voice_import_progress(
            progress,
            phase="saving",
            message="正在保存音色设置",
            processed_bytes=99,
            total_bytes=100,
        )
        save_config({
            "voice_profiles": profiles,
            "default_voice_profile_id": new_id if activated else base_profile_id,
            "voice_engine": MIO_VOICE_ENGINE,
        })
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    return {
        "id": new_id,
        "name": display_name,
        "engine": SO_VITS_SVC_ENGINE,
        "speaker": speaker,
        "base_profile_id": base_profile_id,
        "model_size": model_info.file_size,
        "runtime": "ready" if activated else "missing",
        "activated": activated,
        "runtime_missing": list(runtime.get("missing") or []),
    }


def _import_voice_package_source(
    source: BytesIO | Path,
    *,
    filename: str = "",
    progress: VoiceImportProgress | None = None,
) -> dict[str, Any]:
    """校验并流式导入 ZIP；不按包体积设业务上限，也不把参考音频整体读入内存。"""
    target: Path | None = None
    temporary: Path | None = None
    try:
        _report_voice_import_progress(progress, phase="checking", message="正在检查音色包")
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > VOICE_PACKAGE_MAX_ENTRIES:
                raise ValueError("音色包条目过多，疑似异常压缩包。")
            normalized_infos = {info.filename.replace("\\", "/"): info for info in infos}
            if len(normalized_infos) != len(infos):
                raise ValueError("音色包包含重复路径，已拦截。")
            for name in normalized_infos:
                if name.startswith("/") or name.startswith("\\") or ".." in name.split("/"):
                    raise ValueError("音色包包含非法路径，已拦截。")
            if "manifest.json" not in normalized_infos:
                return _import_so_vits_svc_archive(
                    archive,
                    normalized_infos,
                    filename=filename,
                    progress=progress,
                )
            manifest_info = normalized_infos["manifest.json"]
            if manifest_info.file_size > VOICE_PACKAGE_MAX_MANIFEST_BYTES:
                raise ValueError("manifest.json 异常过大，已拦截。")
            try:
                manifest_raw = archive.read(manifest_info)
                manifest = json.loads(manifest_raw.decode("utf-8"))
            except (KeyError, UnicodeDecodeError):
                raise ValueError("manifest.json 无法读取。") from None
            if not isinstance(manifest, dict):
                raise ValueError("manifest.json 格式不正确。")
            if str(manifest.get("format") or "") != VOICE_PACKAGE_FORMAT:
                raise ValueError("这不是 Mio 音色包格式，已拦截。")
            name = str(manifest.get("name") or "").strip()[:80]
            if not name:
                raise ValueError("音色包缺少名称。")
            audio_name = str(manifest.get("reference_audio") or "").strip()
            audio_info: zipfile.ZipInfo | None = None
            if audio_name:
                if Path(audio_name).name != audio_name or "/" in audio_name or "\\" in audio_name:
                    raise ValueError("参考音频名称包含非法路径。")
                entry = "reference_audio/" + audio_name.replace("\\", "/")
                if entry not in normalized_infos:
                    raise ValueError(f"音色包里找不到参考音频：{audio_name}")
                audio_info = normalized_infos[entry]
                extension = Path(audio_name).suffix.lower()
                if extension not in VOICE_REFERENCE_ALLOWED_EXTENSIONS:
                    raise ValueError("参考音频格式不受支持。")

                ratio = audio_info.file_size / max(1, audio_info.compress_size)
                if ratio > VOICE_PACKAGE_MAX_COMPRESSION_RATIO:
                    raise ValueError("参考音频压缩比异常，疑似 ZIP 炸弹。")

            config = load_config()
            profiles = dict(config.get("voice_profiles") or {})
            if len(profiles) >= 20:
                raise ValueError("音色数量已达上限（20 个），请先删除一个再导入。")
            new_id = _new_voice_profile_id(profiles)
            profile: dict[str, Any] = {
                "name": name,
                "engine": str(manifest.get("engine") or MIO_VOICE_ENGINE),
                "gpt_sovits_ref_audio": "",
                "gpt_sovits_prompt_text": str(manifest.get("prompt_text") or ""),
                "gpt_sovits_prompt_language": str(manifest.get("prompt_language") or "zh"),
                "gpt_sovits_text_language": str(manifest.get("text_language") or "auto"),
                "gpt_sovits_translate_to_japanese": bool(manifest.get("translate_to_japanese", False)),
                "gpt_sovits_gpt_weights": "",
                "gpt_sovits_sovits_weights": "",
                "use_emotion_references": bool(manifest.get("use_emotion_references", True)),
            }
            if audio_info is not None:
                settings.companion_dir.mkdir(parents=True, exist_ok=True)
                free_bytes = shutil.disk_usage(settings.companion_dir).free
                if audio_info.file_size > max(0, free_bytes - VOICE_PACKAGE_DISK_RESERVE_BYTES):
                    raise ValueError("磁盘空间不足，无法导入参考音频。")
                safe_profile_id = re.sub(r"[^A-Za-z0-9._-]+", "-", new_id).strip("-.") or "voice"
                target = settings.companion_dir / f"音色参考-{safe_profile_id}{Path(audio_name).suffix.lower()}"
                temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
                digest = hashlib.sha256()
                copied_bytes = 0
                with archive.open(audio_info, "r") as source_audio, temporary.open("wb") as output:
                    while True:
                        chunk = source_audio.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        output.write(chunk)
                        copied_bytes += len(chunk)
                        _report_voice_import_progress(
                            progress,
                            phase="extracting",
                            message="正在复制参考音频",
                            processed_bytes=copied_bytes,
                            total_bytes=audio_info.file_size,
                        )
                expected = str(manifest.get("sha256") or "").strip().lower()
                if expected and digest.hexdigest() != expected:
                    raise ValueError("参考音频校验和不一致，文件可能已损坏。")
                temporary.replace(target)
                temporary = None
                profile["gpt_sovits_ref_audio"] = str(target.resolve())
    except zipfile.BadZipFile:
        raise ValueError("音色包不是有效的 ZIP 文件。") from None
    except json.JSONDecodeError:
        raise ValueError("manifest.json 不是有效 JSON。") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    profiles[new_id] = profile
    changes: dict[str, Any] = {"voice_profiles": profiles}
    if not config.get("default_voice_profile_id"):
        changes["default_voice_profile_id"] = new_id
    try:
        _report_voice_import_progress(
            progress,
            phase="saving",
            message="正在保存音色设置",
            processed_bytes=99,
            total_bytes=100,
        )
        save_config(changes)
    except Exception:
        if target is not None:
            target.unlink(missing_ok=True)
        raise
    return {"id": new_id, "name": name, "reference_audio": audio_name}


def import_voice_package(raw: bytes, _filename: str = "") -> dict[str, Any]:
    """兼容内存调用；HTTP 路由使用文件版，避免大包占满内存。"""
    if not raw:
        raise ValueError("音色包为空。")
    return _import_voice_package_source(BytesIO(raw), filename=_filename)


def import_voice_package_file(
    path: Path,
    *,
    progress: VoiceImportProgress | None = None,
) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("音色包为空。")
    return _import_voice_package_source(path, filename=path.name, progress=progress)


def _new_voice_profile_id(profiles: dict[str, Any]) -> str:
    base = f"voice-{int(time.time() * 1000)}"
    candidate = base
    counter = 1
    while candidate in profiles:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _voice_weight_options() -> dict[str, list[dict[str, str]]]:
    roots = [
        settings.voice_training_dir,
        settings.workspace_root,
        settings.project_root,
        settings.source_workspace_root,
        Path("D:/GPT-SoVITS"),
        Path("D:/GPT-SoVITS-v2"),
        Path("D:/GPT-SoVITS-v2proplus"),
    ]
    groups: dict[str, tuple[str, ...]] = {
        "gpt": ("GPT_weights", "GPT_weights_v2", "GPT_weights_v2Pro", "GPT_weights_v2ProPlus"),
        "sovits": (
            "SoVITS_weights",
            "SoVITS_weights_v2",
            "SoVITS_weights_v2Pro",
            "SoVITS_weights_v2ProPlus",
        ),
    }
    result: dict[str, list[dict[str, str]]] = {"gpt": [], "sovits": []}
    for kind, folder_names in groups.items():
        suffix = ".ckpt" if kind == "gpt" else ".pth"
        seen: set[str] = set()
        for root in roots:
            for source in (root, root / "GPT-SoVITS"):
                for folder_name in folder_names:
                    folder = source / folder_name
                    if not folder.is_dir():
                        continue
                    for path in sorted(folder.glob(f"*{suffix}"), key=lambda item: item.stat().st_mtime, reverse=True):
                        resolved = str(path.resolve())
                        if resolved in seen or not _voice_weight_path(path, kind):
                            continue
                        seen.add(resolved)
                        result[kind].append({"name": path.name, "path": resolved})
    return result


def _runtime_voice_weights() -> dict[str, str]:
    source = settings.voice_training_dir / "GPT-SoVITS"
    config_path = source / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return {"gpt": "", "sovits": ""}
    custom = payload.get("custom") if isinstance(payload, dict) else None
    if not isinstance(custom, dict):
        return {"gpt": "", "sovits": ""}

    result: dict[str, str] = {}
    for kind, key in (("gpt", "t2s_weights_path"), ("sovits", "vits_weights_path")):
        raw = str(custom.get(key) or "").strip()
        path = Path(raw).expanduser()
        if raw and not path.is_absolute():
            path = source / path
        result[kind] = str(path.resolve()) if raw and path.is_file() else ""
    return result


def _emotion_reference_status() -> dict[str, Any]:
    mapping_path = settings.voice_training_dir / "emotion-references.json"
    try:
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    available: list[str] = []
    if isinstance(payload, dict):
        for emotion in SPEECH_EMOTION_LABELS:
            item = payload.get(emotion)
            if not isinstance(item, dict):
                continue
            audio = Path(str(item.get("audio") or "")).expanduser()
            if not audio.is_absolute():
                audio = mapping_path.parent / audio
            if audio.is_file() and str(item.get("text") or "").strip():
                available.append(emotion)
    return {
        "ready": bool(available),
        "count": len(available),
        "emotions": available,
    }


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


def voice_training_status() -> dict[str, Any]:
    root = settings.voice_training_dir
    source = root / "GPT-SoVITS"
    environment_python = root / ".voice-env" / "Scripts" / "python.exe"
    environment_marker = root / ".voice-env" / ".setup-complete"
    pretrained_dir = source / "GPT_SoVITS" / "pretrained_models"
    pretrained_marker = root / ".pretrained-v2-complete"
    required_models = (
        "chinese-hubert-base/config.json",
        "chinese-hubert-base/preprocessor_config.json",
        "chinese-hubert-base/pytorch_model.bin",
        "chinese-roberta-wwm-ext-large/config.json",
        "chinese-roberta-wwm-ext-large/pytorch_model.bin",
        "chinese-roberta-wwm-ext-large/tokenizer.json",
        "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
        "gsv-v2final-pretrained/s2D2333k.pth",
        "gsv-v2final-pretrained/s2G2333k.pth",
    )
    present_models = [name for name in required_models if (pretrained_dir / name).is_file()]
    status_data: dict[str, Any] = {}
    status_kind = "setup"
    training_status: dict[str, Any] = {}
    for kind, filename in (("setup", "setup-status.json"), ("training", "training-status.json")):
        try:
            candidate = json.loads((root / filename).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, dict):
            continue
        if kind == "training":
            training_status = candidate
        if str(candidate.get("updated_at") or "") >= str(status_data.get("updated_at") or ""):
            status_data = candidate
            status_kind = kind

    training_result: dict[str, Any] = {}
    try:
        candidate = json.loads((root / "training-result.json").read_text(encoding="utf-8-sig"))
        if isinstance(candidate, dict):
            training_result = candidate
    except (OSError, json.JSONDecodeError):
        pass
    gpt_model = Path(str(training_result.get("gpt_model") or ""))
    sovits_model = Path(str(training_result.get("sovits_model") or ""))
    trained_ready = bool(
        training_status.get("success")
        and training_status.get("stage") == "complete"
        and gpt_model.is_file()
        and sovits_model.is_file()
    )
    if not trained_ready:
        runtime_weights = _runtime_voice_weights()
        runtime_gpt = Path(runtime_weights["gpt"])
        runtime_sovits = Path(runtime_weights["sovits"])
        if runtime_gpt.is_file() and runtime_sovits.is_file():
            gpt_model = runtime_gpt
            sovits_model = runtime_sovits
            trained_ready = True
    stage = str(status_data.get("stage") or "")
    raw_message = str(status_data.get("message") or "")
    stage_messages = {
        "1/5": "正在 D 盘创建独立 Python 环境",
        "2/5": "正在准备安装工具和编译依赖",
        "3/5": "正在确认 RTX 4060 的 CUDA 版 PyTorch",
        "4/5": "正在安装 GPT-SoVITS 依赖",
        "5/5": "正在检查 CUDA 和训练程序依赖",
        "complete": "训练环境已安装，可以继续下载基础模型",
        "models-complete": "v2 基础模型已准备完成",
    }
    display_message = stage_messages.get(stage, raw_message)
    if status_kind == "training" and stage == "complete":
        display_message = "Mio 的第一版专属音色已训练完成"
    if stage == "models-error":
        display_message = f"基础模型下载失败：{raw_message}"
    elif stage == "error":
        display_message = f"训练环境初始化失败：{raw_message}"
    elif raw_message.startswith("Downloading: "):
        display_message = f"正在下载：{raw_message.removeprefix('Downloading: ')}"
    elif raw_message.startswith("Already present: "):
        display_message = f"已存在：{raw_message.removeprefix('Already present: ')}"
    material_dir = root / "materials" / "raw-japanese"
    material_count = sum(1 for path in material_dir.iterdir() if path.is_file()) if material_dir.is_dir() else 0
    return {
        "root": str(root),
        "source_ready": (source / "webui.py").is_file(),
        "environment_ready": environment_python.is_file() and environment_marker.is_file(),
        "pretrained_ready": pretrained_marker.is_file() and len(present_models) == len(required_models),
        "model_count": len(present_models),
        "expected_model_count": len(required_models),
        "material_count": material_count,
        "trained_ready": trained_ready,
        "gpt_model": str(gpt_model) if trained_ready else "",
        "sovits_model": str(sovits_model) if trained_ready else "",
        "stage": stage,
        "message": display_message,
        "updated_at": str(status_data.get("updated_at") or ""),
    }


def launch_voice_training(action: str) -> dict[str, Any]:
    root = settings.voice_training_dir
    if action == "folder":
        root.mkdir(parents=True, exist_ok=True)
        os.startfile(str(root))
        return voice_training_status()

    if action == "check":
        script = root / "检查训练环境.ps1"
        if not script.is_file():
            raise FileNotFoundError(f"找不到音色训练脚本：{script}")
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Json",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise OSError(completed.stderr.strip() or "音色训练环境检查失败。")
        return voice_training_status()

    scripts = {
        "setup": root / "安装训练环境.ps1",
        "models": root / "下载基础模型.ps1",
        "open": root / "启动音色训练.ps1",
    }
    script = scripts.get(action)
    if script is None:
        raise ValueError("不支持的音色训练操作。")
    if not script.is_file():
        raise FileNotFoundError(f"找不到音色训练脚本：{script}")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=str(root),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
    status = voice_training_status()
    status["launched_action"] = action
    status["message"] = {
        "setup": "初始化已启动，请查看弹出的进度窗口",
        "models": "基础模型下载已启动，请查看弹出的进度窗口",
        "open": "训练工具正在启动",
    }.get(action, status.get("message", ""))
    return status


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
