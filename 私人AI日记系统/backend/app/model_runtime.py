"""Shared operation identity, lifecycle and cancellation for model-driven modes."""
from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import logging
import time
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from typing import Literal, Callable

from . import db, maintenance_service
from .config import SettingsConflictError

Mode = Literal["chat", "agent", "record", "proactive", "vision", "translation", "profile", "memory"]
MODES = ("chat", "agent", "record", "proactive", "vision", "translation", "profile", "memory")
logger = logging.getLogger(__name__)


class OperationBlocked(ValueError):
    pass


class OperationStopped(asyncio.CancelledError):
    """A supervised child stopped; its recurring scheduler can remain alive."""


@dataclass(frozen=True)
class OperationContext:
    id: str
    mode: Mode
    purpose: str
    request_id: str
    conversation_id: str = ""
    task_id: str = ""
    parent_id: str = ""
    automatic: bool = False


_context: contextvars.ContextVar[OperationContext | None] = contextvars.ContextVar("mio_operation", default=None)
_active: dict[str, dict] = {}


def initialize() -> None:
    with closing(db.get_conn()) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runtime_operations (
                id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, request_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL, task_id TEXT NOT NULL, mode TEXT NOT NULL,
                purpose TEXT NOT NULL, automatic INTEGER NOT NULL, status TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_operations_request ON runtime_operations(request_id);
            CREATE INDEX IF NOT EXISTS idx_runtime_operations_task ON runtime_operations(task_id,started_at);
            CREATE TABLE IF NOT EXISTS runtime_model_calls (
                id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, request_id TEXT NOT NULL,
                task_id TEXT NOT NULL, conversation_id TEXT NOT NULL, mode TEXT NOT NULL,
                selected_model_id TEXT NOT NULL, actual_model_id TEXT NOT NULL DEFAULT '',
                provider_id TEXT NOT NULL DEFAULT '', reasoning_level TEXT NOT NULL,
                status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
                prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
                cost_yuan REAL, cost_source TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '',
                http_status INTEGER NOT NULL DEFAULT 0, duration_ms REAL
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_model_calls_mode ON runtime_model_calls(mode,started_at);
            CREATE INDEX IF NOT EXISTS idx_runtime_model_calls_request ON runtime_model_calls(request_id);
            CREATE TABLE IF NOT EXISTS runtime_model_policies (
                mode TEXT PRIMARY KEY, policy_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1
            );
        """)
        if "duration_ms" not in {row[1] for row in conn.execute("PRAGMA table_info(runtime_model_calls)")}:
            conn.execute("ALTER TABLE runtime_model_calls ADD COLUMN duration_ms REAL")
        conn.execute("UPDATE runtime_operations SET status='interrupted',finished_at=? WHERE status='running'", (db.now_iso(),))
        conn.execute("UPDATE runtime_model_calls SET status='unknown',finished_at=?,error_code='interrupted' WHERE status='running'", (db.now_iso(),))


def model_policy(mode: Mode) -> dict:
    if mode not in MODES:
        raise ValueError("未知运行模式。")
    default = {"selection": "follow_chat", "model_id": "", "reasoning_level": "inherit", "enabled": True}
    if mode == "record" and db.settings.daily_diary_model_id:
        default.update(selection="fixed", model_id=db.settings.daily_diary_model_id)
    if db.settings.db_path.is_file():
        with closing(db.get_conn()) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_model_policies'").fetchone():
                row = conn.execute("SELECT * FROM runtime_model_policies WHERE mode=?", (mode,)).fetchone()
                if row:
                    return {"mode": mode, **default, **json.loads(row["policy_json"]), "revision": row["revision"]}
    return {"mode": mode, **default, "revision": 0}


def sync_record_model_policy(model_id: str) -> None:
    """Keep the older daily_diary_model_id setting compatible with the new policy UI."""
    if not db.settings.db_path.is_file():
        return
    current_policy = model_policy("record")
    values = {"selection": "fixed", "model_id": model_id} if model_id else {"selection": "follow_chat", "model_id": ""}
    try:
        save_model_policy("record", values, expected_revision=int(current_policy.get("revision") or 0))
    except (SettingsConflictError, ValueError):
        logger.warning("无法同步日记模型策略，请从模型策略面板重新保存。", exc_info=True)


def save_model_policy(mode: Mode, values: dict, *, expected_revision: int) -> dict:
    current_policy = model_policy(mode)
    allowed = {"selection", "model_id", "reasoning_level", "enabled"}
    if set(values) - allowed:
        raise ValueError("模型策略包含未知字段。")
    policy = {key: values.get(key, current_policy[key]) for key in allowed}
    if policy["selection"] not in {"follow_chat", "fixed"} or not isinstance(policy["enabled"], bool):
        raise ValueError("模型选择策略无效。")
    if not isinstance(policy["reasoning_level"], str) or len(policy["reasoning_level"]) > 40:
        raise ValueError("推理档位无效。")
    if policy["selection"] == "fixed":
        from .model_registry import get_model_profile
        if not policy["model_id"]:
            raise ValueError("请选择固定模型。")
        get_model_profile(str(policy["model_id"]))
    with closing(db.get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT revision FROM runtime_model_policies WHERE mode=?", (mode,)).fetchone()
        revision = row[0] if row else 0
        if revision != expected_revision:
            from .config import SettingsConflictError
            raise SettingsConflictError("模型策略已经更新，请重新载入。")
        conn.execute("INSERT INTO runtime_model_policies VALUES(?,?,?) ON CONFLICT(mode) DO UPDATE SET policy_json=excluded.policy_json,revision=excluded.revision",
                     (mode, json.dumps(policy, ensure_ascii=False), revision + 1))
    return model_policy(mode)


def model_selection(mode: Mode, model_id: str = "", reasoning_level: str = "standard") -> tuple[str, str]:
    policy = model_policy(mode)
    if not policy["enabled"]:
        raise OperationBlocked("此模式的模型调用已暂停。")
    # Unconfigured modes retain their dedicated selectors (especially vision).
    # Once saved, a mode policy also governs persisted/background callers.
    if model_id and not policy["revision"] and policy["selection"] != "fixed":
        return model_id, reasoning_level
    from .companion_service import load_config
    shared = load_config()
    selected = (policy["model_id"] if policy["selection"] == "fixed"
                else str(shared.get("chat_model_id") or "auto"))
    reasoning = policy["reasoning_level"]
    if reasoning == "inherit":
        reasoning = (reasoning_level if not policy["revision"] and reasoning_level not in {"", "standard", "auto"}
                     else str(shared.get("chat_reasoning_level") or "auto"))
    return str(selected), str(reasoning)


_model_depth: contextvars.ContextVar[int] = contextvars.ContextVar("mio_model_depth", default=0)


def _call_record(call_id: str, context: OperationContext, values: dict, *, result=None, error=None, duration_ms: float | None = None) -> None:
    if result is None and error is None:
        entry = _active.get(context.id)
        if entry is not None and not entry.get("persisted"):
            _record(context, "running")
            entry["persisted"] = True
    if not db.settings.db_path.is_file():
        return
    with closing(db.get_conn()) as conn, conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_model_calls'").fetchone():
            return
        if result is None and error is None:
            conn.execute("INSERT INTO runtime_model_calls(id,operation_id,request_id,task_id,conversation_id,mode,selected_model_id,reasoning_level,status,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (call_id, context.id, values.get("request_id") or context.request_id, context.task_id,
                          context.conversation_id, context.mode, values.get("model_id") or "auto",
                          values.get("reasoning_level") or "auto", "running", db.now_iso()))
        elif error is not None:
            conn.execute("UPDATE runtime_model_calls SET status=?,finished_at=?,error_code=?,http_status=?,duration_ms=? WHERE id=?",
                         ("cancelled" if isinstance(error, asyncio.CancelledError) else "failed", db.now_iso(), type(error).__name__, int(getattr(error, "http_status", 0) or 0), duration_ms, call_id))
        else:
            conn.execute("UPDATE runtime_model_calls SET status='completed',finished_at=?,actual_model_id=?,provider_id=?,prompt_tokens=?,completion_tokens=?,cost_yuan=?,cost_source=?,http_status=?,duration_ms=? WHERE id=?",
                         (db.now_iso(), result.profile_id or values.get("model_id") or result.model, result.provider_id,
                          result.prompt_tokens, result.completion_tokens, result.cost_yuan, result.cost_source, result.http_status, duration_ms, call_id))


def model_call(callback):
    """Both streaming and regular calls share routing and a single usage ledger."""
    signature = inspect.signature(callback)
    @functools.wraps(callback)
    async def run(*args, **kwargs):
        if _model_depth.get():
            return await callback(*args, **kwargs)
        async def invoke(request_id=""):
            context = current()
            check_allowed(context)
            bound = signature.bind_partial(*args, **kwargs)
            selected, reasoning = model_selection(context.mode, str(bound.arguments.get("model_id") or ""), str(bound.arguments.get("reasoning_level") or "standard"))
            bound.arguments.update(model_id=selected, reasoning_level=reasoning)
            if "request_id" in signature.parameters and not bound.arguments.get("request_id"):
                bound.arguments["request_id"] = context.request_id
            call_id = "call_" + uuid.uuid4().hex
            token = _model_depth.set(1)
            _call_record(call_id, context, bound.arguments)
            started = time.monotonic()
            try:
                result = await callback(*bound.args, **bound.kwargs)
                _call_record(call_id, context, bound.arguments, result=result, duration_ms=(time.monotonic()-started)*1000)
                return result
            except BaseException as error:
                _call_record(call_id, context, bound.arguments, error=error, duration_ms=(time.monotonic()-started)*1000)
                raise
            finally:
                _model_depth.reset(token)
        if current():
            return await invoke()
        values = signature.bind_partial(*args, **kwargs).arguments
        return await operation("chat")(invoke)(request_id=str(values.get("request_id") or ""))
    return run


def current() -> OperationContext | None:
    return _context.get()


def bind_task(task_id: str) -> None:
    context = current()
    if context is None:
        return
    updated = replace(context, task_id=task_id)
    _context.set(updated)
    if context.id in _active:
        _active[context.id]["task_id"] = task_id
    with closing(db.get_conn()) as conn, conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_operations'").fetchone():
            conn.execute("UPDATE runtime_operations SET task_id=? WHERE id=?", (task_id, context.id))


def protocol_session_id(scope: str = "") -> str:
    context = current()
    identity = (context.conversation_id or context.task_id or f"{context.mode}:{context.purpose}") if context else scope or "model-connection-test"
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, str(db.settings.db_path.resolve()))
    return "mio-" + uuid.uuid5(namespace, identity).hex


def privacy_blocked() -> bool:
    from .privacy_service import _load_state
    state = _load_state()
    return bool(state.get("paused") or state.get("transition") in {
        "pausing", "pause_incomplete", "resume_incomplete", "state_uncertain"})


def check_allowed(context: OperationContext | None = None) -> None:
    context = context or current()
    if maintenance_service.status()["blocked"]:
        raise OperationBlocked("Mio 正在维护数据，当前操作已停止。")
    if context and context.automatic and privacy_blocked():
        raise OperationBlocked("隐私暂停中，自动模型操作已停止。")
    if context and context.conversation_id and db.conversation_deleted(context.conversation_id):
        raise OperationBlocked("对话正在删除或已删除，当前操作已停止。")


def _record(context: OperationContext, status: str, error_code: str = "") -> None:
    # Import-time and isolated library calls need not create a runtime database.
    if not db.settings.db_path.is_file():
        return
    with closing(db.get_conn()) as conn, conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_operations'").fetchone() is None:
            return
        if status == "running":
            conn.execute("INSERT OR IGNORE INTO runtime_operations(id,parent_id,request_id,conversation_id,task_id,mode,purpose,automatic,status,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (context.id, context.parent_id, context.request_id, context.conversation_id,
                          context.task_id, context.mode, context.purpose, context.automatic, status, db.now_iso()))
        else:
            conn.execute("UPDATE runtime_operations SET status=?,finished_at=?,error_code=? WHERE id=?",
                         (status, db.now_iso(), error_code, context.id))


def operation(mode: Mode | Callable[[dict], Mode], *, automatic: bool = False, persist: bool = True):
    """Supervise the complete operation, including post-model persistence."""
    if not callable(mode) and mode not in MODES:
        raise ValueError("未知运行模式。")
    def decorate(callback):
        signature = inspect.signature(callback)
        @functools.wraps(callback)
        async def run(*args, **kwargs):
            parent = current()
            values = dict(signature.bind_partial(*args, **kwargs).arguments)
            for parameter in signature.parameters.values():
                if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                    values.update(values.get(parameter.name, {}))
            selected_mode = mode(values) if callable(mode) else mode
            if selected_mode not in MODES:
                raise ValueError("未知运行模式。")
            try:
                row = dict(values.get("row") or {})
            except (TypeError, ValueError):
                row = {}
            identifier = "op_" + uuid.uuid4().hex
            task_id = str(values.get("task_id") or getattr(values.get("agent"), "task_id", "") or (parent.task_id if parent else ""))
            conversation_id = str(values.get("conversation_id") or row.get("conversation_id") or (parent.conversation_id if parent else ""))
            if task_id and not conversation_id:
                with closing(db.get_conn()) as conn:
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_tasks'").fetchone():
                        task = conn.execute("SELECT conversation_id FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
                        if task:
                            conversation_id = task[0]
            context = OperationContext(
                id=identifier, mode=selected_mode, purpose=callback.__name__, automatic=automatic or bool(parent and parent.automatic),
                request_id=str(values.get("request_id") or (parent.request_id if parent else identifier)),
                conversation_id=conversation_id,
                task_id=task_id,
                parent_id=parent.id if parent else "",
            )
            check_allowed(context)
            state = {**asdict(context), "started_at": db.now_iso(), "stop_reason": "", "persisted": persist}
            async def invoke():
                token = _context.set(context)
                try:
                    check_allowed(context)
                    return await callback(*args, **kwargs)
                finally:
                    _context.reset(token)
            if persist:
                _record(context, "running")
            process = asyncio.create_task(invoke(), name=f"mio:{selected_mode}:{callback.__name__}")
            state["process"] = process
            _active[identifier] = state
            try:
                result = await process
                if state["persisted"]:
                    _record(context, "completed")
                return result
            except asyncio.CancelledError:
                if state["persisted"]:
                    _record(context, "cancelled", state["stop_reason"] or "cancelled")
                if state["stop_reason"]:
                    raise OperationStopped(state["stop_reason"]) from None
                raise
            except Exception as exc:
                if state["persisted"]:
                    _record(context, "failed", type(exc).__name__)
                raise
            finally:
                _active.pop(identifier, None)
        return run
    return decorate


def live_operations(*, automatic_only: bool = False) -> list[dict]:
    return [{key: value for key, value in entry.items() if key != "process"}
            for entry in list(_active.values()) if not automatic_only or entry["automatic"]]


async def cancel_operations(*, automatic_only: bool = False, conversation_id: str = "", reason: str = "cancelled") -> int:
    pending = []
    for entry in list(_active.values()):
        process = entry["process"]
        if process is asyncio.current_task() or process.done():
            continue
        if automatic_only and not entry["automatic"]:
            continue
        if conversation_id and entry["conversation_id"] != conversation_id:
            continue
        entry["stop_reason"] = reason
        pending.append(process)
        process.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        # Let supervising callers finish their status updates before acknowledging stop.
        await asyncio.sleep(0)
    return len(pending)
