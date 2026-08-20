from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Mapping, Sequence

from .model_latency_service import get_latency_stats
from .model_registry import ModelProfile, list_model_profiles, model_reasoning_config
from .route_observation_service import model_performance_snapshot


CONTINUATION_RE = re.compile(r"^(继续|然后呢|接着|按这个做|详细说说|再说说|就这样|可以|嗯|好)[吧呢啊。！!？?\s]*$")
STANDARD_KEYWORDS = (
    "分析", "比较", "区别", "建议", "计划", "总结", "解释", "判断", "怎么做", "为什么",
    "日记", "回顾", "整理", "选择", "评估",
)
COMPLEX_KEYWORDS = (
    "实现", "调试", "报错", "代码", "架构", "数据库", "接口", "重构", "审查", "测试方案",
    "完整方案", "详细方案", "权衡", "风险", "推导", "证明", "法律", "医疗", "财务",
)
CODE_MARKERS = ("```", "traceback", "exception", "error:", "http 4", "http 5", "select ", "def ", "class ")
REFLECTIVE_REQUEST_RE = re.compile(
    r"(?:你|Mio|澪).{0,10}(?:有什么|有没有|想要|希望).{0,14}(?:想法|建议|功能|改进|我做)"
    r"|(?:有什么|哪些).{0,12}(?:想法|建议|功能|改进)"
)
TOOL_TASK_RE = re.compile(
    r"(?:记住|记录|写进|加入|保存|创建|提醒|待办|跟进|完成).{0,12}(?:记忆|日记|素材|状态|事项|任务|提醒)"
    r"|(?:读取|查看|检查|搜索|检索).{0,12}(?:状态|记忆|日记|能力|服务|页面|路由)"
    r"|(?:语音|音色|电话|麦克风|桌宠|QQ|NapCat|屏幕|系统声音|服务).{0,16}"
    r"(?:故障|报错|错误|异常|没声音|不能用|用不了|不工作|出问题|有问题)"
)
WEB_TASK_RE = re.compile(
    r"(?:联网|上网|搜索|搜一下|查一下|查查|官网|网页|新闻|热搜|天气|汇率|股价|票价|实时|最新)"
)
HIGH_RISK_RE = re.compile(r"删除|覆盖|公开|发送给|修改人格|供应商|启动观察|持续监听")


@dataclass(frozen=True)
class TaskProfile:
    task_type: str
    difficulty: str
    modalities: tuple[str, ...]
    requires_vision: bool
    requires_tools: bool
    requires_structured_output: bool
    context_chars: int
    estimated_context_tokens: int
    risk_level: str
    latency_priority: str
    cost_priority: str
    reason: str

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AutoRoute:
    model_id: str
    model: str
    reasoning_level: str
    difficulty: str
    reason: str
    latency_budget_ms: int = 0
    task_profile: dict[str, object] = field(default_factory=dict)
    candidates: tuple[dict[str, object], ...] = ()
    fallback_model_id: str = ""
    fallback_reasoning_level: str = ""

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


def _row_content(row: Mapping[str, object]) -> str:
    try:
        return str(row["content"] or "")
    except (KeyError, TypeError):
        return ""


def _topic_text(message: str, history_rows: Sequence[Mapping[str, object]]) -> str:
    clean = message.strip()
    if not CONTINUATION_RE.match(clean):
        return clean
    previous_user_messages = [
        _row_content(row)
        for row in history_rows
        if str(row["role"] or "") == "user" and _row_content(row).strip()
    ]
    return (previous_user_messages[-1] + "\n" + clean) if previous_user_messages else clean


def classify_difficulty(
    message: str,
    history_rows: Sequence[Mapping[str, object]] = (),
    image_count: int = 0,
    text_attachment_chars: int = 0,
) -> tuple[str, str]:
    topic = _topic_text(message, history_rows)
    lower = topic.lower()
    score = 0
    reasons: list[str] = []

    if len(topic) >= 120:
        score += 1
        reasons.append("内容较长")
    if len(topic) >= 500:
        score += 1
        reasons.append("需要处理长文本")
    if any(keyword in topic for keyword in STANDARD_KEYWORDS):
        score += 1
        reasons.append("需要分析判断")
    if REFLECTIVE_REQUEST_RE.search(topic):
        score += 1
        reasons.append("需要形成具体想法")
    complex_matches = sum(1 for keyword in COMPLEX_KEYWORDS if keyword in topic)
    if complex_matches:
        score += 2
        if complex_matches >= 2:
            score += 1
        reasons.append("包含复杂任务")
    if any(marker in lower for marker in CODE_MARKERS):
        score += 2
        reasons.append("包含代码或错误信息")
    if image_count:
        score += 1
        reasons.append("需要识图")
    if text_attachment_chars >= 2000:
        score += 1
        reasons.append("包含文件")
    if text_attachment_chars >= 20000:
        score += 1
        reasons.append("文件较长")

    if score <= 0:
        return "simple", "普通对话"
    if score <= 2:
        return "standard", "、".join(reasons[:2])
    return "complex", "、".join(reasons[:3])


def build_task_profile(
    message: str,
    history_rows: Sequence[Mapping[str, object]] = (),
    image_count: int = 0,
    text_attachment_chars: int = 0,
) -> TaskProfile:
    topic = _topic_text(message, history_rows)
    difficulty, reason = classify_difficulty(
        message,
        history_rows=history_rows,
        image_count=image_count,
        text_attachment_chars=text_attachment_chars,
    )
    lower = topic.lower()
    requires_tools = bool(TOOL_TASK_RE.search(topic) or WEB_TASK_RE.search(topic))
    if image_count:
        task_type = "vision"
    elif text_attachment_chars:
        task_type = "document"
    elif any(marker in lower for marker in CODE_MARKERS) or any(item in topic for item in ("代码", "调试", "架构", "数据库", "接口")):
        task_type = "technical"
    elif requires_tools:
        task_type = "agent_tool"
    elif any(item in topic for item in ("分析", "比较", "区别", "判断", "评估", "建议", "计划", "总结", "解释")):
        task_type = "analysis"
    else:
        task_type = "conversation"
    modalities = ["text"]
    if image_count:
        modalities.append("image")
    if text_attachment_chars:
        modalities.append("document")
    history_chars = sum(len(_row_content(row)) for row in history_rows)
    context_chars = len(message.strip()) + history_chars + max(0, int(text_attachment_chars))
    estimated_context_tokens = max(1, (context_chars + 2) // 3)
    return TaskProfile(
        task_type=task_type,
        difficulty=difficulty,
        modalities=tuple(modalities),
        requires_vision=bool(image_count),
        requires_tools=requires_tools,
        requires_structured_output=requires_tools,
        context_chars=context_chars,
        estimated_context_tokens=estimated_context_tokens,
        risk_level="high" if HIGH_RISK_RE.search(topic) else "low" if requires_tools else "read_only",
        latency_priority="high" if difficulty == "simple" else "balanced",
        cost_priority="high" if difficulty == "simple" else "balanced",
        reason=reason,
    )


def _price_score(profile: ModelProfile) -> float:
    prices = profile.input_price_cny_per_million + profile.output_price_cny_per_million
    return prices if prices > 0 else 1_000_000.0


def _estimated_cost(profile: ModelProfile, task: TaskProfile) -> float | None:
    if profile.pricing_source == "unconfigured":
        return None
    output_tokens = {"simple": 300, "standard": 700, "complex": 1400}[task.difficulty]
    cost = (
        task.estimated_context_tokens * profile.input_price_cny_per_million
        + output_tokens * profile.output_price_cny_per_million
    ) / 1_000_000
    return round(max(0.0, cost), 6)


def _available_profiles() -> list[ModelProfile]:
    return [profile for profile in list_model_profiles() if profile.base_urls and profile.api_key]


def _latency_budget_ms(difficulty: str) -> int:
    return {"simple": 2800, "standard": 6500, "complex": 12000}.get(difficulty, 6500)


def _metric_number(metric: Mapping[str, object], name: str) -> float | None:
    value = metric.get(name)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _candidate_record(
    profile: ModelProfile,
    task: TaskProfile,
    metric: Mapping[str, object],
) -> dict[str, object]:
    reasons: list[str] = []
    eligible = True
    if task.requires_vision and not profile.supports_vision:
        eligible = False
        reasons.append("不支持图片")
    if task.requires_structured_output and not (profile.supports_tool_calls or profile.supports_structured_output):
        eligible = False
        reasons.append("不支持工具或结构化输出")
    minimum_context = task.estimated_context_tokens + 4096
    if profile.context_window_tokens < minimum_context:
        eligible = False
        reasons.append("上下文容量不足")

    samples = max(0, int(metric.get("sample_count") or 0))
    success_rate = _metric_number(metric, "success_rate")
    measured_first = _metric_number(metric, "first_token_p95_ms")
    if measured_first is None:
        measured_first = get_latency_stats(profile.id).preferred_first_token_ms
    measured_cost = _metric_number(metric, "average_cost_yuan")
    estimated_cost = _estimated_cost(profile, task)
    health_penalty = 1 if samples >= 5 and success_rate is not None and success_rate < 0.5 else 0
    if health_penalty:
        reasons.append("近期成功率低")
    return {
        "model_id": profile.id,
        "eligible": eligible,
        "capability_reasons": reasons,
        "supports_vision": profile.supports_vision,
        "supports_tool_calls": profile.supports_tool_calls,
        "supports_structured_output": profile.supports_structured_output,
        "context_window_tokens": profile.context_window_tokens,
        "privacy_location": profile.privacy_location,
        "sample_count": samples,
        "success_rate": success_rate,
        "first_token_p95_ms": measured_first,
        "average_cost_yuan": measured_cost,
        "estimated_cost_yuan": estimated_cost,
        "health_penalty": health_penalty,
        "is_default": profile.is_default,
        "price_score": _price_score(profile),
    }


def _candidate_sort_key(candidate: Mapping[str, object], difficulty: str, budget: int) -> tuple[float, ...]:
    success_rate = _metric_number(candidate, "success_rate")
    samples = int(candidate.get("sample_count") or 0)
    success_score = success_rate if success_rate is not None and samples >= 3 else 0.7
    latency = _metric_number(candidate, "first_token_p95_ms")
    latency = latency if latency is not None else float(budget)
    over_budget = max(0.0, latency - budget)
    cost = _metric_number(candidate, "average_cost_yuan")
    if cost is None:
        cost = _metric_number(candidate, "estimated_cost_yuan")
    cost = cost if cost is not None else 1_000_000.0
    health_penalty = float(candidate.get("health_penalty") or 0)
    default_penalty = 0.0 if candidate.get("is_default") else 1.0
    if difficulty == "complex":
        return (health_penalty, -success_score, over_budget, latency, cost, default_penalty)
    if difficulty == "standard":
        return (health_penalty, over_budget, -success_score, latency, cost, default_penalty)
    return (health_penalty, over_budget, latency, cost, -success_score, default_penalty)


def _reasoning_level(profile: ModelProfile, difficulty: str) -> str:
    config = model_reasoning_config(profile.model)
    parameter = str(config["parameter"])
    if parameter == "reasoning_effort":
        return {"simple": "low", "standard": "medium", "complex": "high"}[difficulty]
    if parameter == "deepseek_thinking":
        return {"simple": "low", "standard": "high", "complex": "max"}[difficulty]
    return str(config["default"])


def _dynamic_reasoning_level(
    profile: ModelProfile,
    difficulty: str,
    candidate: Mapping[str, object],
) -> str:
    level = _reasoning_level(profile, difficulty)
    observed = _metric_number(candidate, "first_token_p95_ms")
    if observed is None:
        return level
    budget = _latency_budget_ms(difficulty)
    if observed <= budget:
        return level
    options = [str(option["id"]) for option in model_reasoning_config(profile.model)["options"]]
    if difficulty == "simple" and "low" in options:
        return "low"
    if difficulty == "standard" and observed > budget * 1.15:
        for value in ("low", "medium"):
            if value in options:
                return value
    if difficulty == "complex" and observed > budget * 2:
        for value in ("high", "medium", "low"):
            if value in options:
                return value
    return level


def _fallback_reasoning_level(profile: ModelProfile, difficulty: str) -> str:
    normal = _reasoning_level(profile, difficulty)
    options = [str(option["id"]) for option in model_reasoning_config(profile.model)["options"]]
    if difficulty == "simple":
        for value in ("medium", "high", normal):
            if value in options:
                return value
    if difficulty == "standard":
        for value in ("high", "max", normal):
            if value in options:
                return value
    return normal


def select_auto_route(
    message: str,
    history_rows: Sequence[Mapping[str, object]] = (),
    image_count: int = 0,
    text_attachment_chars: int = 0,
    profiles: list[ModelProfile] | None = None,
    performance: Mapping[str, Mapping[str, object]] | None = None,
) -> AutoRoute:
    task = build_task_profile(
        message,
        history_rows=history_rows,
        image_count=image_count,
        text_attachment_chars=text_attachment_chars,
    )
    available = profiles if profiles is not None else _available_profiles()
    if not available:
        raise ValueError("没有可用于自动路由的模型。")
    metrics = dict(performance) if performance is not None else model_performance_snapshot(task.task_type)
    budget = _latency_budget_ms(task.difficulty)
    candidate_pairs = [
        (profile, _candidate_record(profile, task, metrics.get(profile.id, {})))
        for profile in available
    ]
    eligible = [item for item in candidate_pairs if bool(item[1]["eligible"])]
    if not eligible:
        reasons = "；".join(
            f"{item[0].display_name}: {'、'.join(item[1]['capability_reasons']) or '能力不匹配'}"
            for item in candidate_pairs
        )
        raise ValueError(f"没有满足本轮硬能力的模型。{reasons}"[:500])
    eligible.sort(key=lambda item: _candidate_sort_key(item[1], task.difficulty, budget))
    selected_profile, selected_candidate = eligible[0]
    ranked_ids = {profile.id: index + 1 for index, (profile, _) in enumerate(eligible)}
    public_candidates: list[dict[str, object]] = []
    for profile, candidate in candidate_pairs:
        item = {
            key: value
            for key, value in candidate.items()
            if key not in {"price_score", "health_penalty", "is_default"}
        }
        item["rank"] = ranked_ids.get(profile.id, 0)
        public_candidates.append(item)
    fallback_profile = eligible[1][0] if len(eligible) > 1 else None
    selected_reasoning = _dynamic_reasoning_level(selected_profile, task.difficulty, selected_candidate)
    measured = int(selected_candidate.get("sample_count") or 0) >= 3
    reason = (
        f"{task.reason}；任务类型 {task.task_type}；"
        + ("按近期成功率、P95、费用排序" if measured else "暂无足量实测，按硬能力、延迟和费用保守排序")
    )
    return AutoRoute(
        model_id=selected_profile.id,
        model=selected_profile.model,
        reasoning_level=selected_reasoning,
        difficulty=task.difficulty,
        reason=reason,
        latency_budget_ms=budget,
        task_profile=task.public_dict(),
        candidates=tuple(public_candidates),
        fallback_model_id=fallback_profile.id if fallback_profile is not None else "",
        fallback_reasoning_level=(
            _fallback_reasoning_level(fallback_profile, task.difficulty)
            if fallback_profile is not None
            else ""
        ),
    )


__all__ = [
    "AutoRoute",
    "TaskProfile",
    "build_task_profile",
    "classify_difficulty",
    "select_auto_route",
]
