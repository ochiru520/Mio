"""Observed QQ login/account state for UI and runtime diagnostics."""
from __future__ import annotations
from .config import settings
from . import proactive_service
from .napcat_service import get_napcat_login_status
from .qq_group_service import public_group_status
from .onebot_transport_service import active_websocket_count, connected_self_ids


async def status() -> dict[str, object]:
    count = active_websocket_count()
    websocket_connected = count > 0
    login_status = await get_napcat_login_status(websocket_connected=websocket_connected)
    configured_account = str(settings.napcat_account or "").strip()
    connected_accounts = connected_self_ids()
    connected_account = connected_accounts[0] if len(connected_accounts) == 1 else ""
    if not connected_account:
        connected_account = str(login_status.get("webui_account") or "").strip()
    logged_in = bool(login_status.get("logged_in") or connected_accounts)
    account_matches = bool(
        configured_account
        and connected_account
        and configured_account == connected_account
    )
    account_ready = logged_in and (not configured_account or account_matches)
    diagnostic_code = str(login_status.get("diagnostic_code") or "")
    diagnostic_message = str(login_status.get("diagnostic_message") or "")
    if configured_account and connected_account and not account_matches:
        diagnostic_code = "account_mismatch"
        diagnostic_message = (
            f"当前实际登录的是 QQ {connected_account}，但 Mio 配置的是 {configured_account}。"
            "测试发送已被阻止；请彻底退出旧机器人 QQ 后重新启动。"
        )
    elif account_ready and connected_account:
        diagnostic_code = "ready"
        diagnostic_message = f"机器人 QQ {connected_account} 已登录并连接 OneBot。"
    connected = websocket_connected and account_ready
    return {
        "enabled": settings.qq_bot_enabled,
        "connected": connected,
        "websocket_connected": websocket_connected,
        "websocket_connections": count,
        "configured_account": configured_account,
        "connected_account": connected_account,
        "connected_accounts": connected_accounts,
        "account_matches": account_matches,
        "account_ready": account_ready,
        "logged_in": logged_in,
        "login_source": "onebot" if connected_accounts else ("webui" if login_status.get("login_checked") else ""),
        "diagnostic_code": diagnostic_code,
        "diagnostic_message": diagnostic_message,
        "group_chat": public_group_status(),
        "proactive_runtime": proactive_service.get_proactive_status(),
        **{key: value for key, value in login_status.items() if key not in {"logged_in", "diagnostic_code", "diagnostic_message"}},
    }
