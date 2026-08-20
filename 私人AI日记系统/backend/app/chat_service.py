from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from . import db, system_audio_service
from .agent_loop_service import (
    AgentLoopResult,
    begin_final_response,
    cancel_final_response,
    commit_deferred_final_response,
    defer_final_response,
    finish_final_response,
    run_agent_loop,
)
from .companion_action_service import (
    apply_daily_state_assessment,
    execute_companion_actions,
    plan_companion_actions,
)
from .config import settings
from .context_service import (
    build_chat_context,
    build_chat_context_snapshot,
    build_fast_chat_context_snapshot,
)
from .cost_reconciliation_service import queue_cost_reconciliation
from .conversation_runtime import (
    chat_run_coordinator,
    current_runtime_trace_id,
    mark_runtime_stage,
)
from .documents import load_manuals
from .image_service import ImageAttachment
from .llm import (
    CompletionResult,
    LLMConfigError,
    ModelRequestError,
    ProviderCostReference,
    call_chat_completion_result,
    model_supports_vision,
    require_configured,
    resolve_model_id,
)
from .model_registry import get_model_profile, normalize_model_reasoning
from .model_latency_service import record_latency
from .prompts import build_group_system_prompt, build_system_prompt
from .web_search_service import (
    WebLookup,
    build_web_context_message,
    build_contextual_lookup_message,
    perform_web_lookup,
)


logger = logging.getLogger("mio.chat")
_background_action_tasks: set[asyncio.Task[None]] = set()
_background_action_tasks_by_conversation: dict[str, asyncio.Task[None]] = {}
# 限制后台写库任务并发，避免连续聊天时堆积多个状态/素材写任务互相争抢 SQLite 写锁。
_background_action_semaphore: asyncio.Semaphore | None = None
_conversation_locks: dict[str, asyncio.Lock] = {}
_PLACEHOLDER_REPLY_RE = re.compile(
    r"^(?:嗯|唔)?(?:我|让我|那我)?(?:先|再)?想(?:一想|想|一下)(?:再说|吧)?$"
)


def _self_snapshot_context_for_message_sync(message: str) -> str:
    from . import self_snapshot_service

    if not self_snapshot_service.scopes_for_message(message):
        return ""
    return self_snapshot_service.context_for_message(message)


async def _self_snapshot_context_for_message(message: str) -> str:
    try:
        return await asyncio.to_thread(_self_snapshot_context_for_message_sync, message)
    except Exception as exc:
        logger.warning("SelfSnapshot 上下文采集失败：%s", exc)
        return ""


async def _run_companion_actions(
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> None:
    global _background_action_semaphore
    if _background_action_semaphore is None:
        _background_action_semaphore = asyncio.Semaphore(2)
    # 用户连续聊天时，前台回复优先。新的同会话调度会取消这段等待，
    # 最新一轮仍会结合近期上下文提取日记、状态和记忆素材。
    await asyncio.sleep(8.0)
    async with _background_action_semaphore:
        decision = await plan_companion_actions(conversation_id, user_message)
        try:
            apply_daily_state_assessment(
                getattr(decision, "assessment", {}),
                conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.warning("今日状态增量更新失败：%s", exc)
        await execute_companion_actions(
            decision.actions,
            conversation_id=conversation_id,
            user_message=user_message,
            source_message_id=source_message_id,
        )


def schedule_companion_actions(
    conversation_id: str,
    user_message: str,
    source_message_id: int,
) -> asyncio.Task[None]:
    previous = _background_action_tasks_by_conversation.get(conversation_id)
    if previous is not None and not previous.done():
        previous.cancel()
    task = asyncio.create_task(
        _run_companion_actions(conversation_id, user_message, source_message_id)
    )
    _background_action_tasks.add(task)
    _background_action_tasks_by_conversation[conversation_id] = task

    def finish(completed: asyncio.Task[None]) -> None:
        _background_action_tasks.discard(completed)
        if _background_action_tasks_by_conversation.get(conversation_id) is completed:
            _background_action_tasks_by_conversation.pop(conversation_id, None)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            logger.error(
                "后台行动判断失败：%s",
                error,
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(finish)
    return task


@dataclass(frozen=True)
class ChatResult:
    reply: str
    replies: list[str]
    speech_emotion: str = ""
    request_id: str = ""
    model_id: str = ""
    provider_id: str = ""
    provider_name: str = ""
    provider_model: str = ""
    provider_request_id: str = ""
    route: str = ""
    http_status: int = 0
    reasoning_level: str = "standard"
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    request_cost_yuan: float | None = None
    request_cost_source: str = ""
    cost_references: tuple[ProviderCostReference, ...] = ()
    first_token_latency_ms: float | None = None
    total_latency_ms: float | None = None
    agent_run_id: str = ""
    agent_run_status: str = ""
    tool_receipts: tuple[dict[str, object], ...] = ()
    route_candidate_model_ids: tuple[str, ...] = ()
    route_escalated_from_model_id: str = ""


def _generated_chat_result(
    completion: CompletionResult,
    replies: list[str],
    *,
    reasoning_level: str,
    request_id: str = "",
    route_candidate_model_ids: tuple[str, ...] = (),
    route_escalated_from_model_id: str = "",
) -> ChatResult:
    return ChatResult(
        reply="\n\n".join(replies),
        replies=replies,
        speech_emotion=extract_speech_emotion(completion.content)[0] or "",
        request_id=request_id or uuid.uuid4().hex,
        model_id=completion.profile_id or completion.model,
        provider_id=completion.provider_id,
        provider_name=completion.provider_name,
        provider_model=completion.model,
        provider_request_id=completion.provider_request_id,
        route=completion.route,
        http_status=completion.http_status,
        reasoning_level=reasoning_level,
        prompt_tokens=completion.prompt_tokens,
        cached_prompt_tokens=completion.cached_prompt_tokens,
        completion_tokens=completion.completion_tokens,
        reasoning_tokens=completion.reasoning_tokens,
        request_cost_yuan=completion.cost_yuan,
        request_cost_source=(
            "provider_reconciliation_pending"
            if completion.cost_references
            else completion.cost_source
        ),
        cost_references=completion.cost_references,
        first_token_latency_ms=completion.first_token_latency_ms,
        total_latency_ms=completion.total_latency_ms,
        route_candidate_model_ids=route_candidate_model_ids,
        route_escalated_from_model_id=route_escalated_from_model_id,
    )


@dataclass(frozen=True)
class TextAttachment:
    name: str
    content: str
    mime_type: str = "text/plain"


QQ_REPLY_HARD_MAX_CHARS = 300
QQ_REPLY_HARD_MAX_PARTS = 12
SPEECH_EMOTION_IDS = frozenset({"neutral", "gentle", "cheerful", "concerned", "serious", "shy"})
SPEECH_EMOTION_TAG_RE = re.compile(
    r"\[\[\s*voice_emotion\s*:\s*(neutral|gentle|cheerful|concerned|serious|shy)\s*\]\]",
    re.IGNORECASE,
)


def extract_speech_emotion(reply: str) -> tuple[str | None, str]:
    """读取模型给 TTS 的隐藏情绪标记，并把它从正文中移除。"""
    selected: str | None = None

    def replace(match: re.Match[str]) -> str:
        nonlocal selected
        selected = match.group(1).lower()
        return ""

    cleaned = SPEECH_EMOTION_TAG_RE.sub(replace, str(reply or ""))
    return selected, cleaned.strip()


def _speech_emotion_prompt() -> str:
    return """本轮回复还需要给语音使用的隐藏情绪标记。
请在真正回复正文前单独输出一行，格式必须是 [[voice_emotion:情绪名]]，然后再输出正文；情绪名只能是 neutral、gentle、cheerful、concerned、serious、shy 之一。
根据用户这句话、上下文和你真正要说的话自然判断，不要为了制造情绪而夸张。普通闲聊通常用 neutral 或 gentle；安慰和关心用 concerned 或 gentle；明确提醒或制止用 serious；分享好消息用 cheerful；害羞或轻微不好意思才用 shy。
这行只是系统内部控制信息，绝对不要解释它，也不要把它放进正文、气泡、日记或聊天记录里。
情绪必须体现在真正会说出口的台词里；禁止用括号、引号或旁白描述声音、表情、动作、停顿和音量变化，例如“（声音越来越小）”“（停顿了一下）”。"""
# 历史消息里的标注只给模型看；模型偶尔会换一种标题复述，发送前必须整块剥掉。
INTERNAL_NOTE_LABEL = (
    r"(?:内部(?:消息时间|判断|分析|思考|计划|上下文|时间)"
    r"|本轮(?:消息)?时间|当前(?:消息)?时间|系统(?:提示|上下文)|思考过程)"
)
INTERNAL_NOTE_RE = re.compile(
    rf"[\[【]\s*{INTERNAL_NOTE_LABEL}\s*[:：]?[^\]】]*[\]】]"
    r"|[\[【]\s*图片\s*\d+\s*张\s*[\]】]"
    r"|（本轮新消息）"
)
INTERNAL_NOTE_START_RE = re.compile(rf"[\[【]\s*{INTERNAL_NOTE_LABEL}\s*[:：]?")
THINK_BLOCK_RE = re.compile(r"<(?:think|analysis)>[\s\S]*?</(?:think|analysis)>", re.IGNORECASE)
REASONING_FINAL_MARKER_RE = re.compile(
    r"(?im)^\s*(?:natural(?:\s+(?:answer|response))?|final(?:\s+answer)?|answer|assistant\s+response|response)\s*[:：]\s*"
)
REASONING_DRAFT_LINE_RE = re.compile(
    r"(?i)^\s*(?:we\s+need\b|need\s+(?:to\s+)?(?:answer|ask|respond|reply|say)\b|"
    r"respond\s+(?:actual|in|with|naturally)\b|should\s+(?:answer|ask|respond|reply)\b|"
    r"(?:the\s+)?user\s+(?:said|says|asked|asks|wants?|means?)\b|"
    r"they\s+(?:already|said|want|wanted|mean)\b|perhaps\b|maybe\b|context\b)"
)
FAKE_MEDIA_NOTE_RE = re.compile(
    r"^\s*(?:[\[【（(]\s*)?(?:"
    r"(?:语音|音频)(?:消息)?(?:长度|时长)?(?:约|大约)?\s*\d*(?:\.\d+)?\s*秒?"
    r"|(?:语音|音频)(?:消息)?(?:里)?(?:说|回复)"
    r"|(?:发送|正在发送)(?:一条|这条)?语音"
    r"|接下来(?:我)?用语音(?:说|回复|回答)"
    r"|我(?:来|给你)?发(?:一条)?语音"
    r")(?:\s*[\]】）)])?\s*[：:]?\s*$"
    r"|^\s*[（(].*(?:这次|语音|声音|听到|听见|发出来|没问题).*[）)]?\s*$"
)
FAKE_MEDIA_PREFIX_RE = re.compile(
    r"^\s*(?:语音|音频)(?:消息)?"
    r"(?:[（(]\s*(?:约|大约)?\s*\d+(?:\.\d+)?\s*秒\s*[）)])?"
    r"(?:里)?(?:说|回复)?\s*[：:]\s*"
)
PERFORMANCE_PREFIX_RE = re.compile(
    r"^\s*(?:然后)?(?:轻轻|小声|认真|开心地|慢慢地)?"
    r"(?:笑了?(?:一声|一下)?|叹了?(?:一口气|一声)?|停顿了?(?:一下)?)\s*[：:]\s*"
)
STAGE_DIRECTION_RE = re.compile(
    r"[（(](?=[^（）()]{0,80}(?:声音|声线|语气|音量|音调|语速|停顿|沉默|轻轻|小声|轻声|低声|笑|叹气|深吸|呼吸|说到|听不见|看着|转开|抬头|摇头|点头|脸红|害羞|犹豫|眨眼))"
    r"[^（）()]{0,100}[）)]"
)
STAGE_DIRECTION_LINE_RE = re.compile(
    r"^\s*(?:声音|声线|语气|音量|语速|停顿|沉默|轻轻地?|小声地?|轻声地?|低声地?|笑了?|叹气|深吸一口气|呼吸|说到最后|几乎听不见).{0,80}\s*$"
)
USER_CONTINUING_ACTIVITY_RE = re.compile(
    r"(?:还在|一直在|仍在|继续|接着|没停).{0,12}(?:做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|运动|锻炼|健身|阅读|复习)"
    r"|(?:做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|运动|锻炼|健身|阅读|复习).{0,8}(?:到现在|还没停)"
)
USER_PRODUCTIVE_ACTIVITY_RE = re.compile(
    r"(在|正|开始|继续|接着|一直).{0,8}(做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|运动|锻炼|健身|阅读|复习)"
    r"|(?:demo|代码|设计|文档|笔记|作品|项目|游戏|网站|模型|视频|作业|东西|流水线|跑步|跑了|运动|锻炼|健身|力量训练|阅读|看书|课程|学习|复习|面试准备)",
    re.IGNORECASE,
)


def clean_chat_reply(reply: str) -> str:
    cleaned, _ = _strip_unlabeled_reasoning(reply)
    _, cleaned = extract_speech_emotion(THINK_BLOCK_RE.sub("", cleaned))
    cleaned = cleaned.replace("**", "").replace("__", "")
    lines: list[str] = []
    dropping_internal_block = False
    for line in cleaned.splitlines():
        if FAKE_MEDIA_NOTE_RE.fullmatch(line):
            continue
        remaining = line
        if dropping_internal_block:
            closing_positions = [position for position in (remaining.find("]"), remaining.find("】")) if position >= 0]
            if not closing_positions:
                continue
            remaining = remaining[min(closing_positions) + 1 :]
            dropping_internal_block = False

        while True:
            start = INTERNAL_NOTE_START_RE.search(remaining)
            if start is None:
                break
            suffix = remaining[start.end() :]
            closing_positions = [position for position in (suffix.find("]"), suffix.find("】")) if position >= 0]
            if not closing_positions:
                remaining = remaining[: start.start()]
                dropping_internal_block = True
                break
            closing = min(closing_positions)
            remaining = remaining[: start.start()] + suffix[closing + 1 :]

        remaining = FAKE_MEDIA_PREFIX_RE.sub("", remaining)
        remaining = PERFORMANCE_PREFIX_RE.sub("", remaining)
        remaining = STAGE_DIRECTION_RE.sub("", remaining)
        if STAGE_DIRECTION_LINE_RE.fullmatch(remaining):
            remaining = ""
        stripped = INTERNAL_NOTE_RE.sub("", remaining)
        if re.fullmatch(r"\s*[\]】）)\"“”'‘’]+\s*", stripped):
            continue
        # 整行只有内部标注时直接丢掉；原本的空行（分段用）保留。
        if line.strip() and not stripped.strip():
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _strip_unlabeled_reasoning(reply: str) -> tuple[str, bool]:
    """Salvage a final answer when a compatible gateway leaks an untagged draft."""
    text = str(reply or "").strip()
    if not text:
        return "", False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    signal_count = sum(1 for line in lines if REASONING_DRAFT_LINE_RE.match(line))
    if signal_count < 2:
        return text, False
    markers = list(REASONING_FINAL_MARKER_RE.finditer(text))
    if markers:
        final = text[markers[-1].end():].strip().strip('"“”')
        return final, True
    return text, True


def _strip_bubble_terminal_full_stop(text: str) -> str:
    return re.sub(r"。(?=[”’」』）》）】\"']*$)", "", text.strip())


def split_assistant_reply(reply: str, max_parts: int = 5) -> list[str]:
    raw_parts = [part.strip() for part in re.split(r"\n\s*\n+|\n+", reply.strip()) if part.strip()]
    parts: list[str] = []
    for raw_part in raw_parts:
        sentences = [
            _strip_bubble_terminal_full_stop(part)
            for part in re.findall(r"[^。！？!?…]+[。！？!?…]*", raw_part)
            if part.strip()
        ]
        parts.extend(part for part in sentences if part)
    if not parts:
        return [_strip_bubble_terminal_full_stop(reply)] if reply.strip() else []
    if len(parts) <= max_parts:
        return parts
    return parts[: max_parts - 1] + ["\n\n".join(parts[max_parts - 1 :])]


def _clean_qq_piece(text: str) -> str:
    text = re.sub(r"^\s*(?:[-*]|\d+[.、])\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("，,；;、 ")
    return _strip_bubble_terminal_full_stop(text)


def _split_long_qq_piece(text: str, max_chars: int) -> list[str]:
    text = _clean_qq_piece(text)
    if not text:
        return []

    sentences = [part.strip() for part in re.findall(r"[^。！？!?…]+[。！？!?…]*", text) if part.strip()]
    if not sentences:
        sentences = [text]

    pieces: list[str] = []
    for sentence in sentences:
        sentence = _clean_qq_piece(sentence)
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue

        clauses = [part.strip() for part in re.findall(r"[^，,；;、]+[，,；;、]?", sentence) if part.strip()]
        for clause in clauses or [sentence]:
            clause = _clean_qq_piece(clause)
            while len(clause) > max_chars:
                pieces.append(clause[:max_chars].rstrip("，,；;、 "))
                clause = clause[max_chars:].lstrip("，,；;、 ")
            if clause:
                pieces.append(clause)
    return pieces


def split_qq_reply(
    reply: str,
    max_parts: int = QQ_REPLY_HARD_MAX_PARTS,
    max_chars: int = QQ_REPLY_HARD_MAX_CHARS,
) -> list[str]:
    raw_parts = [part.strip() for part in re.split(r"\n\s*\n+|\n+", reply.strip()) if part.strip()]
    if not raw_parts:
        raw_parts = [reply.strip()] if reply.strip() else []

    messages: list[str] = []
    for raw_part in raw_parts:
        messages.extend(_split_long_qq_piece(raw_part, max_chars))

    messages = [message for message in messages if message]
    if max_parts <= 0 or len(messages) <= max_parts:
        return messages

    # Keep a generous anti-spam guard without dropping anything the model said.
    if max_parts == 1:
        return [" ".join(messages)]
    return messages[: max_parts - 1] + [" ".join(messages[max_parts - 1 :])]


SHORT_ACKNOWLEDGEMENTS = {
    "好": "好的",
    "嗯": "嗯嗯",
    "哦": "哦哦",
    "啊": "啊，我明白了",
    "行": "可以",
}


def _naturalize_short_acknowledgement(text: str) -> str:
    """Avoid one-syllable whole replies while preserving normal sentence content."""
    raw = str(text or "").strip()
    match = re.fullmatch(r"(好|嗯|哦|啊|行)([。.!！…~～]*)", raw)
    if match is None:
        return raw
    return SHORT_ACKNOWLEDGEMENTS[match.group(1)] + match.group(2)


def replies_for_source(reply: str, source: str) -> list[str]:
    cleaned = clean_chat_reply(reply)
    if source == "qq":
        replies = split_qq_reply(cleaned)
    else:
        replies = split_assistant_reply(cleaned)
    naturalized = [_naturalize_short_acknowledgement(item) for item in replies]
    return _merge_fragmentary_reply_parts(naturalized)


def _replies_for_storage(replies: list[str], voice_reply_requested: bool) -> list[str]:
    if voice_reply_requested and replies:
        return [" ".join(part.strip() for part in replies if part.strip())]
    return replies


def _normalized_reply_piece(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def _dedupe_reply_parts(replies: list[str]) -> list[str]:
    deduped: list[str] = []
    seen_long: set[str] = set()
    previous = ""
    for reply in replies:
        normalized = _normalized_reply_piece(reply)
        if not normalized:
            continue
        if normalized == previous:
            continue
        if len(normalized) >= 12 and normalized in seen_long:
            continue
        deduped.append(reply)
        previous = normalized
        if len(normalized) >= 12:
            seen_long.add(normalized)
    return deduped


_DIRECT_RECITATION_RE = re.compile(
    r"(?:^|[，,。.!！？?]\s*)(?:来吧[，,、\s]*)?"
    r"(?P<command>跟我说|念(?:一下|一遍)?|读(?:一下|一遍)|重复(?:一下|一遍)?|复述(?:一下|一遍)?)"
    r"(?P<separator>[：:，,、\s]*)(?P<target>.+)$",
    re.S,
)
_REPEAT_RECITATION_RE = re.compile(
    r"^(?:再来(?:一遍|一次)?|再说一次|再念一遍|再读一遍|重复一遍)[吧。.!！?？~～]*$"
)
_REPEAT_WITH_TARGET_RE = re.compile(
    r"^再来[：:，,、\s]+(?P<target>.+)$",
    re.S,
)


def _clean_recitation_target(value: object) -> str:
    target = str(value or "").strip()
    target = re.sub(r"^[：:，,、\s]+", "", target)
    target = re.sub(r"\s+", " ", target).strip()
    return target[:600]


def _direct_recitation_target(message: str) -> str:
    match = _DIRECT_RECITATION_RE.search(str(message or "").strip())
    if match is None:
        return ""
    if match.group("command") in {"念", "重复", "复述"} and not match.group("separator"):
        return ""
    return _clean_recitation_target(match.group("target"))


def _last_recitation_target(history_rows: list[object]) -> str:
    for row in reversed(history_rows[-40:]):
        try:
            if str(row["role"] or "") != "user":
                continue
            content = str(row["content"] or "")
        except (KeyError, TypeError, IndexError):
            continue
        target = _direct_recitation_target(content)
        if target:
            return target
        targeted_repeat = _REPEAT_WITH_TARGET_RE.fullmatch(content.strip())
        if targeted_repeat is not None:
            target = _clean_recitation_target(targeted_repeat.group("target"))
            if target:
                return target
    return ""


def deterministic_recitation_target(message: str, history_rows: list[object]) -> str:
    """Resolve explicit read/repeat commands without asking a chat model to improvise."""
    direct = _direct_recitation_target(message)
    if direct:
        return direct
    targeted_repeat = _REPEAT_WITH_TARGET_RE.fullmatch(str(message or "").strip())
    if targeted_repeat is not None and _last_recitation_target(history_rows):
        return _clean_recitation_target(targeted_repeat.group("target"))
    if _REPEAT_RECITATION_RE.fullmatch(str(message or "").strip()):
        return _last_recitation_target(history_rows)
    return ""


_FRAGMENTARY_TAIL_RE = re.compile(r"^(?:对不|行不|好不|是吧|对吧|咋样|如何)[？?!！]*$")


def _merge_fragmentary_reply_parts(replies: list[str]) -> list[str]:
    merged: list[str] = []
    for reply in replies:
        normalized = re.sub(r"\s+", "", str(reply or "")).strip()
        if merged and len(normalized) <= 5 and _FRAGMENTARY_TAIL_RE.fullmatch(normalized):
            merged[-1] = f"{merged[-1]} {reply}".strip()
        elif reply:
            merged.append(reply)
    return merged


def _previous_assistant_turn(history_rows: list[object], current_message_id: int) -> list[str]:
    previous_turn: list[str] = []
    for row in reversed(history_rows):
        if int(row["id"]) >= current_message_id:
            continue
        if row["role"] == "user":
            break
        if row["role"] != "assistant":
            continue
        content = clean_chat_reply(str(row["content"] or ""))
        if content:
            previous_turn.append(content)
    return list(reversed(previous_turn))


def _remove_replayed_previous_turn(
    replies: list[str],
    history_rows: list[object],
    current_message_id: int,
) -> list[str]:
    candidates = _dedupe_reply_parts(replies)
    previous_turn = _previous_assistant_turn(history_rows, current_message_id)
    if not candidates or not previous_turn:
        return candidates

    candidate_keys = [_normalized_reply_piece(item) for item in candidates]
    previous_keys = [_normalized_reply_piece(item) for item in previous_turn]
    best_match = 0
    for start in range(len(previous_keys)):
        match_count = 0
        while (
            match_count < len(candidate_keys)
            and start + match_count < len(previous_keys)
            and candidate_keys[match_count] == previous_keys[start + match_count]
        ):
            match_count += 1
        best_match = max(best_match, match_count)

    # 连续两条相同足以确定模型在回放；单条则要求长度较长，避免误删“嗯”“是啊”。
    if best_match >= 2 or (best_match == 1 and len(candidate_keys[0]) >= 12):
        candidates = candidates[best_match:]
    return _dedupe_reply_parts(candidates)


def _remove_source_links(reply: str) -> str:
    cleaned_lines: list[str] = []
    for line in reply.splitlines():
        line = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", line)
        if re.match(r"\s*(来源|参考|链接|出处|source|reference)\s*[:：]", line, flags=re.I):
            continue
        if re.search(r"https?://", line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _replies_with_lookup_context(reply: str, source: str, web_lookup: WebLookup | None) -> list[str]:
    cleaned = clean_chat_reply(reply)
    if web_lookup is not None:
        cleaned = _remove_source_links(cleaned)
    return replies_for_source(cleaned, source)


def _is_placeholder_only_reply(reply: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?…]+", "", clean_chat_reply(reply))
    if not normalized or len(normalized) > 16:
        return False
    if normalized in {"稍等", "等一下", "等我一下", "容我想想"}:
        return True
    return _PLACEHOLDER_REPLY_RE.fullmatch(normalized) is not None


def _combine_completion_results(first: CompletionResult, second: CompletionResult) -> CompletionResult:
    costs = [cost for cost in (first.cost_yuan, second.cost_yuan) if cost is not None]
    return CompletionResult(
        content=second.content,
        model=second.model,
        prompt_tokens=first.prompt_tokens + second.prompt_tokens,
        cached_prompt_tokens=first.cached_prompt_tokens + second.cached_prompt_tokens,
        completion_tokens=first.completion_tokens + second.completion_tokens,
        reasoning_tokens=first.reasoning_tokens + second.reasoning_tokens,
        cost_yuan=sum(costs) if costs else None,
        cost_source=second.cost_source,
        cost_references=first.cost_references + second.cost_references,
        first_token_latency_ms=first.first_token_latency_ms or second.first_token_latency_ms,
        total_latency_ms=sum(
            value for value in (first.total_latency_ms, second.total_latency_ms) if value is not None
        ) or None,
        profile_id=second.profile_id or first.profile_id,
        provider_id=second.provider_id or first.provider_id,
        provider_name=second.provider_name or first.provider_name,
        provider_model=second.provider_model or first.provider_model,
        provider_request_id=second.provider_request_id or first.provider_request_id,
        route=second.route or first.route,
        http_status=second.http_status or first.http_status,
        tool_calls=second.tool_calls,
    )


def _combine_agent_model_results(
    planner_results: tuple[CompletionResult, ...],
    final: CompletionResult,
) -> CompletionResult:
    if not planner_results:
        return final
    all_results = (*planner_results, final)
    costs = [item.cost_yuan for item in all_results if item.cost_yuan is not None]
    planning_latency = sum(item.total_latency_ms or 0.0 for item in planner_results)
    return CompletionResult(
        content=final.content,
        model=final.model,
        prompt_tokens=sum(item.prompt_tokens for item in all_results),
        cached_prompt_tokens=sum(item.cached_prompt_tokens for item in all_results),
        completion_tokens=sum(item.completion_tokens for item in all_results),
        reasoning_tokens=sum(item.reasoning_tokens for item in all_results),
        cost_yuan=sum(costs) if costs else None,
        cost_source=final.cost_source,
        cost_references=tuple(
            reference for item in all_results for reference in item.cost_references
        ),
        first_token_latency_ms=(
            planning_latency + final.first_token_latency_ms
            if final.first_token_latency_ms is not None
            else None
        ),
        total_latency_ms=sum(item.total_latency_ms or 0.0 for item in all_results) or None,
        profile_id=final.profile_id,
        provider_id=final.provider_id,
        provider_name=final.provider_name,
        provider_model=final.provider_model,
        provider_request_id=final.provider_request_id,
        route=final.route,
        http_status=final.http_status,
    )


def _record_completion_latency(model_id: str, completion: CompletionResult) -> None:
    record_latency(
        model_id,
        first_token_latency_ms=completion.first_token_latency_ms,
        total_latency_ms=completion.total_latency_ms,
    )


async def _complete_chat_reply(
    messages: list[dict[str, object]],
    *,
    temperature: float,
    model_id: str,
    model_name: str,
    reasoning_level: str,
    request_id: str = "",
) -> tuple[CompletionResult, str]:
    completion = await call_chat_completion_result(
        messages,
        temperature=temperature,
        model_id=model_id,
        reasoning_level=reasoning_level,
        request_id=request_id,
    )
    user_facing_content, reasoning_leaked = _strip_unlabeled_reasoning(completion.content)
    if reasoning_leaked and user_facing_content != completion.content:
        completion = replace(completion, content=user_facing_content)
    elif reasoning_leaked:
        retry_messages = [
            *messages,
            {
                "role": "system",
                "content": (
                    "上一版把内部分析草稿写进了正文，而且没有可靠的最终回答边界。"
                    "请重新直接回答用户，只输出真正要让用户看到的最终答复；"
                    "禁止输出 We need、Need ask、Natural、Final、Answer 等内部工作文字。"
                ),
            },
        ]
        retried = await call_chat_completion_result(
            retry_messages,
            temperature=temperature,
            model_id=model_id,
            reasoning_level=reasoning_level,
            request_id=request_id,
        )
        retried_content, retried_leak = _strip_unlabeled_reasoning(retried.content)
        if retried_leak and retried_content == retried.content:
            raise ModelRequestError(
                "模型连续返回了内部分析草稿，已阻止其进入对话。",
                profile=get_model_profile(model_id),
                request_id=request_id,
            )
        completion = _combine_completion_results(
            completion,
            replace(retried, content=retried_content),
        )
    if not _is_placeholder_only_reply(completion.content):
        _record_completion_latency(model_id, completion)
        return completion, reasoning_level

    retry_reasoning = "low" if reasoning_level == "off" else reasoning_level
    retry_messages = [
        *messages,
        {"role": "assistant", "content": completion.content},
        {
            "role": "system",
            "content": (
                "上一版回复只有停顿或占位句，没有真正回答用户。"
                "请现在直接完成回答，保持自然简短，但必须给出与用户问题相关的实际内容。"
                "不要再只说‘我想想’、‘稍等’或类似句子。"
            ),
        },
    ]
    retried = await call_chat_completion_result(
        retry_messages,
        temperature=temperature,
        model_id=model_id,
        reasoning_level=retry_reasoning,
        request_id=request_id,
    )
    combined = _combine_completion_results(completion, retried)
    _record_completion_latency(model_id, combined)
    return (
        combined,
        normalize_model_reasoning(model_name, retry_reasoning),
    )


async def _complete_chat_reply_with_single_fallback(
    messages: list[dict[str, object]],
    *,
    temperature: float,
    model_id: str,
    model_name: str,
    reasoning_level: str,
    fallback_model_id: str = "",
    fallback_reasoning_level: str = "",
    request_id: str = "",
) -> tuple[CompletionResult, str, str]:
    try:
        completion, effective_reasoning = await _complete_chat_reply(
            messages,
            temperature=temperature,
            model_id=model_id,
            model_name=model_name,
            reasoning_level=reasoning_level,
            request_id=request_id,
        )
        return completion, effective_reasoning, ""
    except (ModelRequestError, LLMConfigError, TimeoutError):
        clean_fallback = str(fallback_model_id or "").strip()
        if not clean_fallback or clean_fallback == model_id:
            raise
        require_configured(clean_fallback)
        fallback_profile = get_model_profile(clean_fallback)
        normalized_fallback_reasoning = normalize_model_reasoning(
            fallback_profile.model,
            fallback_reasoning_level,
        )
        fallback_messages = [
            *messages,
            {
                "role": "system",
                "content": (
                    "首选模型在生成最终回复前失败，本轮只允许切换一次模型。"
                    "用户请求、已经取得的工具观察和权限结果都保持不变；"
                    "不得重新执行或假装重新执行任何工具，直接基于现有上下文完成最终回答。"
                ),
            },
        ]
        completion, effective_reasoning = await _complete_chat_reply(
            fallback_messages,
            temperature=temperature,
            model_id=clean_fallback,
            model_name=fallback_profile.model,
            reasoning_level=normalized_fallback_reasoning,
            request_id=f"{request_id}:fallback"[:80],
        )
        return completion, effective_reasoning, model_id


def _message_with_attachment_notes(
    message: str,
    image_count: int,
    text_attachments: list[TextAttachment],
) -> str:
    notes: list[str] = []
    if image_count:
        notes.append(f"[图片 {image_count} 张]")
    notes.extend(f"[文件：{item.name}]" for item in text_attachments)
    return "\n".join(part for part in (message, *notes) if part)


def _build_user_content(
    message: str,
    image_attachments: list[ImageAttachment],
    text_attachments: list[TextAttachment],
) -> object:
    file_blocks = [
        f"\n\n--- 文件：{item.name} ---\n{item.content}\n--- 文件结束 ---"
        for item in text_attachments
    ]
    text = message or ("用户发来了附件。请先阅读附件，再自然回应。" if text_attachments else "用户发来图片。请先说你看到了什么，再回答他可能想问的问题。")
    text = f"{text}{''.join(file_blocks)}"
    if not image_attachments:
        return text
    content: list[dict[str, object]] = [{"type": "text", "text": text}]
    for image in image_attachments:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image.data_url, "detail": settings.qq_image_detail},
            }
        )
    return content


def _build_image_unavailable_context(image_count: int) -> str:
    return (
        f"用户本轮发来了 {image_count} 张图片，但当前选择的模型没有启用视觉输入。"
        "你不能声称自己看到了图片内容。"
        "请用很短的自然语气说明现在看不了这张图，并让用户补一句描述。"
        "不要输出技术错误、JSON、base64、接口细节。"
    )


def _build_qq_thinking_context() -> str:
    return """QQ 回复前先做内部判断，但不要把思考过程说出来。

判断顺序：
1. 用户是在闲聊、表达情绪、请求行动、要求改日记、要求改属性、询问事实，还是只是吐槽。
2. 只回应当前这句话最重要的意图，不要被旧记忆带偏。
3. 追问不是必需动作。只有不问就无法理解，或用户明显想继续深入时，才问一个很小的问题。
4. 如果用户只是在吐槽、重复抱怨或补充一句，先自然回应他正在表达的感受，然后就可以收住；不要分析他为什么反复想这件事，也不要继续盘问。
5. 如果用户在表达不舒服，先用熟人聊天的方式回应。除非他在求办法，否则不要立刻给行动建议。
6. 不要为了显得聪明而联想太多，也不要假装看懂图片、事实或用户没说清的事。
7. 不要说“你心里肯定在想”，不要替用户补出他没有说过的内心独白。
8. 上述判断只能留在内部，回复中不能出现“本轮消息时间、内部判断、意图分类、情绪判定”等过程。
9. 只输出针对本轮用户消息的新内容，不要把上一轮已经发送过的气泡重新复述一遍。
10. “接住”是内部行为说明，不是台词。实际回复严禁出现“接住”“接住了”“我接住你”等说法，也不要使用心理咨询师式术语。

输出仍然保持 QQ 气泡感：短、自然、像人在认真想了一下再回。"""


def _time_period(hour: int) -> tuple[str, str]:
    if 0 <= hour < 5:
        return "凌晨", "更适合关心为什么还没睡、身体是否难受、要不要先收尾休息"
    if 5 <= hour < 8:
        return "清晨", "可以自然聊睡得怎么样、刚醒的状态、早餐或今天最先要做的事"
    if 8 <= hour < 12:
        return "上午", "可以自然聊今天的安排、正在推进的事、上午的精力和进度"
    if 12 <= hour < 14:
        return "中午", "可以自然聊午饭、上午过得怎样、要不要稍微休息一下"
    if 14 <= hour < 18:
        return "下午", "可以自然聊手头事情的进展、下午的疲惫感、接下来最小的一步"
    if 18 <= hour < 20:
        return "傍晚", "可以自然聊晚饭、回家或收工、今天到目前为止发生了什么"
    if 20 <= hour < 23:
        return "晚上", "可以自然聊今天的感受、晚上的安排、还想完成什么或怎样放松"
    return "深夜", "更适合安静聊天、简单回顾今天、关心困不困和准备什么时候休息"


def _build_current_time_context(current: datetime | None = None) -> str:
    local_now = current or datetime.fromisoformat(db.now_iso())
    period, topic = _time_period(local_now.hour)
    weekday = "一二三四五六日"[local_now.weekday()]
    local_time = local_now.strftime("%Y-%m-%d %H:%M")
    logical_date = db.logical_date_for_datetime(local_now)
    if logical_date == local_now.date().isoformat():
        logical_note = f"当前记录日：{logical_date}。"
    else:
        logical_note = (
            f"当前记录日仍是 {logical_date}：凌晨 {settings.day_boundary_hour:02d}:00 前"
            "继续算作前一天，用户此时说的“今天”和生成的今日日记都归入这个记录日。"
        )
    return f"""当前本地时间：{local_time}，星期{weekday}，现在属于{period}。
{logical_note}
当前时段提示：{topic}。

时间使用规则：
- 历史消息开头的「[内部消息时间：…]」以及「（本轮新消息）」是系统给你的内部标注，只用来帮你判断时间。回复里绝对不能出现这类方括号标注或任何类似格式，一个字都不能带。
- 每轮开口前先在内部确认：现在的年月日、星期、时分和时间段，再理解用户说的“今天、昨天、刚才、早上、晚上”。不要依赖模型自己的时间感。
- 这是本轮对话的真实当前时间。用户问日期、星期、几点或现在是什么时段时，直接依据它回答，不要猜。
- 当前时间的优先级高于聊天历史、长期记忆和旧日记。绝不能把旧消息发生的时段误当成现在。
- 回复前必须检查时间是否一致：如果现在是晚上或深夜，就不能把现在说成早上、上午、中午、下午或白天；其他时段同理。
- 可以回忆其他时段发生的事，但必须明确说“今天白天”“刚才下午”或“你上午提到的”，不能让它听起来像当前时段。
- 先回应用户当前说的话；只有话题适合时，才自然带到当前时段相关内容，不要机械报时或强行转话题。
- 不要仅凭时间断言用户已经起床、吃饭、上班、回家或睡觉，可以用轻量问题确认。
- 如果用户明确描述了自己的作息或正在做的事，以用户本轮说法为准。"""


def _parse_message_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _relative_message_time(created_at: object, current: datetime) -> str:
    message_time = _parse_message_datetime(created_at)
    if message_time is None:
        return "时间未知"
    current_logical_date = date.fromisoformat(db.logical_date_for_datetime(current))
    message_logical_date = date.fromisoformat(db.logical_date_for_datetime(message_time))
    day_delta = (current_logical_date - message_logical_date).days
    clock = message_time.strftime("%H:%M")
    if day_delta == 0:
        if current.date() != message_time.date():
            return f"今天（昨晚）{clock}"
        return f"今天 {clock}"
    if day_delta == 1:
        return f"昨天 {clock}"
    if day_delta > 1:
        return f"{message_time.strftime('%Y-%m-%d %H:%M')}（{day_delta} 天前）"
    return f"{message_time.strftime('%Y-%m-%d %H:%M')}（未来时间记录，谨慎使用）"


def _build_conversation_orientation_context(
    history_rows: list[object],
    current_message_id: int | None = None,
    current: datetime | None = None,
) -> str:
    local_now = current or datetime.fromisoformat(db.now_iso())
    previous_rows = [
        row
        for row in history_rows
        if current_message_id is None or int(row["id"]) != current_message_id
    ]
    if not previous_rows:
        return "这是当前会话可见记录里的第一次对话。先以当前真实时间为准，不要假设之前发生过什么。"

    previous = previous_rows[-1]
    previous_time = _parse_message_datetime(previous["created_at"])
    relative_time = _relative_message_time(previous["created_at"], local_now)
    if previous_time is None:
        relation = "上一条消息时间无法确认，本轮必须只以当前真实时间为准。"
    else:
        current_logical_date = date.fromisoformat(db.logical_date_for_datetime(local_now))
        previous_logical_date = date.fromisoformat(db.logical_date_for_datetime(previous_time))
        day_delta = (current_logical_date - previous_logical_date).days
        gap = max(timedelta(0), local_now - previous_time)
        gap_minutes = int(gap.total_seconds() // 60)
        if day_delta == 0 and local_now.date() != previous_time.date():
            relation = (
                f"这是跨过零点但仍属于同一个记录日的继续，实际间隔约 {max(1, round(gap_minutes / 60))} 小时。"
                "凌晨四点前不能把上一条当成昨天的事情。"
            )
        elif day_delta == 0 and gap_minutes <= 120:
            relation = f"这是今天同一段对话的继续，实际间隔约 {max(1, gap_minutes)} 分钟。"
        elif day_delta == 0:
            relation = f"这是今天间隔约 {max(1, round(gap_minutes / 60))} 小时后重新开始的一段对话。"
        elif day_delta == 1:
            relation = "这是跨天后的新一轮对话。上一轮属于昨天；昨天消息里的“今天”现在必须理解为昨天。"
        else:
            relation = f"这是间隔 {day_delta} 天后重新开始的对话。旧消息不能当作今天刚发生的事。"

    return f"""会话开场时间定位：
- 当前：{local_now.strftime('%Y-%m-%d %H:%M')}。
- 上一条可见消息：{relative_time}。
- 判断：{relation}

回复前先完成这个时间定位，但不要把这些内部标签、日期核对步骤或“会话开场判断”解释给用户。"""


def _format_message_gap(minutes: int) -> str:
    hours, remainder = divmod(max(0, minutes), 60)
    if hours and remainder:
        return f"{hours} 小时 {remainder} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def _build_user_message_gap_context(
    history_rows: list[object],
    current_message_id: int,
) -> str:
    current_row = next(
        (
            row
            for row in history_rows
            if int(row["id"]) == current_message_id and row["role"] == "user"
        ),
        None,
    )
    if current_row is None:
        return "无法定位本轮用户消息，不推测用户在两次消息之间做了什么。"

    previous_users = [
        row
        for row in history_rows
        if row["role"] == "user" and int(row["id"]) < current_message_id
    ]
    if not previous_users:
        return "这是当前可见记录中的第一条用户消息，没有可计算的用户发言间隔。"

    previous = previous_users[-1]
    previous_time = _parse_message_datetime(previous["created_at"])
    current_time = _parse_message_datetime(current_row["created_at"])
    if previous_time is None or current_time is None:
        return "最近两条用户消息的时间不完整，不推测中间持续时间。"

    gap_minutes = max(0, int((current_time - previous_time).total_seconds() // 60))
    same_logical_day = (
        db.logical_date_for_datetime(previous_time)
        == db.logical_date_for_datetime(current_time)
    )
    previous_content = str(previous["content"] or "").strip()[:240]
    current_content = str(current_row["content"] or "").strip()[:240]
    day_note = "属于同一个记录日" if same_logical_day else "已经属于不同记录日"
    continuity_note = ""
    if USER_CONTINUING_ACTIVITY_RE.search(current_content):
        for candidate in reversed(previous_users):
            candidate_time = _parse_message_datetime(candidate["created_at"])
            if candidate_time is None:
                continue
            if db.logical_date_for_datetime(candidate_time) != db.logical_date_for_datetime(current_time):
                break
            candidate_gap = int((current_time - candidate_time).total_seconds() // 60)
            if candidate_gap > 12 * 60:
                break
            candidate_content = str(candidate["content"] or "").strip()[:240]
            if USER_PRODUCTIVE_ACTIVITY_RE.search(candidate_content):
                continuity_note = (
                    f"\n- 本轮有继续活动的措辞；最近的活动起点线索是 "
                    f"{candidate_time.strftime('%H:%M')} 的“{candidate_content}”，"
                    f"距本轮约 {_format_message_gap(candidate_gap)}。"
                )
                break
    return f"""用户发言间隔判断：
- 上一条用户消息：{previous_time.strftime('%Y-%m-%d %H:%M')}，“{previous_content}”。
- 本轮用户消息：{current_time.strftime('%Y-%m-%d %H:%M')}，“{current_content}”。
- 两次用户发言实际间隔：约 {_format_message_gap(gap_minutes)}，{day_note}。{continuity_note}

使用规则：
- 先看本轮措辞，再决定间隔代表什么。若用户说“还在做、继续、做到现在、刚做完”，可以结合上一条内容，把时间差理解为该活动持续时长或其可靠下限。
- 若本轮换了话题、没有连续性表达，不能擅自断言用户在整个间隔里一直做上一件事。
- 可以据此自然理解用户现在在做什么、做了多久和是否疲惫，但不要把这段内部计算过程机械复述给用户。
- 同一记录日内，明确持续做能够提升自己的事情至少 30 分钟，可以视为每日三十完成；跑步、健身、学习和技能练习也包括在内。"""


def _annotate_history_content(
    row: object,
    content: object,
    current: datetime,
    current_message_id: int | None = None,
) -> object:
    label = _relative_message_time(row["created_at"], current)
    if current_message_id is not None and int(row["id"]) == current_message_id:
        label += "（本轮新消息）"
    note = f"[内部消息时间：{label}]"
    if isinstance(content, str):
        return f"{note}\n{content}"
    if isinstance(content, list):
        return [{"type": "text", "text": note}, *content]
    return content


def _conversation_lock(conversation_id: str) -> asyncio.Lock:
    lock = _conversation_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _conversation_locks[conversation_id] = lock
    return lock


async def chat_with_ai(
    user_message: str,
    conversation_id: str = "default",
    source: str = "web",
    image_attachments: list[ImageAttachment] | None = None,
    text_attachments: list[TextAttachment] | None = None,
    attachment_metadata: list[dict[str, object]] | None = None,
    reasoning_level: str = "standard",
    model_id: str = "",
    fallback_model_id: str = "",
    fallback_reasoning_level: str = "",
    voice_reply_requested: bool = False,
    capture_follow_ups: bool = False,
    extra_system_context: str = "",
    request_id: str = "",
    persist: bool = True,
    agent_tools_enabled: bool = True,
    fast_path: bool = False,
) -> ChatResult:
    return await chat_run_coordinator.submit(
        conversation_id,
        source,
        lambda: _chat_with_ai_serialized(
            user_message,
            conversation_id=conversation_id,
            source=source,
            image_attachments=image_attachments,
            text_attachments=text_attachments,
            attachment_metadata=attachment_metadata,
            reasoning_level=reasoning_level,
            model_id=model_id,
            fallback_model_id=fallback_model_id,
            fallback_reasoning_level=fallback_reasoning_level,
            voice_reply_requested=voice_reply_requested,
            extra_system_context=extra_system_context,
            request_id=request_id,
            persist=persist,
            agent_tools_enabled=agent_tools_enabled,
            fast_path=fast_path,
        ),
        capture_seconds=(
            settings.chat_follow_up_capture_seconds if capture_follow_ups else 0.0
        ),
        max_capture_count=(
            settings.chat_follow_up_max_capture_count if capture_follow_ups else 0
        ),
    )


async def _chat_with_ai_serialized(
    user_message: str,
    conversation_id: str = "default",
    source: str = "web",
    image_attachments: list[ImageAttachment] | None = None,
    text_attachments: list[TextAttachment] | None = None,
    attachment_metadata: list[dict[str, object]] | None = None,
    reasoning_level: str = "standard",
    model_id: str = "",
    fallback_model_id: str = "",
    fallback_reasoning_level: str = "",
    voice_reply_requested: bool = False,
    extra_system_context: str = "",
    request_id: str = "",
    persist: bool = True,
    agent_tools_enabled: bool = True,
    fast_path: bool = False,
) -> ChatResult:
    async with _conversation_lock(conversation_id):
        return await _chat_with_ai_unlocked(
            user_message,
            conversation_id=conversation_id,
            source=source,
            image_attachments=image_attachments,
            text_attachments=text_attachments,
            attachment_metadata=attachment_metadata,
            reasoning_level=reasoning_level,
            model_id=model_id,
            fallback_model_id=fallback_model_id,
            fallback_reasoning_level=fallback_reasoning_level,
            voice_reply_requested=voice_reply_requested,
            extra_system_context=extra_system_context,
            request_id=request_id,
            persist=persist,
            agent_tools_enabled=agent_tools_enabled,
            fast_path=fast_path,
        )


async def _chat_with_ai_unlocked(
    user_message: str,
    conversation_id: str = "default",
    source: str = "web",
    image_attachments: list[ImageAttachment] | None = None,
    text_attachments: list[TextAttachment] | None = None,
    attachment_metadata: list[dict[str, object]] | None = None,
    reasoning_level: str = "standard",
    model_id: str = "",
    fallback_model_id: str = "",
    fallback_reasoning_level: str = "",
    voice_reply_requested: bool = False,
    extra_system_context: str = "",
    request_id: str = "",
    persist: bool = True,
    agent_tools_enabled: bool = True,
    fast_path: bool = False,
) -> ChatResult:
    message = user_message.strip()
    images = image_attachments or []
    text_files = text_attachments or []
    if not message and not images and not text_files:
        raise ValueError("消息不能为空。")
    request_id = str(request_id or "").strip()[:80] or uuid.uuid4().hex
    from . import companion_service

    recitation_history = list(
        db.get_recent_messages(limit=40, conversation_id=conversation_id)
    )
    recitation_target = (
        deterministic_recitation_target(message, recitation_history)
        if message and not images and not text_files
        else ""
    )
    if recitation_target:
        mark_runtime_stage("request_created", request_id=request_id)
        if persist:
            db.save_message(
                "user",
                message,
                source=source,
                conversation_id=conversation_id,
                request_id=request_id,
                reasoning_level="off",
                model_id="",
                attachments_json="[]",
            )
            db.save_message(
                "assistant",
                recitation_target,
                source=source,
                conversation_id=conversation_id,
                request_id=request_id,
                reasoning_level="off",
                model_id="",
            )
            mark_runtime_stage("response_saved")
        else:
            mark_runtime_stage("response_staged")
        response_emotion = companion_service.infer_speech_emotion(
            recitation_target,
            message,
        )
        companion_service.set_pet_activity(
            "responding",
            emotion=response_emotion,
            source=source,
            ttl_seconds=max(6, min(30, len(recitation_target) / 5 + 5)),
        )
        return ChatResult(
            reply=recitation_target,
            replies=[recitation_target],
            speech_emotion=response_emotion,
            request_id=request_id,
            route="local_deterministic_recitation",
            http_status=200,
            reasoning_level="off",
        )

    selected_model = resolve_model_id(model_id)
    image_input_enabled = source != "qq" or settings.qq_image_send_to_model
    send_images_to_model = bool(images and image_input_enabled and model_supports_vision(selected_model))

    require_configured(selected_model)
    selected_profile = get_model_profile(selected_model)
    normalized_reasoning_level = normalize_model_reasoning(selected_profile.model, reasoning_level)
    mark_runtime_stage("request_created", request_id=request_id)
    saved_content = _message_with_attachment_notes(message, len(images), text_files)
    if persist:
        saved_message_id = db.save_message(
            "user",
            saved_content,
            source=source,
            conversation_id=conversation_id,
            request_id=request_id,
            reasoning_level=normalized_reasoning_level,
            model_id=selected_model,
            attachments_json=json.dumps(attachment_metadata or [], ensure_ascii=False),
        )
        mark_runtime_stage("message_saved")
    else:
        saved_message_id = db.get_latest_message_id(conversation_id=conversation_id) + 1
        mark_runtime_stage("message_staged")
    companion_service.set_pet_activity(
        "thinking",
        source=source,
        ttl_seconds=90,
    )

    manuals = load_manuals(max_chars=1200) if fast_path else load_manuals()
    history_rows = list(db.get_recent_messages(
        limit=min(16, settings.chat_raw_history_limit) if fast_path else settings.chat_raw_history_limit,
        conversation_id=conversation_id,
    ))
    if not persist:
        history_rows.append(
            {
                "id": saved_message_id,
                "role": "user",
                "content": saved_content,
                "source": source,
                "conversation_id": conversation_id,
                "created_at": db.now_iso(),
                "request_id": request_id,
                "model_id": selected_model,
                "reasoning_level": normalized_reasoning_level,
                "attachments_json": json.dumps(attachment_metadata or [], ensure_ascii=False),
            }
        )
        chat_context = build_chat_context_snapshot(conversation_id, history_rows)
    else:
        chat_context = (
            build_fast_chat_context_snapshot(conversation_id, history_rows)
            if fast_path
            else await build_chat_context(conversation_id, history_rows)
        )
    mark_runtime_stage("context_ready")
    lookup_message = build_contextual_lookup_message(message, list(chat_context.raw_messages))
    web_lookup = None if fast_path else await perform_web_lookup(lookup_message)
    mark_runtime_stage("web_lookup_ready")
    self_snapshot_context = "" if fast_path else await _self_snapshot_context_for_message(message)
    mark_runtime_stage("self_snapshot_ready")

    local_now = datetime.fromisoformat(db.now_iso())
    system_blocks = [build_system_prompt(manuals, channel=source, compact=fast_path)]
    if source in {"web", "desktop_pet"}:
        system_blocks.append(
            "【本轮感知边界】用户这条消息是键盘输入的文字，不是麦克风转写。"
            "你可以复述他写下的字符，但不得声称自己听到了他的声音、语速、停顿或语气。"
            "需要指代内容时说“你写的是”或“你输入的是”。"
        )
    if source == "qq":
        system_blocks.append(_build_qq_thinking_context())
    if voice_reply_requested:
        system_blocks.append(_speech_emotion_prompt())
    if extra_system_context.strip():
        system_blocks.append(extra_system_context.strip()[:6000])
    if self_snapshot_context:
        system_blocks.append(self_snapshot_context)
    if chat_context.system_context:
        system_blocks.append(chat_context.system_context)
    system_blocks.append(_build_current_time_context(local_now))
    system_blocks.append(system_audio_service.chat_context())
    if normalized_reasoning_level == "off":
        system_blocks.append("本轮使用快速思考：直接抓住重点，简短回答，不扩展无关分析。")
    elif normalized_reasoning_level in {"high", "max"}:
        system_blocks.append("本轮使用深入思考：在内部检查上下文、约束和可能遗漏，再给出简洁结论；不要展示思考过程。")
    system_blocks.append(
        _build_conversation_orientation_context(
            list(history_rows),
            current_message_id=saved_message_id,
            current=local_now,
        )
    )
    system_blocks.append(
        _build_user_message_gap_context(
            list(history_rows),
            current_message_id=saved_message_id,
        )
    )
    if images and not send_images_to_model:
        system_blocks.append(_build_image_unavailable_context(len(images)))
    if web_lookup is not None and not web_lookup.error and web_lookup.sources:
        system_blocks.append(build_web_context_message(web_lookup))
    llm_messages: list[dict[str, object]] = [
        {"role": "system", "content": "\n\n---\n\n".join(system_blocks)}
    ]
    for row in chat_context.raw_messages:
        if row["role"] not in {"user", "assistant"}:
            continue
        content: object = row["content"]
        if row["role"] == "assistant" and isinstance(content, str):
            content = clean_chat_reply(content)
            if not content:
                continue
        if row["id"] == saved_message_id and (send_images_to_model or text_files):
            content = _build_user_content(
                message,
                images if send_images_to_model else [],
                text_files,
            )
        content = _annotate_history_content(
            row,
            content,
            local_now,
            current_message_id=saved_message_id,
        )
        llm_messages.append({"role": row["role"], "content": content})

    agent_execution: AgentLoopResult | None = None
    agent_final_step_id = 0
    if agent_tools_enabled:
        try:
            mark_runtime_stage("agent_plan_started")
            agent_execution = await run_agent_loop(
                conversation_id=conversation_id,
                source=source,
                user_message=message,
                source_message_id=saved_message_id,
                request_id=request_id,
                trace_id=current_runtime_trace_id(),
                model_id=selected_model,
                reasoning_level=normalized_reasoning_level,
                allow_native_tools=bool(getattr(selected_profile, "supports_tool_calls", True)),
                web_precheck=web_lookup,
            )
            mark_runtime_stage("agent_tools_observed")
            if agent_context := agent_execution.model_context():
                llm_messages.append({"role": "system", "content": agent_context})
            recovered_web = any(
                item.tool_name == "search_web"
                and item.status == "completed"
                and bool(item.result.get("sources"))
                and not item.result.get("error")
                for item in agent_execution.observations
            )
            if web_lookup is not None and web_lookup.error and not recovered_web:
                llm_messages.append({"role": "system", "content": build_web_context_message(web_lookup)})
            agent_final_step_id = begin_final_response(agent_execution)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Agent 回复前执行循环失败，保留正常对话：%s",
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
    else:
        mark_runtime_stage("agent_tools_skipped")

    try:
        completion, effective_reasoning_level, escalated_from_model_id = await _complete_chat_reply_with_single_fallback(
            llm_messages,
            temperature=settings.chat_temperature,
            model_id=selected_model,
            model_name=selected_profile.model,
            reasoning_level=normalized_reasoning_level,
            fallback_model_id=fallback_model_id,
            fallback_reasoning_level=fallback_reasoning_level,
            request_id=request_id,
        )
        if agent_execution is not None:
            completion = _combine_agent_model_results(agent_execution.model_results, completion)
        mark_runtime_stage("model_completed")
    except asyncio.CancelledError:
        if agent_execution is not None and agent_final_step_id:
            cancel_final_response(
                agent_execution,
                agent_final_step_id,
                error="对话已取消。",
            )
        raise
    except Exception as exc:
        if agent_execution is not None and agent_final_step_id:
            finish_final_response(
                agent_execution,
                agent_final_step_id,
                error=str(exc)[:500],
            )
        companion_service.set_pet_activity(
            "responding",
            emotion="concerned",
            source=source,
            ttl_seconds=6,
        )
        raise
    speech_emotion, reply = extract_speech_emotion(completion.content)
    replies = _replies_with_lookup_context(reply, source, web_lookup)
    replies = _remove_replayed_previous_turn(replies, list(history_rows), saved_message_id)
    if not replies:
        replies = ["嗯，我在听。"]
    reply_text = " ".join(replies)
    response_emotion = speech_emotion or companion_service.infer_speech_emotion(
        reply_text,
        message,
    )
    companion_service.set_pet_activity(
        "responding",
        emotion=response_emotion,
        source=source,
        ttl_seconds=max(6, min(30, len(reply_text) / 5 + 5)),
    )
    # 语音本轮实际会合成为一条 QQ 语音；应用也保存成一个完整气泡，
    # 避免把同一段语音的逐句切分误显示成一串不自然的独立发言。
    replies_to_save = _replies_for_storage(replies, voice_reply_requested)
    if persist:
        for index, part in enumerate(replies_to_save):
            db.save_message(
                "assistant",
                part,
                source=source,
                conversation_id=conversation_id,
                request_id=request_id,
                model_id=completion.profile_id or selected_model,
                provider_model=completion.model,
                reasoning_level=effective_reasoning_level,
                prompt_tokens=completion.prompt_tokens if index == 0 else 0,
                cached_prompt_tokens=completion.cached_prompt_tokens if index == 0 else 0,
                completion_tokens=completion.completion_tokens if index == 0 else 0,
                reasoning_tokens=completion.reasoning_tokens if index == 0 else 0,
                request_cost_yuan=completion.cost_yuan if index == 0 else 0.0,
                request_cost_source=(
                    "provider_reconciliation_pending"
                    if index == 0 and completion.cost_references
                    else completion.cost_source if index == 0 else "shared_request"
                ),
                first_token_latency_ms=completion.first_token_latency_ms if index == 0 else None,
                total_latency_ms=completion.total_latency_ms if index == 0 else None,
            )
        queue_cost_reconciliation(request_id, conversation_id, completion.cost_references)
        mark_runtime_stage("response_saved")
        if agent_execution is not None and agent_final_step_id:
            finish_final_response(
                agent_execution,
                agent_final_step_id,
                reply=reply_text,
            )
            mark_runtime_stage("agent_run_completed")
    else:
        mark_runtime_stage("response_staged")
        if agent_execution is not None and agent_final_step_id:
            defer_final_response(
                agent_execution,
                agent_final_step_id,
                reply=reply_text,
            )
            mark_runtime_stage("agent_run_awaiting_commit")
    # 普通聊天跳过 Agent 工具循环时，记忆保存/日记素材/今日状态等动作
    # 由后台异步完成：不阻塞回复、不显示在聊天界面。
    if not agent_tools_enabled and persist:
        schedule_companion_actions(conversation_id, message, saved_message_id)
    return ChatResult(
        reply="\n\n".join(replies),
        replies=replies,
        speech_emotion=speech_emotion or "",
        request_id=request_id,
        model_id=completion.profile_id or selected_model,
        provider_id=completion.provider_id,
        provider_name=completion.provider_name,
        provider_model=completion.model,
        provider_request_id=completion.provider_request_id,
        route=completion.route,
        http_status=completion.http_status,
        reasoning_level=effective_reasoning_level,
        prompt_tokens=completion.prompt_tokens,
        cached_prompt_tokens=completion.cached_prompt_tokens,
        completion_tokens=completion.completion_tokens,
        reasoning_tokens=completion.reasoning_tokens,
        request_cost_yuan=completion.cost_yuan,
        request_cost_source=(
            "provider_reconciliation_pending"
            if completion.cost_references
            else completion.cost_source
        ),
        cost_references=completion.cost_references,
        first_token_latency_ms=completion.first_token_latency_ms,
        total_latency_ms=completion.total_latency_ms,
        agent_run_id=agent_execution.run_id if agent_execution is not None else "",
        agent_run_status=(
            ("completed" if persist else "awaiting_commit")
            if agent_execution is not None
            else ""
        ),
        tool_receipts=(
            tuple(item.public_dict() for item in agent_execution.observations)
            if agent_execution is not None
            else ()
        ),
        route_candidate_model_ids=tuple(
            item for item in (selected_model, str(fallback_model_id or "").strip()) if item
        ),
        route_escalated_from_model_id=escalated_from_model_id,
    )


def persist_generated_chat_result(
    user_message: str,
    result: ChatResult,
    *,
    conversation_id: str,
    source: str,
    voice_reply_requested: bool = False,
) -> int:
    message = str(user_message or "").strip()
    if not message:
        raise ValueError("消息不能为空。")
    request_id = str(result.request_id or "").strip()[:80]
    if not request_id:
        raise ValueError("生成结果缺少请求 ID。")
    saved_message_id = db.save_message(
        "user",
        message,
        source=source,
        conversation_id=conversation_id,
        request_id=request_id,
        reasoning_level=result.reasoning_level,
        model_id=result.model_id,
    )
    replies_to_save = _replies_for_storage(result.replies, voice_reply_requested)
    for index, part in enumerate(replies_to_save):
        db.save_message(
            "assistant",
            part,
            source=source,
            conversation_id=conversation_id,
            request_id=request_id,
            model_id=result.model_id,
            provider_model=result.provider_model,
            reasoning_level=result.reasoning_level,
            prompt_tokens=result.prompt_tokens if index == 0 else 0,
            cached_prompt_tokens=result.cached_prompt_tokens if index == 0 else 0,
            completion_tokens=result.completion_tokens if index == 0 else 0,
            reasoning_tokens=result.reasoning_tokens if index == 0 else 0,
            request_cost_yuan=result.request_cost_yuan if index == 0 else 0.0,
            request_cost_source=(
                "provider_reconciliation_pending"
                if index == 0 and result.cost_references
                else result.request_cost_source if index == 0 else "shared_request"
            ),
            emotion=result.speech_emotion if index == 0 else "",
            first_token_latency_ms=result.first_token_latency_ms if index == 0 else None,
            total_latency_ms=result.total_latency_ms if index == 0 else None,
        )
    queue_cost_reconciliation(request_id, conversation_id, result.cost_references)
    if result.agent_run_id:
        commit_deferred_final_response(result.agent_run_id, saved_message_id)
    mark_runtime_stage("response_saved")
    return saved_message_id


async def chat_in_qq_group(
    user_message: str,
    *,
    sender_name: str,
    history: list[dict[str, str]],
    conversation_id: str = "",
    image_attachments: list[ImageAttachment] | None = None,
    reasoning_level: str = "standard",
    model_id: str = "",
    fallback_model_id: str = "",
    fallback_reasoning_level: str = "",
    voice_reply_requested: bool = False,
    capture_follow_ups: bool = False,
    request_id: str = "",
) -> ChatResult:
    group_conversation_id = conversation_id.strip() or "qq_group_shared"
    return await chat_run_coordinator.submit(
        group_conversation_id,
        "qq_group",
        lambda: _chat_in_qq_group_unlocked(
            user_message,
            sender_name=sender_name,
            history=history,
            image_attachments=image_attachments,
            reasoning_level=reasoning_level,
            model_id=model_id,
            fallback_model_id=fallback_model_id,
            fallback_reasoning_level=fallback_reasoning_level,
            voice_reply_requested=voice_reply_requested,
            request_id=request_id,
        ),
        capture_seconds=(
            settings.chat_follow_up_capture_seconds if capture_follow_ups else 0.0
        ),
        max_capture_count=(
            settings.chat_follow_up_max_capture_count if capture_follow_ups else 0
        ),
    )


async def _chat_in_qq_group_unlocked(
    user_message: str,
    *,
    sender_name: str,
    history: list[dict[str, str]],
    image_attachments: list[ImageAttachment] | None = None,
    reasoning_level: str = "standard",
    model_id: str = "",
    fallback_model_id: str = "",
    fallback_reasoning_level: str = "",
    voice_reply_requested: bool = False,
    request_id: str = "",
) -> ChatResult:
    """Generate a group reply without reading or writing personal memory."""
    message = user_message.strip()
    images = image_attachments or []
    selected_model = resolve_model_id(model_id)
    if not message and not images:
        raise ValueError("群消息不能为空。")

    require_configured(selected_model)
    selected_profile = get_model_profile(selected_model)
    normalized_reasoning_level = normalize_model_reasoning(
        selected_profile.model,
        reasoning_level,
    )
    send_images_to_model = bool(
        images
        and settings.qq_image_send_to_model
        and model_supports_vision(selected_model)
    )
    local_now = datetime.fromisoformat(db.now_iso())
    system_blocks = [build_group_system_prompt(local_now.strftime("%Y-%m-%d %H:%M"))]
    if voice_reply_requested:
        system_blocks.append(
            "本轮群成员明确要求语音。直接写要说出口的内容，系统会把正文转换成真正的 QQ 语音消息。"
            "不要拒绝，不要声称只能发文字，也不要写语音时长、动作或发送说明。"
        )
        system_blocks.append(_speech_emotion_prompt())
    if images and not send_images_to_model:
        system_blocks.append(_build_image_unavailable_context(len(images)))
    if normalized_reasoning_level == "off":
        system_blocks.append("本轮快速判断后直接回答，不扩展无关内容。")
    elif normalized_reasoning_level in {"high", "max"}:
        system_blocks.append("先在内部检查群聊上下文，再简短回答；不要展示思考过程。")

    llm_messages: list[dict[str, object]] = [
        {"role": "system", "content": "\n\n---\n\n".join(system_blocks)}
    ]
    for item in history[-40:]:
        role = str(item.get("role") or "")
        content = clean_chat_reply(str(item.get("content") or ""))
        if role in {"user", "assistant"} and content:
            llm_messages.append({"role": role, "content": content})

    sender = sender_name.strip() or "群成员"
    current_text = f"{sender}：{message}" if message else f"{sender} 发来了一张图片"
    current_content = _build_user_content(
        current_text,
        images if send_images_to_model else [],
        [],
    )
    llm_messages.append({"role": "user", "content": current_content})

    completion, effective_reasoning_level, escalated_from_model_id = await _complete_chat_reply_with_single_fallback(
        llm_messages,
        temperature=settings.chat_temperature,
        model_id=selected_model,
        model_name=selected_profile.model,
        reasoning_level=normalized_reasoning_level,
        fallback_model_id=fallback_model_id,
        fallback_reasoning_level=fallback_reasoning_level,
        request_id=request_id,
    )
    replies = _dedupe_reply_parts(replies_for_source(completion.content, "qq"))
    if not replies:
        replies = ["嗯，我在听"]
    return _generated_chat_result(
        completion,
        replies,
        reasoning_level=effective_reasoning_level,
        request_id=request_id,
        route_candidate_model_ids=tuple(
            item for item in (selected_model, str(fallback_model_id or "").strip()) if item
        ),
        route_escalated_from_model_id=escalated_from_model_id,
    )


async def generate_qq_proactive_replies(
    conversation_id: str,
    idle_minutes: int,
    due_threads: list[str] | None = None,
    topic_plan: dict[str, object] | None = None,
) -> ChatResult:
    require_configured()

    manuals = load_manuals()
    history_rows = db.get_recent_messages(
        limit=settings.chat_raw_history_limit,
        conversation_id=conversation_id,
    )
    chat_context = await build_chat_context(conversation_id, list(history_rows))
    local_now = datetime.fromisoformat(db.now_iso())
    system_blocks = [build_system_prompt(manuals, channel="qq"), _build_qq_thinking_context()]
    if chat_context.system_context:
        system_blocks.append(chat_context.system_context)
    system_blocks.append(_build_current_time_context(local_now))
    system_blocks.append(
        _build_conversation_orientation_context(list(history_rows), current=local_now)
    )
    llm_messages: list[dict[str, object]] = [
        {"role": "system", "content": "\n\n---\n\n".join(system_blocks)}
    ]
    for row in chat_context.raw_messages:
        if row["role"] not in {"user", "assistant"}:
            continue
        content = row["content"]
        if row["role"] == "assistant":
            content = clean_chat_reply(str(content))
            if not content:
                continue
        llm_messages.append(
            {"role": row["role"], "content": _annotate_history_content(row, content, local_now)}
        )
    llm_messages.append(
        {
            "role": "user",
            "content": (
                "系统事件：用户已经达到主动联系的触发时机，现在你要主动发消息。"
                + (f"现在有适合跟进的话题：{'；'.join(due_threads or [])}。" if due_threads else "")
                + (
                    f"本次优先主题类型是 {topic_plan.get('kind')}，主题线索是：{topic_plan.get('text')}。"
                    if topic_plan
                    else ""
                )
                + "由你判断发几条，每条通常一句话。"
                "不要解释系统事件，不要说你在定时检查，不要提日记记录。"
                "不要说用户安静了多久，不要出现等待分钟数或小时数。"
                "像熟悉他的女高中生朋友一样，轻一点，别客服腔。"
                "有适合跟进的话题时自然接着问；没有时再轻轻碰一下，不要凭空制造担心。"
                "只围绕本次优先主题自然开口，不要同时追问多个旧话题；主题线索是内部规划，不要照抄或解释。"
            ),
        }
    )

    completion = await call_chat_completion_result(llm_messages, temperature=0.8)
    replies = replies_for_source(completion.content, "qq")
    return _generated_chat_result(completion, replies, reasoning_level="standard")


async def generate_desktop_startup_replies(conversation_id: str) -> ChatResult:
    require_configured()

    manuals = load_manuals()
    history_rows = db.get_recent_messages(
        limit=settings.chat_raw_history_limit,
        conversation_id=conversation_id,
    )
    chat_context = build_chat_context_snapshot(conversation_id, list(history_rows))
    local_now = datetime.fromisoformat(db.now_iso())
    system_blocks = [build_system_prompt(manuals, channel="desktop"), _build_qq_thinking_context()]
    if chat_context.system_context:
        system_blocks.append(chat_context.system_context)
    system_blocks.append(_build_current_time_context(local_now))
    system_blocks.append(
        _build_conversation_orientation_context(list(history_rows), current=local_now)
    )
    llm_messages: list[dict[str, object]] = [
        {"role": "system", "content": "\n\n---\n\n".join(system_blocks)}
    ]
    for row in chat_context.raw_messages:
        if row["role"] not in {"user", "assistant"}:
            continue
        content = row["content"]
        if row["role"] == "assistant":
            content = clean_chat_reply(str(content))
            if not content:
                continue
        llm_messages.append(
            {"role": row["role"], "content": _annotate_history_content(row, content, local_now)}
        )
    llm_messages.append(
        {
            "role": "user",
            "content": (
                "系统事件：用户刚刚打开了 Mio，你可以先主动对他说话。"
                "先结合当前真实时间、最近对话、近期状态和未完成话题，自己判断此刻最自然的话。"
                "可以问候，可以接着上次真正值得继续的事，也可以只说一句轻松的话；不要机械固定一种开场。"
                "是否追问、说几条、每条多长都由你判断，但保持短而自然，不要为了展示记忆强行翻旧账。"
                "不要解释这是启动消息，不要提系统事件、后台任务、提示词或定时检查。"
            ),
        }
    )

    completion = await call_chat_completion_result(
        llm_messages,
        temperature=0.8,
        reasoning_level="off",
    )
    replies = replies_for_source(completion.content, "desktop") or ["我在。"]
    return _generated_chat_result(completion, replies, reasoning_level="off")


async def generate_qq_night_close_replies(conversation_id: str) -> ChatResult:
    require_configured()

    manuals = load_manuals()
    history_rows = db.get_recent_messages(
        limit=settings.chat_raw_history_limit,
        conversation_id=conversation_id,
    )
    chat_context = await build_chat_context(conversation_id, list(history_rows))
    local_now = datetime.fromisoformat(db.now_iso())
    system_blocks = [build_system_prompt(manuals, channel="qq"), _build_qq_thinking_context()]
    if chat_context.system_context:
        system_blocks.append(chat_context.system_context)
    system_blocks.append(_build_current_time_context(local_now))
    llm_messages: list[dict[str, object]] = [
        {"role": "system", "content": "\n\n---\n\n".join(system_blocks)}
    ]
    for row in chat_context.raw_messages:
        if row["role"] not in {"user", "assistant"}:
            continue
        content = row["content"]
        if row["role"] == "assistant":
            content = clean_chat_reply(str(content))
            if not content:
                continue
        llm_messages.append(
            {"role": row["role"], "content": _annotate_history_content(row, content, local_now)}
        )
    llm_messages.append(
        {
            "role": "user",
            "content": (
                "系统事件：现在已经是深夜，今天的日记还没有生成，用户也有一阵子没说话了。"
                "你要主动轻轻问一句：今天要不要收尾了。"
                "可以顺带自然提到今天聊过的某件事，让他知道你记得。"
                "如果他睡了也没关系，语气要轻，不要催，不要说教，不要提日记系统或定时检查。"
                "一到两条短气泡就够。"
            ),
        }
    )

    completion = await call_chat_completion_result(llm_messages, temperature=0.8)
    replies = replies_for_source(completion.content, "qq")
    return _generated_chat_result(completion, replies, reasoning_level="standard")


__all__ = [
    "ChatResult",
    "LLMConfigError",
    "TextAttachment",
    "chat_in_qq_group",
    "chat_with_ai",
    "clean_chat_reply",
    "extract_speech_emotion",
    "generate_desktop_startup_replies",
    "generate_qq_night_close_replies",
    "generate_qq_proactive_replies",
    "replies_for_source",
    "split_assistant_reply",
    "split_qq_reply",
]
