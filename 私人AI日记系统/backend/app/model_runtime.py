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
import hashlib
from dataclasses import asdict, dataclass, replace
from contextlib import asynccontextmanager
from typing import Literal, Callable

from .repositories import model_runtime_repository as repository

from . import db, maintenance_service
from .config import SettingsConflictError
from . import prompt_metrics
from .repositories import prompt_metrics_repository

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
    with repository.unit_of_work() as conn:
        conn.ensure_runtime_tables()
        if "duration_ms" not in {row[1] for row in conn.model_call_columns()}:
            conn.add_call_duration()
        prompt_metrics_repository.initialize(conn.connection)
        prompt_metrics_repository.interrupt_attempts(conn.connection, db.now_iso())
        conn.interrupt_operations((db.now_iso(),))
        conn.interrupt_model_calls((db.now_iso(),))


def model_policy(mode: Mode) -> dict:
    if mode not in MODES:
        raise ValueError("未知运行模式。")
    default = {"selection": "follow_chat", "model_id": "", "reasoning_level": "inherit", "enabled": True}
    if mode == "record" and db.settings.daily_diary_model_id:
        default.update(selection="fixed", model_id=db.settings.daily_diary_model_id)
    if db.settings.db_path.is_file():
        with repository.unit_of_work(write=False) as conn:
            if conn.policies_available():
                row = conn.stored_policy((mode,))
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
    with repository.unit_of_work() as conn:
        conn.lock_for_update()
        row = conn.policy_revision((mode,))
        revision = row[0] if row else 0
        if revision != expected_revision:
            from .config import SettingsConflictError
            raise SettingsConflictError("模型策略已经更新，请重新载入。")
        conn.save_policy((mode, json.dumps(policy, ensure_ascii=False), revision + 1))
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
_attempt_context = contextvars.ContextVar('mio_http_attempts', default=None)


def record_current_prompt(messages, tools, *, route_budget=None):
    state = _attempt_context.get()
    context = current()
    if not state or context is None or not db.settings.db_path.is_file():
        return
    metrics = prompt_metrics.estimate_request(messages, tools)
    if route_budget:
        metrics.update(route_budget)
    from .repositories.context_offload_repository import list_scope
    serialized = json.dumps(messages, ensure_ascii=False, default=str)
    metrics['input_references'] = [item for item in list_scope(context.task_id or context.conversation_id or context.request_id) if item['id'] in serialized]
    with repository.unit_of_work() as conn:
        prompt_metrics_repository.record_estimate(conn.connection, state['call_id'],
            category=prompt_metrics.request_category(context.mode, context.purpose, state.get('request_id') or context.request_id, context.automatic),
            streaming=state.get('streaming', False), metrics=metrics)


@asynccontextmanager
async def transport_attempt(payload, url, route=None):
    """One record per actual send, including compatibility fallbacks and retries."""
    state = _attempt_context.get()
    identifier = 'attempt_' + uuid.uuid4().hex
    active = False
    started = time.monotonic()
    if state and db.settings.db_path.is_file():
        from .model_registry import get_model_profile
        try:
            provider = get_model_profile(state['selected']).provider_id
        except ValueError:
            provider = ''
        with repository.unit_of_work() as conn:
            route_description = ('responses' if str(url).endswith('/responses') else 'chat_completions')
            if route is not None:
                route_description = str(route.label) + ':' + str(route.api_mode) + ':' + hashlib.sha256((str(route.base_url) + '|' + str(route.proxy_url)).encode()).hexdigest()[:12]
            active = prompt_metrics_repository.start_attempt(conn.connection, state['call_id'], identifier,
                model_id=str(payload.get('model') or state['selected']), provider_id=provider,
                protocol_mode='responses' if str(url).endswith('/responses') else 'chat_completions',
                route=route_description, now=db.now_iso())
    received = {}
    try:
        yield received
    except BaseException as exc:
        if active:
            with repository.unit_of_work() as conn:
                prompt_metrics_repository.finish_attempt(conn.connection, identifier, now=db.now_iso(),
                    status='cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed',
                    http_status=received.get('status', 0), duration_ms=(time.monotonic()-started)*1000,
                    error_code=type(exc).__name__)
        raise
    else:
        if active:
            status = int(received.get('status', 0))
            with repository.unit_of_work() as conn:
                prompt_metrics_repository.finish_attempt(conn.connection, identifier, now=db.now_iso(),
                    status='received' if 200 <= status < 300 else 'failed', http_status=status,
                    duration_ms=(time.monotonic()-started)*1000)
            state['attempts'].append(identifier)


def _call_record(call_id: str, context: OperationContext, values: dict, *, result=None, error=None, duration_ms: float | None = None) -> None:
    if result is None and error is None:
        entry = _active.get(context.id)
        if entry is not None and not entry.get("persisted"):
            _record(context, "running")
            entry["persisted"] = True
    if not db.settings.db_path.is_file():
        return
    with repository.unit_of_work() as conn:
        if not conn.calls_available():
            return
        if result is None and error is None:
            conn.insert_model_call((call_id, context.id, values.get("request_id") or context.request_id, context.task_id,
                          context.conversation_id, context.mode, values.get("model_id") or "auto",
                          values.get("reasoning_level") or "auto", "running", db.now_iso()))
            prompt_metrics_repository.record_estimate(conn.connection, call_id,
                category=prompt_metrics.request_category(context.mode, context.purpose,
                    str(values.get("request_id") or context.request_id), context.automatic),
                streaming=bool(values.get("_metrics_streaming")),
                metrics=prompt_metrics.estimate_request(values.get("messages") or [], values.get("tools")))
        elif error is not None:
            conn.fail_model_call(("cancelled" if isinstance(error, asyncio.CancelledError) else "failed", db.now_iso(), type(error).__name__, int(getattr(error, "http_status", 0) or 0), duration_ms, call_id))
        else:
            conn.complete_model_call((db.now_iso(), result.profile_id or values.get("model_id") or result.model, result.provider_id,
                          result.prompt_tokens, result.completion_tokens, result.cost_yuan, result.cost_source, result.http_status, duration_ms, call_id))
            prompt_metrics_repository.record_usage(conn.connection, call_id, result)


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
            from .context_input_policy import summary_model_locked
            locked = summary_model_locked()
            if locked and selected != locked:
                raise OperationBlocked('同前缀摘要的模型策略与原路线不同，未跨模型发送原前缀。请使用独立摘要。')
            bound.arguments.update(model_id=selected, reasoning_level=reasoning)
            if "request_id" in signature.parameters and not bound.arguments.get("request_id"):
                bound.arguments["request_id"] = context.request_id
            call_id = "call_" + uuid.uuid4().hex
            token = _model_depth.set(1)
            attempt_state = {'call_id': call_id, 'selected': selected, 'attempts': [],
                'request_id': bound.arguments.get('request_id', ''),
                'streaming': callback.__name__ == 'call_chat_completion_stream_result'}
            attempt_token = _attempt_context.set(attempt_state)
            started = time.monotonic()
            try:
                _call_record(call_id, context, {**bound.arguments,
                    "_metrics_streaming": callback.__name__ == "call_chat_completion_stream_result"})
                result = await callback(*bound.args, **bound.kwargs)
                _call_record(call_id, context, bound.arguments, result=result, duration_ms=(time.monotonic()-started)*1000)
                if attempt_state['attempts']:
                    with repository.unit_of_work() as conn:
                        prompt_metrics_repository.attempt_usage(conn.connection, attempt_state['attempts'][-1], result)
                        prompt_metrics_repository.finalize_attempts(conn.connection, call_id)
                return result
            except BaseException as error:
                _call_record(call_id, context, bound.arguments, error=error, duration_ms=(time.monotonic()-started)*1000)
                if db.settings.db_path.is_file():
                    with repository.unit_of_work() as conn:
                        prompt_metrics_repository.finalize_attempts(conn.connection, call_id, type(error).__name__)
                raise
            finally:
                _model_depth.reset(token)
                _attempt_context.reset(attempt_token)
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
    with repository.unit_of_work() as conn:
        if conn.operations_available():
            conn.bind_task((task_id, context.id))


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
    with repository.unit_of_work() as conn:
        if conn.operation_records_available() is None:
            return
        if status == "running":
            conn.insert_operation((context.id, context.parent_id, context.request_id, context.conversation_id,
                          context.task_id, context.mode, context.purpose, context.automatic, status, db.now_iso()))
        else:
            conn.finish_operation((status, db.now_iso(), error_code, context.id))


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
                with repository.unit_of_work(write=False) as conn:
                    if conn.tasks_available():
                        task = conn.task_conversation((task_id,))
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
