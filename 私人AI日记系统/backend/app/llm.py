from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from .config import settings
from .model_registry import (
    ModelProfile,
    get_model_profile,
    list_model_profiles,
    model_reasoning_config,
    normalize_model_reasoning,
)
from .provider_compat import (
    api_base_from_endpoint,
    auth_headers,
    auth_scheme_candidates,
    completion_endpoint_candidates,
)


class LLMConfigError(RuntimeError):
    pass


class ModelRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        profile: ModelProfile,
        request_id: str = "",
        http_status: int = 0,
        route: str = "",
        attempts: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.profile_id = profile.id
        self.provider_id = profile.provider_id
        self.provider_name = profile.provider_name
        self.provider_model = profile.model
        self.request_id = request_id
        self.http_status = int(http_status or 0)
        self.route = route
        self.attempts = tuple(attempts or ())

    def public_detail(self) -> dict[str, object]:
        return {
            "code": "model_request_failed",
            "message": str(self),
            "request_id": self.request_id,
            "model_id": self.profile_id,
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "provider_model": self.provider_model,
            "http_status": self.http_status or None,
            "route": self.route,
            "attempts": [dict(item) for item in self.attempts],
        }


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_QUOTA_PER_UNIT_CACHE: dict[str, float] = {}


@dataclass(frozen=True)
class CompletionRoute:
    base_url: str
    proxy_url: str
    label: str
    auth_scheme: str = "bearer"
    api_mode: str = "chat_completions"


@dataclass(frozen=True)
class ProviderCostReference:
    profile_id: str
    provider_request_id: str
    base_url: str
    estimated_cost_yuan: float | None
    estimated_cost_source: str


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class CompletionResult:
    content: str
    model: str
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_yuan: float | None
    cost_source: str
    cost_references: tuple[ProviderCostReference, ...] = ()
    first_token_latency_ms: float | None = None
    total_latency_ms: float | None = None
    profile_id: str = ""
    provider_id: str = ""
    provider_name: str = ""
    provider_model: str = ""
    provider_request_id: str = ""
    route: str = ""
    http_status: int = 0
    tool_calls: tuple[ToolCall, ...] = ()


StreamDeltaCallback = Callable[[str, str], Awaitable[None] | None]


def _chat_message_text(value: object) -> str:
    """Read user-facing text blocks without accepting provider reasoning blocks."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if any(marker in block_type for marker in ("reasoning", "thinking", "analysis")):
            continue
        if block_type and block_type not in {"text", "output_text", "content"}:
            continue
        text = block.get("text")
        if isinstance(text, dict):
            text = text.get("value")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _safe_nonnegative_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _provider_reported_cost_yuan(
    data: dict[str, Any],
    usage: dict[str, Any],
    headers: httpx.Headers,
) -> float | None:
    for container in (usage, data):
        for key in ("cost_yuan", "cost_cny", "total_cost_yuan", "total_cost_cny"):
            amount = _safe_nonnegative_float(container.get(key))
            if amount is not None:
                return amount
        billing = container.get("billing")
        if isinstance(billing, dict) and str(billing.get("currency") or "").upper() in {"CNY", "RMB"}:
            amount = _safe_nonnegative_float(billing.get("amount") or billing.get("cost"))
            if amount is not None:
                return amount
    for key in ("x-request-cost-cny", "x-cost-cny", "x-request-cost-yuan", "x-cost-yuan"):
        amount = _safe_nonnegative_float(headers.get(key))
        if amount is not None:
            return amount
    return None


def _usage_cost_yuan(
    usage: dict[str, Any],
    profile: ModelProfile,
) -> tuple[float | None, int, str]:
    prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
    prompt_details = usage.get("prompt_tokens_details")
    cached_tokens = max(0, int(usage.get("prompt_cache_hit_tokens") or 0))
    if isinstance(prompt_details, dict):
        cached_tokens = max(cached_tokens, int(prompt_details.get("cached_tokens") or 0))
    cached_tokens = min(cached_tokens, prompt_tokens)
    cache_miss_tokens = usage.get("prompt_cache_miss_tokens")
    if cache_miss_tokens is None:
        cache_miss_tokens = prompt_tokens - cached_tokens
    cache_miss_tokens = max(0, min(int(cache_miss_tokens or 0), prompt_tokens))
    completion_tokens = max(0, int(usage.get("completion_tokens") or 0))

    prices = (
        profile.cached_input_price_cny_per_million,
        profile.input_price_cny_per_million,
        profile.output_price_cny_per_million,
    )
    if not any(price > 0 for price in prices):
        return None, cached_tokens, "unavailable"
    cached_input_price = (
        profile.cached_input_price_cny_per_million
        if profile.cached_input_price_cny_per_million > 0
        else profile.input_price_cny_per_million
    )
    cost = (
        cached_tokens * cached_input_price
        + cache_miss_tokens * profile.input_price_cny_per_million
        + completion_tokens * profile.output_price_cny_per_million
    ) / 1_000_000
    source = {
        "official_catalog": "official_estimate",
        "provider_catalog": "provider_estimate",
    }.get(profile.pricing_source, "configured_estimate")
    return cost, cached_tokens, source


def _provider_log_cost_from_payload(
    log_data: object,
    request_id: str,
    quota_per_unit: float,
) -> float | None:
    if quota_per_unit <= 0 or not request_id:
        return None
    rows: object = log_data
    if isinstance(log_data, dict):
        rows = log_data.get("data", [])
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get("data") or []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or str(row.get("request_id") or "") != request_id:
            continue
        quota = _safe_nonnegative_float(row.get("quota"))
        if quota is not None:
            return quota / quota_per_unit
    return None


async def _provider_log_cost_yuan(
    profile: ModelProfile,
    route: CompletionRoute,
    request_id: str,
) -> float | None:
    """Read New API's token log so dynamic group multipliers use the real charge."""
    if not request_id:
        return None
    parsed = urlsplit(route.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if not origin.startswith(("http://", "https://")):
        return None
    client_kwargs: dict[str, object] = {"timeout": 8.0, "trust_env": False}
    if route.proxy_url:
        client_kwargs["proxy"] = route.proxy_url
    headers = auth_headers(profile.api_key, profile.auth_scheme)
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            quota_per_unit = _QUOTA_PER_UNIT_CACHE.get(origin, 0.0)
            if quota_per_unit <= 0:
                status_response = await client.get(f"{origin}/api/status")
                if not status_response.is_success:
                    return None
                status_data = status_response.json()
                if isinstance(status_data, dict) and isinstance(status_data.get("data"), dict):
                    status_data = status_data["data"]
                if not isinstance(status_data, dict) or str(status_data.get("quota_display_type") or "").upper() != "CNY":
                    return None
                quota_per_unit = _safe_nonnegative_float(status_data.get("quota_per_unit")) or 0.0
                if quota_per_unit <= 0:
                    return None
                _QUOTA_PER_UNIT_CACHE[origin] = quota_per_unit

            response = await client.get(
                f"{origin}/api/log/token?p=0&page_size=20",
                headers=headers,
            )
            if response.status_code in {401, 403, 404} or not response.is_success:
                return None
            return _provider_log_cost_from_payload(
                response.json(),
                request_id,
                quota_per_unit,
            )
    except (httpx.HTTPError, TypeError, ValueError):
        return None
    return None


def config_status() -> dict[str, object]:
    return {
        "base_url_set": bool(settings.openai_base_urls),
        "base_url_count": len(settings.openai_base_urls),
        "api_key_set": bool(settings.openai_api_key),
        "model_set": bool(settings.openai_model),
        "model": settings.openai_model or "",
        "models": [profile.id for profile in list_model_profiles()],
        "proxy_set": bool(settings.openai_proxy_url),
        "proxy_mode": settings.openai_proxy_mode,
    }


def resolve_model_id(model_id: str = "") -> str:
    try:
        return get_model_profile(model_id).id
    except ValueError as exc:
        raise LLMConfigError(str(exc)) from exc


def model_supports_vision(model_id: str = "") -> bool:
    try:
        return get_model_profile(model_id).supports_vision
    except ValueError as exc:
        raise LLMConfigError(str(exc)) from exc


def require_configured(model_id: str = "") -> None:
    try:
        profile = get_model_profile(model_id)
    except ValueError as exc:
        raise LLMConfigError(str(exc)) from exc
    if not profile.base_urls:
        raise LLMConfigError("缺少 OPENAI_BASE_URL。请在 backend/.env 中配置中转站地址。")
    if profile.api_key_error:
        raise LLMConfigError(profile.api_key_error)
    if not profile.api_key:
        raise LLMConfigError("缺少 OPENAI_API_KEY。请在 backend/.env 中配置中转站密钥。")
    if not profile.model:
        raise LLMConfigError("缺少 OPENAI_MODEL。请在 backend/.env 中配置模型名。")


def _completion_url(base_url: str, api_mode: str = "chat_completions") -> str:
    base_url = base_url.rstrip("/")
    if not base_url:
        raise LLMConfigError("缺少 OPENAI_BASE_URL。请在 backend/.env 中配置中转站地址。")
    if api_mode == "responses":
        if base_url.endswith("/responses"):
            return base_url
        return f"{base_url}/responses"
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _completion_headers(
    profile: ModelProfile,
    auth_scheme: str = "",
    *,
    stream: bool = False,
    idempotency_key: str = "",
) -> dict[str, str]:
    headers = {
        **auth_headers(profile.api_key, auth_scheme or profile.auth_scheme),
        "Content-Type": "application/json",
    }
    if stream:
        headers["Accept"] = "text/event-stream"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
        headers["X-Client-Request-Id"] = idempotency_key
    return headers


def _provider_idempotency_key(request_id: str, payload: dict[str, Any]) -> str:
    clean_request_id = str(request_id or "").strip()
    if not clean_request_id:
        return ""
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{clean_request_id}\x1f{serialized}".encode("utf-8")).hexdigest()
    return f"mio-{digest}"


def _apply_reasoning_parameters(payload: dict[str, Any], model: str, requested: str) -> str:
    normalized = normalize_model_reasoning(model, requested)
    parameter = str(model_reasoning_config(model)["parameter"])
    if parameter == "reasoning_effort":
        payload["reasoning_effort"] = normalized
    elif parameter == "deepseek_thinking":
        enabled = normalized != "off"
        payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if enabled:
            payload["reasoning_effort"] = normalized
            payload.pop("temperature", None)
    return normalized


def _responses_request_payload(
    profile: ModelProfile,
    messages: list[dict[str, Any]],
    reasoning_level: str,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": profile.model, "input": messages}
    normalized = normalize_model_reasoning(profile.model, reasoning_level)
    if str(model_reasoning_config(profile.model)["parameter"]) == "reasoning_effort":
        payload["reasoning"] = {"effort": normalized}
    if tools:
        converted = []
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if isinstance(function, dict):
                converted.append({"type": "function", **function})
        if converted:
            payload["tools"] = converted
    return payload


def _responses_message(data: dict[str, Any]) -> tuple[str, list[ToolCall]]:
    content = str(data.get("output_text") or "").strip()
    tool_calls: list[ToolCall] = []
    output = data.get("output")
    if isinstance(output, list):
        text_parts: list[str] = []
        for index, item in enumerate(output[:64]):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                tool_calls.append(ToolCall(
                    call_id=str(item.get("call_id") or item.get("id") or f"call_{index + 1}")[:120],
                    name=str(item.get("name") or "")[:80],
                    arguments_json=str(item.get("arguments") or "{}")[:8000],
                ))
            parts = item.get("content")
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                        text_parts.append(str(part.get("text") or ""))
        if not content:
            content = "".join(text_parts).strip()
    return content, [item for item in tool_calls if item.name]


def _completion_routes(profile: ModelProfile) -> list[CompletionRoute]:
    routes: list[CompletionRoute] = []
    mode = settings.openai_proxy_mode
    proxy_url = settings.openai_proxy_url.strip()

    def add_routes(request_mode: str) -> None:
        for configured_url in profile.base_urls:
            bases = [api_base_from_endpoint(endpoint) for endpoint in completion_endpoint_candidates(configured_url)]
            parsed = urlsplit(configured_url.rstrip("/"))
            if request_mode == "responses" and parsed.path.rstrip("/").lower().endswith("/v1"):
                bases.append(f"{parsed.scheme}://{parsed.netloc}")
            for base_url in dict.fromkeys(bases):
                for auth_scheme in auth_scheme_candidates(profile.auth_scheme):
                    if profile.auth_scheme != "auto" and auth_scheme != profile.auth_scheme:
                        continue
                    if mode != "proxy":
                        routes.append(CompletionRoute(base_url=base_url, proxy_url="", label="直连", auth_scheme=auth_scheme, api_mode=request_mode))
                    if proxy_url and mode != "direct":
                        routes.append(CompletionRoute(base_url=base_url, proxy_url=proxy_url, label="代理", auth_scheme=auth_scheme, api_mode=request_mode))

    add_routes(profile.api_mode)
    # A gateway can advertise Responses for Codex while temporarily keeping
    # only the legacy Chat Completions route. Try that route after the
    # documented Responses routes without changing the saved model name.
    if profile.api_mode == "responses":
        add_routes("chat_completions")

    return routes


def _route_error_text(route: CompletionRoute, exc: Exception | httpx.Response) -> str:
    target = f"{route.label} {route.base_url} [{route.auth_scheme}]"
    if route.proxy_url:
        target += " via proxy"
    if isinstance(exc, httpx.Response):
        return f"{target} -> HTTP {exc.status_code}，{exc.text[:500]}"
    return f"{target} -> {exc}"


def _route_name(route: CompletionRoute) -> str:
    return f"{route.label} {route.base_url} [{route.auth_scheme}; {route.api_mode}]"


def _attempt_record(route: CompletionRoute, exc: Exception | httpx.Response) -> dict[str, object]:
    return {
        "route": _route_name(route),
        "http_status": exc.status_code if isinstance(exc, httpx.Response) else None,
        "error": (exc.text[:500] if isinstance(exc, httpx.Response) else str(exc))[:500],
    }


def _request_deadline() -> float:
    return time.monotonic() + max(
        10.0,
        float(getattr(settings, "openai_request_deadline_seconds", 120)),
    )


def _remaining_request_seconds(deadline: float) -> float:
    return deadline - time.monotonic()


async def call_chat_completion_result(
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    model_id: str = "",
    reasoning_level: str = "standard",
    *,
    retry_attempts: int = 3,
    request_id: str = "",
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> CompletionResult:
    require_configured(model_id)
    profile = get_model_profile(resolve_model_id(model_id))

    if profile.api_mode == "messages":
        raise LLMConfigError("这个模型使用 Anthropic /messages，当前版本尚未接入。")
    responses_payload = _responses_request_payload(profile, messages, reasoning_level, tools)
    chat_payload: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        chat_payload["tools"] = tools
        chat_payload["tool_choice"] = tool_choice or "auto"
    _apply_reasoning_parameters(chat_payload, profile.model, reasoning_level)
    routes = _completion_routes(profile)
    if not routes:
        raise LLMConfigError("缺少可用的模型请求路线。请检查 OPENAI_BASE_URL / OPENAI_PROXY_URL。")

    errors: list[str] = []
    attempt_records: list[dict[str, object]] = []
    max_attempts = max(1, min(3, int(retry_attempts)))
    deadline = _request_deadline()

    for route in routes:
        response: httpx.Response | None = None
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            remaining = _remaining_request_seconds(deadline)
            if remaining <= 0:
                errors.append(f"{route.label} {route.base_url} -> 请求总时限已到")
                break
            request_started = time.perf_counter()
            request_api_mode = route.api_mode
            request_payload = responses_payload if request_api_mode == "responses" else chat_payload
            provider_idempotency_key = _provider_idempotency_key(request_id, request_payload)
            try:
                client_kwargs: dict[str, Any] = {
                    "timeout": min(float(settings.openai_timeout_seconds), remaining),
                    "trust_env": False,
                }
                if route.proxy_url:
                    client_kwargs["proxy"] = route.proxy_url
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await client.post(
                        _completion_url(route.base_url, request_api_mode),
                        headers=_completion_headers(
                            profile,
                            route.auth_scheme,
                            idempotency_key=provider_idempotency_key,
                        ),
                        json=request_payload,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    await asyncio.sleep(min(1.2 * (attempt + 1), max(0.0, _remaining_request_seconds(deadline))))
                    continue
                errors.append(_route_error_text(route, exc))
                attempt_records.append(_attempt_record(route, exc))
                break

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_attempts - 1:
                await asyncio.sleep(
                    min(
                        1.2 * (attempt + 1),
                        max(0.0, _remaining_request_seconds(deadline)),
                    )
                )
                continue
            break

        if response is None:
            if last_error and not errors:
                errors.append(_route_error_text(route, last_error))
            continue

        if response.status_code >= 400:
            errors.append(_route_error_text(route, response))
            attempt_records.append(_attempt_record(route, response))
            if response.status_code in {401, 403, 404}:
                continue
            continue

        try:
            data = response.json()
        except ValueError as exc:
            raise ModelRequestError(
                "供应商返回的成功响应不是有效 JSON。",
                profile=profile,
                request_id=request_id,
                http_status=response.status_code,
                route=_route_name(route),
                attempts=attempt_records + [_attempt_record(route, exc)],
            ) from exc
        if route.api_mode == "responses":
            content, parsed_tool_calls = _responses_message(data)
            message = {"content": content, "tool_calls": []}
        else:
            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ModelRequestError(
                    "供应商响应格式不符合 OpenAI-compatible chat completions。",
                    profile=profile,
                    request_id=request_id,
                    http_status=response.status_code,
                    route=_route_name(route),
                    attempts=attempt_records,
                ) from exc
            parsed_tool_calls = []

        if not isinstance(message, dict):
            raise ModelRequestError(
                "供应商响应中的 message 不是对象。",
                profile=profile,
                request_id=request_id,
                http_status=response.status_code,
                route=_route_name(route),
                attempts=attempt_records,
            )
        content = _chat_message_text(message.get("content")).strip()
        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            for index, item in enumerate(raw_tool_calls[:24]):
                if not isinstance(item, dict):
                    continue
                function = item.get("function")
                if not isinstance(function, dict):
                    continue
                name = str(function.get("name") or "").strip()[:80]
                arguments = function.get("arguments", "{}")
                if isinstance(arguments, dict):
                    arguments_json = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                else:
                    arguments_json = str(arguments or "{}").strip()[:8000]
                if not name:
                    continue
                parsed_tool_calls.append(
                    ToolCall(
                        call_id=str(item.get("id") or f"call_{index + 1}").strip()[:120],
                        name=name,
                        arguments_json=arguments_json,
                    )
                )
        if not content and not parsed_tool_calls:
            raise ModelRequestError(
                "供应商返回了空回复。",
                profile=profile,
                request_id=request_id,
                http_status=response.status_code,
                route=_route_name(route),
                attempts=attempt_records,
            )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt_tokens = max(0, int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0))
        completion_tokens = max(0, int(usage.get("completion_tokens") or usage.get("output_tokens") or 0))
        completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details")
        reasoning_tokens = 0
        if isinstance(completion_details, dict):
            reasoning_tokens = max(0, int(completion_details.get("reasoning_tokens") or 0))

        provider_request_id = str(response.headers.get("x-oneapi-request-id") or "").strip()
        cost_yuan = _provider_reported_cost_yuan(data, usage, response.headers)
        calculated_cost, cached_prompt_tokens, cost_source = _usage_cost_yuan(usage, profile)
        if cost_yuan is not None:
            cost_source = "provider_reported"
        else:
            cost_yuan = calculated_cost
        cost_references = ()
        if provider_request_id and cost_source != "provider_reported":
            cost_references = (
                ProviderCostReference(
                    profile_id=profile.id,
                    provider_request_id=provider_request_id,
                    base_url=route.base_url,
                    estimated_cost_yuan=calculated_cost,
                    estimated_cost_source=cost_source,
                ),
            )

        return CompletionResult(
            content=content,
            model=str(data.get("model") or profile.model),
            prompt_tokens=prompt_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_yuan=cost_yuan,
            cost_source=cost_source,
            cost_references=cost_references,
            first_token_latency_ms=round((time.perf_counter() - request_started) * 1000, 2),
            total_latency_ms=round((time.perf_counter() - request_started) * 1000, 2),
            profile_id=profile.id,
            provider_id=profile.provider_id,
            provider_name=profile.provider_name,
            provider_model=profile.model,
            provider_request_id=provider_request_id,
            route=_route_name(route),
            http_status=response.status_code,
            tool_calls=tuple(parsed_tool_calls),
        )

    detail = "；".join(errors[-4:]) if errors else "没有返回有效响应"
    last_attempt = attempt_records[-1] if attempt_records else {}
    raise ModelRequestError(
        f"模型请求失败，已尝试直连/代理/备用路线：{detail}",
        profile=profile,
        request_id=request_id,
        http_status=int(last_attempt.get("http_status") or 0),
        route=str(last_attempt.get("route") or ""),
        attempts=attempt_records,
    )


async def call_chat_completion_stream_result(
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    model_id: str = "",
    reasoning_level: str = "standard",
    *,
    on_delta: StreamDeltaCallback | None = None,
    retry_attempts: int = 2,
    request_id: str = "",
) -> CompletionResult:
    """Read an OpenAI-compatible SSE response while preserving usage metadata."""
    require_configured(model_id)
    profile = get_model_profile(resolve_model_id(model_id))
    if profile.api_mode != "chat_completions":
        result = await call_chat_completion_result(
            messages,
            temperature=temperature,
            model_id=profile.id,
            reasoning_level=reasoning_level,
            retry_attempts=retry_attempts,
            request_id=request_id,
        )
        if on_delta and result.content:
            maybe_awaitable = on_delta(result.content, "content")
            if maybe_awaitable is not None:
                await maybe_awaitable
        return result
    payload = {
        "model": profile.model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    _apply_reasoning_parameters(payload, profile.model, reasoning_level)
    provider_idempotency_key = _provider_idempotency_key(request_id, payload)
    routes = _completion_routes(profile)
    if not routes:
        raise LLMConfigError("缺少可用的模型请求路线。请检查 OPENAI_BASE_URL / OPENAI_PROXY_URL。")

    errors: list[str] = []
    attempt_records: list[dict[str, object]] = []
    max_attempts = max(1, min(3, int(retry_attempts)))
    deadline = _request_deadline()
    for route in routes:
        for attempt in range(max_attempts):
            remaining = _remaining_request_seconds(deadline)
            if remaining <= 0:
                errors.append(f"{route.label} {route.base_url} -> 请求总时限已到")
                break
            content_parts: list[str] = []
            usage: dict[str, Any] = {}
            response_model = profile.model
            response_headers = httpx.Headers()
            response: httpx.Response | None = None
            request_started = time.perf_counter()
            first_token_latency_ms: float | None = None
            try:
                client_kwargs: dict[str, Any] = {
                    "timeout": min(float(settings.openai_timeout_seconds), remaining),
                    "trust_env": False,
                }
                if route.proxy_url:
                    client_kwargs["proxy"] = route.proxy_url
                async with httpx.AsyncClient(**client_kwargs) as client:
                    async with client.stream(
                        "POST",
                        _completion_url(route.base_url),
                        headers=_completion_headers(
                            profile,
                            route.auth_scheme,
                            stream=True,
                            idempotency_key=provider_idempotency_key,
                        ),
                        json=payload,
                    ) as response:
                        response_headers = httpx.Headers(response.headers)
                        if response.status_code >= 400:
                            await response.aread()
                            errors.append(_route_error_text(route, response))
                            attempt_records.append(_attempt_record(route, response))
                            break
                        async for line in response.aiter_lines():
                            clean = line.strip()
                            if not clean or clean.startswith(":"):
                                continue
                            if clean.startswith("data:"):
                                clean = clean[5:].strip()
                            if not clean or clean == "[DONE]":
                                continue
                            try:
                                event = json.loads(clean)
                            except (TypeError, ValueError):
                                continue
                            if not isinstance(event, dict):
                                continue
                            if isinstance(event.get("usage"), dict):
                                usage = dict(event["usage"])
                            response_model = str(event.get("model") or response_model)
                            choices = event.get("choices")
                            if not isinstance(choices, list) or not choices:
                                continue
                            choice = choices[0] if isinstance(choices[0], dict) else {}
                            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                            piece = _chat_message_text(delta.get("content"))
                            if not piece:
                                continue
                            content_parts.append(piece)
                            if first_token_latency_ms is None:
                                first_token_latency_ms = round(
                                    (time.perf_counter() - request_started) * 1000,
                                    2,
                                )
                            if on_delta is not None:
                                callback_result = on_delta(piece, "".join(content_parts))
                                if asyncio.iscoroutine(callback_result):
                                    await callback_result
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if content_parts:
                    raise ModelRequestError(
                        f"模型流式响应中断：{_route_error_text(route, exc)}",
                        profile=profile,
                        request_id=request_id,
                        route=_route_name(route),
                        attempts=attempt_records + [_attempt_record(route, exc)],
                    ) from exc
                if attempt < max_attempts - 1:
                    await asyncio.sleep(min(0.35 * (attempt + 1), max(0.0, _remaining_request_seconds(deadline))))
                    continue
                errors.append(_route_error_text(route, exc))
                attempt_records.append(_attempt_record(route, exc))
                break

            content = "".join(content_parts).strip()
            if not content:
                if response is not None and response.status_code >= 400:
                    break
                errors.append(f"{route.label} {route.base_url} -> 流式响应没有正文")
                if attempt < max_attempts - 1:
                    continue
                break

            prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
            completion_tokens = max(0, int(usage.get("completion_tokens") or 0))
            completion_details = usage.get("completion_tokens_details")
            reasoning_tokens = 0
            if isinstance(completion_details, dict):
                reasoning_tokens = max(0, int(completion_details.get("reasoning_tokens") or 0))
            provider_request_id = str(response_headers.get("x-oneapi-request-id") or "").strip()
            cost_yuan = _provider_reported_cost_yuan({}, usage, response_headers)
            calculated_cost, cached_prompt_tokens, cost_source = _usage_cost_yuan(usage, profile)
            if cost_yuan is not None:
                cost_source = "provider_reported"
            else:
                cost_yuan = calculated_cost
            cost_references = ()
            if provider_request_id and cost_source != "provider_reported":
                cost_references = (
                    ProviderCostReference(
                        profile_id=profile.id,
                        provider_request_id=provider_request_id,
                        base_url=route.base_url,
                        estimated_cost_yuan=calculated_cost,
                        estimated_cost_source=cost_source,
                    ),
                )
            return CompletionResult(
                content=content,
                model=response_model,
                prompt_tokens=prompt_tokens,
                cached_prompt_tokens=cached_prompt_tokens,
                completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens,
                cost_yuan=cost_yuan,
                cost_source=cost_source,
                cost_references=cost_references,
                first_token_latency_ms=first_token_latency_ms,
                total_latency_ms=round((time.perf_counter() - request_started) * 1000, 2),
                profile_id=profile.id,
                provider_id=profile.provider_id,
                provider_name=profile.provider_name,
                provider_model=profile.model,
                provider_request_id=provider_request_id,
                route=_route_name(route),
                http_status=response.status_code if response is not None else 200,
            )

    detail = "；".join(errors[-4:]) if errors else "没有返回有效流式响应"
    last_attempt = attempt_records[-1] if attempt_records else {}
    raise ModelRequestError(
        f"模型流式请求失败，已尝试直连/代理/备用路线：{detail}",
        profile=profile,
        request_id=request_id,
        http_status=int(last_attempt.get("http_status") or 0),
        route=str(last_attempt.get("route") or ""),
        attempts=attempt_records,
    )


async def call_chat_completion(
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    model_id: str = "",
    reasoning_level: str = "standard",
) -> str:
    result = await call_chat_completion_result(
        messages,
        temperature=temperature,
        model_id=model_id,
        reasoning_level=reasoning_level,
    )
    return result.content
