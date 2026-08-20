from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
SOURCE_BACKEND_DIR = APP_DIR.parent
SOURCE_PROJECT_ROOT = SOURCE_BACKEND_DIR.parent
SOURCE_WORKSPACE_ROOT = SOURCE_PROJECT_ROOT.parent
RUNTIME_ROOT_TEXT = os.getenv("MIO_RUNTIME_ROOT", "").strip()
DISABLE_DOTENV = os.getenv("MIO_DISABLE_DOTENV", "").strip().lower() in {"1", "true", "yes", "on"}
PROJECT_ROOT = Path(RUNTIME_ROOT_TEXT).expanduser().resolve() if RUNTIME_ROOT_TEXT else SOURCE_PROJECT_ROOT
WORKSPACE_ROOT = PROJECT_ROOT.parent if RUNTIME_ROOT_TEXT else SOURCE_PROJECT_ROOT.parent
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_PROJECT_ROOT.parent))
BACKEND_DIR = PROJECT_ROOT if RUNTIME_ROOT_TEXT else SOURCE_BACKEND_DIR
if RUNTIME_ROOT_TEXT and (PROJECT_ROOT / "backend").is_dir():
    BACKEND_DIR = PROJECT_ROOT / "backend"

if not DISABLE_DOTENV:
    if RUNTIME_ROOT_TEXT:
        load_dotenv(PROJECT_ROOT / "backend" / ".env")
        load_dotenv(PROJECT_ROOT / ".env")
    else:
        load_dotenv(SOURCE_BACKEND_DIR / ".env")
        load_dotenv(SOURCE_PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or os.getenv(f"\ufeff{name}") or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "启用", "是"}


def _env_list(name: str) -> tuple[str, ...]:
    raw = _env(name)
    if not raw:
        return ()
    normalized = raw.replace("，", ",").replace(";", ",").replace("；", ",")
    return tuple(part.strip() for part in normalized.split(",") if part.strip())


def _load_qq_channel_setup() -> dict[str, str]:
    path = PROJECT_ROOT / "数据" / "QQ通道设置.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "account": str(payload.get("account") or "").strip(),
        "onebot_token": str(payload.get("onebot_token") or "").strip(),
    }


_QQ_CHANNEL_SETUP = _load_qq_channel_setup()


def _openai_base_urls() -> tuple[str, ...]:
    urls: list[str] = []
    for value in (_env("OPENAI_BASE_URL"), *_env_list("OPENAI_BASE_URLS")):
        clean = value.strip().rstrip("/")
        if clean and clean not in urls:
            urls.append(clean)
    return tuple(urls)


def _openai_models() -> tuple[str, ...]:
    models: list[str] = []
    for value in (_env("OPENAI_MODEL"), *_env_list("OPENAI_MODELS")):
        clean = value.strip()
        if clean and clean not in models:
            models.append(clean)
    return tuple(models)


def _default_proxy_url() -> str:
    return (
        _env("OPENAI_PROXY_URL")
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
        or os.getenv("ALL_PROXY", "").strip()
    )


RUNTIME_SETTING_SPECS: dict[str, tuple[str, float | int | None, float | int | None]] = {
    "openai_timeout_seconds": ("int", 5, 300),
    "openai_request_deadline_seconds": ("int", 10, 600),
    "screen_reaction_timeout_seconds": ("int", 5, 120),
    "screen_history_retention_days": ("int", 1, 365),
    "screen_history_max_rows": ("int", 1000, 200000),
    "manual_max_chars": ("int", 1000, 50000),
    "chat_temperature": ("float", 0.0, 2.0),
    "action_planner_temperature": ("float", 0.0, 2.0),
    "chat_history_limit": ("int", 1, 500),
    "chat_raw_history_limit": ("int", 1, 1000),
    "chat_context_max_chars": ("int", 4000, 200000),
    "chat_recent_keep_messages": ("int", 4, 100),
    "memory_context_days": ("int", 1, 90),
    "memory_context_max_chars": ("int", 1000, 50000),
    "memory_context_messages_per_day": ("int", 1, 20),
    "qq_bot_enabled": ("bool", None, None),
    "qq_allowed_user_ids": ("csv", 0, 2000),
    "qq_image_enabled": ("bool", None, None),
    "qq_image_max_count": ("int", 1, 10),
    "qq_image_max_bytes": ("int", 1048576, 52428800),
    "qq_image_detail": ("str", 1, 20),
    "qq_image_send_to_model": ("bool", None, None),
    "qq_message_debounce_seconds": ("float", 0.0, 30.0),
    "qq_message_incomplete_debounce_seconds": ("float", 0.0, 60.0),
    "chat_follow_up_capture_seconds": ("float", 0.0, 20.0),
    "chat_follow_up_max_capture_count": ("int", 0, 5),
    "qq_delivery_ack_timeout_seconds": ("float", 1.0, 30.0),
    "qq_delivery_max_retries": ("int", 0, 3),
    "qq_reply_initial_delay_seconds": ("float", 0.0, 30.0),
    "qq_reply_delay_seconds": ("float", 0.0, 30.0),
    "qq_proactive_enabled": ("bool", None, None),
    "qq_proactive_min_idle_minutes": ("int", 5, 1440),
    "qq_proactive_max_idle_minutes": ("int", 5, 1440),
    "qq_proactive_day_start_hour": ("int", 0, 23),
    "qq_proactive_day_end_hour": ("int", 0, 23),
    "qq_proactive_check_seconds": ("int", 30, 3600),
    "daily_diary_auto_enabled": ("bool", None, None),
    "daily_diary_check_seconds": ("int", 30, 3600),
    "daily_review_auto_enabled": ("bool", None, None),
    "daily_review_auto_hour": ("int", 0, 23),
    "daily_review_auto_minute": ("int", 0, 59),
    "daily_review_auto_notify_qq": ("bool", None, None),
    "daily_review_check_seconds": ("int", 30, 86400),
    "weekly_review_enabled": ("bool", None, None),
    "weekly_review_hour": ("int", 0, 23),
    "weekly_review_notify_qq": ("bool", None, None),
    "weekly_review_check_seconds": ("int", 30, 86400),
    "monthly_review_enabled": ("bool", None, None),
    "monthly_review_hour": ("int", 0, 23),
    "monthly_review_notify_qq": ("bool", None, None),
    "monthly_review_check_seconds": ("int", 30, 86400),
    "backup_enabled": ("bool", None, None),
    "backup_keep_count": ("int", 1, 365),
    "backup_check_seconds": ("int", 60, 86400),
    "night_close_enabled": ("bool", None, None),
    "night_close_start_hour": ("int", 0, 23),
    "night_close_end_hour": ("int", 0, 23),
    "night_close_min_quiet_minutes": ("int", 5, 720),
    "photo_archive_enabled": ("bool", None, None),
    "agent_attachment_max_count": ("int", 1, 20),
    "agent_text_attachment_max_chars": ("int", 10000, 2000000),
    "agent_document_attachment_max_bytes": ("int", 1048576, 104857600),
    "agent_pdf_max_pages": ("int", 1, 1000),
    "agent_document_vision_max_pages": ("int", 1, 50),
    "web_search_enabled": ("bool", None, None),
    "web_search_max_results": ("int", 1, 20),
    "web_search_timeout_seconds": ("int", 3, 60),
    "web_page_max_chars": ("int", 500, 50000),
    "timezone": ("str", 1, 80),
    "day_boundary_hour": ("int", 0, 23),
    "persona_prompt_path": ("path", 1, 1000),
    "runtime_summary_path": ("path", 1, 1000),
    "personal_manual_path": ("path", 1, 1000),
    "talent_manual_path": ("path", 1, 1000),
    "napcat_dir": ("path", 1, 1000),
    "napcat_webui_url": ("url", 0, 500),
    "voice_training_dir": ("path", 1, 1000),
    "local_vision_dir": ("path", 1, 1000),
}

RUNTIME_PATH_FIELDS = frozenset(
    key for key, (kind, _minimum, _maximum) in RUNTIME_SETTING_SPECS.items() if kind == "path"
)


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    app_port: int = _env_int("MIO_PORT", 8000)
    backend_dir: Path = BACKEND_DIR
    app_dir: Path = APP_DIR
    workspace_root: Path = WORKSPACE_ROOT
    source_workspace_root: Path = SOURCE_WORKSPACE_ROOT
    data_dir: Path = PROJECT_ROOT / "数据"
    runtime_config_path: Path = PROJECT_ROOT / "数据" / "运行设置.json"
    diary_dir: Path = PROJECT_ROOT / "数据" / "日记"
    db_path: Path = PROJECT_ROOT / "数据" / "personal_ai.db"
    mio_profile_path: Path = PROJECT_ROOT / "数据" / "澪属性.json"
    mio_avatar_path: Path = PROJECT_ROOT / "数据" / "澪头像.png"
    user_avatar_path: Path = PROJECT_ROOT / "数据" / "用户头像.png"
    chat_background_path: Path = PROJECT_ROOT / "数据" / "对话背景.jpg"
    templates_dir: Path = APP_DIR / "templates"
    static_dir: Path = APP_DIR / "static"
    site_custom_dir: Path = PROJECT_ROOT / "澪_日记网站"
    agent_frontend_dir: Path = Path(
        os.getenv("MIO_AGENT_FRONTEND_DIR", "").strip()
        or (BUNDLE_ROOT / "agent_frontend" if getattr(sys, "frozen", False) else SOURCE_WORKSPACE_ROOT / "澪Agent应用" / "dist")
    )
    agent_control_scripts_dir: Path = Path(
        os.getenv("MIO_AGENT_SCRIPTS_DIR", "").strip()
        or (BUNDLE_ROOT / "agent_scripts" if getattr(sys, "frozen", False) else SOURCE_WORKSPACE_ROOT / "澪Agent应用" / "scripts")
    )

    persona_prompt_path: Path = Path(
        _env("PERSONA_PROMPT_PATH") or WORKSPACE_ROOT / "澪_私人AI人格设定与提示词.md"
    )
    runtime_summary_path: Path = Path(
        _env("RUNTIME_SUMMARY_PATH") or WORKSPACE_ROOT / "澪运行时说明书.md"
    )
    personal_manual_path: Path = Path(
        _env("PERSONAL_MANUAL_PATH") or WORKSPACE_ROOT / "个人说明书.txt"
    )
    talent_manual_path: Path = Path(
        _env("TALENT_MANUAL_PATH") or WORKSPACE_ROOT / "个人天赋使用说明书.txt"
    )

    openai_base_url: str = _env("OPENAI_BASE_URL")
    openai_base_urls: tuple[str, ...] = _openai_base_urls()
    openai_api_key: str = _env("OPENAI_API_KEY")
    openai_model: str = _env("OPENAI_MODEL")
    openai_models: tuple[str, ...] = _openai_models()
    openai_vision_models: tuple[str, ...] = _env_list("OPENAI_VISION_MODELS")
    openai_timeout_seconds: int = _env_int("OPENAI_TIMEOUT_SECONDS", 60)
    # One user request may try multiple provider routes, but it must still
    # have a bounded wall-clock budget.
    openai_request_deadline_seconds: int = _env_int("OPENAI_REQUEST_DEADLINE_SECONDS", 120)
    screen_reaction_timeout_seconds: int = _env_int("SCREEN_REACTION_TIMEOUT_SECONDS", 18)
    screen_history_retention_days: int = max(
        1,
        min(365, _env_int("SCREEN_HISTORY_RETENTION_DAYS", 30)),
    )
    screen_history_max_rows: int = max(
        1000,
        min(200000, _env_int("SCREEN_HISTORY_MAX_ROWS", 20000)),
    )
    openai_input_price_cny_per_million: float = _env_float("OPENAI_INPUT_PRICE_CNY_PER_MILLION", 0.0)
    openai_output_price_cny_per_million: float = _env_float("OPENAI_OUTPUT_PRICE_CNY_PER_MILLION", 0.0)
    openai_proxy_url: str = _default_proxy_url()
    openai_proxy_mode: str = _env("OPENAI_PROXY_MODE", "auto").lower()
    manual_max_chars: int = _env_int("MANUAL_MAX_CHARS", 6000)
    chat_temperature: float = _env_float("CHAT_TEMPERATURE", 0.7)
    action_planner_temperature: float = _env_float("ACTION_PLANNER_TEMPERATURE", 0.1)
    chat_history_limit: int = _env_int("CHAT_HISTORY_LIMIT", 80)
    chat_raw_history_limit: int = _env_int("CHAT_RAW_HISTORY_LIMIT", 160)
    chat_context_max_chars: int = _env_int("CHAT_CONTEXT_MAX_CHARS", 18000)
    # 字符预算继续用于兼容旧设置；token 预算用于判断真实上下文压力。
    chat_context_max_tokens: int = _env_int("CHAT_CONTEXT_MAX_TOKENS", 18000)
    chat_context_warning_ratio: float = _env_float("CHAT_CONTEXT_WARNING_RATIO", 0.75)
    chat_context_compress_ratio: float = _env_float("CHAT_CONTEXT_COMPRESS_RATIO", 0.82)
    chat_recent_keep_messages: int = _env_int("CHAT_RECENT_KEEP_MESSAGES", 24)
    memory_context_days: int = _env_int("MEMORY_CONTEXT_DAYS", 7)
    memory_context_max_chars: int = _env_int("MEMORY_CONTEXT_MAX_CHARS", 5000)
    memory_context_messages_per_day: int = _env_int("MEMORY_CONTEXT_MESSAGES_PER_DAY", 4)
    timezone: str = _env("TIMEZONE", "Asia/Shanghai")
    day_boundary_hour: int = min(23, max(0, _env_int("DAY_BOUNDARY_HOUR", 4)))
    qq_bot_enabled: bool = _env_bool("QQ_BOT_ENABLED", False)
    napcat_dir: Path = Path(_env("NAPCAT_DIR") or WORKSPACE_ROOT / "NapCat")
    napcat_webui_url: str = _env("NAPCAT_WEBUI_URL", "http://127.0.0.1:6099").rstrip("/")
    napcat_account: str = _env("NAPCAT_ACCOUNT") or _QQ_CHANNEL_SETUP.get("account", "")
    qq_onebot_token: str = _env("QQ_ONEBOT_TOKEN") or _QQ_CHANNEL_SETUP.get("onebot_token", "")
    qq_channel_config_path: Path = PROJECT_ROOT / "数据" / "QQ通道设置.json"
    qq_allowed_user_ids: tuple[str, ...] = _env_list("QQ_ALLOWED_USER_IDS")
    qq_allowed_group_ids: tuple[str, ...] = _env_list("QQ_ALLOWED_GROUP_IDS")
    qq_group_mention_required: bool = _env_bool("QQ_GROUP_MENTION_REQUIRED", True)
    qq_group_config_path: Path = PROJECT_ROOT / "数据" / "QQ群聊设置.json"
    qq_image_enabled: bool = _env_bool("QQ_IMAGE_ENABLED", True)
    qq_image_max_count: int = _env_int("QQ_IMAGE_MAX_COUNT", 3)
    qq_image_max_bytes: int = _env_int("QQ_IMAGE_MAX_BYTES", 8 * 1024 * 1024)
    qq_image_detail: str = _env("QQ_IMAGE_DETAIL", "auto")
    qq_image_send_to_model: bool = _env_bool("QQ_IMAGE_SEND_TO_MODEL", False)
    qq_message_debounce_seconds: float = _env_float("QQ_MESSAGE_DEBOUNCE_SECONDS", 3.5)
    qq_message_incomplete_debounce_seconds: float = _env_float("QQ_MESSAGE_INCOMPLETE_DEBOUNCE_SECONDS", 7.0)
    chat_follow_up_capture_seconds: float = _env_float("CHAT_FOLLOW_UP_CAPTURE_SECONDS", 4.0)
    chat_follow_up_max_capture_count: int = _env_int("CHAT_FOLLOW_UP_MAX_CAPTURE_COUNT", 2)
    qq_delivery_ack_timeout_seconds: float = _env_float("QQ_DELIVERY_ACK_TIMEOUT_SECONDS", 8.0)
    qq_delivery_max_retries: int = _env_int("QQ_DELIVERY_MAX_RETRIES", 1)
    qq_reply_initial_delay_seconds: float = _env_float("QQ_REPLY_INITIAL_DELAY_SECONDS", 2.2)
    qq_reply_delay_seconds: float = _env_float("QQ_REPLY_DELAY_SECONDS", 1.2)
    qq_proactive_enabled: bool = _env_bool("QQ_PROACTIVE_ENABLED", False)
    qq_proactive_min_idle_minutes: int = _env_int("QQ_PROACTIVE_MIN_IDLE_MINUTES", 120)
    qq_proactive_max_idle_minutes: int = _env_int("QQ_PROACTIVE_MAX_IDLE_MINUTES", 120)
    qq_proactive_day_start_hour: int = _env_int("QQ_PROACTIVE_DAY_START_HOUR", 9)
    qq_proactive_day_end_hour: int = _env_int("QQ_PROACTIVE_DAY_END_HOUR", 22)
    qq_proactive_check_seconds: int = _env_int("QQ_PROACTIVE_CHECK_SECONDS", 300)
    web_search_enabled: bool = _env_bool("WEB_SEARCH_ENABLED", False)
    web_search_max_results: int = _env_int("WEB_SEARCH_MAX_RESULTS", 5)
    web_search_timeout_seconds: int = _env_int("WEB_SEARCH_TIMEOUT_SECONDS", 12)
    web_page_max_chars: int = _env_int("WEB_PAGE_MAX_CHARS", 4000)
    daily_review_auto_enabled: bool = _env_bool("DAILY_REVIEW_AUTO_ENABLED", False)
    daily_review_auto_hour: int = _env_int("DAILY_REVIEW_AUTO_HOUR", 9)
    daily_review_auto_minute: int = _env_int("DAILY_REVIEW_AUTO_MINUTE", 0)
    daily_review_auto_notify_qq: bool = _env_bool("DAILY_REVIEW_AUTO_NOTIFY_QQ", False)
    daily_review_check_seconds: int = _env_int("DAILY_REVIEW_CHECK_SECONDS", 600)
    daily_diary_auto_enabled: bool = _env_bool("DAILY_DIARY_AUTO_ENABLED", False)
    daily_diary_check_seconds: int = _env_int("DAILY_DIARY_CHECK_SECONDS", 60)
    backup_enabled: bool = _env_bool("BACKUP_ENABLED", True)
    backup_keep_count: int = _env_int("BACKUP_KEEP_COUNT", 14)
    backup_check_seconds: int = _env_int("BACKUP_CHECK_SECONDS", 3600)
    weekly_review_enabled: bool = _env_bool("WEEKLY_REVIEW_ENABLED", False)
    weekly_review_hour: int = _env_int("WEEKLY_REVIEW_HOUR", 9)
    weekly_review_notify_qq: bool = _env_bool("WEEKLY_REVIEW_NOTIFY_QQ", False)
    weekly_review_check_seconds: int = _env_int("WEEKLY_REVIEW_CHECK_SECONDS", 3600)
    monthly_review_enabled: bool = _env_bool("MONTHLY_REVIEW_ENABLED", False)
    monthly_review_hour: int = _env_int("MONTHLY_REVIEW_HOUR", 10)
    monthly_review_notify_qq: bool = _env_bool("MONTHLY_REVIEW_NOTIFY_QQ", False)
    monthly_review_check_seconds: int = _env_int("MONTHLY_REVIEW_CHECK_SECONDS", 3600)
    night_close_enabled: bool = _env_bool("NIGHT_CLOSE_ENABLED", True)
    night_close_start_hour: int = _env_int("NIGHT_CLOSE_START_HOUR", 23)
    night_close_end_hour: int = _env_int("NIGHT_CLOSE_END_HOUR", 1)
    night_close_min_quiet_minutes: int = _env_int("NIGHT_CLOSE_MIN_QUIET_MINUTES", 45)
    photo_archive_enabled: bool = _env_bool("PHOTO_ARCHIVE_ENABLED", True)
    photo_dir: Path = PROJECT_ROOT / "数据" / "照片"
    agent_attachment_dir: Path = PROJECT_ROOT / "数据" / "Agent附件"
    model_profiles_path: Path = PROJECT_ROOT / "数据" / "模型供应商.json"
    agent_attachment_max_count: int = _env_int("AGENT_ATTACHMENT_MAX_COUNT", 5)
    agent_text_attachment_max_chars: int = _env_int("AGENT_TEXT_ATTACHMENT_MAX_CHARS", 200000)
    agent_document_attachment_max_bytes: int = _env_int(
        "AGENT_DOCUMENT_ATTACHMENT_MAX_BYTES", 20 * 1024 * 1024
    )
    agent_pdf_max_pages: int = _env_int("AGENT_PDF_MAX_PAGES", 200)
    agent_document_vision_max_pages: int = _env_int("AGENT_DOCUMENT_VISION_MAX_PAGES", 8)
    companion_dir: Path = PROJECT_ROOT / "数据" / "桌宠"
    companion_config_path: Path = PROJECT_ROOT / "数据" / "桌宠" / "设置.json"
    companion_avatar_path: Path = PROJECT_ROOT / "数据" / "桌宠" / "头像.png"
    companion_sprite_dir: Path = PROJECT_ROOT / "数据" / "桌宠" / "动作"
    companion_game_preview_path: Path = PROJECT_ROOT / "数据" / "桌宠" / "游戏预览.jpg"
    voice_training_dir: Path = Path(
        _env("MIO_VOICE_TRAINING_DIR") or WORKSPACE_ROOT / "音色训练"
    )
    local_vision_dir: Path = Path(
        _env("MIO_LOCAL_VISION_DIR") or WORKSPACE_ROOT / "本地视觉"
    )
    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.diary_dir.mkdir(parents=True, exist_ok=True)
        self.site_custom_dir.mkdir(parents=True, exist_ok=True)
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self.agent_attachment_dir.mkdir(parents=True, exist_ok=True)
        self.companion_dir.mkdir(parents=True, exist_ok=True)
        self.companion_sprite_dir.mkdir(parents=True, exist_ok=True)
        self.local_vision_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()


def _coerce_runtime_setting(key: str, value: object) -> Any:
    if key not in RUNTIME_SETTING_SPECS:
        raise ValueError(f"不支持的运行设置：{key}")
    kind, minimum, maximum = RUNTIME_SETTING_SPECS[key]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on", "启用", "是"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off", "禁用", "否"}:
            return False
        raise ValueError(f"{key} 必须是开关值。")
    if kind == "csv":
        source = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
        items: list[str] = []
        for item in source:
            clean = str(item).strip()
            if clean and clean not in items:
                items.append(clean)
        comparable = len(",".join(items))
        if minimum is not None and comparable < minimum:
            raise ValueError(f"{key} 低于允许范围。")
        if maximum is not None and comparable > maximum:
            raise ValueError(f"{key} 超出允许范围。")
        return tuple(items)
    if kind == "int":
        try:
            normalized: Any = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是整数。") from exc
    elif kind == "float":
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是数字。") from exc
    else:
        normalized = str(value or "").strip()
    comparable = len(normalized) if isinstance(normalized, str) else normalized
    if minimum is not None and comparable < minimum:
        raise ValueError(f"{key} 低于允许范围。")
    if maximum is not None and comparable > maximum:
        raise ValueError(f"{key} 超出允许范围。")
    if kind == "url" and normalized and not normalized.startswith(("http://", "https://")):
        raise ValueError(f"{key} 必须以 http:// 或 https:// 开头。")
    if key == "qq_image_detail" and normalized not in {"low", "auto", "high"}:
        raise ValueError("qq_image_detail 只能是 low、auto 或 high。")
    if key == "timezone":
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone 必须是有效的 IANA 时区，例如 Asia/Shanghai。") from exc
    if kind == "path":
        path = Path(normalized).expanduser()
        if not path.is_absolute():
            path = settings.project_root / path
        return path.resolve()
    return normalized


def _runtime_settings_payload() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key in RUNTIME_SETTING_SPECS:
        value = getattr(settings, key)
        if isinstance(value, Path):
            values[key] = str(value)
        elif isinstance(value, tuple):
            values[key] = ",".join(str(item) for item in value)
        else:
            values[key] = value
    return values


def _normalize_runtime_settings(values: dict[str, object]) -> dict[str, Any]:
    normalized = {key: _coerce_runtime_setting(key, value) for key, value in values.items()}
    minimum_idle = int(normalized.get("qq_proactive_min_idle_minutes", settings.qq_proactive_min_idle_minutes))
    maximum_idle = int(normalized.get("qq_proactive_max_idle_minutes", settings.qq_proactive_max_idle_minutes))
    if maximum_idle < minimum_idle:
        raise ValueError("主动消息最长等待时间不能短于最短等待时间。")
    raw_history_limit = int(normalized.get("chat_raw_history_limit", settings.chat_raw_history_limit))
    history_limit = int(normalized.get("chat_history_limit", settings.chat_history_limit))
    recent_keep = int(normalized.get("chat_recent_keep_messages", settings.chat_recent_keep_messages))
    if history_limit > raw_history_limit:
        raise ValueError("显示历史消息条数不能超过原始历史上限。")
    if recent_keep > raw_history_limit:
        raise ValueError("压缩后保留消息数不能超过原始历史上限。")
    return normalized


def _apply_runtime_settings(values: dict[str, object]) -> dict[str, Any]:
    normalized = _normalize_runtime_settings(values)
    for key, value in normalized.items():
        object.__setattr__(settings, key, value)
    return _runtime_settings_payload()


def load_runtime_settings() -> dict[str, Any]:
    try:
        saved = json.loads(settings.runtime_config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        saved = {}
    if isinstance(saved, dict):
        valid: dict[str, Any] = {}
        for key, value in saved.items():
            if key not in RUNTIME_SETTING_SPECS:
                continue
            try:
                valid[key] = _coerce_runtime_setting(key, value)
            except ValueError:
                continue
        # Frozen builds own their optional voice runtime under the selected
        # data root.  A runtime settings file may have been copied from an
        # older source checkout, so never let that stale absolute path win
        # over the launcher's resolved installation path.
        configured_voice_root = os.getenv("MIO_VOICE_TRAINING_DIR", "").strip()
        if configured_voice_root:
            valid["voice_training_dir"] = Path(configured_voice_root).expanduser().resolve()
        minimum_idle = int(valid.get("qq_proactive_min_idle_minutes", settings.qq_proactive_min_idle_minutes))
        maximum_idle = int(valid.get("qq_proactive_max_idle_minutes", settings.qq_proactive_max_idle_minutes))
        if maximum_idle < minimum_idle:
            valid.pop("qq_proactive_min_idle_minutes", None)
            valid.pop("qq_proactive_max_idle_minutes", None)
        raw_history_limit = int(valid.get("chat_raw_history_limit", settings.chat_raw_history_limit))
        history_limit = int(valid.get("chat_history_limit", settings.chat_history_limit))
        recent_keep = int(valid.get("chat_recent_keep_messages", settings.chat_recent_keep_messages))
        if history_limit > raw_history_limit:
            valid.pop("chat_history_limit", None)
            valid.pop("chat_raw_history_limit", None)
        elif recent_keep > raw_history_limit:
            valid.pop("chat_recent_keep_messages", None)
        try:
            _apply_runtime_settings(valid)
        except ValueError:
            # A future cross-field rule must not make an older local config
            # prevent the application from starting.
            for key, value in valid.items():
                try:
                    _apply_runtime_settings({key: value})
                except ValueError:
                    continue
    return _runtime_settings_payload()


def save_runtime_settings(changes: dict[str, object]) -> dict[str, Any]:
    if not isinstance(changes, dict):
        raise ValueError("运行设置必须是对象。")
    unknown = sorted(set(changes) - set(RUNTIME_SETTING_SPECS))
    if unknown:
        raise ValueError(f"不支持的运行设置：{unknown[0]}")
    proposed: dict[str, object] = _runtime_settings_payload()
    proposed.update(changes)
    normalized = _normalize_runtime_settings(proposed)
    serializable = {
        key: str(value) if isinstance(value, Path) else ",".join(value) if isinstance(value, tuple) else value
        for key, value in normalized.items()
    }
    settings.runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.runtime_config_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(serializable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(settings.runtime_config_path)
    return _apply_runtime_settings(normalized)


load_runtime_settings()
