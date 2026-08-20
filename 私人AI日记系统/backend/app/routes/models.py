from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import companion_service, onboarding_service
from ..config import settings
from ..model_registry import (
    delete_provider,
    delete_custom_profile,
    get_model_profile,
    list_model_profiles,
    model_registry_snapshot,
    model_catalog_entry,
    normalize_provider_api_mode,
    provider_model_api_mode,
    public_model_profile,
    restore_model_registry_snapshot,
    restore_default_provider,
    save_custom_provider,
    save_custom_profile,
    suggest_model_metadata,
)
from ..provider_compat import (
    api_base_from_endpoint,
    auth_headers,
    auth_scheme_candidates,
    completion_endpoint_candidates,
    models_endpoint_candidates,
    normalize_api_base_url,
    response_is_json,
)
from ..provider_presets import provider_preset, public_provider_presets


router = APIRouter()


def _provider_client_routes() -> list[tuple[str, str]]:
    mode = str(settings.openai_proxy_mode or "auto").strip().lower()
    proxy_url = str(settings.openai_proxy_url or "").strip()
    routes: list[tuple[str, str]] = []
    if mode != "proxy":
        routes.append(("直连", ""))
    if proxy_url and mode != "direct":
        routes.append(("应用代理", proxy_url))
    return routes or [("直连", "")]


def _provider_client_kwargs(proxy_url: str) -> dict[str, object]:
    kwargs: dict[str, object] = {"timeout": 20, "trust_env": False}
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return kwargs


class ModelProfileRequest(BaseModel):
    provider_name: str
    display_name: str = ""
    family_name: str = ""
    variant_name: str = ""
    model: str
    base_url: str
    api_key: str
    supports_vision: bool = False
    supports_tool_calls: bool | None = None
    supports_structured_output: bool = True
    context_window_tokens: int = Field(default=32768, ge=4096, le=10_000_000)
    privacy_location: str = Field(default="external_provider", pattern="^(external_provider|local_device)$")
    cached_input_price_cny_per_million: float = 0.0
    input_price_cny_per_million: float = 0.0
    output_price_cny_per_million: float = 0.0
    pricing_source: str = ""
    provider_kind: str = "relay"
    provider_protocol: str = "openai"
    auth_scheme: str = "bearer"
    preset_id: str = ""
    default_api_mode: str = "auto"


class ProviderModelRequest(BaseModel):
    model: str
    display_name: str = ""
    family_name: str = ""
    variant_name: str = ""
    supports_vision: bool = False
    supports_tool_calls: bool | None = None
    supports_structured_output: bool = True
    context_window_tokens: int = Field(default=32768, ge=4096, le=10_000_000)
    privacy_location: str = Field(default="external_provider", pattern="^(external_provider|local_device)$")
    cached_input_price_cny_per_million: float = 0.0
    input_price_cny_per_million: float = 0.0
    output_price_cny_per_million: float = 0.0
    pricing_source: str = ""
    api_mode: str = ""


class ProviderCreateRequest(BaseModel):
    provider_name: str
    base_url: str
    api_key: str
    provider_kind: str = "relay"
    provider_protocol: str = "openai"
    auth_scheme: str = "auto"
    preset_id: str = ""
    default_api_mode: str = "auto"
    models: list[ProviderModelRequest] = Field(min_length=1, max_length=200)


class ModelDiscoveryRequest(BaseModel):
    provider_kind: str = "relay"
    provider_protocol: str = "openai"
    base_url: str = ""
    api_key: str
    preset_id: str = ""
    default_api_mode: str = "auto"


OFFICIAL_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "provider_name": "OpenAI 官方",
        "base_url": "https://api.openai.com/v1",
    },
    "deepseek": {
        "provider_name": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1",
    },
}


def _normalize_provider_request(
    provider_kind: str,
    provider_protocol: str,
    base_url: str,
    preset_id: str = "",
) -> tuple[str, str, str]:
    kind = provider_kind.strip().lower() or "relay"
    protocol = provider_protocol.strip().lower() or "openai"
    if kind not in {"official", "relay"}:
        raise HTTPException(status_code=400, detail="请选择官方 API 或中转站。")
    if protocol not in {"openai", "deepseek", "opencode_go"}:
        raise HTTPException(status_code=400, detail="当前只支持 OpenAI 兼容、DeepSeek 官方和 OpenCode Go 协议。")
    selected_preset = provider_preset(preset_id)
    if selected_preset is not None:
        return (
            str(selected_preset["kind"]),
            str(selected_preset["protocol"]),
            str(selected_preset["base_url"]),
        )
    if kind == "official":
        preset = OFFICIAL_PROVIDER_PRESETS.get(protocol)
        if preset is None:
            raise HTTPException(status_code=400, detail="这个官方供应商暂未接入。")
        return kind, protocol, preset["base_url"]
    try:
        normalized_url = normalize_api_base_url(base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return kind, protocol, normalized_url


@router.get("/models/provider-presets")
async def provider_presets():
    return {"presets": public_provider_presets()}


def _models_endpoint_candidates(base_url: str) -> list[str]:
    return models_endpoint_candidates(base_url)


async def _request_models_endpoint(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
) -> tuple[httpx.Response, str, list[str], str]:
    attempts: list[str] = []
    last_response: httpx.Response | None = None
    preferred_error: httpx.Response | None = None
    for endpoint in _models_endpoint_candidates(base_url):
        for auth_scheme in auth_scheme_candidates():
            response = await client.get(endpoint, headers=auth_headers(api_key, auth_scheme))
            json_label = "JSON" if response_is_json(response) else "非JSON"
            attempts.append(f"{endpoint} [{auth_scheme}] -> HTTP {response.status_code} {json_label}")
            last_response = response
            if response.is_success and response_is_json(response):
                try:
                    records = _model_records_from_payload(response.json())
                except ValueError:
                    records = None
                if isinstance(records, list):
                    return response, endpoint, attempts, auth_scheme
                attempts[-1] += "，未识别到模型列表"
                preferred_error = response
                break
            if response.is_success:
                preferred_error = response
                break
            if response.status_code not in {401, 403}:
                if response.status_code not in {404, 405}:
                    preferred_error = response
                break
            preferred_error = response
    if last_response is None:
        raise HTTPException(status_code=502, detail="没有可用的模型列表地址。")
    selected = preferred_error or last_response
    return selected, str(selected.request.url), attempts, ""


def _provider_http_error_detail(response: httpx.Response, endpoint: str, attempts: list[str] | None = None) -> str:
    attempted = f" 已尝试：{'；'.join(attempts)}。" if attempts else ""
    if response.status_code in {401, 403}:
        return (
            f"供应商地址可以访问，但 API Key 被拒绝（HTTP {response.status_code}，接口 {endpoint}）。"
            "已尝试 Bearer、x-api-key 和 api-key；请确认 Key 来自这个站点且仍然有效。"
            f"{attempted}部分中转站不开放模型列表，可手动填写模型 ID 后保存并测试聊天。"
        )
    if response.is_success and not response_is_json(response):
        return f"供应商返回了网页而不是 JSON 模型目录（{endpoint}）。{attempted}"
    return f"供应商模型列表接口返回 HTTP {response.status_code}（{endpoint}）。{attempted}"


async def _request_models_via_routes(
    base_url: str,
    api_key: str,
) -> tuple[httpx.Response, str, list[str], str, str]:
    all_attempts: list[str] = []
    last_result: tuple[httpx.Response, str, str, str] | None = None
    route_errors: list[str] = []
    for route_label, proxy_url in _provider_client_routes():
        try:
            async with httpx.AsyncClient(**_provider_client_kwargs(proxy_url)) as client:
                response, endpoint, attempts, auth_scheme = await _request_models_endpoint(
                    client,
                    base_url,
                    api_key,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            route_errors.append(f"{route_label} -> {exc}")
            continue
        all_attempts.extend(f"{route_label}：{attempt}" for attempt in attempts)
        last_result = (response, endpoint, auth_scheme, route_label)
        if response.is_success and response_is_json(response):
            try:
                records = _model_records_from_payload(response.json())
            except ValueError:
                records = None
            if isinstance(records, list):
                return response, endpoint, all_attempts, auth_scheme, route_label
    if last_result is not None:
        response, endpoint, auth_scheme, route_label = last_result
        return response, endpoint, all_attempts, auth_scheme, route_label
    detail = "；".join(route_errors) or "没有可用的网络路线"
    raise HTTPException(status_code=502, detail=f"获取模型列表失败：{detail}")


def _new_api_pricing_map(pricing_data: object, status_data: object) -> dict[str, dict[str, object]]:
    if not isinstance(pricing_data, dict) or not isinstance(status_data, dict):
        return {}
    status = status_data.get("data") if isinstance(status_data.get("data"), dict) else status_data
    if str(status.get("quota_display_type") or "").upper() != "CNY":
        return {}
    try:
        quota_per_unit = float(status.get("quota_per_unit") or 0)
    except (TypeError, ValueError):
        return {}
    records = pricing_data.get("data")
    if quota_per_unit <= 0 or not isinstance(records, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        model = str(record.get("model_name") or "").strip()
        if not model:
            continue
        try:
            if int(record.get("quota_type") or 0) != 0:
                continue
            model_ratio = max(0.0, float(record.get("model_ratio") or 0))
            completion_ratio = max(0.0, float(record.get("completion_ratio") or 1))
            cache_ratio = max(0.0, float(record.get("cache_ratio") or 1))
        except (TypeError, ValueError):
            continue
        input_price = model_ratio * 1_000_000 / quota_per_unit
        result[model] = {
            "cached_input_price_cny_per_million": input_price * cache_ratio,
            "input_price_cny_per_million": input_price,
            "output_price_cny_per_million": input_price * completion_ratio,
            "pricing_source": "provider_catalog",
        }
    return result


def _model_records_from_payload(data: object) -> list[dict[str, object]] | None:
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"id": str(item)} for item in data]
    if not isinstance(data, dict):
        return None
    for key in ("data", "models", "result", "items", "model_list"):
        records = data.get(key)
        if isinstance(records, dict):
            nested = records.get("data") or records.get("models") or records.get("items") or records.get("model_list")
            if isinstance(nested, list):
                records = nested
            elif records and all(isinstance(name, str) for name in records):
                records = [
                    ({"id": name, **value} if isinstance(value, dict) else {"id": name})
                    for name, value in records.items()
                ]
        if isinstance(records, list):
            return [item if isinstance(item, dict) else {"id": str(item)} for item in records]
    if data and all(isinstance(name, str) for name in data):
        values = list(data.values())
        if all(isinstance(value, dict) for value in values):
            return [
                {"id": name, **value} if "id" not in value else value
                for name, value in data.items()
            ]
    return None


def _discovery_records(
    response: httpx.Response,
    provider_prices: dict[str, dict[str, object]],
    attempts: list[str] | None = None,
) -> tuple[list[dict[str, object]], str]:
    warning = ""
    if response.status_code >= 400:
        if response.status_code in {401, 403}:
            warning = "供应商拒绝了当前 API Key。已改用站点公开模型目录；可以先保存模型，但实际聊天前必须填写有效 Key。"
        else:
            warning = f"模型列表接口返回 HTTP {response.status_code}，已改用站点公开模型目录。"
        if provider_prices:
            return [{"id": model} for model in provider_prices], warning
        detail = _provider_http_error_detail(response, str(response.request.url), attempts)
        raise HTTPException(status_code=502, detail=detail)
    if not response_is_json(response):
        raise HTTPException(
            status_code=502,
            detail=_provider_http_error_detail(response, str(response.request.url), attempts),
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="供应商返回的模型列表不是有效JSON。") from exc
    records = _model_records_from_payload(data)
    if not isinstance(records, list):
        raise HTTPException(status_code=502, detail="供应商返回了成功响应，但其中没有可识别的模型列表。可以在下方手动填写模型 ID 后保存。")
    return records, warning


async def _request_chat_endpoint(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    preferred_auth_scheme: str = "",
    api_mode: str = "chat_completions",
) -> tuple[httpx.Response, str, list[str], str]:
    attempts: list[str] = []
    last_response: httpx.Response | None = None
    chat_endpoints = completion_endpoint_candidates(base_url)
    endpoints = chat_endpoints
    if api_mode == "responses":
        response_endpoints = [endpoint[: -len("/chat/completions")] + "/responses" for endpoint in chat_endpoints]
        parsed = urlsplit(base_url.rstrip("/"))
        if parsed.path.rstrip("/").lower().endswith("/v1"):
            origin = f"{parsed.scheme}://{parsed.netloc}"
            response_endpoints.extend((f"{origin}/responses", f"{origin}/chat/completions"))
        # Keep the documented Responses route first, then tolerate gateways
        # that moved back to their legacy chat route after returning 404.
        endpoints = list(dict.fromkeys(response_endpoints + chat_endpoints))
    for endpoint in endpoints:
        request_api_mode = "responses" if endpoint.rstrip("/").lower().endswith("/responses") else "chat_completions"
        for auth_scheme in auth_scheme_candidates(preferred_auth_scheme):
            payload = (
                {"model": model, "input": "只回复OK", "max_output_tokens": 4}
                if request_api_mode == "responses"
                else {
                    "model": model,
                    "messages": [{"role": "user", "content": "只回复OK"}],
                    "temperature": 0,
                    "max_tokens": 4,
                }
            )
            response = await client.post(
                endpoint,
                headers={**auth_headers(api_key, auth_scheme), "Content-Type": "application/json"},
                json=payload,
            )
            response_label = "JSON" if response_is_json(response) else "非JSON"
            attempts.append(f"{endpoint} [{auth_scheme}] -> HTTP {response.status_code} {response_label}")
            last_response = response
            if _chat_response_is_valid(response, request_api_mode):
                return response, endpoint, attempts, auth_scheme
            if response.is_success:
                attempts[-1] += "，未识别到聊天结果"
                break
            if response.status_code == 400:
                payload.pop("max_tokens", None)
                payload.pop("max_output_tokens", None)
                response = await client.post(
                    endpoint,
                    headers={**auth_headers(api_key, auth_scheme), "Content-Type": "application/json"},
                    json=payload,
                )
                response_label = "JSON" if response_is_json(response) else "非JSON"
                attempts.append(
                    f"{endpoint} [{auth_scheme}, 简化参数] -> HTTP {response.status_code} {response_label}"
                )
                last_response = response
                if _chat_response_is_valid(response, request_api_mode):
                    return response, endpoint, attempts, auth_scheme
                if response.is_success:
                    attempts[-1] += "，未识别到聊天结果"
                    break
            if response.status_code not in {401, 403}:
                break
    if last_response is None:
        raise HTTPException(status_code=502, detail="没有可用的聊天接口地址。")
    return last_response, str(last_response.request.url), attempts, ""


def _chat_response_is_valid(response: httpx.Response, api_mode: str = "chat_completions") -> bool:
    if not response.is_success or not response_is_json(response):
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    if api_mode == "responses":
        return bool(data.get("output_text")) or (isinstance(data.get("output"), list) and bool(data["output"]))
    return isinstance(data.get("choices"), list) and bool(data["choices"])


def _provider_chat_error_detail(
    response: httpx.Response,
    *,
    profile: object,
    endpoint: str,
    attempts: list[str],
    connection_route: str,
) -> dict[str, object]:
    upstream_code = ""
    upstream_message = ""
    upstream_request_id = ""
    try:
        document = response.json()
    except ValueError:
        document = None
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(error, dict):
            upstream_code = str(error.get("code") or "").strip()[:120]
            upstream_message = str(error.get("message") or "").strip()[:500]
            upstream_request_id = str(
                error.get("request_id") or error.get("requestId") or ""
            ).strip()[:160]
        elif error:
            upstream_message = str(error).strip()[:500]
        if not upstream_request_id:
            upstream_request_id = str(
                document.get("request_id") or document.get("requestId") or ""
            ).strip()[:160]
    if not upstream_message:
        upstream_message = response.text.strip()[:500]
    if not upstream_request_id:
        upstream_request_id = str(
            response.headers.get("x-request-id")
            or response.headers.get("x-oneapi-request-id")
            or response.headers.get("request-id")
            or ""
        ).strip()[:160]
    if not upstream_request_id and upstream_message:
        match = re.search(r"request\s*id\s*[:=]\s*([A-Za-z0-9_-]{8,160})", upstream_message, re.I)
        if match:
            upstream_request_id = match.group(1)

    provider_name = str(getattr(profile, "provider_name", "") or "当前供应商")
    provider_model = str(getattr(profile, "model", "") or "当前模型")
    status_code = int(response.status_code)
    if status_code in {401, 403} or upstream_code.lower() in {
        "invalid_token",
        "invalid token",
        "unauthorized",
    } or "invalid token" in upstream_message.lower():
        message = (
            f"供应商“{provider_name}”拒绝了模型“{provider_model}”使用的 API Key"
            f"（HTTP {status_code}）。请在这台电脑重新输入属于该站点的有效 Key。"
        )
        code = "provider_api_key_rejected"
    elif status_code == 404:
        message = (
            f"供应商“{provider_name}”返回 HTTP 404：当前模型或接口地址不存在（{endpoint}）。"
            "请核对该站点支持的模型 ID 和 API 模式；这不是本地网络连接成功的证明。"
        )
        code = "provider_endpoint_or_model_not_found"
    else:
        message = f"供应商“{provider_name}”的聊天接口未通过（HTTP {status_code}）。"
        code = "provider_model_test_failed"
    if upstream_request_id:
        message += f" 请求 ID：{upstream_request_id}。"
    return {
        "code": code,
        "message": message,
        "model_id": str(getattr(profile, "id", "") or ""),
        "provider_id": str(getattr(profile, "provider_id", "") or ""),
        "provider_name": provider_name,
        "provider_model": provider_model,
        "http_status": status_code,
        "endpoint": endpoint,
        "route": connection_route,
        "request_id": upstream_request_id,
        "upstream_code": upstream_code,
        "upstream_message": upstream_message,
        "attempts": attempts,
        "requires_local_key_reentry": code == "provider_api_key_rejected",
    }


async def _request_chat_via_routes(
    base_url: str,
    api_key: str,
    model: str,
    preferred_auth_scheme: str = "",
    api_mode: str = "chat_completions",
) -> tuple[httpx.Response, str, list[str], str, str]:
    all_attempts: list[str] = []
    last_result: tuple[httpx.Response, str, str, str] | None = None
    route_errors: list[str] = []
    for route_label, proxy_url in _provider_client_routes():
        try:
            async with httpx.AsyncClient(**_provider_client_kwargs(proxy_url)) as client:
                response, endpoint, attempts, auth_scheme = await _request_chat_endpoint(
                    client,
                    base_url,
                    api_key,
                    model,
                    preferred_auth_scheme,
                    api_mode,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            route_errors.append(f"{route_label} -> {exc}")
            continue
        all_attempts.extend(f"{route_label}：{attempt}" for attempt in attempts)
        last_result = (response, endpoint, auth_scheme, route_label)
        if _chat_response_is_valid(response, api_mode):
            return response, endpoint, all_attempts, auth_scheme, route_label
    if last_result is not None:
        response, endpoint, auth_scheme, route_label = last_result
        return response, endpoint, all_attempts, auth_scheme, route_label
    detail = "；".join(route_errors) or "没有可用的网络路线"
    raise HTTPException(status_code=502, detail=f"实际聊天连接失败：{detail}")


@router.post("/models")
async def create_model_profile(payload: ModelProfileRequest):
    kind, protocol, base_url = _normalize_provider_request(
        payload.provider_kind,
        payload.provider_protocol,
        payload.base_url,
        payload.preset_id,
    )
    try:
        preset = provider_preset(payload.preset_id)
        default_api_mode = normalize_provider_api_mode(
            (preset or {}).get("default_api_mode") or payload.default_api_mode,
            base_url=base_url,
        )
        values = payload.model_dump()
        values.update({
            "provider_kind": kind,
            "provider_protocol": protocol,
            "base_url": base_url,
            "default_api_mode": default_api_mode,
        })
        if values.get("supports_tool_calls") is None:
            values["supports_tool_calls"] = kind == "official"
        if kind == "official" and not str(values.get("provider_name") or "").strip():
            values["provider_name"] = OFFICIAL_PROVIDER_PRESETS.get(protocol, {}).get(
                "provider_name", "官方供应商"
            )
        profile = save_custom_profile(values)
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_model_profile(profile)


@router.post("/providers")
async def create_provider(payload: ProviderCreateRequest):
    kind, protocol, base_url = _normalize_provider_request(
        payload.provider_kind,
        payload.provider_protocol,
        payload.base_url,
        payload.preset_id,
    )
    values = payload.model_dump(exclude={"models"})
    preset = provider_preset(payload.preset_id)
    try:
        default_api_mode = normalize_provider_api_mode(
            (preset or {}).get("default_api_mode") or payload.default_api_mode,
            base_url=base_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    values.update({
        "provider_kind": kind,
        "provider_protocol": protocol,
        "base_url": base_url,
        "default_api_mode": default_api_mode,
    })
    if kind == "official" and not str(values.get("provider_name") or "").strip():
        preset = provider_preset(payload.preset_id)
        values["provider_name"] = str(
            (preset or {}).get("name")
            or OFFICIAL_PROVIDER_PRESETS.get(protocol, {}).get("provider_name")
            or "官方供应商"
        )
    try:
        model_records = []
        for item in payload.models:
            record = item.model_dump()
            if record.get("supports_tool_calls") is None:
                record["supports_tool_calls"] = kind == "official"
            model_records.append(record)
        provider_id, profiles = save_custom_provider(
            values,
            model_records,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "provider": {
            "provider_id": provider_id,
            "display_name": str(values.get("provider_name") or "").strip(),
            "provider_kind": kind,
            "provider_protocol": protocol,
        },
        "models": [public_model_profile(profile) for profile in profiles],
    }


@router.post("/models/discover")
async def discover_models(payload: ModelDiscoveryRequest):
    kind, protocol, base_url = _normalize_provider_request(
        payload.provider_kind,
        payload.provider_protocol,
        payload.base_url,
        payload.preset_id,
    )
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写API Key。")
    preset = provider_preset(payload.preset_id)
    try:
        default_api_mode = normalize_provider_api_mode(
            (preset or {}).get("default_api_mode") or payload.default_api_mode,
            base_url=base_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider_prices: dict[str, dict[str, object]] = {}
    attempts: list[str] = []
    endpoint = ""
    response, endpoint, attempts, auth_scheme, connection_route = await _request_models_via_routes(
        base_url,
        api_key,
    )
    if kind == "relay":
        parsed_url = urlsplit(base_url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        selected_proxy = next(
            (proxy for label, proxy in _provider_client_routes() if label == connection_route),
            "",
        )
        try:
            async with httpx.AsyncClient(**_provider_client_kwargs(selected_proxy)) as client:
                pricing_response, status_response = await asyncio.gather(
                    client.get(f"{origin}/api/pricing"), client.get(f"{origin}/api/status")
                )
                if pricing_response.is_success and status_response.is_success:
                    provider_prices = _new_api_pricing_map(pricing_response.json(), status_response.json())
        except (httpx.HTTPError, ValueError):
            provider_prices = {}
    records, warning = _discovery_records(response, provider_prices, attempts)
    models: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        model = str(record.get("id") or "").strip()
        if not model:
            continue
        metadata = suggest_model_metadata(model)
        pricing = model_catalog_entry(model) or provider_prices.get(model, {})
        api_mode = provider_model_api_mode(
            protocol,
            model,
            base_url=base_url,
            default_api_mode=default_api_mode,
        )
        models.append({
            "model": model,
            **metadata,
            "cached_input_price_cny_per_million": float(pricing.get("cached_input_price_cny_per_million") or 0),
            "input_price_cny_per_million": float(pricing.get("input_price_cny_per_million") or 0),
            "output_price_cny_per_million": float(pricing.get("output_price_cny_per_million") or 0),
            "pricing_source": str(pricing.get("pricing_source") or "unconfigured"),
            "api_mode": api_mode,
            "api_supported": api_mode != "messages",
        })
    models.sort(key=lambda item: str(item["model"]).lower())
    return {
        "models": models,
        "warning": warning,
        "authorization_valid": response.status_code not in {401, 403},
        "provider_kind": kind,
        "provider_protocol": protocol,
        "default_api_mode": default_api_mode,
        "resolved_base_url": base_url,
        "resolved_api_base_url": api_base_from_endpoint(endpoint),
        "auth_scheme": auth_scheme or "bearer",
        "models_endpoint": endpoint,
        "attempts": attempts,
        "connection_route": connection_route,
    }


@router.post("/models/{profile_id}/test")
async def test_model_profile(profile_id: str):
    try:
        profile = get_model_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    api_key_error = str(getattr(profile, "api_key_error", "") or "")
    if api_key_error or not profile.api_key:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "provider_key_requires_reentry" if api_key_error else "provider_key_missing",
                "message": api_key_error or "这个供应商还没有可用的 API Key，请先填写并保存。",
                "model_id": profile.id,
                "provider_id": profile.provider_id,
                "provider_name": profile.provider_name,
                "provider_model": profile.model,
                "requires_local_key_reentry": bool(api_key_error),
            },
        )
    response, endpoint, attempts, auth_scheme, catalog_route = await _request_models_via_routes(
        profile.base_urls[0],
        profile.api_key,
    )
    completion_response, completion_url, chat_attempts, chat_auth_scheme, connection_route = (
        await _request_chat_via_routes(
            profile.base_urls[0],
            profile.api_key,
            profile.model,
            auth_scheme or profile.auth_scheme,
            str(getattr(profile, "api_mode", "chat_completions") or "chat_completions"),
        )
    )
    if not _chat_response_is_valid(
        completion_response,
        str(getattr(profile, "api_mode", "chat_completions") or "chat_completions"),
    ):
        status_code = completion_response.status_code
        raise HTTPException(
            status_code=400 if status_code in {400, 401, 403} else 502,
            detail=_provider_chat_error_detail(
                completion_response,
                profile=profile,
                endpoint=completion_url,
                attempts=chat_attempts,
                connection_route=connection_route,
            ),
        )
    onboarding_service.record_model_verification(profile.id)
    catalog_available = response.is_success and response_is_json(response)
    return {
        "ok": True,
        "model_id": profile.id,
        "provider_id": profile.provider_id,
        "provider_name": profile.provider_name,
        "provider_model": profile.model,
        "http_status": completion_response.status_code,
        "message": (
            f"模型和聊天均可用（{connection_route}）"
            if catalog_available
            else f"聊天可用（{connection_route}）；供应商未开放模型列表"
        ),
        "models_endpoint": endpoint,
        "attempts": attempts + chat_attempts,
        "resolved_api_base_url": api_base_from_endpoint(completion_url),
        "auth_scheme": chat_auth_scheme,
        "chat_verified": True,
        "catalog_available": catalog_available,
        "connection_route": connection_route,
        "catalog_connection_route": catalog_route,
    }


@router.delete("/models/{profile_id}")
async def remove_model_profile(profile_id: str):
    snapshot = model_registry_snapshot()
    config_snapshot = _file_snapshot(settings.companion_config_path)
    try:
        delete_custom_profile(profile_id)
        reference_updates = _cleanup_model_references({profile_id})
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        restore_model_registry_snapshot(snapshot)
        _restore_file_snapshot(settings.companion_config_path, config_snapshot)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "deleted_model_ids": [profile_id], "reference_updates": reference_updates}


def _cleanup_model_references(deleted_model_ids: set[str]) -> dict[str, object]:
    config = companion_service.load_config()
    changes: dict[str, object] = {}
    if str(config.get("chat_model_id") or "") in deleted_model_ids:
        changes["chat_model_id"] = "auto"
    if str(config.get("pet_chat_model_id") or "") in deleted_model_ids:
        changes["pet_chat_model_id"] = "auto"
    if str(config.get("screen_vision_model_id") or "") in deleted_model_ids:
        changes["screen_vision_model_id"] = "auto-fast"
    if changes:
        companion_service.save_config(changes)
    return changes


def _file_snapshot(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore_file_snapshot(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".rollback")
    temporary.write_bytes(snapshot)
    temporary.replace(path)


@router.delete("/providers/{provider_id}")
async def remove_provider(provider_id: str):
    snapshot = model_registry_snapshot()
    config_snapshot = _file_snapshot(settings.companion_config_path)
    try:
        matching = [profile for profile in list_model_profiles() if profile.provider_id == provider_id]
        provider_name = matching[0].provider_name if matching else ""
        deleted_model_ids = delete_provider(provider_id)
        reference_updates = _cleanup_model_references(set(deleted_model_ids))
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        restore_model_registry_snapshot(snapshot)
        _restore_file_snapshot(settings.companion_config_path, config_snapshot)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "provider_id": provider_id,
        "provider_name": provider_name,
        "deleted_model_ids": deleted_model_ids,
        "reference_updates": reference_updates,
    }


@router.post("/providers/{provider_id}/restore")
async def restore_provider(provider_id: str):
    try:
        restored_model_ids = restore_default_provider(provider_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "provider_id": provider_id, "restored_model_ids": restored_model_ids}
