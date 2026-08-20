from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import companion_service, db, local_vision_service, pet_event_service, system_audio_service
from .auto_router import select_auto_route
from .chat_service import ChatResult, replies_for_source
from .config import settings
from .cost_reconciliation_service import queue_cost_reconciliation
from .llm import (
    CompletionResult,
    call_chat_completion_result,
)
from .model_registry import ModelProfile, list_model_profiles
from .mio_profile import load_mio_profile
from .screen_behavior_service import (
    BehaviorDecision,
    Observation,
    decide_behavior,
    game_state_summary,
    is_duplicate_summary,
    is_new_event_occurrence,
    normalize_event_type,
    update_game_state,
)
from .screen_frame_processor import process_frame


_state_lock = threading.Lock()
_last_analyzed_frame_id = 0
_last_analysis_monotonic = 0.0
_last_analyzed_at = ""
_last_reply = ""
_last_error = ""
_last_model = ""
_last_reaction_model = ""
_last_cost_yuan: float | None = None
_last_event_type = ""
_last_event_summary = ""
_last_event_priority = 0.0
_last_event_confidence = 0.0
_last_emotion = "neutral"
_last_game_state: dict[str, Any] = {}
_last_pipeline_timings: dict[str, float | str | bool] = {}
_last_spoken_monotonic = 0.0
_capture_only = False
_in_progress = False
_session_started_at = db.now_iso()
_session_request_count = 0
_session_generation_count = 0
_session_cost_yuan = 0.0
_session_unknown_cost_count = 0
_session_attempt_count = 0
_session_failure_count = 0
_cloud_profile_health: dict[str, dict[str, Any]] = {}
CLOUD_PROFILE_BASE_COOLDOWN_SECONDS = 30.0
CLOUD_PROFILE_MAX_COOLDOWN_SECONDS = 300.0
DESKTOP_PET_CONVERSATION_ID = "desktop_pet"
# 游戏窗口需要比整屏观察更敏感：对白框、选项和立绘变化通常只占画面一小块。
# 这只影响“何时送视觉请求”，不会让普通桌面整屏观察变成高频上传。
GAME_WINDOW_CHANGE_FACTOR = 0.6
GAME_WINDOW_MIN_CHANGE_THRESHOLD = 2.0
GAME_EVENT_REPLY_TYPES = {
    "activity_progress",
    "interesting_content",
    "progress",
    "victory",
    "achievement",
    "rare_reward",
    "match_result",
    "death",
    "failure",
    "boss_battle",
    "boss_phase",
    "low_health",
    "danger",
    "warning",
    "error",
    "stuck",
    "dialogue_choice",
    "cutscene_turn",
}
GAME_EVENT_TYPES = {
    "gameplay",
    "movement",
    "exploration",
    "death",
    "failure",
    "victory",
    "boss_battle",
    "boss_phase",
    "low_health",
    "danger",
    "rare_reward",
    "achievement",
    "match_result",
    "dialogue_choice",
    "cutscene_turn",
}
SCREEN_FOLLOW_UP_RE = re.compile(
    r"^(?:"
    r"\u73b0\u5728\u5462|\u73b0\u5728\u600e\u4e48\u6837|\u7136\u540e\u5462|\u63a5\u4e0b\u6765\u5462|"
    r"\u8fd9\u4e2a\u5462|\u90a3\u8fd9\u4e2a\u5462|\u518d\u770b\u770b|\u4f60\u518d\u770b(?:\u770b)?|"
    r"\u73b0\u5728\u770b\u5230\u4e86\u5417|\u73b0\u5728\u80fd\u770b\u5230\u4e86\u5417|"
    r"\u8fd8\u662f\u770b\u4e0d\u89c1|\u8fd8\u80fd\u770b\u5230\u5417|\u6709\u753b\u9762\u5417|"
    r"\u753b\u9762\u5462|\u5c4f\u5e55\u5462|\u89c6\u89c9\u529f\u80fd(?:\u5462|\u600e\u4e48\u4e86|\u6709\u53cd\u5e94\u5417)?"
    r")[\uFF0C\u3002\uFF01\uFF1F!?/\\\s]*$"
)
SCREEN_CONTEXT_INTENT_RE = re.compile(
    r"(?:看(?:看|一下)?(?:现在|当前)?(?:的)?(?:屏幕|画面)|"
    r"(?:屏幕|画面)(?:上|里|中)?(?:是|有|变成|显示)|"
    r"你看现在|看得到(?:屏幕|画面)吗|视觉功能|画面识别|屏幕观察)"
)
SCREEN_FOLLOW_UP_CONTEXT_SECONDS = 5 * 60
# A screen follow-up is an explicit user request about what is visible now.
# Never answer it from an old preview or from the ordinary text-chat model.
MAX_SCREEN_FOLLOW_UP_FRAME_AGE_SECONDS = 8.0
LOCAL_SCREEN_SAFE_SUMMARIES = {
    "activity_change": "用户切换了当前活动",
    "activity_progress": "用户当前做的事情有了新的进展",
    "activity_pause": "用户当前做的事情暂时停了下来",
    "interesting_content": "用户当前屏幕出现了值得随口回应的内容",
    "watching_game_video": "用户正在观看游戏相关视频",
    "watching_video": "用户正在观看视频内容",
    "watching_movie": "用户正在观看电影、番剧或剧情内容",
    "coding": "用户正在编写或调试代码",
    "writing": "用户正在写作或编辑文档",
    "reading": "用户正在阅读内容",
    "browsing": "用户正在浏览网页",
    "gameplay": "用户正在进行一段普通游戏过程",
    "progress": "用户当前正在做的事情有了进展",
    "notable_scene": "用户屏幕上出现了值得回应的变化",
    "warning": "用户当前使用的程序出现了提醒",
    "error": "用户当前使用的程序似乎遇到了问题",
    "loading": "用户正在等待内容加载",
}


def _screen_persona_context() -> str:
    try:
        profile = load_mio_profile()
    except Exception:
        return "Mio 与用户的关系和称呼由本机属性定义；自然、直接地回应，不编造共同经历。"
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    speaking = profile.get("speaking_style") if isinstance(profile.get("speaking_style"), dict) else {}
    preferences = profile.get("preferences") if isinstance(profile.get("preferences"), dict) else {}
    parts = [
        f"身份：{str(identity.get('core') or '').strip()}",
        f"语气：{str(speaking.get('tone') or '').strip()}",
        f"用户称呼：{str(preferences.get('user_address') or '').strip()}",
        f"关系距离：{str(preferences.get('relationship_distance') or '').strip()}",
    ]
    return "\n".join(part for part in parts if not part.endswith("："))[:1800]


@dataclass(frozen=True)
class CombinedVisionResult:
    observation: Observation
    should_reply: bool
    importance: float
    emotion: str
    reply: str
    reason: str


def _fallback_reaction(observation: Observation) -> str:
    replies = {
        "victory": "赢下来了，刚才那一下挺漂亮的",
        "match_result": "这一局结束了，先缓口气吧",
        "failure": "差一点，缓一下再来",
        "death": "没事，下一次会更顺一点",
        "warning": "画面好像在提醒什么，先看一眼吧",
        "error": "好像出了点问题，先看看提示吧",
        "loading": "还在加载，我陪你等一下",
        "boss_battle": "这段要认真一点了",
        "dialogue_choice": "这个选择得想一下",
        "progress": "有进展了，继续吧",
        "activity_progress": "有进展了，刚才那一步挺顺的",
        "activity_pause": "先停一下也好，别把自己绷得太紧",
        "interesting_content": "这个还挺有意思的",
        "watching_game_video": "这段操作还挺稳的",
        "watching_video": "这个片段还挺有意思的",
        "watching_movie": "这段气氛一下子起来了",
        "coding": "这一步跑通了，顺多了",
        "writing": "这一段比刚才顺多了",
        "reading": "这段好像挺值得慢慢看的",
        "browsing": "这个页面还挺有意思的",
        "menu": "准备好就出发吧",
    }
    return replies.get(observation.event_type, "我看到了，继续吧")


def _desktop_pet_conversation_id() -> str:
    return DESKTOP_PET_CONVERSATION_ID


def _recent_context(conversation_id: str, *, limit: int = 8, max_chars: int = 220) -> str:
    rows = db.get_recent_messages(limit=max(1, min(8, int(limit))), conversation_id=conversation_id)
    lines: list[str] = []
    for row in rows:
        role = "用户" if str(row["role"] or "") == "user" else "Mio"
        content = " ".join(str(row["content"] or "").split()).strip()
        if content:
            lines.append(f"{role}：{content[:max(40, min(220, int(max_chars)))]}")
    return "\n".join(lines) or "最近没有对话。"


def _configured_profiles():
    return [profile for profile in list_model_profiles() if profile.base_urls and profile.api_key]


def _vision_profiles():
    return [profile for profile in _configured_profiles() if profile.supports_vision]


def _vision_latency_rank(profile: ModelProfile) -> tuple[int, float, str]:
    name = f"{getattr(profile, 'model', '')} {getattr(profile, 'variant_name', '')}".lower()
    marker_rank = 4
    for marker, rank in (
        ("flash", 0),
        ("luna", 1),
        ("mini", 2),
        ("turbo", 2),
        ("sol", 5),
        ("pro", 6),
    ):
        if marker in name:
            marker_rank = rank
            break
    price = float(getattr(profile, "input_price_cny_per_million", 0) or 0) + float(
        getattr(profile, "output_price_cny_per_million", 0) or 0
    )
    return marker_rank, price if price > 0 else 1_000_000.0, str(profile.id)


def _selected_vision_profile(config: dict[str, Any], profiles: list[ModelProfile]) -> ModelProfile:
    requested = str(config.get("screen_vision_model_id") or "auto-fast").strip()
    if requested not in {"", "auto", "auto-fast"}:
        selected = next((profile for profile in profiles if profile.id == requested), None)
        if selected is not None:
            return selected
    if not profiles:
        raise ValueError("没有配置可识图的模型。")
    return min(profiles, key=_vision_latency_rank)


def _ordered_vision_profiles(config: dict[str, Any], profiles: list[ModelProfile]) -> list[ModelProfile]:
    if not profiles:
        return []
    primary = _selected_vision_profile(config, profiles)
    remaining = sorted(
        (profile for profile in profiles if profile.id != primary.id),
        key=_vision_latency_rank,
    )
    return [primary, *remaining]


def _healthy_vision_profile(
    config: dict[str, Any],
    profiles: list[ModelProfile],
    *,
    now: float | None = None,
) -> ModelProfile | None:
    checked_at = time.monotonic() if now is None else float(now)
    with _state_lock:
        cooldowns = {
            profile_id: float(state.get("cooldown_until") or 0.0)
            for profile_id, state in _cloud_profile_health.items()
        }
    return next(
        (
            profile
            for profile in _ordered_vision_profiles(config, profiles)
            if cooldowns.get(profile.id, 0.0) <= checked_at
        ),
        None,
    )


def _mark_cloud_profile_success(profile_id: str) -> None:
    if not profile_id:
        return
    with _state_lock:
        _cloud_profile_health[profile_id] = {
            "consecutive_failures": 0,
            "cooldown_until": 0.0,
            "last_error": "",
            "last_success_at": db.now_iso(),
        }


def _mark_cloud_profile_failure(profile_id: str, error: str) -> float:
    if not profile_id:
        return 0.0
    now = time.monotonic()
    with _state_lock:
        previous = dict(_cloud_profile_health.get(profile_id) or {})
        failures = max(0, int(previous.get("consecutive_failures") or 0)) + 1
        cooldown = min(
            CLOUD_PROFILE_MAX_COOLDOWN_SECONDS,
            CLOUD_PROFILE_BASE_COOLDOWN_SECONDS * (2 ** min(3, failures - 1)),
        )
        previous.update(
            {
                "consecutive_failures": failures,
                "cooldown_until": now + cooldown,
                "last_error": " ".join(str(error or "视觉请求失败").split())[:500],
                "last_failure_at": db.now_iso(),
            }
        )
        _cloud_profile_health[profile_id] = previous
    return cooldown


def _cloud_health_status(profiles: list[ModelProfile]) -> list[dict[str, Any]]:
    now = time.monotonic()
    with _state_lock:
        states = {key: dict(value) for key, value in _cloud_profile_health.items()}
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        state = states.get(profile.id) or {}
        cooldown_remaining = max(0.0, float(state.get("cooldown_until") or 0.0) - now)
        rows.append(
            {
                "id": profile.id,
                "label": str(getattr(profile, "display_name", "") or getattr(profile, "model", "") or profile.id),
                "consecutive_failures": max(0, int(state.get("consecutive_failures") or 0)),
                "cooldown_remaining_seconds": round(cooldown_remaining, 1),
                "available_now": cooldown_remaining <= 0,
                "last_error": str(state.get("last_error") or ""),
                "last_failure_at": str(state.get("last_failure_at") or ""),
                "last_success_at": str(state.get("last_success_at") or ""),
            }
        )
    return rows


def _observation_lines(rows: list[Any]) -> str:
    lines: list[str] = []
    for row in rows:
        try:
            summary = " ".join(str(row["summary"] or "").split())
            event_type = str(row["event_type"] or "unknown")
            confidence = float(row["confidence"] or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if summary:
            lines.append(f"- {event_type}（{confidence:.0%}）：{summary[:160]}")
    return "\n".join(lines) or "- 暂无"


def _foreground_details() -> dict[str, Any]:
    try:
        foreground = pet_event_service.status().get("foreground") or {}
    except (AttributeError, RuntimeError, TypeError):
        return {"title": "", "process_name": "", "process_id": 0}
    return {
        "title": " ".join(str(foreground.get("title") or "").split())[:300],
        "process_name": " ".join(str(foreground.get("process_name") or "").split())[:260],
        "process_id": int(foreground.get("process_id") or 0),
    }


def _foreground_context() -> str:
    foreground = _foreground_details()
    title = str(foreground["title"] or "")
    process_name = str(foreground["process_name"] or "")
    if process_name and title:
        return f"{process_name} | {title}"
    return process_name or title or "未记录"


_SELF_PROCESS_NAMES = {"mio.exe", "mioagent.exe"}
_SELF_WINDOW_MARKERS = ("mio agent", "mio", "澪", "桌宠")


def _is_self_ui_foreground(frame: dict[str, Any] | None = None) -> bool:
    """Return whether full-screen observation is currently looking at Mio itself."""
    foreground = _foreground_details()
    process_name = str(foreground.get("process_name") or "").strip().casefold()
    title = " ".join(str(foreground.get("title") or "").split()).casefold()
    frame_title = " ".join(str((frame or {}).get("title") or "").split()).casefold()
    if process_name in _SELF_PROCESS_NAMES:
        return True
    combined = f"{title} {frame_title}".strip()
    return any(marker.casefold() in combined for marker in _SELF_WINDOW_MARKERS)


def _audio_context() -> str:
    transcript = system_audio_service.recent_transcript(max_age_seconds=24)
    return transcript or "最近没有识别到清晰台词或系统声音"


BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
}
VIDEO_PLATFORM_MARKERS = (
    "哔哩哔哩", "bilibili", "youtube", "腾讯视频", "爱奇艺", "优酷", "芒果tv",
    "netflix", "disney+", "prime video",
)
MEDIA_PLAYER_PROCESSES = {
    "potplayermini64.exe", "potplayer.exe", "vlc.exe", "mpv.exe", "mpc-hc64.exe",
    "moviesandtv.exe", "wmplayer.exe",
}
CODE_EDITOR_MARKERS = (
    "code.exe", "devenv.exe", "idea64.exe", "pycharm64.exe", "rider64.exe", "unity.exe",
    "visual studio code", "visual studio", "pycharm", "intellij idea", "cursor",
)
DOCUMENT_EDITOR_MARKERS = (
    "winword.exe", "wps.exe", "et.exe", "powerpnt.exe", "notepad.exe", "notepad++.exe",
    "microsoft word", "wps office", "obsidian", "typora",
)
GAME_EVENTS = {
    "gameplay", "movement", "exploration", "death", "failure", "victory", "progress",
    "boss_battle", "boss_phase", "low_health", "danger", "rare_reward", "achievement",
    "match_result", "stuck", "dialogue_choice", "cutscene_turn",
}


def _activity_context(frame: dict[str, Any] | None = None) -> dict[str, str]:
    foreground = _foreground_details()
    process_name = str(foreground["process_name"] or "").casefold()
    foreground_title = str(foreground["title"] or "")
    frame_title = " ".join(str((frame or {}).get("title") or "").split())[:300]
    combined = f"{process_name} {foreground_title} {frame_title}".casefold()
    kind = "application"
    guidance = "结合前台应用和画面共同判断当前活动，不要只根据画面中的内容下结论。"
    if any(marker.casefold() in combined for marker in VIDEO_PLATFORM_MARKERS):
        kind = "video_platform"
        guidance = (
            "当前是视频网站或视频应用。即使画面里出现游戏，也应判断为用户在看游戏视频，"
            "不能说成用户正在玩；电影、番剧或剧情视频应判断为观看剧情内容。"
        )
    elif process_name in MEDIA_PLAYER_PROCESSES:
        kind = "media_player"
        guidance = (
            "当前是本地媒体播放器。画面中的游戏通常是录播，人物和场景通常是影片内容；"
            "除非有明确交互证据，否则不要判断为用户正在玩游戏。"
        )
    elif process_name in BROWSER_PROCESSES:
        kind = "browser"
        guidance = (
            "当前是浏览器。先判断用户是在浏览网页、看视频、阅读还是使用网页工具；"
            "仅凭网页里出现游戏画面，不能判断用户正在玩游戏。"
        )
    elif any(marker in combined for marker in CODE_EDITOR_MARKERS):
        kind = "code_editor"
        guidance = "当前是代码或开发工具，优先识别编写代码、调试、运行和报错等活动。"
    elif any(marker in combined for marker in DOCUMENT_EDITOR_MARKERS):
        kind = "document_editor"
        guidance = "当前是文档或写作工具，优先识别写作、编辑、整理和阅读等活动。"
    return {
        "kind": kind,
        "process_name": str(foreground["process_name"] or ""),
        "foreground_title": foreground_title,
        "frame_title": frame_title,
        "guidance": guidance,
    }


def _correct_observation_for_activity(
    observation: Observation,
    frame: dict[str, Any],
) -> Observation:
    context = _activity_context(frame)
    if context["kind"] not in {"video_platform", "media_player"}:
        return observation
    text = f"{observation.summary} {' '.join(observation.tags)}".casefold()
    movie_markers = ("电影", "影片", "剧情", "角色", "番剧", "动漫", "电视剧", "镜头")
    if observation.event_type in GAME_EVENTS or observation.game_name:
        event_type = "watching_game_video"
        summary = "用户正在视频应用中观看包含游戏画面的内容"
    elif any(marker in text for marker in movie_markers):
        event_type = "watching_movie"
        summary = observation.summary
    elif observation.event_type in {"idle", "unknown", "notable_scene", "interesting_content"}:
        event_type = "watching_video"
        summary = observation.summary
    else:
        return observation
    details = dict(observation.details)
    details["activity_kind"] = context["kind"]
    return Observation(
        event_type=event_type,
        summary=summary,
        confidence=observation.confidence,
        game_name="",
        details=details,
        tags=observation.tags,
    )


def _cursor_prompt_context(frame: dict[str, Any]) -> str:
    cursor = frame.get("cursor")
    if not isinstance(cursor, dict) or not cursor.get("available"):
        return "当前鼠标位置：未获取"
    if str(frame.get("mode") or "") != "window":
        return "当前鼠标位置：已获取系统坐标，但整屏观察不据此推断操作结果"
    if not cursor.get("inside_capture"):
        return "当前鼠标位置：在所选窗口外"
    try:
        x_percent = max(0, min(100, round(float(cursor.get("relative_x")) * 100)))
        y_percent = max(0, min(100, round(float(cursor.get("relative_y")) * 100)))
    except (TypeError, ValueError):
        return "当前鼠标位置：位于所选窗口内，具体位置不可用"
    return (
        f"当前鼠标位置：所选窗口内横向约 {x_percent}%，纵向约 {y_percent}%。"
        "这只是用户关注或操作区域的弱证据，不能单独推断点击结果；"
        "如果游戏使用画面内自绘光标，以视觉内容为准。"
    )


def _compact_combined_prompt(
    frame: dict[str, Any],
    recent_observations: list[Any],
    state: dict[str, Any],
    frame_metadata: dict[str, Any],
    conversation_id: str,
    *,
    seconds_since_last_speech: float,
) -> str:
    is_window = str(frame.get("mode") or "") == "window"
    activity = _activity_context(frame)
    activity_rule = (
        "这是用户指定的单个窗口，先判断是游戏、工作、学习、创作、阅读、视频还是普通软件；游戏按低频陪玩处理，不要把指定窗口一律当成游戏。"
        if is_window
        else "这是日常桌面陪伴，用户可能在工作、学习、写作、画画、编程、阅读、看视频或使用普通软件。"
    )
    privacy_rule = (
        "云端整屏识别不得转录聊天、通知、账号、文件名、路径、网址、密钥或验证码，只概括活动和事件。"
        if not is_window
        else "不要转录与当前活动无关的隐私文字。"
    )
    return f"""你负责看懂当前画面，并替 Mio 决定此刻是否自然开口。一次完成识别与回复判断，只返回 JSON。
Mio 的身份与语气：{_screen_persona_context()[:600]}
画面：{'指定窗口' if is_window else '整个屏幕'}；标题：{frame.get('title') or '未记录'}；变化：{float(frame.get('change_percent') or 0):.1f}%；尺寸：{frame_metadata.get('width', '?')}x{frame_metadata.get('height', '?')}
前台应用：{activity['process_name'] or '未记录'}；前台窗口：{activity['foreground_title'] or frame.get('title') or '未记录'}；应用类型：{activity['kind']}
应用判断：{activity['guidance'][:320]}
{_cursor_prompt_context(frame)}
当前状态：{game_state_summary(state)[:500]}
最近观察：{_observation_lines(recent_observations[-4:])[:600]}
最近对话：{_recent_context(conversation_id, limit=4, max_chars=120)[:650]}
最近系统声音台词：{_audio_context()[:240]}
距离上次开口：约 {max(0, int(seconds_since_last_speech))} 秒

规则：
1. {activity_rule} 先判断用户正在操作什么应用，再解释画面内容。
2. 浏览器或播放器里的游戏画面是 watching_game_video，不是用户正在玩；电影和剧情视频是 watching_movie。不要把视频台词当成用户对你说话。
3. 只选一个可确认事件，不猜游戏名、结果或剧情。重复画面、普通移动和无具体内容时保持安静。
4. 指定游戏窗口中，新的对白、对白选项、立绘/镜头推进、剧情进展、胜负、危险或卡住都属于值得回应的事件；这类事件应设置 should_reply=true，并给出 1-2 句短口语。普通移动和连续相同画面仍保持安静。
5. 其他应用只有在新进展、卡住、警告、错误、结果或确实值得回应的内容出现时 should_reply=true。不要播报画面，不说“我看到”。
5. {privacy_rule}

输出字段必须完整：
{{"event":"idle|movement|exploration|gameplay|activity_change|activity_progress|activity_pause|interesting_content|watching_game_video|watching_video|watching_movie|coding|writing|reading|browsing|menu|loading|death|failure|victory|progress|boss_battle|boss_phase|low_health|danger|rare_reward|achievement|match_result|stuck|dialogue_choice|cutscene_turn|error|warning|notable_scene|unknown","summary":"最多50字","confidence":0.0,"game":"","state":{{"boss":"","location":"","phase":"","objective":"","mode":"","outcome":"","health_status":""}},"tags":[],"should_reply":false,"importance":0.0,"emotion":"neutral|gentle|cheerful|concerned|serious|shy","reply":"无需说话时为空","reason":"简短原因"}}"""


def _combined_analysis_messages(
    frame: dict[str, Any],
    recent_observations: list[Any],
    state: dict[str, Any],
    frame_metadata: dict[str, Any],
    conversation_id: str,
    *,
    wake: bool = False,
    interactive: bool = False,
    seconds_since_last_speech: float = 1_000_000,
) -> list[dict[str, Any]]:
    image_url = "data:image/jpeg;base64," + base64.b64encode(frame["content"]).decode("ascii")
    cursor_context = _cursor_prompt_context(frame)
    is_window = str(frame.get("mode") or "") == "window"
    observation_kind = "指定窗口" if is_window else "整个屏幕"
    privacy_rule = ""
    if not is_window:
        privacy_rule = (
            "这是用户主动选择的云端整屏识别。不要转录聊天、通知、账号、文件名、路径、网址、"
            "密钥、验证码或其他原文，只概括活动和可回应事件。"
        )
    wake_rule = (
        "这是桌宠刚启动后的第一次观察。只要画面可辨识，就自然说一句简短问候或随口反应。"
        if wake
        else (
            "用户刚刚用‘现在呢’一类短句明确追问当前画面。必须根据这一次的新画面直接回答，"
            "should_reply=true，reply 不得为空；不要回答看不到，也不要要求用户重新开启观察。"
            if interactive
            else "重复画面或没有具体可说内容时保持安静，不要为了达到频率硬找话题。"
        )
    )
    activity = _activity_context(frame)
    self_ui_rule = (
        "The foreground may be Mio's own chat or pet window. Ignore Mio's old chat text, bubbles, subtitles, and pet content; never treat them as the user's current activity."
        if _is_self_ui_foreground(frame)
        else "If Mio's own interface appears in the image, treat it only as an overlay and never use its historical text as current activity."
    )
    rhythm_rule = (
        (
            "这是用户指定的单个窗口，可能是游戏，也可能是工作、学习、创作、阅读、视频或普通软件。"
            "先根据画面主体判断活动类型：游戏按低频陪玩处理，非游戏按日常桌面陪伴处理。"
            "有明确的新步骤、进展、卡住、等待或值得回应的内容时，可以自然说一句；不要把指定窗口一律当成游戏。"
        )
        if is_window
        else (
            "这是日常桌面陪伴，不只是在陪用户玩游戏。用户可能在工作、学习、写作、画画、"
            "写代码、整理文件、看视频、阅读或使用普通软件。出现新的步骤、进展、卡住、等待、"
            "有趣内容或明显任务切换时，可以自然说一句；不要把非游戏活动一律判成无需回复。"
        )
    )
    game_reply_rule = (
        "指定游戏窗口的对白、选项、立绘或镜头推进、剧情进展、胜负、危险和卡住都应视为可回应的新事件；"
        "只要当前帧能确认，就设置 should_reply=true 并给出 1-2 句短口语。普通移动、待机和重复画面仍保持安静。"
        if is_window
        else "整屏观察继续保持克制，只有明确的新进展、错误、结果或值得回应的内容才开口。"
    )
    persona_context = _screen_persona_context()
    detailed_prompt = f"""你同时负责看懂画面，并替 Mio 决定此刻是否自然开口。一次完成，不要再把任务交给另一个模型。

Mio 当前属性摘要：
{persona_context}

画面类型：{observation_kind}
窗口标题：{frame.get('title') or observation_kind}
捕获时间：{frame.get('captured_at') or '刚刚'}
自上次分析后的最大变化：{float(frame.get('change_percent') or 0):.1f}%
处理后尺寸：{frame_metadata.get('width', '?')}x{frame_metadata.get('height', '?')}

当前状态：
{game_state_summary(state)}

当前前台应用：{activity['process_name'] or '未记录'}
当前前台窗口：{activity['foreground_title'] or frame.get('title') or '未记录'}
        {self_ui_rule}
        {cursor_context}
应用类型判断：{activity['kind']}
应用判断提示：{activity['guidance']}
最近系统声音台词：{_audio_context()}
距离上次开口：约 {max(0, int(seconds_since_last_speech))} 秒

最近观察：
{_observation_lines(recent_observations)}

最近对话：
{_recent_context(conversation_id)}

{privacy_rule}
{wake_rule}
{rhythm_rule}
{game_reply_rule}

先客观识别一个明确的事件，再根据 Mio 当前属性判断她此刻是否真的会说话。
必须先判断用户当前是在操作什么应用，再解释画面内容。浏览器、哔哩哔哩、视频网站或播放器里出现游戏画面时，
优先判断为 watching_game_video，不能说成用户正在玩游戏；电影、番剧和剧情视频使用 watching_movie。
用户确实在运行原生游戏并操作时才使用 gameplay 等游戏事件。写代码、写作、阅读和浏览分别使用 coding、writing、reading、browsing。
系统声音台词只作为画面的补充证据：结合前台应用和画面理解人物正在说什么，不要把视频台词当成用户对你说话。
台词中可能混入 Mio 刚刚播放的语音；与最近对话中 Mio 的话相同或近似时忽略它。
Mio 要按当前属性里定义的关系和语气回应用户，不是旁白、客服或攻略机器人。不要播报画面，不要说截图、识图、模型或内部判断。
需要回应时只说 1-2 句短口语，每句尽量不超过 28 个字；不要复述用户正在做什么，不要用“我看到了”“我注意到”。
观看电影或番剧时，可以只围绕当前明确的剧情、角色行为、镜头或气氛讨论一句；不要剧透，不要猜没有出现的情节。
不确定游戏名、Boss、位置和结果时留空，不要猜。情绪只能使用 neutral、gentle、cheerful、concerned、serious、shy。

只输出一个 JSON 对象，不要代码块，字段必须完整：
{{
  "event": "idle、movement、exploration、gameplay、activity_change、activity_progress、activity_pause、interesting_content、watching_game_video、watching_video、watching_movie、coding、writing、reading、browsing、menu、loading、death、failure、victory、progress、boss_battle、boss_phase、low_health、danger、rare_reward、achievement、match_result、stuck、dialogue_choice、cutscene_turn、error、warning、notable_scene 或 unknown",
  "summary": "客观画面摘要，最多 60 字",
  "confidence": 0.0,
  "game": "能确认时填写，否则空字符串",
  "state": {{"boss":"","location":"","phase":"","objective":"","mode":"","outcome":"","health_status":""}},
  "tags": [],
  "should_reply": false,
  "importance": 0.0,
  "emotion": "neutral",
  "reply": "不需要说话时为空字符串",
  "reason": "一句简短原因"
}}"""
    prompt = detailed_prompt if (wake or interactive) else _compact_combined_prompt(
        frame,
        recent_observations,
        state,
        frame_metadata,
        conversation_id,
        seconds_since_last_speech=seconds_since_last_speech,
    )
    return [
        {
            "role": "system",
            "content": (
                "你是 Mio 的低延迟多模态感知与反应核心。你必须在一次响应中完成客观识别、"
                "开口判断和自然短回复，并严格返回 JSON。"
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
            ],
        },
    ]


def _analysis_messages(
    frame: dict[str, Any],
    recent_observations: list[Any] | None = None,
    state: dict[str, Any] | None = None,
    frame_metadata: dict[str, Any] | None = None,
    *,
    local_vision: bool = True,
) -> list[dict[str, Any]]:
    image_url = "data:image/jpeg;base64," + base64.b64encode(frame["content"]).decode("ascii")
    cursor_context = _cursor_prompt_context(frame)
    is_window = str(frame.get("mode") or "") == "window"
    observation_kind = "指定窗口" if is_window else "整个屏幕"
    privacy_rule = ""
    if not is_window:
        privacy_rule = (
            ("这是本地隐私识别。" if local_vision else "这是用户主动选择的云端整屏识别。")
            + "不要转录屏幕上的聊天、通知、账号、文件名、路径、网址、密钥、验证码或其他原文；"
            "只概括用户活动和可回应事件。"
        )
    self_ui_rule = (
        "The foreground may be Mio's own chat or pet window. Ignore Mio's old chat text, bubbles, subtitles, and pet content; never treat them as the user's current activity."
        if _is_self_ui_foreground(frame)
        else "If Mio's own interface appears in the image, treat it only as an overlay and never use its historical text as current activity."
    )
    event_examples = (
        "idle、movement、exploration、gameplay、activity_change、activity_progress、activity_pause、"
        "interesting_content、watching_game_video、watching_video、watching_movie、coding、writing、"
        "reading、browsing、menu、loading、death、failure、"
        "victory、progress、boss_battle、boss_phase、low_health、danger、rare_reward、achievement、"
        "match_result、stuck、dialogue_choice、cutscene_turn、error、warning、notable_scene、unknown"
    )
    event_examples = self_ui_rule + "\n" + event_examples
    metadata = frame_metadata or {}
    activity = _activity_context(frame)
    prompt = f"""识别当前画面最明确的一件事，只返回 JSON。
画面：{observation_kind}；标题：{frame.get('title') or observation_kind}；变化：{float(frame.get('change_percent') or 0):.1f}%；尺寸：{metadata.get('width', '?')}x{metadata.get('height', '?')}
前台：{activity['process_name'] or '未记录'} / {activity['foreground_title'] or frame.get('title') or '未记录'}；类型：{activity['kind']}
{cursor_context}
状态：{game_state_summary(state or {})[:360]}
最近：{_observation_lines((recent_observations or [])[-3:])[:360]}
声音：{_audio_context()[:180]}

规则：
1. 先判断应用，再判断内容。原生游戏才用 gameplay；网页或播放器中的游戏用 watching_game_video，番剧/剧情视频用 watching_movie。
2. Galgame 对白或立绘推进属于 gameplay/cutscene_turn；出现可选项用 dialogue_choice。普通移动、静止或只有小动画用 movement/idle。
3. 工作成果用 activity_progress；等待或卡住用 activity_pause；代码、写作、阅读、网页分别用 coding、writing、reading、browsing。
4. 系统声音只作辅助，不能当用户对 Mio 说话。不要猜游戏名、剧情、结果。{privacy_rule}

事件可选：{event_examples}
JSON：{{"event":"idle","summary":"最多60字","confidence":0.0,"game":"","state":{{"boss":"","location":"","phase":"","objective":"","mode":"","outcome":"","health_status":""}},"tags":[]}}"""
    return [
        {
            "role": "system",
            "content": (
                "你是严格的视觉事件识别器。只描述可确认的事实，返回结构化 JSON。"
                "你不扮演角色，不输出建议，不决定是否回应用户。"
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]


def _local_analysis_parts(messages: list[dict[str, Any]]) -> tuple[str, str]:
    system_prompt = str(messages[0].get("content") or "")
    user_content = messages[1].get("content")
    if not isinstance(user_content, list):
        raise ValueError("本地视觉提示词格式不正确")
    text_item = next(
        (item for item in user_content if isinstance(item, dict) and item.get("type") == "text"),
        None,
    )
    if not isinstance(text_item, dict):
        raise ValueError("本地视觉提示词缺少文字内容")
    return system_prompt, str(text_item.get("text") or "")


def _safe_observation_for_cloud(observation: Observation) -> Observation:
    return Observation(
        event_type=observation.event_type,
        summary=LOCAL_SCREEN_SAFE_SUMMARIES.get(
            observation.event_type,
            "用户当前屏幕出现了一处值得回应的变化",
        ),
        confidence=observation.confidence,
        game_name="",
        details={},
        tags=(),
    )


def _clean_reply(content: str) -> str:
    clean = str(content or "").strip()
    clean = re.sub(r"```(?:text)?|```", "", clean, flags=re.IGNORECASE).strip()
    if not clean or re.fullmatch(r"(?:NO_REPLY|不回复|无需回复)[。.!！\s]*", clean, re.IGNORECASE):
        return ""
    return clean


def _parse_observation(content: str) -> Observation:
    clean = str(content or "").strip()
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end <= start:
        legacy = _clean_reply(clean)
        return Observation(
            event_type="notable_scene" if legacy else "idle",
            summary=legacy[:200] if legacy else "画面没有明确的新事件",
            confidence=0.75 if legacy else 0.9,
            game_name="",
            details={},
            tags=(),
        )
    try:
        payload = json.loads(clean[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("视觉模型返回的观察 JSON 无法读取") from exc
    if not isinstance(payload, dict):
        raise ValueError("视觉模型返回的观察不是对象")
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    details = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    event_type = normalize_event_type(str(payload.get("event") or "unknown"))
    summary = " ".join(str(payload.get("summary") or "画面事件不明确").split())[:240]
    return Observation(
        event_type=event_type,
        summary=summary,
        confidence=confidence,
        game_name=" ".join(str(payload.get("game") or "").split())[:160],
        details={str(key): value for key, value in details.items()},
        tags=tuple(" ".join(str(item).split())[:40] for item in tags[:5] if str(item).strip()),
    )


def _parse_combined_vision_result(content: str) -> CombinedVisionResult:
    clean = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        str(content or "").strip(),
        flags=re.IGNORECASE,
    ).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("多模态模型没有返回可读取的 JSON")
    try:
        payload = json.loads(clean[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("多模态模型返回的 JSON 无法读取") from exc
    if not isinstance(payload, dict):
        raise ValueError("多模态模型返回的结果不是对象")
    observation = _parse_observation(json.dumps(payload, ensure_ascii=False))
    try:
        importance = max(0.0, min(1.0, float(payload.get("importance") or 0)))
    except (TypeError, ValueError):
        importance = 0.0
    emotion = str(payload.get("emotion") or "neutral").strip().lower()
    if emotion not in {"neutral", "gentle", "cheerful", "concerned", "serious", "shy"}:
        emotion = "neutral"
    should_reply = payload.get("should_reply") is True
    reply = _clean_reply(str(payload.get("reply") or ""))
    return CombinedVisionResult(
        observation=observation,
        should_reply=should_reply and bool(reply),
        importance=importance,
        emotion=emotion,
        reply=reply,
        reason=" ".join(str(payload.get("reason") or "").split())[:160],
    )


def _reaction_messages(
    observation: Observation,
    decision: Any,
    state: dict[str, Any],
    conversation_id: str,
) -> list[dict[str, str]]:
    activity = _activity_context()
    persona_context = _screen_persona_context()
    prompt = f"""你正和用户待在一起，眼前刚发生了一件值得开口的事。

Mio 当前属性摘要：
{persona_context}

客观观察：{observation.summary}
事件类型：{observation.event_type}
可信度：{observation.confidence:.0%}
行为层决定：{decision.reason}
连续次数：{decision.repeat_count}
当前活动状态：{game_state_summary(state)}
当前前台应用：{activity['process_name'] or '未记录'}
当前前台窗口：{activity['foreground_title'] or '未记录'}
应用类型判断：{activity['kind']}
应用判断提示：{activity['guidance']}
最近系统声音台词：{_audio_context()}

最近对话：
{_recent_context(conversation_id)}

先想清楚：按 Mio 当前属性定义的关系，一个就在用户旁边的熟人此刻真的会说什么。只挑一个最自然的反应，不要总结整幅画面。
可以是简短感受、轻微吐槽、关心、替用户高兴，或对眼前局面的随口反应；没有必要时不要追问。
只说 1-2 句短话，每句尽量不超过 28 个字。
不要报告截图、识图、事件类型、可信度或内部判断过程。
不要像攻略机器人逐项分析，不确定的玩法细节不要编造。
如果用户在看游戏视频，不得说成用户正在玩；如果在看电影或番剧，只讨论画面中已经明确出现的剧情、角色或气氛，不剧透也不猜后续。
可以结合刚听到的台词接剧情或人物反应，但不要逐句复读台词，也不要把视频中的话当成用户在和你说话。
不要用“我看到了”“看起来”“我注意到”“我会在旁边”“我会静静看着”“我会陪着你”来说明自己的功能或存在。
不要每次都先说“嗯”，不要复述“你正在做什么”。
只输出真正要对用户说的话，不要 Markdown，不要情绪标签。"""
    return [
        {
            "role": "system",
            "content": (
                "你是 Mio，具体身份、关系、称呼和语气以本轮提供的 Mio 当前属性摘要为准。"
                "你不是旁白、客服或攻略助手，像坐在用户旁边的人，只在值得开口时自然说一句。"
                "你不会对每个动作发表评论，也不会假装知道不确定的事情。"
            ),
        },
        {"role": "user", "content": prompt},
    ]


def _wake_reaction_messages(
    observation: Observation,
    state: dict[str, Any],
    conversation_id: str,
) -> list[dict[str, str]]:
    activity = _activity_context()
    persona_context = _screen_persona_context()
    prompt = f"""你刚刚随桌宠窗口一起醒来，第一眼看见了用户当前的电脑画面。

Mio 当前属性摘要：
{persona_context}

你看见的内容：{observation.summary}
当前画面状态：{game_state_summary(state)}
当前前台应用：{activity['process_name'] or '未记录'}
当前前台窗口：{activity['foreground_title'] or '未记录'}
应用类型判断：{activity['kind']}
应用判断提示：{activity['guidance']}
最近系统声音台词：{_audio_context()}

最近对话：
{_recent_context(conversation_id)}

先理解画面和时间感，再决定此刻最自然的一句话。优先回应眼前具体的细节，不要介绍自己的观察能力。
像熟人刚坐到旁边那样开口，可以随口接一句、轻微吐槽或简单问候，不要做场景播报。
只说 1-2 句简短口语，每句尽量不超过 28 个字。普通桌面也要自然开口，不要强行找话题。
不要说截图、识图、模型、事件、可信度、分析或“我醒了”。
不要用“我看到了”“你正在……”“我会在旁边”“我会静静看着”“我会陪着你”这类模板句。
不要每次都以“嗯”开头，也不要承诺会一直做什么。
不要描述自己的思考过程，不要 Markdown，不要情绪标签。"""
    return [
        {
            "role": "system",
            "content": (
                "你是刚刚来到用户桌面的 Mio，具体身份、关系、称呼和语气以本轮提供的 Mio 当前属性摘要为准。"
                "你能直接看见当前电脑画面，像陪在用户旁边的人一样自然说话。"
            ),
        },
        {"role": "user", "content": prompt},
    ]


def _budget_state(config: dict[str, Any]) -> dict[str, Any]:
    daily = db.get_screen_analysis_usage()
    daily_cost_limit = float(config.get("screen_daily_cost_limit_yuan", 5.0))
    with _state_lock:
        session_count = _session_request_count
        generation_count = _session_generation_count
        attempt_count = _session_attempt_count
        failure_count = _session_failure_count
    session_costs = db.get_screen_analysis_costs_since(_session_started_at)
    session_pending = int(session_costs["pending_request_count"]) + max(
        0,
        session_count - int(session_costs["tracked_request_count"]),
    )
    paused_reason = ""
    if float(daily["budget_cost_yuan"]) >= daily_cost_limit:
        paused_reason = f"今天的屏幕观察费用已达到 ¥{daily_cost_limit:.2f} 上限"
    return {
        "paused": bool(paused_reason),
        "paused_reason": paused_reason,
        "session_started_at": _session_started_at,
        "session_request_count": session_count,
        "session_generation_count": generation_count,
        "session_attempt_count": attempt_count,
        "session_failure_count": failure_count,
        "session_cost_yuan": round(float(session_costs["confirmed_cost_yuan"]), 6),
        "session_confirmed_request_count": int(session_costs["confirmed_request_count"]),
        "session_unknown_cost_count": session_pending,
        "daily": daily,
        "daily_cost_limit_yuan": daily_cost_limit,
    }


def _record_completion_usage(completion: Any, *, analysis: bool) -> None:
    global _session_request_count, _session_generation_count
    global _session_cost_yuan, _session_unknown_cost_count
    request_id = uuid.uuid4().hex
    cost_references = tuple(getattr(completion, "cost_references", ()) or ())
    cost_source = (
        "provider_reconciliation_pending"
        if cost_references
        else str(getattr(completion, "cost_source", "") or "")
    )
    db.record_screen_analysis_usage(
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens + completion.reasoning_tokens,
        cost_yuan=completion.cost_yuan,
        request_id=request_id,
        request_kind="analysis" if analysis else "reaction",
        model_id=str(getattr(completion, "model", "") or ""),
        cost_source=cost_source,
    )
    queue_cost_reconciliation(
        request_id,
        f"screen_analysis:{db.today_string()}",
        cost_references,
    )
    with _state_lock:
        if analysis:
            _session_request_count += 1
        else:
            _session_generation_count += 1
        if cost_source == "provider_reported" and completion.cost_yuan is not None:
            _session_cost_yuan += max(0.0, float(completion.cost_yuan))
        elif cost_source in {"local", "local_fallback"}:
            pass
        else:
            _session_unknown_cost_count += 1


def _combined_cost(*values: float | None) -> float | None:
    return sum(float(value) for value in values) if all(value is not None for value in values) else None


def status() -> dict[str, Any]:
    config = companion_service.load_config()
    profiles = _vision_profiles()
    observer_status = companion_service.window_observer.status()
    local_status = local_vision_service.status()
    is_window = str(observer_status.get("mode") or "screen") == "window"
    uses_local_vision = str(config.get("screen_vision_route") or "local") == "local"
    configured_profile = None if uses_local_vision or not profiles else _selected_vision_profile(config, profiles)
    selected_profile = None if uses_local_vision or not profiles else _healthy_vision_profile(config, profiles)
    budget = _budget_state(config)
    cloud_health = _cloud_health_status(profiles)
    with _state_lock:
        return {
            "enabled": bool(config.get("screen_ai_enabled", True)),
            "capture_only": _capture_only,
            "capture": dict(observer_status),
            "vision_available": (
                bool(local_status.get("runtime_installed") and local_status.get("model_installed"))
                if uses_local_vision
                else bool(profiles)
            ),
            "vision_models": [profile.id for profile in profiles],
            "vision_model_options": [
                {
                    "id": profile.id,
                    "label": profile.display_name,
                    "model": profile.model,
                    "low_latency_rank": _vision_latency_rank(profile)[0],
                }
                for profile in sorted(profiles, key=_vision_latency_rank)
            ],
            "selected_vision_model_id": (
                local_vision_service.DEFAULT_MODEL if uses_local_vision else (selected_profile.id if selected_profile else "")
            ),
            "configured_vision_model_id": (
                local_vision_service.DEFAULT_MODEL
                if uses_local_vision
                else (configured_profile.id if configured_profile else "")
            ),
            "using_fallback_vision_model": bool(
                configured_profile and selected_profile and configured_profile.id != selected_profile.id
            ),
            "vision_route": "local" if uses_local_vision else "cloud",
            "vision_route_label": (
                "本地视觉 · 图片不上传"
                if uses_local_vision
                else ("云端视觉 · 仅游戏窗口" if is_window else "云端视觉 · 上传当前屏幕")
            ),
            "local_vision": local_status,
            "cloud_profile_health": cloud_health,
            "in_progress": _in_progress,
            "last_analyzed_at": _last_analyzed_at,
            "last_reply": _last_reply,
            "last_error": _last_error,
            "capture_diagnostic": (
                "捕获进程未运行"
                if not bool(observer_status.get("running"))
                else str(observer_status.get("capture_health") or "捕获状态未知")
            ),
            "last_model": _last_model,
            "last_reaction_model": _last_reaction_model,
            "last_cost_yuan": _last_cost_yuan,
            "last_event_type": _last_event_type,
            "last_event_summary": _last_event_summary,
            "last_event_importance": _last_event_priority,
            "last_event_confidence": _last_event_confidence,
            "last_emotion": _last_emotion,
            "game_state": dict(_last_game_state),
            "pipeline_timings": dict(_last_pipeline_timings),
            "change_threshold": float(config.get("screen_change_threshold", 8.0)),
            "effective_change_threshold": round(
                _effective_change_threshold(config, observer_status),
                2,
            ),
            "minimum_interval_seconds": int(config.get("screen_analysis_interval_seconds", 5)),
            "request_timeout_seconds": int(config.get("screen_request_timeout_seconds", 25)),
            "voice_cooldown_seconds": int(config.get("screen_voice_cooldown_seconds", 5)),
            "minimum_importance": float(config.get("screen_minimum_importance", 0.62)),
            "budget": budget,
        }


def runtime_health_status() -> dict[str, Any]:
    """Return observation health without model, file, or network probes."""
    config = companion_service.load_config()
    observer_status = companion_service.window_observer.status()
    with _state_lock:
        return {
            "enabled": bool(config.get("screen_ai_enabled", True)),
            "capture": observer_status,
            "analysis_in_progress": _in_progress,
            "last_analyzed_at": _last_analyzed_at,
            "last_error": _last_error,
            "last_model": _last_model,
            "last_pipeline_timings": dict(_last_pipeline_timings),
            "session_attempt_count": _session_attempt_count,
            "session_failure_count": _session_failure_count,
        }


def _newer_frame_supersedes(
    analyzed_frame_id: int,
    observer_status: dict[str, Any],
    *,
    change_threshold: float,
) -> bool:
    return bool(
        int(observer_status.get("frame_id") or 0) > int(analyzed_frame_id)
        and float(observer_status.get("pending_change_percent") or 0)
        >= max(float(change_threshold), GAME_WINDOW_MIN_CHANGE_THRESHOLD)
    )


def _effective_change_threshold(config: dict[str, Any], observer_status: dict[str, Any]) -> float:
    """Return the capture gate for the current scope.

    A selected game window uses a lower gate because a dialogue box or a
    character portrait can change without materially changing the whole
    frame.  Full-screen observation keeps the configured value to control
    upload frequency and cost.
    """
    configured = max(1.0, float(config.get("screen_change_threshold", 8.0)))
    if str(observer_status.get("mode") or "screen") == "window":
        return max(
            GAME_WINDOW_MIN_CHANGE_THRESHOLD,
            configured * GAME_WINDOW_CHANGE_FACTOR,
        )
    return configured


def _game_event_can_open(
    observation: Observation,
    decision: BehaviorDecision,
    *,
    event_is_new: bool,
    recent_summaries: list[str],
    seconds_since_last_speech: float,
    cooldown_seconds: int,
) -> bool:
    """Prevent a conservative multimodal should_reply flag from silencing a
    clearly new game event that already passed deterministic behavior gates.

    The model still supplies the wording; this only decides whether a
    qualifying event is allowed to use that wording (or the local fallback).
    """
    is_game = bool(observation.game_name) or observation.event_type in GAME_EVENT_TYPES
    if not is_game or not event_is_new or observation.event_type not in GAME_EVENT_REPLY_TYPES:
        return False
    if not decision.should_speak:
        return False
    if observation.confidence < 0.5 or decision.priority < 0.5:
        return False
    if is_duplicate_summary(observation.summary, recent_summaries):
        return False
    # Dialogue/choice/cutscene changes should feel responsive; major events
    # continue to use the regular event policy and cooldown.
    minimum_gap = 8.0 if observation.event_type in {"dialogue_choice", "cutscene_turn"} else 12.0
    return seconds_since_last_speech >= max(minimum_gap, float(cooldown_seconds))


async def analyze_once(
    *,
    force: bool = False,
    wake: bool = False,
    interactive: bool = False,
    conversation_id: str = "",
    request_id: str = "",
    allow_direct_speech: bool | None = None,
) -> bool:
    global _in_progress, _last_analysis_monotonic, _last_analyzed_at
    global _last_analyzed_frame_id, _last_reply, _last_error, _last_model
    global _last_reaction_model, _last_cost_yuan, _last_event_type
    global _last_event_summary, _last_event_priority, _last_event_confidence
    global _last_emotion, _last_game_state, _last_spoken_monotonic
    global _last_pipeline_timings
    global _session_attempt_count, _session_failure_count

    pipeline_started = time.monotonic()
    config = companion_service.load_config()
    with _state_lock:
        capture_only = _capture_only
    if capture_only:
        return False
    # The privacy switch is authoritative even for explicit wake/manual calls.
    # A stale observation task must never turn a disabled cloud/local AI route
    # back into a model request after settings were changed.
    if not bool(config.get("screen_ai_enabled", True)):
        with _state_lock:
            _last_error = "屏幕 AI 已关闭；仅保留画面捕获，不会发起分析请求。"
        return False
    observer_status = companion_service.window_observer.status()
    if not force and not observer_status["running"]:
        return False
    budget = _budget_state(config)
    if budget["paused"]:
        with _state_lock:
            _last_error = f"AI 屏幕分析已暂停：{budget['paused_reason']}。本地画面接收仍在运行。"
        return False

    now = time.monotonic()
    minimum_interval = int(config.get("screen_analysis_interval_seconds", 5))
    with _state_lock:
        if _in_progress or (not force and now - _last_analysis_monotonic < minimum_interval):
            return False
        last_frame_id = _last_analyzed_frame_id
    pending_change = float(observer_status.get("pending_change_percent") or 0)
    change_threshold = _effective_change_threshold(config, observer_status)
    if not force and last_frame_id > 0 and pending_change < change_threshold:
        return False

    use_local_vision = str(config.get("screen_vision_route") or "local") == "local"
    profiles = _vision_profiles()
    if not use_local_vision and not profiles:
        with _state_lock:
            _last_error = "没有配置可识图的模型。请在模型设置中启用视觉能力。"
        return False
    selected_profile = None if use_local_vision else _healthy_vision_profile(config, profiles)
    if not use_local_vision and selected_profile is None:
        health = _cloud_health_status(profiles)
        remaining = min(
            (float(item["cooldown_remaining_seconds"]) for item in health if not item["available_now"]),
            default=CLOUD_PROFILE_BASE_COOLDOWN_SECONDS,
        )
        with _state_lock:
            _last_error = f"云端视觉模型暂时不可用，将在约 {max(1, int(round(remaining)))} 秒后重试。画面捕获仍在运行。"
        return False

    frame = companion_service.window_observer.claim_analysis_frame(last_frame_id)
    if frame is None:
        with _state_lock:
            _last_error = "当前没有取得有效的新画面；捕获器可能已过期、失败或目标窗口已最小化。"
            _last_pipeline_timings = {
                "frame_claimed": False,
                "frame_claim_failure": "no_fresh_frame",
            }
        return False
    frame_age_seconds = frame.get("frame_age_seconds")
    if frame_age_seconds is not None and float(frame_age_seconds) > MAX_SCREEN_FOLLOW_UP_FRAME_AGE_SECONDS:
        with _state_lock:
            _last_analyzed_frame_id = int(frame.get("frame_id") or 0)
            _last_error = (
                f"当前没有取得有效的新画面；最近一帧已过期 {float(frame_age_seconds):.1f} 秒。"
            )
            _last_pipeline_timings = {
                "frame_claimed": False,
                "frame_claim_failure": "stale_frame",
                "frame_age_seconds": round(float(frame_age_seconds), 3),
            }
        frame.clear()
        return False
    is_full_screen = str(frame.get("mode") or "screen") != "window"
    if is_full_screen and _is_self_ui_foreground(frame) and not (wake or interactive):
        # Full-screen capture includes Mio's own chat/WebView and Live2D windows.
        # Treating that UI as user activity feeds old chat text back into vision
        # and can cause stale story references and unsolicited speech.
        with _state_lock:
            _last_analyzed_frame_id = int(frame.get("frame_id") or 0)
            _last_analysis_monotonic = now
            _last_analyzed_at = str(frame.get("captured_at") or db.now_iso())
            _last_error = "已忽略 Mio 自身窗口；请切到要观察的应用"
            _last_pipeline_timings = {
                "self_ui_suppressed": True,
                "self_ui_process": _foreground_details().get("process_name", ""),
                "self_ui_title": _foreground_details().get("title", ""),
            }
        return False
    processed = process_frame(
        bytes(frame["content"]),
        max_width=768 if use_local_vision else 640,
        max_height=432 if use_local_vision else 360,
        jpeg_quality=72 if use_local_vision else 64,
    )
    frame["content"] = processed.content
    vision_route = None if use_local_vision else select_auto_route(
        "低延迟识别并回应当前屏幕事件" if is_full_screen else "低延迟识别并回应当前指定窗口事件",
        image_count=1,
        profiles=[selected_profile],
    )
    vision_model_id = local_vision_service.DEFAULT_MODEL if use_local_vision else vision_route.model_id
    conversation_id = str(conversation_id or "").strip() or _desktop_pet_conversation_id()
    session_id = db.start_screen_session(
        str(frame.get("mode") or "screen"),
        str(frame.get("title") or ""),
        vision_model_id,
    )
    recent_observations = db.recent_observations(session_id)
    current_state = db.get_game_session_state(session_id)
    with _state_lock:
        _in_progress = True
        _last_error = ""
        _last_analysis_monotonic = now
        _last_pipeline_timings = {
            "started_at": db.now_iso(),
            "single_multimodal_call": not use_local_vision,
            "streamed_model": False,
            "transport_mode": "local" if use_local_vision else "nonstream_json",
            "vision_model_id": vision_model_id,
            "configured_vision_model_id": (
                local_vision_service.DEFAULT_MODEL
                if use_local_vision
                else _selected_vision_profile(config, profiles).id
            ),
            "using_fallback_vision_model": bool(
                not use_local_vision
                and selected_profile
                and selected_profile.id != _selected_vision_profile(config, profiles).id
            ),
            "image_width": processed.width,
            "image_height": processed.height,
            "image_encoded_bytes": len(processed.content),
        }

    cloud_result_ready = bool(use_local_vision)
    try:
        request_timeout = max(5, int(config.get("screen_request_timeout_seconds", 25)))

        seconds_since_last_speech = (
            now - _last_spoken_monotonic if _last_spoken_monotonic else 1_000_000
        )
        with _state_lock:
            _session_attempt_count += 1
        if use_local_vision:
            analysis_messages = _analysis_messages(
                frame,
                recent_observations,
                current_state,
                processed.metadata(),
                local_vision=True,
            )
            system_prompt, prompt = _local_analysis_parts(analysis_messages)
            vision_completion = await asyncio.wait_for(
                local_vision_service.analyze_image(
                    prompt=prompt,
                    image=bytes(frame["content"]),
                    system_prompt=system_prompt,
                ),
                timeout=request_timeout,
            )
            combined_result = None
        else:
            analysis_messages = _combined_analysis_messages(
                frame,
                recent_observations,
                current_state,
                processed.metadata(),
            conversation_id,
            wake=wake,
            interactive=interactive,
            seconds_since_last_speech=seconds_since_last_speech,
            )
            vision_completion = await asyncio.wait_for(
                call_chat_completion_result(
                    analysis_messages,
                    temperature=0.45,
                    model_id=vision_route.model_id,
                    reasoning_level="low",
                    retry_attempts=1,
                ),
                timeout=request_timeout,
            )
            combined_result = _parse_combined_vision_result(vision_completion.content)
            _mark_cloud_profile_success(selected_profile.id)
            cloud_result_ready = True
        with _state_lock:
            first_token_ms = getattr(vision_completion, "first_token_latency_ms", None)
            if first_token_ms is not None:
                _last_pipeline_timings["first_token_seconds"] = round(
                    max(0.0, float(first_token_ms)) / 1000,
                    3,
                )
            _last_pipeline_timings["vision_seconds"] = round(
                max(0.0, time.monotonic() - pipeline_started),
                3,
            )
        _record_completion_usage(vision_completion, analysis=True)
        observation = (
            combined_result.observation
            if combined_result is not None
            else _parse_observation(vision_completion.content)
        )
        corrected_observation = _correct_observation_for_activity(observation, frame)
        if combined_result is not None and corrected_observation != observation:
            corrected_reply = (
                _fallback_reaction(corrected_observation)
                if combined_result.should_reply
                else ""
            )
            combined_result = CombinedVisionResult(
                observation=corrected_observation,
                should_reply=bool(corrected_reply),
                importance=combined_result.importance,
                emotion=combined_result.emotion,
                reply=corrected_reply,
                reason="根据前台应用纠正活动类型",
            )
        observation = corrected_observation
        if processed.nearly_blank and observation.event_type not in {"error", "warning"}:
            observation = Observation(
                event_type="black_screen",
                summary="画面接近全黑或没有可辨识内容",
                confidence=max(0.95, observation.confidence),
                game_name=observation.game_name,
                details=observation.details,
                tags=observation.tags,
            )

        occurred_at = str(frame.get("captured_at") or db.now_iso())
        event_is_new = is_new_event_occurrence(
            observation,
            recent_observations,
            occurred_at=occurred_at,
        )
        observation_id = db.save_observation(
            session_id=session_id,
            frame_id=int(frame["frame_id"]),
            game_name=observation.game_name,
            event_type=observation.event_type,
            summary=observation.summary,
            confidence=observation.confidence,
            details_json=json.dumps(
                {
                    "state": observation.details,
                    "tags": list(observation.tags),
                    "frame": processed.metadata(),
                    "event_is_new": event_is_new,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            source="vision",
            model_id=vision_completion.model,
            request_cost_yuan=vision_completion.cost_yuan,
            occurred_at=occurred_at,
        )
        updated_state = update_game_state(
            current_state,
            observation,
            count_occurrence=event_is_new,
        )
        db.upsert_game_session_state(
            session_id,
            str(updated_state.get("game_name") or observation.game_name),
            updated_state,
        )
        recent_summaries = [str(row["summary"] or "") for row in recent_observations]
        decision = decide_behavior(
            observation,
            updated_state,
            recent_summaries=recent_summaries,
            seconds_since_last_speech=seconds_since_last_speech,
            cooldown_seconds=int(config.get("screen_voice_cooldown_seconds", 5)),
            minimum_priority=float(config.get("screen_minimum_importance", 0.62)),
            event_is_new=event_is_new,
            force=force,
        )
        if combined_result is not None:
            model_emotion = combined_result.emotion or decision.emotion
            model_priority = max(decision.priority, combined_result.importance)
            game_reply_override = _game_event_can_open(
                observation,
                decision,
                event_is_new=event_is_new,
                recent_summaries=recent_summaries,
                seconds_since_last_speech=seconds_since_last_speech,
                cooldown_seconds=int(config.get("screen_voice_cooldown_seconds", 5)),
            )
            ambient_reply = (
                is_full_screen
                and combined_result.should_reply
                and bool(combined_result.reply)
                and combined_result.importance >= float(config.get("screen_minimum_importance", 0.62))
                and observation.confidence >= 0.5
                and seconds_since_last_speech >= int(config.get("screen_voice_cooldown_seconds", 5))
                and not processed.nearly_blank
                and (
                    not is_duplicate_summary(observation.summary, recent_summaries)
                    or seconds_since_last_speech >= max(
                        45,
                        int(config.get("screen_voice_cooldown_seconds", 5)) * 6,
                    )
                )
            )
            decision = BehaviorDecision(
                should_speak=ambient_reply or (
                    decision.should_speak
                    and (
                        combined_result.should_reply
                        or (bool(combined_result.reply) and decision.priority >= 0.66)
                        or game_reply_override
                        or wake
                    )
                ),
                priority=model_priority,
                emotion=model_emotion,
                reason=combined_result.reason or decision.reason,
                repeat_count=decision.repeat_count,
            )
        if wake:
            decision = BehaviorDecision(
                should_speak=True,
                priority=max(0.65, observation.confidence, decision.priority),
                emotion=(combined_result.emotion if combined_result is not None else decision.emotion),
                reason="桌宠启动后第一次观察屏幕",
                repeat_count=decision.repeat_count,
            )
        elif interactive:
            decision = BehaviorDecision(
                should_speak=True,
                priority=max(0.65, observation.confidence, decision.priority),
                emotion=(combined_result.emotion if combined_result is not None else decision.emotion),
                reason="用户在近期屏幕对话中明确追问当前画面",
                repeat_count=decision.repeat_count,
            )
        event_id = db.save_screen_event(
            session_id=session_id,
            frame_id=int(frame["frame_id"]),
            event_type=observation.event_type,
            event_summary=observation.summary,
            importance=decision.priority,
            should_speak=decision.should_speak,
            emotion=decision.emotion,
            change_percent=float(frame.get("change_percent") or 0),
            model_id=vision_completion.model,
            request_cost_yuan=vision_completion.cost_yuan,
            occurred_at=occurred_at,
            observation_id=observation_id,
            conversation_id=conversation_id,
        )
        motion_hint = {
            "victory": "celebrate",
            "achievement": "celebrate",
            "success": "celebrate",
            "death": "concern",
            "defeat": "concern",
            "warning": "alert",
            "error": "alert",
            "activity_change": "attention",
            "activity_progress": "attention",
            "activity_pause": "attention",
            "interesting_content": "observe",
            "gameplay": "observe",
        }.get(observation.event_type, "observe")
        pet_event_service.publish(
            "visual_event",
            {
                "event_id": event_id,
                "observation_id": observation_id,
                "frame_id": int(frame["frame_id"]),
                "mode": str(frame.get("mode") or "screen"),
                "title": str(frame.get("title") or "")[:300],
                "event_type": observation.event_type,
                "summary": observation.summary[:500],
                "importance": round(float(decision.priority), 4),
                "confidence": round(float(observation.confidence), 4),
                "emotion": decision.emotion,
                "motion_hint": motion_hint,
                "should_speak": bool(decision.should_speak),
                "event_is_new": bool(event_is_new),
                "occurred_at": occurred_at,
            },
        )
        with _state_lock:
            _last_analyzed_frame_id = int(frame["frame_id"])
            _last_analyzed_at = occurred_at
            _last_model = vision_completion.model
            _last_reaction_model = ""
            _last_cost_yuan = vision_completion.cost_yuan
            _last_event_type = observation.event_type
            _last_event_summary = observation.summary
            _last_event_priority = decision.priority
            _last_event_confidence = observation.confidence
            _last_emotion = decision.emotion
            _last_game_state = dict(updated_state)
        if not decision.should_speak and not interactive:
            return False
        current_observer = companion_service.window_observer.status()
        if not force and not current_observer["running"]:
            return False
        if not force:
            pending_change = float(current_observer.get("pending_change_percent") or 0)
            if _newer_frame_supersedes(
                int(frame["frame_id"]),
                current_observer,
                change_threshold=_effective_change_threshold(config, current_observer),
            ):
                with _state_lock:
                    _last_pipeline_timings["superseded_by_newer_frame"] = True
                    _last_pipeline_timings["superseded_change_percent"] = round(pending_change, 3)
                return False
        if wake and not companion_service.pet_running():
            return False

        if combined_result is not None:
            reaction_completion = CompletionResult(
                content=combined_result.reply or _fallback_reaction(observation),
                model=vision_completion.model,
                prompt_tokens=0,
                cached_prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=0,
                cost_yuan=0.0,
                cost_source="shared_multimodal_request",
            )
            reply = _clean_reply(combined_result.reply or _fallback_reaction(observation))
            with _state_lock:
                _last_pipeline_timings["reaction_seconds"] = 0.0
        else:
            reaction_route = select_auto_route(
                "分析当前情境和最近对话，生成一句自然、不机械的角色回应",
                profiles=_configured_profiles(),
            )
            reaction_observation = (
                _safe_observation_for_cloud(observation)
                if is_full_screen
                else observation
            )
            try:
                reaction_started = time.monotonic()
                reaction_completion = await asyncio.wait_for(
                    call_chat_completion_result(
                        (
                            _wake_reaction_messages(
                                reaction_observation,
                                {} if is_full_screen else updated_state,
                                conversation_id,
                            )
                            if wake
                            else _reaction_messages(
                                reaction_observation,
                                decision,
                                {} if is_full_screen else updated_state,
                                conversation_id,
                            )
                        ),
                        temperature=0.72,
                        model_id=reaction_route.model_id,
                        reasoning_level="low",
                    ),
                    timeout=max(5, settings.screen_reaction_timeout_seconds),
                )
                _record_completion_usage(reaction_completion, analysis=False)
                with _state_lock:
                    _last_pipeline_timings["reaction_seconds"] = round(
                        max(0.0, time.monotonic() - reaction_started),
                        3,
                    )
                    _last_error = ""
            except Exception as exc:
                reason = "生成屏幕回应超时" if isinstance(exc, asyncio.TimeoutError) else f"生成屏幕回应失败：{exc}"
                reaction_completion = CompletionResult(
                    content=_fallback_reaction(reaction_observation),
                    model="local-fallback",
                    prompt_tokens=0,
                    cached_prompt_tokens=0,
                    completion_tokens=0,
                    reasoning_tokens=0,
                    cost_yuan=0.0,
                    cost_source="local_fallback",
                )
                with _state_lock:
                    _last_pipeline_timings["reaction_seconds"] = round(
                        max(0.0, time.monotonic() - reaction_started),
                        3,
                    )
                    _last_error = f"{reason}，本轮已改用本地短回应；下一轮会继续尝试。"
            reply = _clean_reply(reaction_completion.content)
        if not reply:
            return False
        normalized = " ".join(reply.split())
        with _state_lock:
            if not wake and not interactive and normalized == " ".join(_last_reply.split()):
                return False
            _last_reply = reply

        total_cost = (
            vision_completion.cost_yuan
            if combined_result is not None
            else _combined_cost(vision_completion.cost_yuan, reaction_completion.cost_yuan)
        )
        request_id = str(request_id or "").strip() or uuid.uuid4().hex
        game_event_types = {
            "gameplay", "movement", "exploration", "death", "failure", "victory",
            "boss_battle", "boss_phase", "low_health", "danger", "rare_reward",
            "achievement", "match_result", "dialogue_choice", "cutscene_turn",
        }
        source_is_game = bool(observation.game_name) or observation.event_type in game_event_types
        message_source = (
            "desktop_pet_wake"
            if wake
            else ("game" if source_is_game else "screen")
        )
        parts = replies_for_source(reply, "desktop") or [reply]
        for index, part in enumerate(parts[:3]):
            db.save_message(
                "assistant",
                part,
                source=message_source,
                conversation_id=conversation_id,
                request_id=request_id,
                model_id=reaction_completion.model,
                reasoning_level=("off" if reaction_completion.model == "local-fallback" else "low"),
                prompt_tokens=(vision_completion.prompt_tokens + (0 if combined_result is not None else reaction_completion.prompt_tokens)) if index == 0 else 0,
                cached_prompt_tokens=(vision_completion.cached_prompt_tokens + (0 if combined_result is not None else reaction_completion.cached_prompt_tokens)) if index == 0 else 0,
                completion_tokens=(vision_completion.completion_tokens + (0 if combined_result is not None else reaction_completion.completion_tokens)) if index == 0 else 0,
                reasoning_tokens=(vision_completion.reasoning_tokens + (0 if combined_result is not None else reaction_completion.reasoning_tokens)) if index == 0 else 0,
                request_cost_yuan=total_cost if index == 0 else 0.0,
                request_cost_source=(reaction_completion.cost_source if index == 0 else "shared_request"),
                emotion=decision.emotion,
            )
        db.save_companion_reaction(
            screen_event_id=event_id,
            request_id=request_id,
            text=reply,
            emotion=decision.emotion,
            trigger_reason=decision.reason,
            model_id=reaction_completion.model,
            request_cost_yuan=reaction_completion.cost_yuan,
        )
        with _state_lock:
            _last_spoken_monotonic = now
            _last_reaction_model = reaction_completion.model
            _last_cost_yuan = total_cost
            _last_pipeline_timings["text_ready_seconds"] = round(
                max(0.0, time.monotonic() - pipeline_started),
                3,
            )

        direct_voice_enabled = bool(config.get("screen_direct_voice_enabled", True))
        should_direct_speak = bool(config.get("voice_enabled", True)) and direct_voice_enabled and (
            wake
            or (source_is_game and bool(config.get("speak_game_observations", True)))
            or (not source_is_game and bool(config.get("speak_screen_observations", False)))
        )
        if allow_direct_speech is not None:
            should_direct_speak = should_direct_speak and bool(allow_direct_speech)
        if should_direct_speak and not pet_event_service.has_clients():
            def mark_audio_started(tts_seconds: float) -> None:
                with _state_lock:
                    if _last_pipeline_timings.get("request_id") == request_id:
                        _last_pipeline_timings["tts_first_audio_seconds"] = round(
                            max(0.0, time.monotonic() - pipeline_started),
                            3,
                        )
                        _last_pipeline_timings["tts_generation_seconds"] = round(
                            max(0.0, float(tts_seconds)),
                            3,
                        )

            with _state_lock:
                _last_pipeline_timings["request_id"] = request_id
                _last_pipeline_timings["tts_streaming"] = bool(config.get("voice_streaming_enabled", True))
            companion_service.set_pet_activity(
                "speaking",
                emotion=decision.emotion,
                source=message_source,
                ttl_seconds=max(8, min(60, len(reply) / 3 + 10)),
            )
            companion_service.speak_text(
                reply,
                context=observation.summary,
                emotion=decision.emotion,
                streaming=True,
                on_audio_started=mark_audio_started,
                model_id=reaction_completion.model,
                language=str(config.get("pet_speech_language") or "zh"),
                source="screen",
            )
        return True
    except Exception as exc:
        error_text = (
            f"视觉请求超过 {max(5, int(config.get('screen_request_timeout_seconds', 25)))} 秒，已跳过本轮"
            if isinstance(exc, asyncio.TimeoutError)
            else str(exc)
        )
        cooldown_seconds = 0.0
        if not use_local_vision and selected_profile is not None and not cloud_result_ready:
            cooldown_seconds = _mark_cloud_profile_failure(selected_profile.id, error_text)
            with _state_lock:
                _session_failure_count += 1
        with _state_lock:
            _last_analyzed_frame_id = int(frame["frame_id"])
            _last_pipeline_timings["failed_seconds"] = round(
                max(0.0, time.monotonic() - pipeline_started),
                3,
            )
            _last_pipeline_timings["timed_out"] = isinstance(exc, asyncio.TimeoutError)
            if cooldown_seconds:
                _last_pipeline_timings["profile_cooldown_seconds"] = round(cooldown_seconds, 1)
            _last_error = (
                f"{error_text}。画面捕获会继续运行，且不会自动转发云端；请检查本地视觉模型。"
                if use_local_vision
                else f"{error_text}。该视觉模型已暂时冷却，下一观察周期会尝试可用备用模型。"
            )
        return False
    finally:
        frame.clear()
        with _state_lock:
            _in_progress = False


def is_screen_chat_follow_up(
    message: str,
    history_rows: list[Any] | tuple[Any, ...] = (),
    *,
    observation_status: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Return true only when a short deictic message continues recent screen context."""
    status_payload = observation_status if observation_status is not None else status()
    normalized_message = re.sub(
        r"[\uFF0C\u3002\uFF01\uFF1F!?/\\\s]+$",
        "",
        str(message or "").strip(),
    )
    recent_screen_context = False
    for row in list(history_rows)[-10:]:
        try:
            role = str(row["role"] or "")
            source = str(row["source"] or "")
            content = str(row["content"] or "")
        except (KeyError, TypeError, IndexError):
            role = str(getattr(row, "role", "") or "")
            source = str(getattr(row, "source", "") or "")
            content = str(getattr(row, "content", "") or "")
        if role == "assistant" and source in {"screen", "game", "desktop_pet_wake"}:
            recent_screen_context = True
            break
        if role == "user" and SCREEN_CONTEXT_INTENT_RE.search(content):
            recent_screen_context = True
            break
    if recent_screen_context and normalized_message in {"", "？", "?"}:
        return True
    if not SCREEN_FOLLOW_UP_RE.fullmatch(str(message or "").strip()):
        return bool(SCREEN_CONTEXT_INTENT_RE.search(str(message or "")))

    analyzed_at = str(status_payload.get("last_analyzed_at") or "").strip()
    if analyzed_at:
        try:
            analyzed_time = datetime.fromisoformat(analyzed_at)
            current_time = now or datetime.fromisoformat(db.now_iso())
            if analyzed_time.tzinfo is not None and current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=analyzed_time.tzinfo)
            elif analyzed_time.tzinfo is None and current_time.tzinfo is not None:
                analyzed_time = analyzed_time.replace(tzinfo=current_time.tzinfo)
            age_seconds = (current_time - analyzed_time).total_seconds()
            if 0 <= age_seconds <= SCREEN_FOLLOW_UP_CONTEXT_SECONDS:
                return True
        except ValueError:
            pass

    def history_id(row: Any) -> int:
        try:
            return int(row["id"] or 0)
        except (KeyError, TypeError, IndexError):
            return int(getattr(row, "id", 0) or 0)

    ordered_history = sorted(list(history_rows), key=history_id)
    for row in reversed(ordered_history[-8:]):
        try:
            source = str(row["source"] or "")
            role = str(row["role"] or "")
            content = str(row["content"] or "")
        except (KeyError, TypeError, IndexError):
            source = str(getattr(row, "source", "") or "")
            role = str(getattr(row, "role", "") or "")
            content = str(getattr(row, "content", "") or "")
        if role == "assistant" and source in {"screen", "game", "desktop_pet_wake"}:
            return True
        if role == "user" and SCREEN_CONTEXT_INTENT_RE.search(content):
            return True
    return False


def _screen_follow_up_failure_reply(analysis_status: dict[str, Any]) -> str:
    if not bool(analysis_status.get("enabled", True)):
        return "屏幕观察现在是关闭的，我没法重新看当前画面。"
    budget = analysis_status.get("budget") if isinstance(analysis_status.get("budget"), dict) else {}
    if bool(budget.get("paused")):
        return f"屏幕观察已暂停：{str(budget.get('paused_reason') or '今天的测试预算已用完')}。"
    if not bool(analysis_status.get("vision_available", False)):
        return "当前没有可用的视觉模型，我还不能重新看这张画面。"
    detail = str(analysis_status.get("last_error") or analysis_status.get("capture_diagnostic") or "").strip()
    return f"刚才没能取得新的画面：{detail or '屏幕捕获暂时不可用'}。"


async def analyze_screen_chat_follow_up(
    message: str,
    *,
    conversation_id: str,
    request_id: str,
    source: str,
) -> ChatResult:
    """Analyze one fresh frame for a chat follow-up without creating a second reply or TTS."""
    observer_status = await asyncio.to_thread(companion_service.window_observer.status)
    was_running = bool(observer_status.get("running"))
    analysis_before = await asyncio.to_thread(status)
    previous_capture_only = bool(analysis_before.get("capture_only"))
    started_at = time.monotonic()
    db.save_message(
        "user",
        str(message or "").strip(),
        source=source,
        conversation_id=conversation_id,
        request_id=request_id,
    )

    replied = False
    analysis_after: dict[str, Any] = {}
    try:
        if not bool(analysis_before.get("enabled", True)):
            raise RuntimeError("屏幕 AI 已关闭")
        budget = analysis_before.get("budget") if isinstance(analysis_before.get("budget"), dict) else {}
        if bool(budget.get("paused")):
            raise RuntimeError(str(budget.get("paused_reason") or "屏幕观察预算已暂停"))
        if not bool(analysis_before.get("vision_available", False)):
            raise RuntimeError("没有可用的视觉模型")
        capture_before = analysis_before.get("capture") if isinstance(analysis_before.get("capture"), dict) else {}
        if was_running and not bool(capture_before.get("running", True)):
            raise RuntimeError("屏幕捕获器没有运行")
        if str(analysis_before.get("vision_route") or "local") == "local":
            await local_vision_service.ensure_ready()
        else:
            await asyncio.to_thread(local_vision_service.unload_model)
        await asyncio.to_thread(set_capture_only, False)
        if not was_running:
            await asyncio.to_thread(companion_service.window_observer.select_screen, "primary")
        # Force one synchronous capture immediately before claiming a frame.
        # This prevents a previous preview from being mistaken for the answer
        # to “现在呢？” after the target window was closed, minimized, or
        # covered by another window.
        try:
            await asyncio.to_thread(companion_service.window_observer.capture)
        except (ValueError, RuntimeError, OSError) as exc:
            raise RuntimeError(f"无法取得当前新画面：{exc}") from exc
        replied = await analyze_once(
            force=True,
            interactive=True,
            conversation_id=conversation_id,
            request_id=request_id,
            allow_direct_speech=False,
        )
        analysis_after = await asyncio.to_thread(status)
    except (ValueError, RuntimeError, OSError):
        replied = False
        analysis_after = await asyncio.to_thread(status)
    finally:
        if not was_running:
            await asyncio.to_thread(companion_service.window_observer.stop)
            await asyncio.to_thread(end_session)
        else:
            await asyncio.to_thread(set_capture_only, previous_capture_only)

    if not analysis_after:
        analysis_after = await asyncio.to_thread(status)
    rows = [
        row
        for row in db.get_recent_messages(limit=12, conversation_id=conversation_id)
        if str(row["role"] or "") == "assistant" and str(row["request_id"] or "") == request_id
    ]
    if not replied or not rows:
        fallback = _screen_follow_up_failure_reply(analysis_after)
        db.save_message(
            "assistant",
            fallback,
            source="screen",
            conversation_id=conversation_id,
            request_id=request_id,
            model_id="local-status",
            reasoning_level="off",
            total_latency_ms=max(0.0, (time.monotonic() - started_at) * 1000),
        )
        return ChatResult(
            reply=fallback,
            replies=[fallback],
            request_id=request_id,
            model_id="local-status",
            reasoning_level="off",
            total_latency_ms=max(0.0, (time.monotonic() - started_at) * 1000),
        )

    replies = [str(row["content"] or "").strip() for row in rows if str(row["content"] or "").strip()]
    first = rows[0]
    timings = analysis_after.get("pipeline_timings") if isinstance(analysis_after.get("pipeline_timings"), dict) else {}
    return ChatResult(
        reply="\n\n".join(replies),
        replies=replies,
        speech_emotion=str(first["emotion"] or analysis_after.get("last_emotion") or ""),
        request_id=request_id,
        model_id=str(first["model_id"] or analysis_after.get("last_model") or ""),
        provider_model=str(analysis_after.get("last_model") or ""),
        reasoning_level=str(first["reasoning_level"] or "low"),
        prompt_tokens=int(first["prompt_tokens"] or 0),
        cached_prompt_tokens=int(first["cached_prompt_tokens"] or 0),
        completion_tokens=int(first["completion_tokens"] or 0),
        reasoning_tokens=int(first["reasoning_tokens"] or 0),
        request_cost_yuan=first["request_cost_yuan"],
        request_cost_source=str(first["request_cost_source"] or ""),
        first_token_latency_ms=(
            float(timings["first_token_seconds"]) * 1000
            if timings.get("first_token_seconds") is not None
            else None
        ),
        total_latency_ms=max(0.0, (time.monotonic() - started_at) * 1000),
    )


async def observation_loop() -> None:
    global _last_error
    while True:
        try:
            await analyze_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with _state_lock:
                _last_error = str(exc)
        await asyncio.sleep(1.0)


def set_capture_only(enabled: bool) -> None:
    global _capture_only
    with _state_lock:
        _capture_only = bool(enabled)


def end_session() -> None:
    global _last_game_state, _capture_only, _last_pipeline_timings
    db.end_screen_sessions()
    with _state_lock:
        _last_game_state = {}
        _capture_only = False
        _last_pipeline_timings = {}
