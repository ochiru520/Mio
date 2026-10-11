from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any
from pydantic import BaseModel, ConfigDict

class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyToolInput(ToolInput):
    pass


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


