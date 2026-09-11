"""Durable handoff from a conversational surface to the Agent workspace."""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import closing

from . import db, agent_task_service as tasks
from .agent_tool_service import ToolExecutionContext
from .model_runtime import model_selection
from .tool_registry import tool_registry


def create_handoff(arguments: dict, context: ToolExecutionContext, *, conversation_context: list[dict] | None = None) -> dict[str, object]:
    goal = str(arguments.get("goal") or "").strip()
    if not goal:
        raise ValueError("转交 Agent 必须包含明确目标。")
    request_key = str(context.request_id or context.run_id or "handoff").strip()
    suffix = hashlib.sha256(request_key.encode("utf-8")).hexdigest()[:24]
    conversation_id = f"desktop_agent_{suffix}"
    run_id = f"handoff:{request_key}"
    existing = tasks.for_run(run_id)
    if existing:
        return {"task_id": existing["id"], "conversation_id": existing["conversation_id"],
                "status": existing["status"], "message": "这件事已经交给 Agent 处理。"}
    model_id, reasoning = model_selection("agent")
    db.assert_conversation_writable(context.conversation_id)
    db.assert_conversation_writable(conversation_id)
    budget = tasks.limits()["cost_yuan"]
    history = [item for item in (conversation_context or []) if item.get("role") in {"user", "assistant"}]
    if not history:
        history = [{"role": "user", "content": context.user_message}]
    snapshot = {
        "goal": goal, "updates": [goal], "plan": [], "blocker": "", "next_step": "",
        "constraints": arguments.get("constraints") or [],
        "completion_criteria": arguments.get("completion_criteria") or [],
        "source_conversation_id": context.conversation_id,
        "source_message_id": context.source_message_id,
        "handoff_reason": str(arguments.get("reason") or "模型判断需要 Agent 持续执行")[:500],
    }
    task_id = "task_" + uuid.uuid4().hex[:24]
    now = db.now_iso()
    # All durable identity and payload records commit together, including retries.
    with closing(db.get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT t.id,t.conversation_id,t.status FROM agent_tasks t JOIN agent_task_runs r ON r.task_id=t.id WHERE r.run_id=?", (run_id,)).fetchone()
        if previous:
            return {"task_id": previous["id"], "conversation_id": previous["conversation_id"], "status": previous["status"]}
        if conn.execute("SELECT 1 FROM deleted_conversations WHERE id IN (?,?)", (context.conversation_id, conversation_id)).fetchone():
            raise ValueError("来源对话或目标对话已删除，不能转交。")
        conn.execute("INSERT INTO agent_conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)", (conversation_id, f"Agent：{goal[:48]}", now, now))
        conn.execute("INSERT INTO agent_tasks(id,conversation_id,source,status,original_goal,snapshot_json,model_id,reasoning_level,allowed_tools_json,context_json,budget_yuan,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (task_id, conversation_id, "chat_handoff", "ready", goal, tasks._json(snapshot), model_id, reasoning,
                      tasks._json([item.name for item in tool_registry.list() if item.name != "handoff_to_agent"]),
                      tasks._json(history), budget, now, now))
        conn.execute("INSERT INTO agent_task_runs VALUES(?,?,?)", (run_id, task_id, f"{run_id}:request"))
    task = tasks.get(task_id)
    return {"task_id": task["id"], "conversation_id": conversation_id, "status": "ready",
            "message": "我已经把这件事交给 Agent 继续处理了。详细进度会出现在 Agent 页面。"}


def latest_handoff(source_conversation_id: str) -> dict | None:
    tasks.initialize()
    with closing(db.get_conn()) as conn:
        row = conn.execute("SELECT id,conversation_id,status FROM agent_tasks WHERE source='chat_handoff' AND json_extract(snapshot_json,'$.source_conversation_id')=? ORDER BY created_at DESC,id DESC LIMIT 1", (source_conversation_id,)).fetchone()
    return {"task_id": row["id"], "conversation_id": row["conversation_id"], "status": row["status"]} if row else None
