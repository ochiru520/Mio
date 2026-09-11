"""Runtime profiles for Mio's companion and agent surfaces.

The profile is a policy boundary, not an intent detector.  The model is still
free to choose among the capabilities exposed by the active profile; the
execution layer validates permissions, dependencies and receipts.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tool_registry import ToolPermission, tool_registry


COMPANION_TOOL_NAMES = frozenset(
    {
        "get_self_state",
        "list_capabilities",
        "get_active_view",
        "get_service_health",
        "explain_last_route",
        "get_today_state",
        "search_memory",
        "get_diary",
        "add_diary_material",
        "set_daily_thirty",
        "set_daily_mood",
        "update_today_state",
        "remember_thread",
        "resolve_thread",
        "record_follow_up_result",
        "remember_memory",
        "handoff_to_agent",
    }
)


@dataclass(frozen=True)
class RuntimeProfile:
    mode: str
    allow_agent_loop: bool
    show_execution_trace: bool
    show_cost: bool
    allowed_tool_names: frozenset[str]
    fast_path_default: bool


def agent_tool_names() -> frozenset[str]:
    """Return the current registry as the Agent surface capability set."""

    return frozenset(item.name for item in tool_registry.list())


def resolve_runtime_profile(mode: str = "", *, agent_workspace: bool = False) -> RuntimeProfile:
    selected = str(mode or "").strip().lower()
    if agent_workspace or selected in {"agent", "workspace", "agent_workspace"}:
        return RuntimeProfile(
            mode="agent",
            allow_agent_loop=True,
            show_execution_trace=True,
            show_cost=True,
            allowed_tool_names=agent_tool_names(),
            fast_path_default=False,
        )
    return RuntimeProfile(
        mode="companion",
        allow_agent_loop=True,
        show_execution_trace=False,
        show_cost=False,
        allowed_tool_names=COMPANION_TOOL_NAMES,
        fast_path_default=True,
    )


def is_high_risk_tool(name: str) -> bool:
    definition = tool_registry.get(name)
    return bool(definition and definition.permission == ToolPermission.HIGH_RISK_WRITE)


__all__ = [
    "COMPANION_TOOL_NAMES",
    "RuntimeProfile",
    "agent_tool_names",
    "is_high_risk_tool",
    "resolve_runtime_profile",
]
