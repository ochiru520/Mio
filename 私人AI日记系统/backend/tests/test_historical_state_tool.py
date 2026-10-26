"""Historical state retrieval through real execution, using synthetic local data."""
import asyncio
from contextlib import closing
from unittest.mock import patch

import pytest

from app import db
from app.agent_tool_service import ToolExecutionContext, execute_tool_call
from app.runtime_profiles import resolve_runtime_profile
from app.tool_registry import ToolPermission, tool_registry
from test_runtime_safety_v2 import isolated


TODAY = "2026-10-24"
YESTERDAY = "2026-10-23"


def context(conversation_id="desktop_history_test", step_index=1):
    run_id = "run-history-" + conversation_id
    db.create_agent_run(run_id, "request-history", conversation_id=conversation_id,
                        source="desktop", source_message_id=0, model_id="synthetic")
    return ToolExecutionContext(run_id=run_id, request_id="request-history", trace_id="trace-history",
        conversation_id=conversation_id, source_message_id=0, user_message="查看指定日期的状态", step_index=step_index)


def test_given_date_and_today_read_distinct_records_without_mutation():
    db.upsert_daily_state(YESTERDAY, "done", "昨天的合成情绪", "昨天的合成事件", "", "")
    db.upsert_daily_state(TODAY, "unknown", "今天的合成情绪", "今天的合成事件", "", "")
    with closing(db.get_conn()) as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM daily_states ORDER BY date")]
    with patch.object(db, "today_string", return_value=TODAY):
        past = asyncio.run(execute_tool_call("get_daily_state", {"date": YESTERDAY}, context()))
        current = asyncio.run(execute_tool_call("get_today_state", {}, context(step_index=2)))
    assert past.status == current.status == "completed"
    assert past.result["date"] == past.result["state"]["date"] == YESTERDAY
    assert past.result["state"]["key_events"] == "昨天的合成事件"
    assert current.result["date"] == current.result["state"]["date"] == TODAY
    assert current.result["state"]["key_events"] == "今天的合成事件"
    assert past.action_id == current.action_id == 0
    assert past.receipt_id > 0 and current.receipt_id > 0
    with closing(db.get_conn()) as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM daily_states ORDER BY date")] == before


def test_no_record_returns_none_without_creating_state():
    result = asyncio.run(execute_tool_call("get_daily_state", {"date": YESTERDAY}, context()))
    assert result.status == "completed" and result.result == {"date": YESTERDAY, "state": None}
    assert db.get_daily_state(YESTERDAY) is None


@pytest.mark.parametrize("arguments", [{}, {"date": ""}, {"date": "昨天"}, {"date": "2026-2-03"},
    {"date": "2026-02-30"}, {"date": "2026-13-01"}, {"date": "0000-01-01"}, {"date": "2026-10-23T00:00:00"},
    {"date": YESTERDAY, "extra": "forbidden"}])
def test_invalid_date_is_rejected_before_dispatch(arguments):
    with patch.object(db, "get_daily_state", side_effect=AssertionError("invalid inputs must not read state")):
        with pytest.raises(ValueError, match="Schema"):
            asyncio.run(execute_tool_call("get_daily_state", arguments, context()))


def test_registry_requires_explicit_date_and_companion_can_discover_the_read_tool():
    definition = tool_registry.require("get_daily_state")
    assert definition.permission == ToolPermission.READ_ONLY
    schema = definition.native_schema()["function"]["parameters"]
    assert "date" in schema["required"]
    assert "get_daily_state" in resolve_runtime_profile("companion").allowed_tool_names
    assert "get_daily_state" in resolve_runtime_profile("agent").allowed_tool_names
    assert definition.validate_arguments({"date": "2024-02-29"}) == {"date": "2024-02-29"}
    assert tool_registry.require("get_today_state").validate_arguments({}) == {}


@pytest.mark.parametrize("name,arguments", [("get_daily_state", {"date": YESTERDAY}), ("get_today_state", {})])
def test_group_chat_cannot_read_private_states(name, arguments):
    db.upsert_daily_state(YESTERDAY, "done", "合成私人情绪", "合成私人状态内容", "", "")
    with patch.object(db, "get_daily_state", side_effect=AssertionError("group chat must not read private state")):
        result = asyncio.run(execute_tool_call(name, arguments, context("qq_group_synthetic")))
    assert result.status == "failed" and "群聊" in result.error
    assert "state" not in result.result and "合成私人状态内容" not in str(result.public_dict())
