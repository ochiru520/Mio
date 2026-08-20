from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class Observation:
    event_type: str
    summary: str
    confidence: float
    game_name: str
    details: dict[str, Any]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class BehaviorDecision:
    should_speak: bool
    priority: float
    emotion: str
    reason: str
    repeat_count: int


EVENT_ALIASES = {
    "player_dead": "death",
    "game_over": "death",
    "defeat": "failure",
    "failed": "failure",
    "win": "victory",
    "won": "victory",
    "level_complete": "progress",
    "objective_complete": "progress",
    "boss_fight": "boss_battle",
    "boss": "boss_battle",
    "low_hp": "low_health",
    "rare_drop": "rare_reward",
    "match_end": "match_result",
}

# priority, emotion, cooldown multiplier
EVENT_POLICIES: dict[str, tuple[float, str, float]] = {
    "gameplay": (0.66, "neutral", 2.00),
    "activity_change": (0.68, "gentle", 2.00),
    "activity_progress": (0.74, "gentle", 1.20),
    "activity_pause": (0.68, "gentle", 1.80),
    "interesting_content": (0.72, "neutral", 1.20),
    "watching_game_video": (0.72, "neutral", 1.80),
    "watching_video": (0.68, "neutral", 2.00),
    "watching_movie": (0.74, "gentle", 1.60),
    "coding": (0.72, "gentle", 1.80),
    "writing": (0.70, "gentle", 2.00),
    "reading": (0.66, "neutral", 2.40),
    "browsing": (0.64, "neutral", 3.00),
    "victory": (0.95, "cheerful", 0.35),
    "achievement": (0.92, "cheerful", 0.40),
    "rare_reward": (0.90, "cheerful", 0.45),
    "progress": (0.82, "cheerful", 0.60),
    "match_result": (0.86, "neutral", 0.45),
    "death": (0.88, "concerned", 0.55),
    "failure": (0.78, "gentle", 0.65),
    "boss_battle": (0.78, "serious", 0.65),
    "boss_phase": (0.84, "serious", 0.50),
    "danger": (0.90, "serious", 0.30),
    "low_health": (0.88, "concerned", 0.35),
    "warning": (0.90, "serious", 0.30),
    "error": (0.92, "concerned", 0.30),
    "stuck": (0.74, "concerned", 1.20),
    "puzzle_progress": (0.72, "neutral", 0.80),
    "dialogue_choice": (0.70, "neutral", 0.75),
    "cutscene_turn": (0.76, "neutral", 0.70),
    "notable_scene": (0.72, "gentle", 1.00),
}

SILENT_EVENTS = {
    "idle",
    "movement",
    "exploration",
    "menu",
    "loading",
    "black_screen",
    "scene_change",
    "unknown",
}
REPEAT_MILESTONES = {3, 5, 10}
OCCURRENCE_EVENTS = {
    "victory",
    "achievement",
    "rare_reward",
    "progress",
    "match_result",
    "death",
    "failure",
    "boss_phase",
    "error",
    "stuck",
}
NON_GAME_ACTIVITY_EVENTS = {
    "watching_game_video",
    "watching_video",
    "watching_movie",
    "coding",
    "writing",
    "reading",
    "browsing",
}


def normalize_event_type(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", str(value or "unknown").strip().lower()).strip("_")
    return EVENT_ALIASES.get(normalized, normalized or "unknown")


def _normalized_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "").casefold())


def is_duplicate_summary(summary: str, recent_summaries: list[str]) -> bool:
    current = _normalized_text(summary)
    if len(current) < 4:
        return False
    for previous in recent_summaries:
        normalized = _normalized_text(previous)
        if current == normalized:
            return True
        if len(normalized) >= 4 and SequenceMatcher(None, current, normalized).ratio() >= 0.86:
            return True
    return False


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = getattr(row, key, default)
    return default if value is None else value


def _seconds_between(current: str, previous: str) -> float | None:
    try:
        current_value = datetime.fromisoformat(str(current))
        previous_value = datetime.fromisoformat(str(previous))
        return max(0.0, (current_value - previous_value).total_seconds())
    except (TypeError, ValueError):
        return None


def is_new_event_occurrence(
    observation: Observation,
    recent_observations: list[Any],
    *,
    occurred_at: str,
    continuation_seconds: int = 90,
) -> bool:
    """Return false while a discrete event is still the same visible occurrence."""
    if observation.event_type not in OCCURRENCE_EVENTS or not recent_observations:
        return True
    latest = recent_observations[0]
    latest_type = normalize_event_type(str(_row_value(latest, "event_type", "unknown")))
    if latest_type != observation.event_type:
        return True
    elapsed = _seconds_between(occurred_at, str(_row_value(latest, "occurred_at", "")))
    if elapsed is not None and elapsed > max(30, int(continuation_seconds)):
        return True
    return False


def update_game_state(
    current: dict[str, Any],
    observation: Observation,
    *,
    count_occurrence: bool = True,
) -> dict[str, Any]:
    state = deepcopy(current or {})
    event_counts = dict(state.get("event_counts") or {})
    if count_occurrence:
        event_counts[observation.event_type] = int(event_counts.get(observation.event_type) or 0) + 1
    state["event_counts"] = event_counts
    state["game_name"] = observation.game_name or str(state.get("game_name") or "")
    if observation.event_type in NON_GAME_ACTIVITY_EVENTS:
        state["game_name"] = ""
        for key in ("boss", "phase", "objective", "mode", "outcome", "health_status"):
            state.pop(key, None)
    state["last_event_type"] = observation.event_type
    state["last_event_summary"] = observation.summary
    state["last_confidence"] = observation.confidence

    for key in ("boss", "location", "phase", "objective", "mode", "outcome", "health_status"):
        value = observation.details.get(key)
        if value not in (None, "", [], {}):
            state[key] = value
    if observation.event_type == "death" and count_occurrence:
        state["death_count"] = int(state.get("death_count") or 0) + 1
        state["attempt"] = max(int(state.get("attempt") or 1) + 1, state["death_count"] + 1)
    elif observation.event_type == "failure" and count_occurrence:
        state["failure_count"] = int(state.get("failure_count") or 0) + 1
    elif observation.event_type in {"victory", "progress", "achievement"}:
        state["last_success"] = observation.summary
    return state


def decide_behavior(
    observation: Observation,
    state: dict[str, Any],
    *,
    recent_summaries: list[str],
    seconds_since_last_speech: float,
    cooldown_seconds: int,
    minimum_priority: float,
    event_is_new: bool = True,
    force: bool = False,
) -> BehaviorDecision:
    event_type = observation.event_type
    repeat_count = int((state.get("event_counts") or {}).get(event_type) or 1)
    policy = EVENT_POLICIES.get(event_type)

    if force:
        allowed = observation.confidence >= 0.4 and event_type not in SILENT_EVENTS
        return BehaviorDecision(
            should_speak=allowed,
            priority=max(0.65, observation.confidence),
            emotion=(policy[1] if policy else "neutral"),
            reason="用户主动让 Mio 观察" if allowed else "画面信息不足",
            repeat_count=repeat_count,
        )

    if policy is None or event_type in SILENT_EVENTS:
        return BehaviorDecision(False, 0.0, "neutral", "普通画面变化，保持安静", repeat_count)
    if event_type in OCCURRENCE_EVENTS and not event_is_new:
        return BehaviorDecision(False, 0.0, policy[1], "同一事件仍在持续", repeat_count)
    if observation.confidence < 0.58:
        return BehaviorDecision(False, 0.0, policy[1], "事件可信度不足", repeat_count)

    base_priority, emotion, cooldown_multiplier = policy
    priority = min(1.0, base_priority * 0.72 + observation.confidence * 0.28)
    if priority < minimum_priority:
        return BehaviorDecision(False, priority, emotion, "事件优先级不足", repeat_count)

    duplicate = is_duplicate_summary(observation.summary, recent_summaries)
    repeated_milestone = event_type in {"death", "failure", "stuck"} and repeat_count in REPEAT_MILESTONES
    # 普通游戏过程允许在一段时间没有说话后重新开口，但不在每个观察周期播报。
    rhythm_break = event_type in {
        "gameplay",
        "notable_scene",
        "activity_change",
        "activity_progress",
        "activity_pause",
        "interesting_content",
        "watching_game_video",
        "watching_video",
        "watching_movie",
        "coding",
        "writing",
        "reading",
        "browsing",
    } and (
        seconds_since_last_speech >= max(20.0, cooldown_seconds * 4)
    )
    if duplicate and not repeated_milestone and not rhythm_break:
        return BehaviorDecision(False, priority, emotion, "相同事件刚刚已经回应过", repeat_count)

    required_cooldown = max(3.0, cooldown_seconds * cooldown_multiplier)
    if seconds_since_last_speech < required_cooldown and not repeated_milestone:
        return BehaviorDecision(False, priority, emotion, "仍在回应冷却时间内", repeat_count)
    reason = f"{event_type} 事件达到回应阈值"
    if repeated_milestone:
        reason = f"{event_type} 已连续出现 {repeat_count} 次"
    return BehaviorDecision(True, priority, emotion, reason, repeat_count)


def game_state_summary(state: dict[str, Any]) -> str:
    if not state:
        return "本次还没有形成稳定的活动状态。"
    parts: list[str] = []
    labels = {
        "game_name": "游戏",
        "boss": "Boss",
        "location": "位置",
        "phase": "阶段",
        "objective": "目标",
        "attempt": "当前尝试",
        "death_count": "死亡次数",
        "failure_count": "失败次数",
        "last_success": "最近进展",
    }
    for key, label in labels.items():
        value = state.get(key)
        if value not in (None, "", 0):
            parts.append(f"{label}：{value}")
    return "；".join(parts[:8]) or "本次还没有形成稳定的活动状态。"


__all__ = [
    "BehaviorDecision",
    "Observation",
    "decide_behavior",
    "game_state_summary",
    "is_duplicate_summary",
    "is_new_event_occurrence",
    "normalize_event_type",
    "update_game_state",
]
