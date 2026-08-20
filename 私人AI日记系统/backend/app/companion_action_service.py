from __future__ import annotations

import json
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import db, maintenance_service
from .config import settings
from .llm import call_chat_completion
from .prompts import DAILY_THIRTY_RULES
from .tool_registry import ToolPermission, tool_registry

logger = logging.getLogger(__name__)


ALLOWED_ACTION_TYPES = {
    "add_diary_material",
    "set_daily_thirty",
    "set_daily_mood",
    "update_today_state",
    "edit_today_diary",
    "generate_today_diary",
    "update_profile",
    "remember_thread",
    "resolve_thread",
    "record_follow_up_result",
    "remember_memory",
}
MAX_ACTIONS_PER_TURN = 6
ACTION_POLICIES = {
    name: (
        "confirmation"
        if tool_registry.require(name).permission == ToolPermission.HIGH_RISK_WRITE
        else "automatic"
    )
    for name in ALLOWED_ACTION_TYPES
}
FORBIDDEN_PROFILE_ACTION_RE = re.compile(
    r"(api\s*key|apikey|secret|token|密码|密钥|账号|系统命令|执行命令|删除文件|"
    r"\.env|代理|proxy|git|源代码|python\s*代码|后端代码)",
    re.IGNORECASE,
)
DAY_CLOSING_RE = re.compile(r"(准备睡|要睡了|睡觉了|我睡了|晚安|今天就这样|今天差不多|今天先这样|收尾了|准备休息)")
NOT_CLOSING_RE = re.compile(r"(不睡|还不睡|不准备睡|不能睡|别睡)")
DIARY_GENERATION_VERB_RE = re.compile(r"(生成|写(?:一篇|一下)?|创建|整理(?:成|一下)?|做一篇)")
DIARY_TODAY_RE = re.compile(r"(今天|今日|当天|今儿)")
DIARY_GENERATION_NEGATION_RE = re.compile(r"(不要|别|不用|暂时不|先不|取消).{0,8}(生成|写|创建|整理|做).{0,12}日记")
DIARY_INFORMATIONAL_RE = re.compile(r"^\s*(怎么|如何|为什么|哪里|什么情况下).{0,12}(生成|写|创建|整理|做).{0,12}日记")
CONTINUING_ACTIVITY_RE = re.compile(
    r"(?:还在|一直在|仍在|继续|接着|没停).{0,12}(?:做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|运动|锻炼|健身|阅读|复习)"
    r"|(?:做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|运动|锻炼|健身|阅读|复习).{0,8}(?:到现在|还没停)"
)
PRODUCTIVE_ACTIVITY_RE = re.compile(
    r"(在|正|开始|继续|接着|一直).{0,8}(做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|锻炼|健身|阅读|复习)"
    r"|(?:demo|代码|设计|文档|笔记|作品|项目|游戏|网站|模型|视频|作业|东西|流水线|跑步|跑了|运动|锻炼|健身|力量训练|阅读|看书|课程|学习|复习|面试准备)",
    re.IGNORECASE,
)


def _explicit_today_diary_generation(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    if not normalized or "日记" not in normalized or not DIARY_TODAY_RE.search(normalized):
        return False
    if DIARY_GENERATION_NEGATION_RE.search(normalized) or DIARY_INFORMATIONAL_RE.search(normalized):
        return False
    return DIARY_GENERATION_VERB_RE.search(normalized) is not None


EXPLICIT_MEMORY_RE = re.compile(
    r"(?:记住|写进(?:你的)?记忆(?:里|中)?|放到(?:你的)?(?:属性|记忆|底层代码)(?:里|中)?)"
    r"[：:，,\s]*(.+)",
    re.IGNORECASE,
)
PREFERENCE_MEMORY_RE = re.compile(
    r"我(?:更|比较|一直)?(?:喜欢|不喜欢|讨厌|习惯|更习惯|希望你以后|不希望你|偏好).{2,180}"
)
CURRENT_STATE_MEMORY_RE = re.compile(
    r"我(?:最近|目前|这段时间|接下来|现在正在|现在一直在).{3,180}"
)


@dataclass(frozen=True)
class CompanionDecision:
    reply: str
    actions: list[dict[str, Any]]
    assessment: dict[str, Any]
    structured: bool


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("没有找到 JSON 对象。")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型没有返回 JSON 对象。")
    return data


def _bounded_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _receipt_payload(action: dict[str, Any]) -> str:
    sensitive = re.compile(r"(?:api.?key|secret|token|password|密码|密钥)", re.IGNORECASE)

    def clean(value: object, key: str = "") -> object:
        if sensitive.search(key):
            return "[已脱敏]"
        if isinstance(value, dict):
            return {str(child_key): clean(child_value, str(child_key)) for child_key, child_value in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value[:20]]
        if isinstance(value, str):
            return value[:1000]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)[:1000]

    return json.dumps(clean(action), ensure_ascii=False)


def parse_companion_decision(raw: str) -> CompanionDecision:
    try:
        data = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        return CompanionDecision(reply=raw.strip(), actions=[], assessment={}, structured=False)

    reply_value = data.get("reply")
    if isinstance(reply_value, list):
        reply = "\n".join(str(item).strip() for item in reply_value if str(item).strip())
    else:
        reply = str(reply_value or "").strip()
    if not reply:
        reply = "嗯，我在听。"

    assessment = data.get("assessment")
    if not isinstance(assessment, dict):
        assessment = {}
    assessment = dict(assessment)
    assessment["confidence"] = _bounded_confidence(assessment.get("confidence"))
    assessment["needs_clarification"] = bool(assessment.get("needs_clarification", False))

    raw_actions = data.get("actions")
    actions: list[dict[str, Any]] = []
    if isinstance(raw_actions, list):
        seen: set[tuple[str, str]] = set()
        for item in raw_actions[:MAX_ACTIONS_PER_TURN]:
            if not isinstance(item, dict):
                continue
            parameters = item.get("parameters")
            if isinstance(parameters, dict):
                normalized = {**parameters, **{key: value for key, value in item.items() if key != "parameters"}}
            elif len(item) == 1:
                nested_type, nested_parameters = next(iter(item.items()))
                if nested_type in ALLOWED_ACTION_TYPES and isinstance(nested_parameters, dict):
                    normalized = {**nested_parameters, "type": nested_type}
                else:
                    normalized = dict(item)
            else:
                normalized = dict(item)
            action_type = str(normalized.get("type") or normalized.get("action") or "").strip()
            if not action_type:
                if "status" in normalized and "reason" in normalized:
                    action_type = "set_daily_thirty"
                elif "mood" in normalized:
                    action_type = "set_daily_mood"
                elif "content" in normalized and "follow_up_after" in normalized:
                    action_type = "remember_thread"
                elif "content" in normalized:
                    action_type = "add_diary_material"
            confidence = _bounded_confidence(normalized.get("confidence"))
            if action_type not in ALLOWED_ACTION_TYPES:
                continue
            # 记忆事实不确定时先落候选箱，避免猜测直接污染长期上下文。
            is_memory_candidate = action_type == "remember_memory" and confidence >= 0.55
            if confidence < 0.75 and not is_memory_candidate:
                continue
            normalized.pop("action", None)
            normalized["type"] = action_type
            normalized["confidence"] = confidence
            if is_memory_candidate:
                normalized["_candidate"] = True
            identity = (
                action_type,
                str(normalized.get("content") or normalized.get("instruction") or normalized.get("status") or "")[:300],
            )
            if identity in seen:
                continue
            seen.add(identity)
            actions.append(normalized)

    if assessment.get("needs_clarification"):
        actions = []
    return CompanionDecision(reply=reply, actions=actions, assessment=assessment, structured=True)


def build_companion_planner_messages(
    conversation_id: str,
    user_message: str,
) -> list[dict[str, str]]:
    rows = db.get_recent_messages(limit=10, conversation_id=conversation_id)
    context_lines: list[str] = []
    for row in rows:
        role = "用户" if row["role"] == "user" else "Mio"
        content = _clean_text(row["content"], 300)
        if content:
            context_lines.append(f"[{row['created_at']}] {role}：{content}")
    current_state = None if conversation_id.startswith("qq_group_") else db.get_daily_state()
    current_state_text = "不提供（群聊）" if conversation_id.startswith("qq_group_") else "无"
    if current_state is not None:
        current_state_text = (
            f"情绪={current_state['mood'] or '未判定'}（{current_state['mood_score'] or 0}/5）；"
            f"主线={current_state['key_events'] or '未判定'}；"
            f"可以调整的地方={current_state['avoidance_signals'] or '未判定'}；"
            f"下一步={current_state['next_min_action'] or '未判定'}"
        )

    system = f"""你是私人日记系统的隐藏行动判断器，不是聊天角色。
你不能回复、安慰、追问或评价用户，只能分析本轮原话并输出 JSON。

{DAILY_THIRTY_RULES}

每轮必须按顺序检查：
1. 是否包含值得写进当天日记的明确事件、产出、真实情绪或重要原话。
2. 是否包含每日三十的新证据。尤其是“一上午、一下午、几个小时、半小时”等时长，以及学习、创作、项目推进、跑步、健身、技能练习等自我提升活动。
3. 是否明确纠正了当天日记或今日状态。
4. 是否表达了长期稳定的说话或相处偏好。
5. 是否出现用户明确希望以后继续问、并且有具体跟进时间的计划、约定或未完成事项。
6. 是否在反馈某个旧话题的真实结果：完成、部分完成或未完成；需要保留用户说明，并给出有依据的后续调整。
7. 是否明确要求生成今天的日记，或在晚上明确表达准备睡觉、结束今天。
8. 是否出现值得跨天保留、且有明确原话证据的稳定事实、近期状态或长期经历。

强制规则：
- “跑步 30 分钟、健身一小时、学习一上午、做了一下午 demo、练了几个小时技能”等事实已经同时具备自我提升方向和至少 30 分钟时长，必须输出 add_diary_material 和 set_daily_thirty(done)，不能留空。
- 最近对话带有真实时间戳。若用户先说正在学习、创作、工作推进或运动，之后说“还在做/继续做/做到现在”，必须用两条用户消息的时间差作为连续时长；即使跨过零点，只要仍在凌晨 04:00 前就属于同一个记录日。连续至少 30 分钟时必须输出 set_daily_thirty(done)。
- 用户明确做了能提升自己的活动但没说时长，应输出 add_diary_material 和 set_daily_thirty(partial)，reason 写明时长待确认。
- 纯问候、无事实闲聊、信息不足的猜测才允许 actions 为空。
- 不要因为用户没有使用“记录、修改、生成、每日三十”等命令词就跳过动作。
- “生成今天的日记”“写一篇今日日记”“先生成给我看看”是明确生成指令，必须输出 generate_today_diary；只有“看看/预览已有日记”而没有生成动词时才不生成。
- 用户明确表达当天的情绪或整体状态时，可以输出 set_daily_mood。score 使用 1-5：1=很低落，2=偏差，3=平稳，4=不错，5=很好。
- 普通事实陈述、无法判断的语气和很小的瞬时波动不要强行评分，也不要为了让统计图有数据而编造情绪。
- assessment.daily_state 用于维护右侧“今日状态”：只根据用户明确说过的事实更新；把当前状态和最近对话合并成简短的今日主线、可以调整的地方、下一步。
- 没有新证据的字段写空字符串，不要用“未确认”覆盖已有状态。mood_score 信息不足写 0。daily_state.confidence 低于 0.60 时不会写入。
- 群聊不写私人今日状态。
- remember_thread 只能用于用户自己的未来事件，并且必须有明确的 follow_up_after。Mio 随口提出的建议、普通吐槽、没有时间的“之后再说”、以及已经有结果的事情都不能创建待跟进话题。
- 不要为同一件事反复创建不同措辞的 remember_thread。
- remember_memory 只记录用户明确说出的事实，不记录 Mio 的猜测。L0 仅用于身份事实、长期稳定偏好和关系边界；L1 用于最近三周仍有用的状态或计划；L2 用于重要项目、人物和长期经历。
- remember_memory 置信度 0.55-0.74 时只放入候选箱，不进入上下文；只有确认后才成为 active。无法从用户原话找到证据时不要生成候选。
- memory_key 使用稳定、简短的英文或中文键表达同一事实槽位，例如 preferred_reply_style、current_project、sleep_schedule。用户纠正旧事实时必须沿用同一个 memory_key，让系统替代旧记录。
- 群聊内容禁止写入私人记忆。当前会话若以 qq_group_ 开头，不能输出 remember_memory。
- 属性、日记修改和自动生成动作的 confidence 必须至少 0.90，其他动作至少 0.75。
- 禁止任何代码、命令、密钥、代理、账号、删除文件和 Git 动作。

只允许以下动作及字段：
- add_diary_material: content, confidence
- set_daily_thirty: status(done/partial/missed), reason, correction, confidence
- set_daily_mood: mood, score(1-5), confidence
- edit_today_diary: instruction, confidence
- generate_today_diary: reason, confidence
- update_profile: instruction, confidence
- remember_thread: content, follow_up_after(YYYY-MM-DD HH:MM 或空), confidence
- resolve_thread: content, confidence（仅兼容明确完成）
- record_follow_up_result: content, outcome(completed/partial/not_completed), summary, adjustment, next_follow_up_after(YYYY-MM-DD HH:MM 或空), confidence
- remember_memory: layer(L0/L1/L2), category(identity/preference/relationship/current_state/plan/project/experience/person/other), memory_key, content, confidence

输出结构：
{{
  "assessment": {{
    "intent": "简短分类",
    "emotion": "明确情绪或未知",
    "confidence": 0.0,
    "needs_clarification": false,
    "daily_state": {{
      "mood": "明确的当日整体情绪或空字符串",
      "mood_score": 0,
      "key_events": "合并后的 1-3 个今日主线或空字符串",
      "avoidance_signals": "明确的没有做好之处、阻碍或空字符串",
      "next_min_action": "一个有事实依据的最小行动或空字符串",
      "confidence": 0.0
    }}
  }},
  "actions": []
}}

只输出 JSON，不要 Markdown，不要 reply 字段，不要在前后添加任何文字。"""
    user = f"""当前本地时间：{db.now_iso()}
当前记录日：{db.today_string()}（凌晨 {settings.day_boundary_hour:02d}:00 前仍归前一天）

当前今日状态：
{current_state_text}

最近对话：
{chr(10).join(context_lines) or "无"}

本轮需要判断的用户原话：
{user_message}

现在逐项检查并输出 JSON。不要回复用户。
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def plan_companion_actions(
    conversation_id: str,
    user_message: str,
) -> CompanionDecision:
    try:
        raw = await call_chat_completion(
            build_companion_planner_messages(conversation_id, user_message),
            temperature=settings.action_planner_temperature,
            reasoning_level="off",
        )
    except Exception:
        return CompanionDecision(reply="", actions=[], assessment={}, structured=False)
    return parse_companion_decision(raw)


def _clean_text(value: object, max_chars: int = 1000) -> str:
    return " ".join(str(value or "").split()).strip()[:max_chars]


_EMPTY_STATE_VALUES = frozenset({"", "无", "未知", "未确认", "没有", "没有足够信息", "信息不足"})


def _clean_state_value(value: object, max_chars: int = 500) -> str:
    text = _clean_text(value, max_chars)
    return "" if text in _EMPTY_STATE_VALUES else text


def apply_daily_state_assessment(
    assessment: dict[str, Any],
    *,
    conversation_id: str,
) -> bool:
    if conversation_id.startswith("qq_group_") or not isinstance(assessment, dict):
        return False
    state = assessment.get("daily_state")
    if not isinstance(state, dict):
        return False
    confidence = _bounded_confidence(state.get("confidence", assessment.get("confidence")))
    if confidence < 0.60:
        return False
    try:
        mood_score = int(state.get("mood_score") or 0)
    except (TypeError, ValueError):
        mood_score = 0
    if mood_score not in range(1, 6):
        mood_score = 0
    return db.update_daily_state_summary(
        mood=_clean_state_value(state.get("mood"), 300),
        mood_score=mood_score,
        key_events=_clean_state_value(state.get("key_events")),
        avoidance_signals=_clean_state_value(state.get("avoidance_signals")),
        next_min_action=_clean_state_value(state.get("next_min_action")),
    )


def _valid_follow_up_after(value: object) -> str:
    text = _clean_text(value, 32)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ""
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


def _infer_follow_up_after(user_message: str) -> str:
    if "明天" not in user_message:
        return ""
    current = datetime.fromisoformat(db.now_iso())
    target = current + timedelta(days=1)
    if any(word in user_message for word in ("早上", "早晨", "上午")):
        hour = 9
    elif "中午" in user_message:
        hour = 12
    elif "下午" in user_message:
        hour = 16
    elif any(word in user_message for word in ("晚上", "晚点")):
        hour = 20
    else:
        hour = 18
    return target.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _profile_context(conversation_id: str, user_message: str) -> list[str]:
    rows = db.get_recent_messages(limit=8, conversation_id=conversation_id)
    context = [
        f"{row['role']}: {row['content']}"
        for row in rows
        if row["role"] in {"user", "assistant"} and str(row["content"]).strip()
    ]
    if not context or context[-1] != f"user: {user_message}":
        context.append(f"user: {user_message}")
    return context[-8:]


DAILY_THIRTY_STATUS_LABELS = {
    "done": "完成",
    "partial": "部分完成",
    "missed": "未完成",
    "unknown": "未确认",
}
DIARY_STATUS_LINE_RE = re.compile(r"(##\s*(?:每日三十|今日成长)[\s\S]{0,80}?状态[：:]\s*)([^\n\r]+)")


def _sync_diary_markdown_status(markdown_content: str, status: str, reason: str) -> str:
    label = DAILY_THIRTY_STATUS_LABELS.get(status)
    if not label:
        return markdown_content
    updated, count = DIARY_STATUS_LINE_RE.subn(rf"\g<1>{label}", markdown_content)
    if not count:
        return markdown_content
    if reason:
        updated = re.sub(
            r"(##\s*(?:每日三十|今日成长)[\s\S]{0,200}?原因[：:]\s*)([^\n\r]+)",
            lambda match: f"{match.group(1)}{reason}",
            updated,
            count=1,
        )
    return updated


def _update_existing_diary_status(status: str, reason: str = "") -> None:
    diary = db.get_diary(db.today_string())
    if diary is None:
        return
    markdown_content = _sync_diary_markdown_status(str(diary["markdown_content"]), status, reason)
    if markdown_content != diary["markdown_content"]:
        # 状态变化要真实写回日记正文和磁盘文件，不能只改数据库字段。
        from .routes.diary import save_diary_markdown

        save_diary_markdown(
            diary["date"],
            markdown_content,
            diary["mood_tags"],
            status,
            diary["confirmed_at"],
        )
        return
    db.upsert_diary(
        diary["date"],
        diary["title"],
        diary["markdown_content"],
        diary["mood_tags"],
        status,
        diary["confirmed_at"],
    )


def _parse_message_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _format_elapsed_minutes(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours} 小时 {remainder} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def _explicit_duration_minutes(text: str) -> int | None:
    minute_match = re.search(r"(\d{1,3})\s*(?:分钟|分(?:钟)?)", text)
    if minute_match:
        return int(minute_match.group(1))

    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?(?:小时|钟头)", text)
    if hour_match:
        return round(float(hour_match.group(1)) * 60)
    if re.search(r"半(?:个)?小时", text):
        return 30

    chinese_hours = {
        "一": 1,
        "两": 2,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    chinese_match = re.search(r"([一两二三四五六七八九十])(?:个)?(?:小时|钟头)", text)
    if chinese_match:
        return chinese_hours[chinese_match.group(1)] * 60
    if re.search(r"(?:一上午|一下午|一晚上|大半天)", text):
        return 120
    return None


def _infer_explicit_daily_thirty(user_message: str) -> dict[str, Any] | None:
    text = user_message.strip()
    if not PRODUCTIVE_ACTIVITY_RE.search(text):
        return None
    if re.search(r"(?:打算|计划|准备|待会|等会|明天|之后要|想去|要去).{0,12}(?:跑|运动|锻炼|健身|学习|阅读|练|做|写)", text):
        return None
    if re.search(r"(?:算不算|能不能算|可以算|算.{0,4}每日三十|每日三十.{0,4}[吗嘛么？?])", text):
        return None

    minutes = _explicit_duration_minutes(text)
    if minutes is None or minutes < 30 or minutes > 12 * 60:
        return None
    return {
        "type": "set_daily_thirty",
        "status": "done",
        "reason": f"{_clean_text(text, 180)}，属于能提升自己的持续行动，时长约 {_format_elapsed_minutes(minutes)}",
        "correction": False,
        "confidence": 0.99,
    }


def _infer_continuous_daily_thirty(
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> dict[str, Any] | None:
    if not CONTINUING_ACTIVITY_RE.search(user_message):
        return None

    rows = db.get_recent_messages(
        limit=max(30, min(settings.chat_raw_history_limit, 160)),
        conversation_id=conversation_id,
    )
    current_row = next((row for row in rows if int(row["id"]) == source_message_id), None)
    if current_row is None:
        return None
    previous_users = [
        row
        for row in rows
        if row["role"] == "user" and int(row["id"]) < source_message_id
    ]
    if not previous_users:
        return None
    continued_at = _parse_message_time(current_row["created_at"])
    if continued_at is None:
        return None

    previous = None
    started_at = None
    for candidate in reversed(previous_users):
        candidate_time = _parse_message_time(candidate["created_at"])
        if candidate_time is None:
            continue
        if db.logical_date_for_datetime(candidate_time) != db.logical_date_for_datetime(continued_at):
            break
        candidate_minutes = int((continued_at - candidate_time).total_seconds() // 60)
        if candidate_minutes > 12 * 60:
            break
        if PRODUCTIVE_ACTIVITY_RE.search(str(candidate["content"])):
            previous = candidate
            started_at = candidate_time
            break
    if previous is None or started_at is None:
        return None

    elapsed_minutes = int((continued_at - started_at).total_seconds() // 60)
    if elapsed_minutes < 30 or elapsed_minutes > 12 * 60:
        return None
    duration = _format_elapsed_minutes(elapsed_minutes)
    return {
        "type": "set_daily_thirty",
        "status": "done",
        "reason": (
            f"从 {started_at.strftime('%H:%M')} 到 {continued_at.strftime('%H:%M')} "
            f"持续推进同一件事，已约 {duration}"
        ),
        "correction": False,
        "confidence": 0.99,
    }


def _with_continuous_activity_inference(
    actions: list[dict[str, Any]],
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> list[dict[str, Any]]:
    inferred = _infer_explicit_daily_thirty(user_message) or _infer_continuous_daily_thirty(
        conversation_id, user_message, source_message_id
    )
    if inferred is None:
        return actions
    current_state = db.get_daily_state()
    if (
        current_state is not None
        and current_state["daily_thirty_status"] == "done"
        and not any(action.get("type") == "set_daily_thirty" for action in actions)
    ):
        return actions

    enriched: list[dict[str, Any]] = []
    replaced = False
    for action in actions:
        if action.get("type") == "set_daily_thirty":
            if str(action.get("status") or "") == "done":
                enriched.append(action)
            else:
                enriched.append(inferred)
            replaced = True
        else:
            enriched.append(action)
    if not replaced:
        enriched.insert(0, inferred)
    return enriched


def _memory_key_for_text(category: str, content: str) -> str:
    if category == "preference":
        if re.search(r"(?:回复|说话|短句|长句|追问|语气|称呼|聊天)", content):
            return "preferred_reply_style"
        if re.search(r"(?:主动找|打扰|提醒|消息频率)", content):
            return "preferred_interaction_frequency"
    if category == "current_state":
        if re.search(r"(?:项目|应用|agent|网站|游戏开发)", content, re.IGNORECASE):
            return "current_project"
        if re.search(r"(?:求职|面试|工作)", content):
            return "current_job_state"
        if re.search(r"(?:生病|疼|溃疡|睡眠|失眠|身体)", content):
            return "current_health_state"
    digest = hashlib.sha1(content.casefold().encode("utf-8")).hexdigest()[:10]
    return f"{category}_{digest}"


def _previous_user_message(conversation_id: str, source_message_id: int) -> str:
    rows = db.get_recent_messages(limit=12, conversation_id=conversation_id)
    for row in reversed(rows):
        if row["role"] != "user" or int(row["id"]) >= source_message_id:
            continue
        content = _clean_text(row["content"], 500)
        if content:
            return content
    return ""


def _infer_deterministic_memory(
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> dict[str, Any] | None:
    if conversation_id.startswith("qq_group_"):
        return None
    text = _clean_text(user_message, 500)
    if not text or text.endswith(("?", "？")):
        return None

    explicit = EXPLICIT_MEMORY_RE.search(text)
    if explicit:
        content = _clean_text(explicit.group(1), 500)
        if not content or re.fullmatch(r"(?:这个|这件事|这些|它|上面说的|刚才说的).{0,6}", content):
            content = _previous_user_message(conversation_id, source_message_id)
        if not content:
            return None
        if PREFERENCE_MEMORY_RE.search(content) or re.search(r"(?:以后|习惯|喜欢|不喜欢)", content):
            layer, category = "L0", "preference"
        elif re.search(r"(?:我是|我叫|生日|年龄|家住)", content):
            layer, category = "L0", "identity"
        elif re.search(r"(?:项目|应用|游戏|作品)", content):
            layer, category = "L2", "project"
        else:
            layer, category = "L1", "current_state"
        confidence = 0.98
    elif PREFERENCE_MEMORY_RE.search(text):
        content = text
        layer, category, confidence = "L0", "preference", 0.94
    elif CURRENT_STATE_MEMORY_RE.search(text):
        content = text
        layer, category, confidence = "L1", "current_state", 0.86
    else:
        return None

    return {
        "type": "remember_memory",
        "layer": layer,
        "category": category,
        "memory_key": _memory_key_for_text(category, content),
        "content": content,
        "confidence": confidence,
    }


def _with_deterministic_memory_inference(
    actions: list[dict[str, Any]],
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> list[dict[str, Any]]:
    if any(action.get("type") == "remember_memory" for action in actions):
        return actions
    inferred = _infer_deterministic_memory(
        conversation_id,
        user_message,
        source_message_id,
    )
    return [*actions, inferred] if inferred is not None else actions


def _with_deterministic_diary_generation(
    actions: list[dict[str, Any]],
    user_message: str,
) -> list[dict[str, Any]]:
    if not _explicit_today_diary_generation(user_message):
        return actions
    if any(str(action.get("type") or "") == "generate_today_diary" for action in actions):
        return actions
    return [
        {
            "type": "generate_today_diary",
            "reason": "用户明确要求生成今天的日记。",
            "confidence": 1.0,
        },
        *actions,
    ]


def backfill_explicit_structured_memories(limit: int = 500) -> int:
    if db.list_structured_memories(status="active", limit=1):
        return 0
    from .memory_service import save_memory_item

    saved_count = 0
    for row in db.list_recent_private_user_messages(limit=limit):
        content = str(row["content"] or "")
        explicit = EXPLICIT_MEMORY_RE.search(content) is not None
        preference = PREFERENCE_MEMORY_RE.search(content) is not None
        if not explicit and not preference:
            continue
        action = _infer_deterministic_memory(
            str(row["conversation_id"] or "default"),
            content,
            int(row["id"]),
        )
        if action is None:
            continue
        try:
            save_memory_item(
                layer=str(action["layer"]),
                category=str(action["category"]),
                memory_key=str(action["memory_key"]),
                content=str(action["content"]),
                source_conversation_id=str(row["conversation_id"] or "default"),
                source_message_id=int(row["id"]),
                confidence=float(action["confidence"]),
            )
        except ValueError:
            continue
        saved_count += 1
        if saved_count >= 20:
            break
    return saved_count


async def execute_companion_action_primitive(
    action: dict[str, Any],
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> str:
    action_type = str(action["type"])
    confidence = _bounded_confidence(action.get("confidence"))

    if action_type == "add_diary_material":
        content = _clean_text(action.get("content"))
        material_id = db.add_diary_material_once(content, source="auto_chat")
        return f"material:{material_id}"

    if action_type == "set_daily_thirty":
        status = str(action.get("status") or "unknown").strip().lower()
        reason = _clean_text(action.get("reason"), 500)
        if status == "unknown":
            raise ValueError("没有新证据时不写入未确认状态。")
        if status == "missed" and confidence < 0.90:
            raise ValueError("未完成判定置信度不足。")
        current = db.get_daily_state()
        if (
            current is not None
            and current["daily_thirty_status"] == "done"
            and status != "done"
            and not bool(action.get("correction", False))
        ):
            return "daily_thirty:kept_done"
        db.update_daily_thirty(status, reason)
        _update_existing_diary_status(status, reason)
        return f"daily_thirty:{status}"

    if action_type == "set_daily_mood":
        mood = _clean_text(action.get("mood"), 300)
        try:
            mood_score = int(action.get("score", action.get("mood_score", 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("情绪评分必须是 1 到 5 的整数。") from exc
        if mood_score not in range(1, 6):
            raise ValueError("情绪评分必须是 1 到 5 的整数。")
        db.update_daily_mood(mood, mood_score=mood_score)
        return f"mood:updated:{mood_score}"

    if action_type == "update_today_state":
        fields = {
            "mood": _clean_state_value(action.get("mood"), 300),
            "key_events": _clean_state_value(action.get("key_events"), 500),
            "avoidance_signals": _clean_state_value(action.get("avoidance_signals"), 500),
            "next_min_action": _clean_state_value(action.get("next_min_action"), 500),
        }
        try:
            mood_score = int(action.get("mood_score") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("今日状态情绪评分必须是 0 到 5 的整数。") from exc
        if mood_score not in range(0, 6):
            raise ValueError("今日状态情绪评分必须是 0 到 5 的整数。")
        updated = db.update_daily_state_summary(mood_score=mood_score, **fields)
        # 每日三十状态随今日状态一起写入，避免界面“今日成长”一直停在未判定。
        daily_thirty_status = str(action.get("daily_thirty_status") or "")
        daily_thirty_reason = _clean_text(action.get("daily_thirty_reason"), 500)
        if daily_thirty_status:
            if daily_thirty_status not in {"done", "partial", "missed", "unknown"}:
                raise ValueError("每日三十状态无效。")
            db.update_daily_thirty(daily_thirty_status, reason=daily_thirty_reason)
            updated = True
        return "today_state:updated" if updated else "today_state:unchanged"

    if action_type == "remember_thread":
        if confidence < 0.90:
            raise ValueError("待跟进话题置信度不足。")
        content = _clean_text(action.get("content"), 500)
        follow_up_after = _valid_follow_up_after(action.get("follow_up_after")) or _infer_follow_up_after(user_message)
        if not follow_up_after:
            raise ValueError("没有明确跟进时间，不创建待跟进话题。")
        thread_id = db.remember_pending_thread(
            conversation_id,
            content,
            follow_up_after=follow_up_after,
            source_message_id=source_message_id,
        )
        return f"thread:{thread_id}"

    if action_type == "resolve_thread":
        from .life_loop_service import record_matching_follow_up_result

        content = _clean_text(action.get("content"), 500)
        result = record_matching_follow_up_result(
            conversation_id,
            content,
            outcome="completed",
            summary=_clean_text(user_message, 800),
            source_message_id=source_message_id,
        )
        return f"thread_resolved:{int(result['thread_id']) if result else 0}"

    if action_type == "record_follow_up_result":
        from .life_loop_service import record_matching_follow_up_result

        content = _clean_text(action.get("content"), 500)
        outcome = str(action.get("outcome") or "").strip().lower()
        summary = _clean_text(action.get("summary") or user_message, 800)
        adjustment = _clean_text(action.get("adjustment"), 500)
        next_follow_up_after = _valid_follow_up_after(action.get("next_follow_up_after"))
        result = record_matching_follow_up_result(
            conversation_id,
            content,
            outcome=outcome,
            summary=summary,
            adjustment=adjustment,
            next_follow_up_after=next_follow_up_after,
            source_message_id=source_message_id,
        )
        if result is None:
            raise ValueError("没有找到与这次反馈匹配的待跟进话题。")
        return f"follow_up_result:{result['id']}:{outcome}"

    if action_type == "remember_memory":
        from .memory_service import save_memory_candidate, save_memory_item

        memory_kwargs = {
            "layer": str(action.get("layer") or "L1"),
            "category": str(action.get("category") or "other"),
            "memory_key": str(action.get("memory_key") or ""),
            "content": _clean_text(action.get("content"), 800),
            "source_conversation_id": conversation_id,
            "source_message_id": source_message_id,
            "confidence": confidence,
        }
        saved = save_memory_candidate(**memory_kwargs) if action.get("_candidate") else save_memory_item(**memory_kwargs)
        return f"memory:{saved['id']}:{saved['outcome']}"

    if action_type == "update_profile":
        if confidence < 0.90:
            raise ValueError("属性更新置信度不足。")
        instruction = _clean_text(action.get("instruction"), 800)
        if FORBIDDEN_PROFILE_ACTION_RE.search(instruction):
            raise ValueError("属性更新超出允许范围。")
        from .mio_profile import update_mio_profile_with_instruction

        await update_mio_profile_with_instruction(
            instruction,
            _profile_context(conversation_id, user_message),
        )
        return "profile:updated"

    if action_type == "edit_today_diary":
        if confidence < 0.90:
            raise ValueError("日记修改置信度不足。")
        if db.get_diary(db.today_string()) is None:
            content = _clean_text(action.get("instruction"), 800)
            material_id = db.add_diary_material_once(content, source="auto_diary_correction")
            return f"diary_missing_material:{material_id}"
        from .routes.diary import edit_diary_with_instruction

        await edit_diary_with_instruction(db.today_string(), _clean_text(action.get("instruction"), 800))
        return "diary:edited"

    if action_type == "generate_today_diary":
        if confidence < 0.90:
            raise ValueError("日记生成置信度不足。")
        explicit_generation = _explicit_today_diary_generation(user_message)
        day_closing = DAY_CLOSING_RE.search(user_message) is not None and NOT_CLOSING_RE.search(user_message) is None
        if not explicit_generation and not day_closing:
            raise ValueError("正式生成今日日记需要先确认结束今天（例如“今天就这样”“晚安”）；如果只是想看看今天的日记或素材，我可以直接读取展示。")
        current_hour = datetime.fromisoformat(db.now_iso()).hour
        if not explicit_generation and current_hour < 20 and current_hour >= 5:
            raise ValueError("当前时间还不适合自动结束当天日记。")
        # 当日已生成过且已确认的日记不再重复覆盖（幂等）。
        today = db.today_string()
        existing_diary = db.get_diary(today)
        if existing_diary is not None and existing_diary.get("confirmed_at"):
            return "diary:already_confirmed"
        from .routes.chat import analyze_today_state
        from .routes.diary import generate_today_diary_payload

        try:
            await analyze_today_state()
        except Exception:
            logger.warning("生成日记前今日状态判定失败，继续生成日记", exc_info=True)
        try:
            await generate_today_diary_payload()
        except Exception as exc:
            logger.warning("今日日记生成失败", exc_info=True)
            raise ValueError(f"今日日记生成失败：{exc}") from exc
        return "diary:generated"

    raise ValueError("动作类型不在白名单内。")


async def execute_companion_actions(
    actions: list[dict[str, Any]],
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> list[dict[str, str]]:
    try:
        actions = _with_continuous_activity_inference(
            actions,
            conversation_id,
            user_message,
            source_message_id,
        )
    except Exception:
        # 推断失败不能影响正常聊天，退回模型给出的原始动作。
        pass
    try:
        actions = _with_deterministic_memory_inference(
            actions,
            conversation_id,
            user_message,
            source_message_id,
        )
    except Exception:
        pass
    actions = _with_deterministic_diary_generation(actions, user_message)
    results: list[dict[str, str]] = []
    expensive_action_used = False
    for action in actions[:MAX_ACTIONS_PER_TURN]:
        action_type = str(action.get("type") or "")
        definition = tool_registry.require(action_type)
        resolve_without_confirmation = (
            action_type == "generate_today_diary"
            or (action_type == "edit_today_diary" and db.get_diary(db.today_string()) is None)
            or (
                action_type == "update_profile"
                and FORBIDDEN_PROFILE_ACTION_RE.search(str(action.get("instruction") or "")) is not None
            )
        )
        requires_confirmation = (
            definition.permission == ToolPermission.HIGH_RISK_WRITE
            and not definition.has_explicit_intent(user_message)
            and not resolve_without_confirmation
        )
        receipt_id = db.start_tool_execution_receipt(
            conversation_id,
            action_type,
            definition.permission.name.lower(),
            _receipt_payload(action),
            "needs_confirmation" if requires_confirmation else "running",
        )
        action_id = db.log_companion_action(
            conversation_id,
            action_type,
            json.dumps(action, ensure_ascii=False),
            "needs_confirmation" if requires_confirmation else "running",
            source_message_id=source_message_id,
            requires_confirmation=requires_confirmation,
        )
        if requires_confirmation:
            db.finish_tool_execution_receipt(receipt_id, "needs_confirmation", f"task:{action_id}")
            results.append(
                {
                    "type": action_type,
                    "status": "needs_confirmation",
                    "result": f"task:{action_id}",
                    "receipt_id": str(receipt_id),
                }
            )
            continue
        if action_type in {"update_profile", "edit_today_diary", "generate_today_diary"}:
            if expensive_action_used:
                db.update_companion_action(action_id, "skipped", "本轮已经执行过一个高成本动作。")
                db.finish_tool_execution_receipt(receipt_id, "skipped", "本轮已经执行过一个高成本动作。")
                results.append(
                    {
                        "type": action_type,
                        "status": "skipped",
                        "result": "本轮已经执行过一个高成本动作。",
                        "receipt_id": str(receipt_id),
                    }
                )
                continue
            expensive_action_used = True
        try:
            result = await execute_companion_action_primitive(
                action, conversation_id, user_message, source_message_id
            )
            status = "executed"
        except Exception as exc:
            result = str(exc)[:500]
            status = "failed"
        db.update_companion_action(action_id, status, result)
        db.finish_tool_execution_receipt(receipt_id, status, result)
        results.append(
            {
                "type": action_type,
                "status": status,
                "result": result,
                "receipt_id": str(receipt_id),
            }
        )
    return results


async def approve_companion_action(action_id: int) -> dict[str, str]:
    row = db.get_companion_action(action_id)
    if row is None:
        raise ValueError("没有找到这个任务。")
    if str(row["status"] or "") != "needs_confirmation":
        raise ValueError("这个任务当前不需要确认。")
    action_type = str(row["action_type"] or "")
    if ACTION_POLICIES.get(action_type) != "confirmation":
        raise ValueError("这个动作不在可确认执行范围内。")
    try:
        action = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("任务参数已经损坏，无法执行。") from exc
    source_message_id = int(row["source_message_id"] or 0)
    source_row = db.get_message_by_id(source_message_id) if source_message_id else None
    user_message = str(source_row["content"] or "") if source_row else ""
    definition = tool_registry.require(action_type)
    if int(row["agent_step_id"] or 0):
        pending_step = db.get_agent_run_step(int(row["agent_step_id"]))
        if pending_step is not None and int(pending_step["receipt_id"] or 0):
            db.finish_tool_execution_receipt(
                int(pending_step["receipt_id"]),
                "approved",
                "用户已批准，转入正式执行。",
            )
    receipt_id = db.start_tool_execution_receipt(
        str(row["conversation_id"] or "default"),
        action_type,
        definition.permission.name.lower(),
        _receipt_payload(action),
        "running",
        request_id=str(row["request_id"] or ""),
        trace_id=str(row["trace_id"] or ""),
        agent_run_id=str(row["agent_run_id"] or ""),
        agent_step_id=int(row["agent_step_id"] or 0),
        action_id=action_id,
        idempotency_key=(
            f"{row['idempotency_key']}:approval"
            if str(row["idempotency_key"] or "")
            else ""
        ),
    )
    db.update_companion_action(action_id, "running", approved=True)
    if int(row["agent_step_id"] or 0):
        db.update_agent_run_step(
            int(row["agent_step_id"]),
            "running",
            receipt_id=receipt_id,
            action_id=action_id,
        )
    try:
        with maintenance_service.mutation_scope():
            result = await execute_companion_action_primitive(
                action,
                str(row["conversation_id"] or "default"),
                user_message,
                source_message_id,
            )
    except Exception as exc:
        result = str(exc)[:500]
        db.update_companion_action(action_id, "failed", result)
        db.finish_tool_execution_receipt(receipt_id, "failed", result)
        if int(row["agent_step_id"] or 0):
            db.update_agent_run_step(int(row["agent_step_id"]), "failed", error=result)
        return {"status": "failed", "result": result, "receipt_id": str(receipt_id)}
    db.update_companion_action(action_id, "executed", result)
    db.finish_tool_execution_receipt(receipt_id, "executed", result)
    if int(row["agent_step_id"] or 0):
        db.update_agent_run_step(
            int(row["agent_step_id"]),
            "completed",
            result_json=json.dumps({"result": result}, ensure_ascii=False),
            receipt_id=receipt_id,
            action_id=action_id,
        )
    return {"status": "executed", "result": result, "receipt_id": str(receipt_id)}


def enrich_companion_actions(
    actions: list[dict[str, Any]],
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> list[dict[str, Any]]:
    enriched = list(actions)
    try:
        enriched = _with_continuous_activity_inference(
            enriched,
            conversation_id,
            user_message,
            source_message_id,
        )
    except Exception:
        pass
    try:
        enriched = _with_deterministic_memory_inference(
            enriched,
            conversation_id,
            user_message,
            source_message_id,
        )
    except Exception:
        pass
    return enriched[:MAX_ACTIONS_PER_TURN]


__all__ = [
    "CompanionDecision",
    "apply_daily_state_assessment",
    "approve_companion_action",
    "backfill_explicit_structured_memories",
    "build_companion_planner_messages",
    "enrich_companion_actions",
    "execute_companion_actions",
    "execute_companion_action_primitive",
    "parse_companion_decision",
    "plan_companion_actions",
]
