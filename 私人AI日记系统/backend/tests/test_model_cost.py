from __future__ import annotations

import base64
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

from app.llm import (
    CompletionRoute,
    ModelRequestError,
    _apply_reasoning_parameters,
    _completion_headers,
    _completion_url,
    _chat_message_text,
    _provider_idempotency_key,
    _provider_log_cost_from_payload,
    _provider_reported_cost_yuan,
    _responses_message,
    _responses_request_payload,
    _usage_cost_yuan,
    call_chat_completion_result,
    call_chat_completion_stream_result,
)
from app.model_registry import ModelProfile, model_reasoning_config, provider_model_api_mode, suggest_model_metadata
from app.napcat_service import (
    _decode_qrcode_data_url,
    _with_diagnostic,
    _parse_login_status,
    _render_qrcode_png,
    _repair_mojibake,
    get_napcat_login_status,
)
from app.routes.agent import _discovery_records, _new_api_pricing_map, qq_login_qrcode


class ModelCostTests(unittest.TestCase):
    def test_chat_content_blocks_ignore_structured_reasoning(self) -> None:
        content = _chat_message_text([
            {"type": "reasoning", "text": "We need answer..."},
            {"type": "text", "text": "真正回复"},
        ])

        self.assertEqual(content, "真正回复")

    def test_opencode_go_routes_models_to_supported_protocols(self) -> None:
        self.assertEqual(provider_model_api_mode("opencode_go", "gpt-5.6-luna"), "responses")
        self.assertEqual(provider_model_api_mode("opencode_go", "grok-4"), "responses")
        self.assertEqual(provider_model_api_mode("opencode_go", "deepseek-v3.1"), "chat_completions")
        self.assertEqual(provider_model_api_mode("opencode_go", "claude-sonnet-4"), "messages")
        self.assertEqual(
            _completion_url("https://opencode.ai/zen/go/v1", "responses"),
            "https://opencode.ai/zen/go/v1/responses",
        )

    def test_responses_payload_and_output_are_converted(self) -> None:
        profile = ModelProfile(
            id="luna",
            provider_name="OpenCode Go",
            display_name="gpt-5.6-luna",
            model="gpt-5.6-luna",
            base_urls=("https://opencode.ai/zen/go/v1",),
            api_key="test",
            api_mode="responses",
        )
        payload = _responses_request_payload(
            profile,
            [{"role": "user", "content": "你好"}],
            "high",
            [{"type": "function", "function": {"name": "lookup", "description": "查找", "parameters": {"type": "object"}}}],
        )
        content, calls = _responses_message({
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "你好"}]},
                {"type": "function_call", "call_id": "call-1", "name": "lookup", "arguments": "{}"},
            ]
        })

        self.assertEqual(payload["reasoning"]["effort"], "high")
        self.assertEqual(payload["tools"][0]["name"], "lookup")
        self.assertEqual(content, "你好")
        self.assertEqual(calls[0].name, "lookup")
    def test_provider_retry_uses_stable_payload_idempotency_key(self) -> None:
        payload = {"model": "test", "messages": [{"role": "user", "content": "你好"}]}
        first = _provider_idempotency_key("client-request-1", payload)
        second = _provider_idempotency_key("client-request-1", dict(payload))
        changed = _provider_idempotency_key("client-request-1", {**payload, "temperature": 0.2})
        profile = ModelProfile(
            id="test",
            provider_name="测试",
            display_name="测试",
            model="test",
            base_urls=("https://example.test/v1",),
            api_key="secret",
        )

        headers = _completion_headers(profile, idempotency_key=first)

        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertEqual(headers["Idempotency-Key"], first)
        self.assertEqual(headers["X-Client-Request-Id"], first)

    def test_deepseek_cache_hit_and_miss_use_different_prices(self) -> None:
        profile = ModelProfile(
            id="deepseek-v4-flash",
            provider_name="DeepSeek",
            display_name="DeepSeek V4 · Flash",
            model="deepseek-v4-flash",
            base_urls=("https://api.deepseek.com/v1",),
            api_key="test",
            cached_input_price_cny_per_million=0.02,
            input_price_cny_per_million=1.0,
            output_price_cny_per_million=2.0,
            pricing_source="official_catalog",
        )
        usage = {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 200,
            "prompt_cache_miss_tokens": 800,
            "completion_tokens": 500,
        }

        cost, cached_tokens, source = _usage_cost_yuan(usage, profile)

        self.assertEqual(cached_tokens, 200)
        self.assertAlmostEqual(cost, (200 * 0.02 + 800 * 1 + 500 * 2) / 1_000_000)
        self.assertEqual(source, "official_estimate")

    def test_explicit_provider_cny_cost_is_accepted(self) -> None:
        headers = httpx.Headers({"x-request-cost-cny": "0.012345"})
        self.assertAlmostEqual(_provider_reported_cost_yuan({}, {}, headers), 0.012345)
        self.assertAlmostEqual(
            _provider_reported_cost_yuan({}, {"cost_yuan": 0.02}, httpx.Headers()),
            0.02,
        )

    def test_provider_log_quota_is_converted_to_real_cny_cost(self) -> None:
        payload = {
            "data": [
                {"request_id": "other", "quota": 999},
                {"request_id": "request-1", "quota": 298},
            ]
        }

        cost = _provider_log_cost_from_payload(payload, "request-1", 500000)

        self.assertAlmostEqual(cost, 0.000596)

    def test_known_variant_keeps_provider_model_as_display_name(self) -> None:
        self.assertEqual(
            suggest_model_metadata("gpt-5.6-luna"),
            {
                "family_name": "GPT-5.6",
                "variant_name": "Luna",
                "display_name": "gpt-5.6-luna",
            },
        )

    def test_gpt_and_deepseek_use_their_native_reasoning_parameters(self) -> None:
        gpt_payload = {}
        deepseek_payload = {"temperature": 0.7}
        deepseek_off_payload = {"temperature": 0.7}

        gpt_level = _apply_reasoning_parameters(gpt_payload, "gpt-5.6-luna", "deep")
        deepseek_level = _apply_reasoning_parameters(deepseek_payload, "deepseek-v4-flash", "max")
        deepseek_off_level = _apply_reasoning_parameters(deepseek_off_payload, "deepseek-v4-flash", "off")

        self.assertEqual(gpt_level, "high")
        self.assertEqual(gpt_payload["reasoning_effort"], "high")
        self.assertEqual(deepseek_level, "max")
        self.assertEqual(deepseek_payload["thinking"], {"type": "enabled"})
        self.assertEqual(deepseek_payload["reasoning_effort"], "max")
        self.assertNotIn("temperature", deepseek_payload)
        self.assertEqual(deepseek_off_level, "off")
        self.assertEqual(deepseek_off_payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", deepseek_off_payload)
        self.assertEqual(deepseek_off_payload["temperature"], 0.7)
        deepseek_options = model_reasoning_config("deepseek-v4-flash")
        self.assertEqual(deepseek_options["default"], "low")
        self.assertEqual(
            [option["id"] for option in deepseek_options["options"]],
            ["off", "low", "high", "max"],
        )
        self.assertEqual(model_reasoning_config("deepseek-v4-pro")["default"], "high")

    def test_new_api_public_ratios_become_cny_token_prices(self) -> None:
        pricing = {
            "data": [{
                "model_name": "gpt-5.6-sol",
                "quota_type": 0,
                "model_ratio": 2.5,
                "completion_ratio": 6,
                "cache_ratio": 0.1,
            }]
        }
        status = {"data": {"quota_display_type": "CNY", "quota_per_unit": 500000}}

        result = _new_api_pricing_map(pricing, status)["gpt-5.6-sol"]

        self.assertAlmostEqual(result["cached_input_price_cny_per_million"], 0.5)
        self.assertAlmostEqual(result["input_price_cny_per_million"], 5.0)
        self.assertAlmostEqual(result["output_price_cny_per_million"], 30.0)
        self.assertEqual(result["pricing_source"], "provider_catalog")

    def test_model_discovery_falls_back_to_public_catalog_on_unauthorized(self) -> None:
        response = httpx.Response(
            401,
            request=httpx.Request("GET", "https://example.com/v1/models"),
            json={"error": {"message": "Invalid token"}},
        )
        prices = {
            "gpt-5.6-sol": {
                "input_price_cny_per_million": 5.0,
                "output_price_cny_per_million": 30.0,
            }
        }

        records, warning = _discovery_records(response, prices)

        self.assertEqual(records, [{"id": "gpt-5.6-sol"}])
        self.assertIn("API Key", warning)
        self.assertIn("公开模型目录", warning)

    def test_napcat_login_status_is_not_inferred_from_websocket(self) -> None:
        result = _parse_login_status({
            "data": {
                "isLogin": False,
                "qrcodeurl": "https://example.com/qr",
                "loginError": "登录已失效",
            }
        })

        self.assertTrue(result["login_checked"])
        self.assertFalse(result["logged_in"])
        self.assertTrue(result["qrcode_available"])
        self.assertEqual(result["login_error"], "登录已失效")

    def test_napcat_diagnostic_distinguishes_webui_and_onebot_failures(self) -> None:
        base = {
            "control_scripts_ready": True,
            "napcat_dir_exists": True,
            "napcat_executable_exists": True,
            "process_check_supported": True,
            "napcat_process_running": True,
            "qq_process_running": True,
            "webui_config_exists": True,
            "webui_config_ready": True,
            "webui_reachable": False,
            "login_checked": False,
            "logged_in": False,
            "websocket_connected": False,
        }

        unavailable = _with_diagnostic(dict(base))
        bridgeless = _with_diagnostic({
            **base,
            "webui_reachable": True,
            "login_checked": True,
            "logged_in": True,
        })

        self.assertEqual(unavailable["diagnostic_code"], "webui_unreachable")
        self.assertEqual(bridgeless["diagnostic_code"], "onebot_disconnected")

        stopped = _with_diagnostic({**base, "napcat_process_running": False})
        qq_stopped = _with_diagnostic({**base, "qq_process_running": False})
        self.assertEqual(stopped["diagnostic_code"], "napcat_process_stopped")
        self.assertEqual(qq_stopped["diagnostic_code"], "qq_process_stopped")

    def test_napcat_empty_webui_credential_keeps_complete_diagnostic(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"data": {}}

        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

            async def post(self, *args, **kwargs) -> FakeResponse:
                return FakeResponse()

        config_path = MagicMock()
        config_path.read_text.return_value = json.dumps({"token": "test-token"})
        filesystem = {
            "control_scripts_ready": True,
            "control_scripts": {"start": True, "stop": True, "restart": True},
            "napcat_dir_exists": True,
            "napcat_executable_exists": True,
            "webui_config_exists": True,
            "webui_config_ready": True,
        }
        with (
            patch("app.napcat_service._webui_config_path", return_value=config_path),
            patch("app.napcat_service._filesystem_status", return_value=filesystem),
            patch("app.napcat_service.httpx.AsyncClient", FakeClient),
        ):
            result = asyncio.run(get_napcat_login_status(cache_seconds=0))

        self.assertTrue(result["webui_reachable"])
        self.assertFalse(result["login_checked"])
        self.assertEqual(result["diagnostic_code"], "login_unknown")
        self.assertTrue(result["diagnostic_message"])

    def test_napcat_mojibake_login_error_is_repaired(self) -> None:
        expected = "你的用户身份已失效，为保证账号安全，请你重新登录。"
        broken = expected.encode("utf-8").decode("latin-1")

        self.assertEqual(_repair_mojibake(broken), expected)

    def test_napcat_qrcode_data_url_is_decoded_without_exposing_token(self) -> None:
        encoded = base64.b64encode(b"fake-qr-png").decode("ascii")

        result = _decode_qrcode_data_url(f"data:image/png;base64,{encoded}")

        self.assertEqual(result, (b"fake-qr-png", "image/png"))
        self.assertIsNone(_decode_qrcode_data_url("https://example.com/qr.png"))

    def test_napcat_qrcode_text_is_rendered_as_png(self) -> None:
        result = _render_qrcode_png("https://example.com/qq-login?token=temporary")

        self.assertIsNotNone(result)
        content, media_type = result
        self.assertEqual(media_type, "image/png")
        self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_agent_qrcode_proxy_returns_uncached_image(self) -> None:
        with patch(
            "app.routes.agent.get_napcat_qrcode",
            new=AsyncMock(return_value=(b"fake-qr-png", "image/png")),
        ):
            response = asyncio.run(qq_login_qrcode())

        self.assertEqual(response.body, b"fake-qr-png")
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_agent_qrcode_proxy_returns_404_before_qrcode_is_ready(self) -> None:
        with patch("app.routes.agent.get_napcat_qrcode", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(qq_login_qrcode())

        self.assertEqual(raised.exception.status_code, 404)


class StreamingCompletionTests(unittest.TestCase):
    def test_retryable_http_backoff_cannot_exceed_request_deadline(self) -> None:
        profile = ModelProfile(
            id="deadline-test",
            provider_id="provider-deadline-test",
            provider_name="test",
            display_name="Deadline Test",
            model="deadline-test",
            base_urls=("https://example.test/v1",),
            api_key="secret",
        )

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, *_args, **_kwargs):
                return httpx.Response(
                    503,
                    request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
                )

        async def run() -> None:
            with (
                patch("app.llm.require_configured"),
                patch("app.llm.resolve_model_id", return_value=profile.id),
                patch("app.llm.get_model_profile", return_value=profile),
                patch(
                    "app.llm._completion_routes",
                    return_value=[CompletionRoute("https://example.test/v1", "", "直连")],
                ),
                patch("app.llm.httpx.AsyncClient", FakeClient),
                patch("app.llm._request_deadline", return_value=10.0),
                patch("app.llm._remaining_request_seconds", side_effect=[1.0, 0.25, 0.0]),
                patch("app.llm.asyncio.sleep", new=AsyncMock()) as sleep,
            ):
                with self.assertRaises(ModelRequestError) as raised:
                    await call_chat_completion_result(
                        [{"role": "user", "content": "test"}],
                        model_id=profile.id,
                        request_id="deadline-client-request",
                    )

            sleep.assert_awaited_once_with(0.25)
            detail = raised.exception.public_detail()
            self.assertIn("请求总时限已到", detail["message"])
            self.assertEqual(detail["request_id"], "deadline-client-request")
            self.assertEqual(detail["provider_id"], "provider-deadline-test")
            self.assertEqual(detail["model_id"], "deadline-test")
            self.assertTrue(detail["attempts"])

        asyncio.run(run())

    def test_openai_compatible_sse_is_assembled_and_emitted_incrementally(self) -> None:
        events = [
            {"model": "vision-fast", "choices": [{"delta": {"content": "{\"event\":"}}]},
            {"model": "vision-fast", "choices": [{"delta": {"content": "\"victory\"}"}}]},
            {
                "model": "vision-fast",
                "choices": [],
                "usage": {"prompt_tokens": 120, "completion_tokens": 8},
            },
        ]
        lines = [f"data: {json.dumps(item)}" for item in events] + ["data: [DONE]"]

        class FakeResponse:
            status_code = 200
            headers = httpx.Headers({"x-request-cost-cny": "0.006"})

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def aiter_lines(self):
                for line in lines:
                    yield line

        class FakeClient:
            def __init__(self, **_kwargs):
                self.payload = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def stream(self, _method, _url, *, headers, json):
                self.payload = json
                self.headers = headers
                return FakeResponse()

        profile = ModelProfile(
            id="vision-fast",
            provider_id="provider-test",
            provider_name="test",
            display_name="Vision Fast",
            model="vision-fast",
            base_urls=("https://example.test/v1",),
            api_key="secret",
            supports_vision=True,
        )
        deltas: list[tuple[str, str]] = []

        async def run():
            with (
                patch("app.llm.get_model_profile", return_value=profile),
                patch(
                    "app.llm._completion_routes",
                    return_value=[CompletionRoute("https://example.test/v1", "", "直连")],
                ),
                patch("app.llm.httpx.AsyncClient", FakeClient),
                patch("app.llm.time.perf_counter", side_effect=[10.0, 10.4, 11.2]),
            ):
                return await call_chat_completion_stream_result(
                    [{"role": "user", "content": "test"}],
                    model_id="vision-fast",
                    on_delta=lambda piece, content: deltas.append((piece, content)),
                    retry_attempts=1,
                    request_id="client-stream-1",
                )

        result = asyncio.run(run())

        self.assertEqual(result.content, '{"event":"victory"}')
        self.assertEqual(result.prompt_tokens, 120)
        self.assertEqual(result.completion_tokens, 8)
        self.assertAlmostEqual(result.cost_yuan, 0.006)
        self.assertEqual(deltas[-1][1], result.content)
        self.assertEqual(result.first_token_latency_ms, 400.0)
        self.assertEqual(result.total_latency_ms, 1200.0)
        self.assertEqual(result.profile_id, "vision-fast")
        self.assertEqual(result.provider_id, "provider-test")
        self.assertEqual(result.provider_name, "test")
        self.assertEqual(result.provider_model, "vision-fast")
        self.assertEqual(result.http_status, 200)


if __name__ == "__main__":
    unittest.main()
