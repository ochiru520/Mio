"""Durable goals shared by chat execution, asynchronous jobs and the task UI."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from . import db
from .model_runtime import operation


TERMINAL = {"completed", "cancelled"}
WAITING = {"waiting_jobs", "waiting_confirmation"}
DEFAULT_LIMITS = {"enabled": False, "model_calls": 8, "tool_calls": 20, "seconds": 180, "cost_yuan": 2.0}
_active: dict[str, asyncio.Task] = {}
_background: dict[str, asyncio.Task] = {}
_approvals: dict[str, set[asyncio.Task]] = {}
logger = logging.getLogger(__name__)


def initialize() -> None:
    with db.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL, original_goal TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                model_id TEXT NOT NULL, reasoning_level TEXT NOT NULL,
                allowed_tools_json TEXT NOT NULL, context_json TEXT NOT NULL,
                waiting_jobs_json TEXT NOT NULL DEFAULT '[]',
                spent_yuan REAL NOT NULL DEFAULT 0, budget_yuan REAL NOT NULL DEFAULT 2,
                revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_task_runs (
                run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_agent_tasks_conversation
                ON agent_tasks(conversation_id, updated_at);
            CREATE TABLE IF NOT EXISTS agent_task_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), settings_json TEXT NOT NULL
            );
        """)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _public(row) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for key in ("snapshot", "allowed_tools", "context", "waiting_jobs"):
        item[key] = json.loads(item.pop(key + "_json"))
    return item


def get(task_id: str) -> dict[str, Any] | None:
    initialize()
    with db.get_conn() as conn:
        return _public(conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())


def for_run(run_id: str) -> dict[str, Any] | None:
    initialize()
    with db.get_conn() as conn:
        row = conn.execute("SELECT t.* FROM agent_tasks t JOIN agent_task_runs r ON r.task_id=t.id WHERE r.run_id=?", (run_id,)).fetchone()
    return _public(row)


def list_tasks(conversation_id: str = "", limit: int = 50, *, states: tuple[str, ...] = (), offset: int = 0, oldest: bool = False) -> list[dict[str, Any]]:
    initialize()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_tasks WHERE (?='' OR conversation_id=?)"
            + (" AND status IN (" + ",".join("?" for _ in states) + ")" if states else "")
            + " ORDER BY updated_at " + ("ASC" if oldest else "DESC") + ",id LIMIT ? OFFSET ?",
            (conversation_id, conversation_id, *states, max(1, min(limit, 200)), max(0, offset)),
        ).fetchall()
    return [_public(row) for row in rows]


def limits(changes: dict[str, Any] | None = None) -> dict[str, Any]:
    initialize()
    with db.get_conn() as conn:
        row = conn.execute("SELECT settings_json FROM agent_task_settings WHERE id=1").fetchone()
        result = {**DEFAULT_LIMITS, **(json.loads(row[0]) if row else {})}
        if changes is not None:
            ranges = {"model_calls": (2, 24), "tool_calls": (1, 100), "seconds": (15, 1800), "cost_yuan": (0.01, 100)}
            for key, value in changes.items():
                if key == "enabled":
                    result[key] = bool(value)
                    continue
                if key not in ranges or not ranges[key][0] <= float(value) <= ranges[key][1]:
                    raise ValueError("任务预算参数超出范围。")
                result[key] = float(value) if key == "cost_yuan" else int(value)
            conn.execute("INSERT INTO agent_task_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET settings_json=excluded.settings_json", (_json(result),))
    return result


def prepare(*, run_id: str, request_id: str, conversation_id: str, source: str,
            user_message: str, model_id: str, reasoning_level: str,
            allowed_tools: list[str], context: list[dict], task_id: str = "") -> dict[str, Any]:
    initialize()
    previous = for_run(run_id)
    if previous:
        return previous
    db.assert_conversation_writable(conversation_id)
    task = get(task_id) if task_id else None
    if task_id and (task is None or task["conversation_id"] != conversation_id or task["status"] in {*TERMINAL, "paused"}):
        raise ValueError("任务已停止或不属于当前对话，请明确恢复后继续。")
    now = db.now_iso()
    if task is None:
        task_id = "task_" + uuid.uuid4().hex[:24]
        snapshot = {"goal": user_message, "constraints": [], "completion_criteria": [],
                    "plan": [], "blocker": "", "next_step": "", "updates": [user_message]}
        with db.get_conn() as conn:
            conn.execute("INSERT INTO agent_tasks(id,conversation_id,source,status,original_goal,snapshot_json,model_id,reasoning_level,allowed_tools_json,context_json,budget_yuan,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, conversation_id, source, "running", user_message, _json(snapshot), model_id,
                 reasoning_level, _json(allowed_tools), _json(context), limits()["cost_yuan"], now, now))
    else:
        task_id = task["id"]
        snapshot = task["snapshot"]
        if source != "agent_background":
            snapshot["updates"] = [*snapshot.get("updates", []), user_message][-30:]
        with db.get_conn() as conn:
            conn.execute("UPDATE agent_tasks SET status='running',snapshot_json=?,model_id=?,reasoning_level=?,allowed_tools_json=?,context_json=?,revision=revision+1,updated_at=? WHERE id=?",
                (_json(snapshot), model_id, reasoning_level, _json(allowed_tools), _json(context), now, task_id))
    with db.get_conn() as conn:
        conn.execute("INSERT INTO agent_task_runs VALUES(?,?,?)", (run_id, task_id, request_id))
    return get(task_id)


def update(task_id: str, *, status: str | None = None, snapshot: dict | None = None,
           waiting_jobs: list[str] | None = None, cost: float = 0) -> dict[str, Any]:
    task = get(task_id)
    if task is None:
        raise ValueError("找不到任务。")
    fields = {"updated_at": db.now_iso()}
    if status is not None:
        if task["status"] != "cancelled":
            fields["status"] = status
    if snapshot is not None:
        fields["snapshot_json"] = _json({**task["snapshot"], **snapshot})
    if waiting_jobs is not None:
        fields["waiting_jobs_json"] = _json(list(dict.fromkeys(waiting_jobs)))
    with db.get_conn() as conn:
        conn.execute("UPDATE agent_tasks SET spent_yuan=spent_yuan+?," + ",".join(key + "=?" for key in fields) + " WHERE id=?", (max(0, cost), *fields.values(), task_id))
    return get(task_id)


def observations(task_id: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute("""SELECT s.* FROM agent_run_steps s JOIN agent_task_runs r ON r.run_id=s.run_id
            WHERE r.task_id=? AND s.step_kind='tool_call' ORDER BY s.id DESC LIMIT 100""", (task_id,)).fetchall()
    result = []
    for row in reversed(rows):
        try:
            payload = json.loads(row["result_json"] or "{}")
        except ValueError:
            payload = {}
        result.append({"tool_name": row["tool_name"], "status": row["status"], "result": payload,
                       "error": row["error"], "step_id": row["id"], "action_id": row["action_id"]})
    from .creation_service import get_job
    for item in result:
        job = item["result"].get("job", {})
        if job.get("id"):
            current = get_job(str(job["id"]))
            if current:
                item["result"]["job"] = current
    return result


def bind(task_id: str) -> None:
    current = asyncio.current_task()
    existing = _active.get(task_id)
    if existing is not None and not existing.done() and existing is not current:
        raise ValueError("任务仍在执行，请先停止当前执行。")
    if current:
        _active[task_id] = current
        current.add_done_callback(lambda process: _active.pop(task_id, None) if _active.get(task_id) is process else None)


def unbind(task_id: str) -> None:
    if _active.get(task_id) is asyncio.current_task():
        _active.pop(task_id, None)


async def pause(task_id: str, *, cancel_jobs: bool = False, stop_linked_jobs: bool = False) -> dict[str, Any]:
    task = get(task_id)
    if task is None:
        raise ValueError("找不到任务。")
    status = "cancelled" if cancel_jobs else "paused"
    update(task_id, status=status)
    pending = {p for p in (_active.get(task_id), _background.get(task_id), *_approvals.get(task_id, set()))
               if p and p is not asyncio.current_task() and not p.done()}
    for process in pending:
        process.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    if cancel_jobs:
        with db.get_conn() as conn:
            run_ids = "SELECT run_id FROM agent_task_runs WHERE task_id=?"
            conn.execute(f"UPDATE companion_actions SET status='cancelled' WHERE agent_run_id IN ({run_ids}) AND status IN ('needs_confirmation','running')", (task_id,))
            conn.execute(f"UPDATE agent_run_steps SET status='cancelled' WHERE run_id IN ({run_ids}) AND status IN ('needs_confirmation','running')", (task_id,))
            conn.execute(f"UPDATE tool_execution_receipts SET status='cancelled' WHERE agent_run_id IN ({run_ids}) AND status IN ('needs_confirmation','running')", (task_id,))
    if cancel_jobs or stop_linked_jobs:
        from . import creation_service
        job_ids = set(task["waiting_jobs"])
        for item in observations(task_id):
            job = item["result"].get("job", {})
            if job.get("id"):
                job_ids.add(job["id"])
        for job_id in job_ids:
            job = creation_service.get_job(job_id)
            if job and job["status"] not in creation_service.TERMINAL_STATUSES:
                await creation_service.cancel_job(job_id)
    return update(task_id, status=status)


def resume(task_id: str) -> dict[str, Any]:
    task = get(task_id)
    if task is None or task["status"] in TERMINAL:
        raise ValueError("已完成或取消的任务不能继续，请创建新任务。")
    if task_id in _active and not _active[task_id].done():
        return task
    from .creation_service import get_job
    if any((get_job(job_id) or {}).get('status') == 'unknown' for job_id in task['waiting_jobs']):
        raise ValueError('关联生成结果仍未知，请先核对回执；不要通过继续任务重复生成。')
    with db.get_conn() as conn:
        conn.execute("UPDATE agent_tasks SET budget_yuan=?,status='ready',revision=revision+1,updated_at=? WHERE id=?",
                     (task["spent_yuan"] + limits()["cost_yuan"], db.now_iso(), task_id))
    return get(task_id)


def recover_interrupted() -> None:
    initialize()
    with db.get_conn() as conn:
        # A running write has an unknown outcome after a crash. Reads can be retried safely.
        conn.execute("""UPDATE agent_tasks SET status=CASE WHEN EXISTS(
            SELECT 1 FROM agent_run_steps s JOIN agent_task_runs r ON s.run_id=r.run_id
            WHERE r.task_id=agent_tasks.id AND s.step_kind='tool_call' AND s.status='running'
                AND s.permission!='read_only') THEN 'paused' ELSE 'ready' END,
            updated_at=? WHERE status IN ('running','responding')""", (db.now_iso(),))


async def yield_to_user(conversation_id: str) -> None:
    pending = [process for task_id, process in list(_background.items())
               if not process.done() and process is not asyncio.current_task()
               and (get(task_id) or {}).get("conversation_id") == conversation_id]
    for process in pending:
        process.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def ready_to_continue(task: dict[str, Any]) -> bool:
    if automatic_work_paused() or db.conversation_deleted(task["conversation_id"]):
        return False
    if task["id"] in _active or task["id"] in _background:
        return False
    if task["status"] == "ready":
        return True
    if task["status"] not in WAITING:
        return False
    with db.get_conn() as conn:
        last = conn.execute("SELECT a.status FROM agent_runs a JOIN agent_task_runs r ON a.run_id=r.run_id WHERE r.task_id=? ORDER BY a.created_at DESC LIMIT 1", (task["id"],)).fetchone()
    # run_agent_loop deliberately leaves the run in awaiting_response until the
    # short progress/commit reply is written.  That reply is not a reason to
    # block an async task whose creation job has already reached a terminal
    # state; treating it as resumable also makes restart recovery deterministic.
    if last is not None and last["status"] not in {"completed", "failed", "interrupted", "awaiting_response"}:
        return False
    items = observations(task["id"])
    if task["status"] == "waiting_confirmation":
        return not any(item["status"] in {"needs_confirmation", "running"} for item in items)
    from .creation_service import get_job, TERMINAL_STATUSES
    jobs = [get_job(job_id) for job_id in task["waiting_jobs"]]
    if any(job and job["status"] == "unknown" for job in jobs):
        update(task["id"], status="waiting_user", snapshot={
            "blocker": "关联生成结果待核对，已停止自动推进。",
            "next_step": "在任务页核对供应商回执；确认后再继续。"})
        return False
    return bool(jobs) and all(job is None or job["status"] in TERMINAL_STATUSES for job in jobs)


@operation("agent", automatic=True)
async def continue_task(task_id: str) -> None:
    from .agent_loop_service import run_agent_loop, begin_final_response, finish_final_response, final_model_call
    from .llm import call_chat_completion_result
    from .chat_service import clean_chat_reply, _guard_agent_creation_completion_claim

    task = get(task_id)
    if automatic_work_paused() or task is None or task["status"] not in {"ready", *WAITING}:
        return
    request_id = f"task:{task_id}:{task['revision'] + 1}"
    message = str(task["snapshot"].get("updates", [task["original_goal"]])[-1])
    try:
        result = await run_agent_loop(
            conversation_id=task["conversation_id"], source="agent_background", user_message=message,
            source_message_id=int(task["snapshot"].get("source_message_id", 0)), request_id=request_id,
            trace_id=request_id, model_id=task["model_id"], reasoning_level=task["reasoning_level"],
            allowed_tool_names=set(task["allowed_tools"]), context_snapshot=task["context"], task_id=task_id,
        )
        step_id = begin_final_response(result)
        bind(task_id)
        reply = await final_model_call(result, call_chat_completion_result, [
            {"role": "system", "content": "你是 Mio。根据任务的真实状态简短告知进度或结果，不声称未执行的操作。需要用户补充时只问最必要的问题。"},
            {"role": "user", "content": task["original_goal"]},
            {"role": "system", "content": result.model_context()},
        ], model_id=task["model_id"], reasoning_level=task["reasoning_level"],
            request_id=request_id + ":reply", retry_attempts=1)
        if automatic_work_paused() or (get(task_id) or {}).get("status") in {None, "cancelled", "paused"}:
            raise asyncio.CancelledError
        text = clean_chat_reply(reply.content)
        text = "\n".join(_guard_agent_creation_completion_claim([text], task["conversation_id"], result))
        db.save_message("assistant", text or "任务状态已更新。", source="agent_background",
            conversation_id=task["conversation_id"], request_id=request_id,
            model_id=task["model_id"], provider_model=reply.model, reasoning_level=task["reasoning_level"],
            prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens,
            request_cost_yuan=float(reply.cost_yuan or 0) + sum(float(item.cost_yuan or 0) for item in result.model_results),
            request_cost_source=reply.cost_source, delivery_key=f"task-result:{request_id}")
        finish_final_response(result, step_id, reply=text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("任务续推失败: %s", task_id)
        if get(task_id):
            update(task_id, status="failed", snapshot={"blocker": str(exc)[:500], "next_step": "检查错误后继续任务。"})
        raise
    finally:
        unbind(task_id)


def automatic_work_paused() -> bool:
    from .privacy_service import _load_state
    state = _load_state()
    return bool(state.get("paused") or state.get("transition") in {"pausing", "pause_incomplete", "resume_incomplete", "state_uncertain"})


async def stop_tasks(conversation_id: str = "", *, cancel_jobs: bool = False, stop_linked_jobs: bool = False) -> None:
    initialize()
    with db.get_conn() as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM agent_tasks WHERE (?='' OR conversation_id=?) AND status!='cancelled'", (conversation_id, conversation_id)) if row[0] in _active or row[0] in _background or (get(row[0]) or {}).get("status") != "completed"]
    for task_id in ids:
        await pause(task_id, cancel_jobs=cancel_jobs, stop_linked_jobs=stop_linked_jobs)


async def process_ready() -> int:
    if automatic_work_paused():
        return 0
    count = 0
    initialize()
    with db.get_conn() as conn:
        candidates = [_public(row) for row in conn.execute("SELECT * FROM agent_tasks WHERE status IN ('ready','waiting_jobs','waiting_confirmation') ORDER BY updated_at,id")]
    for task in candidates:
        if ready_to_continue(task):
            process = asyncio.create_task(continue_task(task["id"]), name=f"agent-task:{task['id']}")
            _background[task["id"]] = process
            def finished(process, key=task["id"]):
                if _background.get(key) is process:
                    _background.pop(key, None)
                if not process.cancelled():
                    process.exception()  # Failure is persisted by continue_task and its supervisor.
            process.add_done_callback(finished)
            count += 1
    return count


async def task_loop() -> None:
    try:
        while True:
            await process_ready()
            await asyncio.sleep(2)
    finally:
        pending = list(_background.values())
        for process in pending:
            process.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        _background.clear()
