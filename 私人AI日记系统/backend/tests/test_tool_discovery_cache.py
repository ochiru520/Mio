"""Durable discovery keeps schemas stable without widening task permissions."""
from unittest.mock import patch

import pytest

from app import db, agent_task_service as tasks
from app.agent_loop_service import _native_tools
from app.tool_discovery import CORE, exposed
from app.tool_registry import tool_registry
from test_agent_result_recovery import make_task, store
from test_runtime_safety_v2 import isolated


def allowed_tools():
    return {definition.name for definition in tool_registry.list()}


@pytest.mark.parametrize('status', ['completed', 'executed'])
def test_discovery_survives_more_than_100_unrelated_steps(status):
    task = make_task()
    allowed = allowed_tools()
    discovered = 'get_today_state'
    assert len(allowed) > 16 and discovered not in CORE
    step_id = store(task, {'enabled_tools': [discovered]}, 'agent_discover_tools', 'discovery')
    db.update_agent_run_step(step_id, status)
    first = exposed(allowed, tasks.observations(task['id']), task_id=task['id'])
    for index in range(101):
        store(task, {'ordinal': index}, run_id=f'unrelated-{index}')
    observations = tasks.observations(task['id'])
    assert len(observations) == 100
    assert all(item['tool_name'] != 'agent_discover_tools' for item in observations)
    second = exposed(allowed, observations, task_id=task['id'])
    assert discovered in first and second == first
    assert _native_tools(first) == _native_tools(second)


def test_failed_other_task_and_revoked_discovery_do_not_expand_permissions():
    task, other = make_task(), make_task()
    allowed = allowed_tools()
    store(task, {'enabled_tools': ['get_today_state']}, 'agent_discover_tools', 'own-discovery')
    failed = store(task, {'enabled_tools': ['get_diary']}, 'agent_discover_tools', 'failed-discovery')
    db.update_agent_run_step(failed, 'failed')
    store(other, {'enabled_tools': ['generate_today_diary']}, 'agent_discover_tools', 'other-discovery')
    names = exposed(allowed, [], task_id=task['id'])
    assert names == (CORE & allowed) | {'get_today_state'}
    assert exposed(allowed - {'get_today_state'}, [], task_id=task['id']) == CORE & allowed


def test_discovered_schema_still_checks_live_availability():
    task = make_task()
    allowed = allowed_tools()
    store(task, {'enabled_tools': ['get_today_state']}, 'agent_discover_tools', 'discovery')
    names = exposed(allowed, [], task_id=task['id'])
    with patch('app.agent_loop_service.tool_availability',
               side_effect=lambda definition: (definition.name != 'get_today_state', 'synthetic unavailable')):
        assert all(item['function']['name'] != 'get_today_state' for item in _native_tools(names))


def test_small_or_explicit_tool_selection_keeps_existing_behavior():
    with patch.object(db, 'get_conn', side_effect=AssertionError('must not read discovery history')):
        assert exposed({'get_today_state'}, [], task_id='synthetic') == {'get_today_state'}
        allowed = allowed_tools() - {'agent_discover_tools'}
        assert len(allowed) > 16
        assert exposed(allowed, [], task_id='synthetic') == allowed
