"""Model-selected schema discovery, independent of intent keywords."""
import json

from pydantic import Field
from .tool_types import ToolInput

CORE = {'agent_discover_tools','agent_task_update','agent_read_tool_result','get_self_state',
        'search_web','search_memory','execution_capabilities','compress_current_context'}

class DiscoverInput(ToolInput):
    names: list[str] = Field(min_length=1,max_length=12,description='从工具目录选择本次需要的工具名称，下一轮将提供完整参数定义。')


def catalog(allowed):
    from .tool_registry import tool_registry
    from .agent_tool_service import tool_availability
    return '\n'.join(f'{d.name}: {d.description[:65]}' for d in tool_registry.list()
                     if d.name in allowed and tool_availability(d)[0])


def exposed(allowed, observations, *, task_id=''):
    # Small explicit tool selections used by callers remain intact.
    if len(allowed)<=16 or 'agent_discover_tools' not in allowed: return set(allowed)
    names=CORE & allowed
    if task_id:
        # UI observations contain only the latest 100 steps. Discovery remains
        # enabled across that window, but never expands the current allowlist.
        from .repositories.agent_tasks_repository import completed_discovery_results
        for payload in completed_discovery_results(task_id):
            try:
                result = json.loads(payload or '{}')
            except (TypeError, ValueError):
                continue
            enabled = result.get('enabled_tools') if isinstance(result, dict) else None
            if isinstance(enabled, list):
                names.update(name for name in enabled if isinstance(name, str) and name in allowed)
    for item in observations:
        if item.get('tool_name')=='agent_discover_tools' and item.get('status') in {'completed','executed'}:
            names.update(set(item.get('result',{}).get('enabled_tools',[])) & allowed)
    return names


def discover(names, context):
    from . import agent_task_service as tasks
    from .tool_registry import tool_registry
    from .agent_tool_service import tool_availability
    task=tasks.get(context.task_id) if context and context.task_id else None
    allowed=set(task['allowed_tools']) if task else {d.name for d in tool_registry.list()}
    available=[name for name in dict.fromkeys(names) if name in allowed and tool_registry.get(name)
               and tool_availability(tool_registry.require(name))[0]]
    return {'enabled_tools':available,'unavailable_tools':[n for n in names if n not in available],
            'instruction':'下一轮原生工具定义包含以上已启用工具；权限仍由执行层核对。'}
