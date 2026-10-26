"""OneBot connection ownership, acknowledged delivery and disconnect cleanup.

Inbound HTTP/WebSocket authentication and event processing remain in the route.
"""
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo
from fastapi import WebSocket, status
from .config import settings

class ConnectionDebouncer(Protocol):
    async def close(self) -> None:
        """Stop pending inbound event batches on disconnect."""
        ...


@dataclass
class OneBotConnection:
    websocket: WebSocket
    lock: asyncio.Lock
    self_id: str = ""
    outbox: asyncio.Queue[tuple[dict[str, Any], asyncio.Future[Any]]] = field(
        default_factory=asyncio.Queue
    )
    pending_acks: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    worker: asyncio.Task[None] | None = None
    debouncer: ConnectionDebouncer | None = None
    processing_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    closing: bool = False

_delivery_debug: dict[str, Any] = {
    "queued_count": 0,
    "sent_count": 0,
    "failed_count": 0,
    "retry_count": 0,
    "pending_count": 0,
    "last_status": "",
    "last_error": "",
    "last_echo": "",
    "last_at": "",
}

_active_connections: dict[int, OneBotConnection] = {}

def active_websocket_count() -> int:
    return len(_active_connections)

def connected_self_ids() -> list[str]:
    return sorted({connection.self_id for connection in _active_connections.values() if connection.self_id})

def runtime_health_status() -> dict[str, Any]:
    connections = list(_active_connections.values())
    return {
        "enabled": settings.qq_bot_enabled,
        "websocket_connections": len(connections),
        "connected_accounts": connected_self_ids(),
        "active_queue_count": sum(connection.outbox.qsize() for connection in connections),
        "active_pending_ack_count": sum(len(connection.pending_acks) for connection in connections),
        "delivery": dict(_delivery_debug),
    }

def _debug_now() -> str:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(tz).isoformat(timespec="seconds")

def _as_id(value: object) -> str:
    return str(value or "").strip()

def _private_user_id_param(user_id: str) -> int | str:
    return int(user_id) if user_id.isdigit() else user_id

def _private_send_action(user_id: str, message: str) -> dict[str, Any]:
    return {
        "action": "send_private_msg",
        "params": {
            "user_id": _private_user_id_param(user_id),
            "message": message,
        },
        "echo": f"qq-{uuid.uuid4().hex}",
    }

class TaskApiRejected(RuntimeError):
    """An explicit negative acknowledgement, distinct from an unknown outcome."""

def has_task_connection(self_id: str) -> bool:
    return any(item.self_id == self_id and not item.closing for item in _active_connections.values())

async def request_task_api(origin: dict, action: str, params: dict) -> dict:
    from . import mobile_task_service as mobile
    if (not settings.qq_bot_enabled or not mobile.valid_origin(origin) or mobile.channel(origin) != 'qq'
            or action not in {'send_private_msg', 'upload_private_file', 'get_private_file_url'}):
        raise TaskApiRejected('QQ 任务通道未授权或未开启。')
    if action != 'get_private_file_url' and str(params.get('user_id')) != origin['user_id']:
        raise TaskApiRejected('QQ 收件人不匹配。')
    connection = next((item for item in _active_connections.values() if item.self_id == origin['self_id'] and not item.closing), None)
    if connection is None:
        raise TaskApiRejected('对应的 QQ 机器人未连接。')
    result = await _enqueue_connection(connection, {'action': action, 'params': params, 'echo': 'qq-task-' + uuid.uuid4().hex})
    if not isinstance(result, dict):
        raise RuntimeError('QQ 回执格式无效。')
    return result

async def _send_with_lock(connection: OneBotConnection, payload: dict[str, Any]) -> dict[str, Any] | None:
    echo = _as_id(payload.get("echo"))
    last_error: Exception | None = None
    attempts = 1 if echo.startswith('qq-task-') else max(0, int(settings.qq_delivery_max_retries)) + 1
    for attempt in range(attempts):
        ack_future: asyncio.Future[dict[str, Any]] | None = None
        try:
            response_payload: dict[str, Any] | None = None
            if echo:
                ack_future = asyncio.get_running_loop().create_future()
                connection.pending_acks[echo] = ack_future
            async with connection.lock:
                await connection.websocket.send_json(payload)
            if ack_future is not None:
                response = await asyncio.wait_for(
                    asyncio.shield(ack_future),
                    timeout=max(1.0, float(settings.qq_delivery_ack_timeout_seconds)),
                )
                if str(response.get("status") or "ok").lower() != "ok" or int(
                    response.get("retcode") or 0
                ) != 0:
                    raise TaskApiRejected('QQ 接口拒绝了请求，请检查 NapCat 状态。')
                response_payload = response
            _delivery_debug.update(
                sent_count=int(_delivery_debug["sent_count"]) + 1,
                pending_count=len(connection.pending_acks),
                last_status="sent",
                last_error="",
                last_echo=echo,
                last_at=_debug_now(),
            )
            return response_payload
        except asyncio.TimeoutError:
            # The request may have reached QQ even if its response was lost.
            # Do not blindly resend and create duplicate messages.
            _delivery_debug.update(
                failed_count=int(_delivery_debug["failed_count"]) + 1,
                pending_count=len(connection.pending_acks),
                last_status="ack_timeout",
                last_error="QQ接口确认超时，未自动重复发送",
                last_echo=echo,
                last_at=_debug_now(),
            )
            raise
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            _delivery_debug["retry_count"] = int(_delivery_debug["retry_count"]) + 1
            await asyncio.sleep(0.25 * (attempt + 1))
        finally:
            if echo and connection.pending_acks.get(echo) is ack_future:
                connection.pending_acks.pop(echo, None)
    _delivery_debug.update(
        failed_count=int(_delivery_debug["failed_count"]) + 1,
        pending_count=len(connection.pending_acks),
        last_status="failed",
        last_error=str(last_error or "QQ发送失败")[:240],
        last_echo=echo,
        last_at=_debug_now(),
    )
    raise last_error or RuntimeError("QQ发送失败")

async def _outbox_worker(connection: OneBotConnection) -> None:
    while True:
        payload, future = await connection.outbox.get()
        try:
            receipt = await _send_with_lock(connection, payload)
            if not future.done():
                future.set_result(receipt)
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
        finally:
            connection.outbox.task_done()

def _fail_connection_pending(connection: OneBotConnection, exc: BaseException) -> None:
    """让断线时所有等待中的发送立即结束，避免调用方永久挂起。"""
    for future in list(connection.pending_acks.values()):
        if not future.done():
            future.set_exception(exc)
    connection.pending_acks.clear()
    while True:
        try:
            _, future = connection.outbox.get_nowait()
        except asyncio.QueueEmpty:
            break
        if not future.done():
            future.set_exception(exc)
        connection.outbox.task_done()
    _delivery_debug.update(
        pending_count=connection.outbox.qsize() + len(connection.pending_acks),
        last_status="disconnected",
        last_error=str(exc)[:240],
        last_at=_debug_now(),
    )

async def _close_connection(
    connection_key: int,
    connection: OneBotConnection,
    reason: str,
    *,
    close_websocket: bool = True,
    close_code: int = status.WS_1001_GOING_AWAY,
) -> bool:
    registered = _active_connections.get(connection_key) is connection
    if connection.closing and not registered:
        return False
    connection.closing = True
    disconnect_error = RuntimeError(reason)
    _fail_connection_pending(connection, disconnect_error)
    if connection.debouncer is not None:
        await connection.debouncer.close()
        connection.debouncer = None
    current_task = asyncio.current_task()
    tasks = [task for task in connection.processing_tasks if task is not current_task]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    connection.processing_tasks.difference_update(tasks)
    if connection.worker is not None and connection.worker is not current_task:
        connection.worker.cancel()
        await asyncio.gather(connection.worker, return_exceptions=True)
        connection.worker = None
    if close_websocket:
        try:
            await connection.websocket.close(code=close_code, reason=reason[:120])
        except (OSError, RuntimeError):
            pass
    if _active_connections.get(connection_key) is connection:
        _active_connections.pop(connection_key, None)
    if not _active_connections:
        from . import proactive_service

        proactive_service.note_qq_connection_state(False)
    return registered

def _resolve_delivery_ack(connection: OneBotConnection, event: dict[str, Any]) -> bool:
    """Resolve a matching OneBot API response without entering message handling."""
    echo = _as_id(event.get("echo"))
    if not echo:
        return False
    ack_future = connection.pending_acks.get(echo)
    if ack_future is None:
        return False
    if not ack_future.done():
        ack_future.set_result(event)
    return True

async def _enqueue_connection(
    connection: OneBotConnection,
    payload: dict[str, Any],
) -> Any:
    if connection.closing:
        raise ConnectionError("OneBot 连接正在关闭")
    future = asyncio.get_running_loop().create_future()
    connection.outbox.put_nowait((payload, future))
    _delivery_debug["queued_count"] = int(_delivery_debug["queued_count"]) + 1
    _delivery_debug["pending_count"] = connection.outbox.qsize() + len(connection.pending_acks)
    return await asyncio.shield(future)

async def send_private_message(user_id: str, message: str) -> bool:
    for key, connection in list(_active_connections.items()):
        try:
            await _enqueue_connection(connection, _private_send_action(user_id, message))
            return True
        except Exception as exc:
            delivery_unknown = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            reason = "QQ接口确认超时，消息可能已送达，连接已关闭且不会自动重发" if delivery_unknown else f"QQ发送失败：{exc}"
            await _close_connection(
                key,
                connection,
                reason,
                close_code=status.WS_1011_INTERNAL_ERROR,
            )
            _delivery_debug.update(
                last_status="delivery_unknown" if delivery_unknown else "failed_closed",
                last_error=reason[:240],
                last_at=_debug_now(),
            )
            return False
    return False

async def send_private_message_receipt(user_id: str, message: str) -> dict[str, Any]:
    """Send a private message and return the OneBot API acknowledgement payload."""
    for key, connection in list(_active_connections.items()):
        try:
            receipt = await _enqueue_connection(connection, _private_send_action(user_id, message))
            return {
                "sent": True,
                "acknowledged": True,
                "message_id": (receipt or {}).get("data", {}).get("message_id") if isinstance(receipt, dict) else None,
                "response": receipt or {},
            }
        except Exception as exc:
            delivery_unknown = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            reason = "QQ接口确认超时，消息可能已送达，连接已关闭且不会自动重发" if delivery_unknown else f"QQ发送失败：{exc}"
            await _close_connection(key, connection, reason, close_code=status.WS_1011_INTERNAL_ERROR)
            return {"sent": False, "acknowledged": False, "error": reason}
    return {"sent": False, "acknowledged": False, "error": "没有已连接的 OneBot 通道。"}

async def disconnect_all_connections(reason: str = "Mio 已暂停 QQ 通道") -> int:
    connections = list(_active_connections.items())
    for connection_key, connection in connections:
        await _close_connection(connection_key, connection, reason)
    return len(connections)
