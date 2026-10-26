"""Privacy-safe, local text estimates; never provider billing or cache estimates.

Only canonical categories and numbers leave these functions. Multimodal image
tokens, provider framing, retries and tokenizer differences are not estimated.
"""
from __future__ import annotations

import json
import re

from .prompt_budget import estimate_tokens as estimate_text_tokens


_HEADINGS = {
    "当前状态板": "runtime_current_state",
    "相关历史状态": "runtime_historical_state",
    "日记摘录": "runtime_diary",
    "相关AI回顾": "runtime_review",
    "分层私人记忆": "runtime_structured_memory",
    "压缩记忆": "runtime_compressed_memory",
    "已到期约定": "runtime_due_followups",
    "现实行动回访": "runtime_action_followup",
    "当前本地时间": "runtime_current_time",
    "当前时间": "runtime_current_time",
    "用户发言间隔": "runtime_message_gap",
    "用户发言间隔判断": "runtime_message_gap",
    "会话开场时间定位": "runtime_conversation_orientation",
    "上一条可见消息": "runtime_conversation_orientation",
    "当前会话可见记录": "runtime_conversation_orientation",
    "上一条用户消息": "runtime_message_gap",
}
_HEADING = re.compile(r"^(?:【([^】]+)】|\[([^\]]+)\]|([^：:\n]+)[：:])")


def request_category(mode: str, purpose: str, request_id: str, automatic: bool) -> str:
    """Use operation metadata, never infer model purpose from private content."""
    if purpose == "_compress_old_messages" or ":context-compress:" in request_id:
        return "context_compression"
    if mode == "agent":
        return "agent_reply" if purpose == "final_model_call" else "agent_planner"
    if mode == "memory":
        return "memory"
    if automatic:
        return "background"
    return mode if mode in {"chat", "record", "vision", "translation", "profile", "proactive"} else "other"


def _runtime_category(value: str) -> str:
    if value.startswith("以下是较早聊天的压缩记忆。"):
        return "runtime_compressed_memory"
    if value.startswith(("本轮用户消息未定位", "这是当前可见记录中的第一条用户消息", "最近两条用户消息的时间不完整")):
        return "runtime_message_gap"
    match = _HEADING.match(value.strip())
    title = next((part for part in match.groups() if part), "") if match else ""
    title = re.split(r"[（(]", title, maxsplit=1)[0].strip()
    return _HEADINGS.get(title, "runtime_other")


def _split_runtime_block(value: str) -> list[tuple[str, str]]:
    """Recognize application headings only inside the runtime JSON envelope."""
    parts = []
    for section in re.split(r"\n\n---\n\n", value):
        category, lines = "runtime_other", []
        for line in section.splitlines(keepends=True):
            next_category = _runtime_category(line)
            if next_category != "runtime_other":
                if lines:
                    parts.append((category, "".join(lines)))
                category, lines = next_category, []
            lines.append(line)
        if lines:
            parts.append((category, "".join(lines)))
    return parts


def _runtime_parts(message: dict) -> list[tuple[str, str]] | None:
    content = message.get("content")
    if not isinstance(content, str):
        return None
    if message.get("role") == "user" and content.lstrip().startswith("{"):
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and set(payload) == {"应用运行资料"} and isinstance(payload["应用运行资料"], list):
            parts = []
            for block in payload["应用运行资料"]:
                if isinstance(block, str):
                    parts.extend(_split_runtime_block(block))
            return parts
    # These are application-owned Agent envelopes, not arbitrary title guesses.
    for prefix, category in (
        ("本次规划的当前运行信息：", "runtime_current_time"),
        ("持久任务状态与先前观察（", "runtime_agent_state"),
        ("已自动压缩的历史工作摘要（", "runtime_compressed_memory"),
        ("当前项目背景（", "runtime_project"),
        ("工具目录（名称与用途，", "runtime_tool_catalog"),
    ):
        if content.startswith(prefix):
            return [(category, content)]
    return None


def _text_projection(value, counts: dict) -> object:
    """Avoid treating base64/media URLs or private runtime metadata as text."""
    if isinstance(value, list):
        return [_text_projection(item, counts) for item in value]
    if isinstance(value, dict):
        if value.get("type") in {"image_url", "input_image", "image"}:
            counts["images"] += 1
            return {"type": "image"}
        return {key: _text_projection(item, counts) for key, item in value.items()
                if not key.startswith("_mio_")}
    return value


def estimate_request(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """Split one logical request into text blocks without returning any input.

    Categories describe supplied envelopes, not trusted content or instruction
    authority. The current request is the last non-runtime user message. Some
    callers embed history in that message; their internal split is unavailable.
    """
    runtime = [_runtime_parts(message) for message in messages]
    candidates = [i for i, message in enumerate(messages)
                  if message.get("role") == "user" and runtime[i] is None]
    current = candidates[-1] if candidates else -1
    blocks = []
    counts = {"images": 0}

    def add(category, tokens, message_index=-1):
        blocks.append({"category": category, "estimated_tokens": tokens, "message_index": message_index})

    for index, message in enumerate(messages):
        if runtime[index] is not None:
            # JSON/framing is an estimate too; keep it separate from source text.
            add("runtime_framing", 8, index)
            for category, value in runtime[index]:
                add(category, estimate_text_tokens(value), index)
            continue
        category = ("fixed_instructions" if message.get("role") in {"system", "developer"}
                    else "current_request" if index == current else "history")
        projected = _text_projection(message, counts)
        add(category, estimate_text_tokens(json.dumps(projected, ensure_ascii=False, separators=(",", ":"))) + 8, index)
    if tools:
        add("tool_schema", estimate_text_tokens(json.dumps(tools, ensure_ascii=False, separators=(",", ":"))))
    return {
        "version": 1, "method": "mixed_text_heuristic_v1", "scope": "logical_request_text_and_tools",
        "is_billing_usage": False, "estimated_input_tokens": sum(block["estimated_tokens"] for block in blocks),
        "unestimated_image_count": counts["images"], "blocks": blocks,
    }
