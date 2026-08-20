from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyToolInput(ToolInput):
    pass


class SelfStateInput(ToolInput):
    scopes: list[str] = Field(default_factory=list, max_length=12)


class SearchMemoryInput(ToolInput):
    query: str = Field(default="", max_length=500)
    limit: int = Field(default=12, ge=1, le=40)


class WebSearchInput(ToolInput):
    query: str = Field(min_length=2, max_length=160)


class DiaryLookupInput(ToolInput):
    date: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")


class DiaryMaterialInput(ToolInput):
    content: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DailyThirtyInput(ToolInput):
    status: str = Field(pattern=r"^(done|partial|missed)$")
    reason: str = Field(default="", max_length=500)
    correction: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DailyMoodInput(ToolInput):
    mood: str = Field(min_length=1, max_length=300)
    score: int = Field(ge=1, le=5)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TodayStateInput(ToolInput):
    mood: str = Field(default="", max_length=300)
    mood_score: int = Field(default=0, ge=0, le=5)
    key_events: str = Field(default="", max_length=500)
    avoidance_signals: str = Field(default="", max_length=500)
    next_min_action: str = Field(default="", max_length=500)
    daily_thirty_status: str = Field(default="", pattern=r"^(|done|partial|missed|unknown)$")
    daily_thirty_reason: str = Field(default="", max_length=500)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ThreadInput(ToolInput):
    content: str = Field(min_length=1, max_length=500)
    follow_up_after: str = Field(default="", max_length=32)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ResolveThreadInput(ToolInput):
    content: str = Field(min_length=1, max_length=500)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class FollowUpResultInput(ToolInput):
    content: str = Field(min_length=1, max_length=500)
    outcome: str = Field(pattern=r"^(completed|partial|not_completed)$")
    summary: str = Field(min_length=1, max_length=800)
    adjustment: str = Field(default="", max_length=500)
    next_follow_up_after: str = Field(default="", max_length=32)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class MemoryInput(ToolInput):
    layer: str = Field(pattern=r"^(L0|L1|L2)$")
    category: str = Field(
        pattern=r"^(identity|preference|relationship|current_state|plan|project|experience|person|other)$"
    )
    memory_key: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=800)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class InstructionInput(ToolInput):
    instruction: str = Field(min_length=1, max_length=800)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class GenerateDiaryInput(ToolInput):
    reason: str = Field(default="", max_length=500)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ToolPermission(IntEnum):
    READ_ONLY = 0
    LOW_RISK_WRITE = 1
    HIGH_RISK_WRITE = 2

    @property
    def label(self) -> str:
        return {
            self.READ_ONLY: "只读",
            self.LOW_RISK_WRITE: "低风险写入",
            self.HIGH_RISK_WRITE: "高风险写入",
        }[self]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    permission: ToolPermission
    description: str
    explicit_intent_pattern: str = ""
    handler: str = ""
    arguments_model: type[ToolInput] = EmptyToolInput
    result_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    dependencies: tuple[str, ...] = ()
    timeout_seconds: float = 15.0
    supports_cancellation: bool = True
    idempotent: bool = True
    verifier: str = "non_error_result"
    compensation: str = ""

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "permission": self.permission.name.lower(),
            "permission_level": int(self.permission),
            "permission_label": self.permission.label,
            "description": self.description,
            "requires_explicit_intent": bool(self.explicit_intent_pattern),
            "handler": self.handler or self.name,
            "input_schema": self.arguments_model.model_json_schema(),
            "output_schema": dict(self.result_schema),
            "dependencies": list(self.dependencies),
            "timeout_seconds": self.timeout_seconds,
            "supports_cancellation": self.supports_cancellation,
            "idempotent": self.idempotent,
            "verifier": self.verifier,
            "compensation": self.compensation,
        }

    def has_explicit_intent(self, user_message: str) -> bool:
        if not self.explicit_intent_pattern:
            return True
        return re.search(self.explicit_intent_pattern, str(user_message or ""), re.IGNORECASE) is not None

    def validate_arguments(self, arguments: object) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON 对象。")
        return self.arguments_model.model_validate(arguments).model_dump()

    def native_schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> ToolDefinition:
        name = definition.name.strip()
        if not name:
            raise ValueError("工具名不能为空。")
        if name in self._definitions:
            raise ValueError(f"工具已经注册：{name}")
        self._definitions[name] = definition
        return definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(str(name or "").strip())

    def require(self, name: str) -> ToolDefinition:
        definition = self.get(name)
        if definition is None:
            raise ValueError(f"未注册的工具：{name}")
        return definition

    def list(self) -> list[ToolDefinition]:
        return sorted(self._definitions.values(), key=lambda item: (int(item.permission), item.name))

    def register_many(self, definitions: Iterable[ToolDefinition]) -> None:
        for definition in definitions:
            self.register(definition)


tool_registry = ToolRegistry()
tool_registry.register_many(
    [
        ToolDefinition(
            "get_self_state",
            ToolPermission.READ_ONLY,
            "读取脱敏的当前自我状态",
            handler="get_self_state",
            arguments_model=SelfStateInput,
        ),
        ToolDefinition("list_capabilities", ToolPermission.READ_ONLY, "列出当前能力、限制与风险", handler="list_capabilities"),
        ToolDefinition("get_active_view", ToolPermission.READ_ONLY, "读取主应用当前页面", handler="get_active_view"),
        ToolDefinition("get_service_health", ToolPermission.READ_ONLY, "读取本地服务健康状态", handler="get_service_health"),
        ToolDefinition("explain_last_route", ToolPermission.READ_ONLY, "解释最近一次模型与思考选择", handler="explain_last_route"),
        ToolDefinition("get_today_state", ToolPermission.READ_ONLY, "读取今日状态", handler="get_today_state"),
        ToolDefinition(
            "search_web",
            ToolPermission.READ_ONLY,
            "按明确查询词联网查证事实；适合验证简称、别名、地点和实时信息",
            handler="search_web",
            arguments_model=WebSearchInput,
            dependencies=("web_search_enabled",),
            timeout_seconds=25.0,
        ),
        ToolDefinition(
            "search_memory",
            ToolPermission.READ_ONLY,
            "检索私人记忆",
            handler="search_memory",
            arguments_model=SearchMemoryInput,
        ),
        ToolDefinition(
            "get_diary",
            ToolPermission.READ_ONLY,
            "读取指定日记",
            handler="get_diary",
            arguments_model=DiaryLookupInput,
        ),
        ToolDefinition(
            "add_diary_material",
            ToolPermission.LOW_RISK_WRITE,
            "追加日记素材",
            handler="companion_action",
            arguments_model=DiaryMaterialInput,
            compensation="remove_created_material",
        ),
        ToolDefinition(
            "set_daily_thirty",
            ToolPermission.LOW_RISK_WRITE,
            "更新每日三十状态",
            handler="companion_action",
            arguments_model=DailyThirtyInput,
            compensation="restore_previous_daily_state",
        ),
        ToolDefinition(
            "set_daily_mood",
            ToolPermission.LOW_RISK_WRITE,
            "更新今日情绪",
            handler="companion_action",
            arguments_model=DailyMoodInput,
            compensation="restore_previous_daily_state",
        ),
        ToolDefinition(
            "update_today_state",
            ToolPermission.LOW_RISK_WRITE,
            "根据明确证据更新今日主线、阻碍和下一步。用户询问今日状态、今日成长、成长判断或每日三十判断时，也必须先读取今日状态再调用本工具完成今日判断写入，不能只读查询后声称已记录",
            handler="companion_action",
            arguments_model=TodayStateInput,
            compensation="restore_previous_daily_state",
        ),
        ToolDefinition(
            "remember_thread",
            ToolPermission.LOW_RISK_WRITE,
            "保存有明确时间的待跟进话题",
            handler="companion_action",
            arguments_model=ThreadInput,
            compensation="resolve_created_thread",
        ),
        ToolDefinition(
            "resolve_thread",
            ToolPermission.LOW_RISK_WRITE,
            "把明确完成的待跟进话题记录为已完成",
            handler="companion_action",
            arguments_model=ResolveThreadInput,
            compensation="reopen_resolved_thread",
        ),
        ToolDefinition(
            "record_follow_up_result",
            ToolPermission.LOW_RISK_WRITE,
            "根据用户真实反馈记录待跟进事项的完成、部分完成或未完成结果，并保存后续调整",
            handler="companion_action",
            arguments_model=FollowUpResultInput,
            compensation="reopen_resolved_thread",
        ),
        ToolDefinition(
            "remember_memory",
            ToolPermission.LOW_RISK_WRITE,
            "保存有原话证据的结构化记忆",
            handler="companion_action",
            arguments_model=MemoryInput,
            compensation="supersede_created_memory",
        ),
        ToolDefinition(
            "edit_today_diary",
            ToolPermission.HIGH_RISK_WRITE,
            "修改今日正式日记",
            r"(?:修改|改一下|补充|加上|加入|删掉|删除|写进).{0,16}(?:日记)|(?:日记).{0,16}(?:修改|补充|加上|删掉)",
            handler="companion_action",
            arguments_model=InstructionInput,
            dependencies=("configured_model",),
            timeout_seconds=120.0,
            compensation="restore_diary_snapshot",
        ),
        ToolDefinition(
            "generate_today_diary",
            ToolPermission.HIGH_RISK_WRITE,
            "生成或覆盖今日正式日记",
            r"(?:生成|整理|写).{0,12}(?:今天|今日|当天)?.{0,6}日记|日记.{0,12}(?:生成|整理|写)"
            r"|(?:今天|今日).{0,12}(?:就这样|结束|收尾)|(?:准备|要|该).{0,6}(?:睡|休息)|晚安",
            handler="companion_action",
            arguments_model=GenerateDiaryInput,
            dependencies=("configured_model",),
            timeout_seconds=180.0,
            compensation="restore_diary_snapshot",
        ),
        ToolDefinition(
            "update_profile",
            ToolPermission.HIGH_RISK_WRITE,
            "修改 Mio 的人格或属性",
            r"(?:记住|加入|加进|写入|修改|更新|调整|改成|放到).{0,24}(?:属性|人格|人设|设定|底层)"
            r"|(?:以后|今后).{0,24}(?:别|不要|少|多|改用|用)",
            handler="companion_action",
            arguments_model=InstructionInput,
            dependencies=("configured_model",),
            timeout_seconds=120.0,
            compensation="restore_profile_snapshot",
        ),
    ]
)


__all__ = [
    "ToolDefinition",
    "ToolInput",
    "ToolPermission",
    "ToolRegistry",
    "tool_registry",
]
