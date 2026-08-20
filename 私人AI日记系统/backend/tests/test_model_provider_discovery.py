from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.routes.models import (
    ModelDiscoveryRequest,
    _chat_response_is_valid,
    _models_endpoint_candidates,
    _normalize_provider_request,
    _model_records_from_payload,
    _provider_client_routes,
    _provider_http_error_detail,
    _provider_chat_error_detail,
    _request_chat_endpoint,
    _request_chat_via_routes,
    _request_models_endpoint,
    discover_models,
    test_model_profile as _test_model_profile,
)
from app.provider_compat import completion_endpoint_candidates
from app.provider_compat import response_is_json
from app.provider_presets import PROVIDER_PRESETS


class ModelProviderDiscoveryTests(unittest.TestCase):
    def test_successful_chat_test_records_onboarding_verification(self) -> None:
        profile = SimpleNamespace(
            id="model-id",
            provider_id="provider-id",
            provider_name="测试供应商",
            model="model-a",
            base_urls=["https://relay.example/v1"],
            api_key="test-key",
            auth_scheme="bearer",
        )
        catalog = httpx.Response(
            200,
            request=httpx.Request("GET", "https://relay.example/v1/models"),
            json={"data": [{"id": "model-a"}]},
        )
        completion = httpx.Response(
            200,
            request=httpx.Request("POST", "https://relay.example/v1/chat/completions"),
            json={"choices": [{"message": {"content": "OK"}}]},
        )
        with (
            patch("app.routes.models.get_model_profile", return_value=profile),
            patch(
                "app.routes.models._request_models_via_routes",
                new=AsyncMock(return_value=(catalog, str(catalog.request.url), ["catalog"], "bearer", "直连")),
            ),
            patch(
                "app.routes.models._request_chat_via_routes",
                new=AsyncMock(return_value=(completion, str(completion.request.url), ["chat"], "bearer", "直连")),
            ),
            patch("app.routes.models.onboarding_service.record_model_verification") as record,
        ):
            result = asyncio.run(_test_model_profile(profile.id))

        self.assertTrue(result["chat_verified"])
        record.assert_called_once_with(profile.id)

    def test_official_provider_uses_known_address(self) -> None:
        kind, protocol, base_url = _normalize_provider_request(
            "official",
            "deepseek",
            "https://untrusted.example/v1",
        )

        self.assertEqual(kind, "official")
        self.assertEqual(protocol, "deepseek")
        self.assertEqual(base_url, "https://api.deepseek.com/v1")

    def test_opencode_go_preset_uses_its_fixed_api_root(self) -> None:
        kind, protocol, base_url = _normalize_provider_request(
            "official",
            "openai",
            "https://wrong.example/v1",
            "opencode_go",
        )

        self.assertEqual(kind, "official")
        self.assertEqual(protocol, "opencode_go")
        self.assertEqual(base_url, "https://opencode.ai/zen/go/v1")

    def test_discovery_uses_each_selected_official_preset_instead_of_openai(self) -> None:
        for preset in (item for item in PROVIDER_PRESETS if item["kind"] == "official"):
            with self.subTest(preset=preset["id"]):
                endpoint = f"{preset['base_url']}/models"
                response = httpx.Response(
                    200,
                    request=httpx.Request("GET", endpoint),
                    json={"data": [{"id": "model-a"}]},
                )
                request_models = AsyncMock(
                    return_value=(response, endpoint, ["catalog"], "bearer", "直连")
                )
                payload = ModelDiscoveryRequest(
                    provider_kind="official",
                    provider_protocol="openai",
                    base_url="https://wrong.example/v1",
                    api_key="test-key",
                    preset_id=str(preset["id"]),
                )
                with patch(
                    "app.routes.models._request_models_via_routes",
                    new=request_models,
                ):
                    result = asyncio.run(discover_models(payload))

                request_models.assert_awaited_once_with(
                    str(preset["base_url"]),
                    "test-key",
                )
                self.assertEqual(result["resolved_base_url"], preset["base_url"])
                self.assertEqual(result["provider_protocol"], preset["protocol"])
                self.assertEqual([item["model"] for item in result["models"]], ["model-a"])

    def test_ekti_preset_discovers_models_as_responses(self) -> None:
        endpoint = "https://chat.ekti.cc/v1/models"
        response = httpx.Response(
            200,
            request=httpx.Request("GET", endpoint),
            json={"data": [{"id": "gpt-5.6-sol"}, {"id": "gpt-5.6-luna"}]},
        )
        pricing_miss = httpx.Response(
            404,
            request=httpx.Request("GET", "https://chat.ekti.cc/api/pricing"),
        )
        status_miss = httpx.Response(
            404,
            request=httpx.Request("GET", "https://chat.ekti.cc/api/status"),
        )
        pricing_client = AsyncMock()
        pricing_client.__aenter__.return_value = pricing_client
        pricing_client.__aexit__.return_value = False
        pricing_client.get.side_effect = [pricing_miss, status_miss]
        payload = ModelDiscoveryRequest(
            provider_kind="relay",
            provider_protocol="openai",
            base_url="https://wrong.example/v1",
            api_key="test-key",
            preset_id="ekti",
        )

        with (
            patch(
                "app.routes.models._request_models_via_routes",
                new=AsyncMock(return_value=(response, endpoint, ["catalog"], "bearer", "直连")),
            ),
            patch("app.routes.models.httpx.AsyncClient", return_value=pricing_client),
        ):
            result = asyncio.run(discover_models(payload))

        self.assertEqual(result["resolved_base_url"], "https://chat.ekti.cc/v1")
        self.assertEqual(result["default_api_mode"], "responses")
        self.assertEqual({item["api_mode"] for item in result["models"]}, {"responses"})

    def test_relay_without_v1_tries_both_model_endpoints(self) -> None:
        self.assertEqual(
            _models_endpoint_candidates("https://relay.example"),
            [
                "https://relay.example/models",
                "https://relay.example/v1/models",
                "https://relay.example/api/models",
                "https://relay.example/api/v1/models",
                "https://relay.example/openai/v1/models",
            ],
        )
        self.assertEqual(
            _models_endpoint_candidates("https://relay.example/v1"),
            ["https://relay.example/v1/models"],
        )

    def test_discovery_falls_back_to_v1_after_not_found(self) -> None:
        first = httpx.Response(404, request=httpx.Request("GET", "https://relay.example/models"))
        second = httpx.Response(
            200,
            request=httpx.Request("GET", "https://relay.example/v1/models"),
            json={"data": [{"id": "model-a"}]},
        )
        client = AsyncMock()
        client.get.side_effect = [first, second]

        response, endpoint, attempts, auth_scheme = asyncio.run(
            _request_models_endpoint(client, "https://relay.example", "test-key")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(endpoint, "https://relay.example/v1/models")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(auth_scheme, "bearer")

    def test_discovery_falls_back_to_v1_after_root_rejects_key(self) -> None:
        rejected = [
            httpx.Response(401, request=httpx.Request("GET", "https://relay.example/models"))
            for _ in range(3)
        ]
        second = httpx.Response(
            200,
            request=httpx.Request("GET", "https://relay.example/v1/models"),
            json={"data": [{"id": "model-a"}]},
        )
        client = AsyncMock()
        client.get.side_effect = [*rejected, second]

        response, endpoint, attempts, auth_scheme = asyncio.run(
            _request_models_endpoint(client, "https://relay.example", "test-key")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(endpoint, "https://relay.example/v1/models")
        self.assertEqual(len(attempts), 4)
        self.assertEqual(auth_scheme, "bearer")

    def test_nested_and_plain_model_lists_are_supported(self) -> None:
        self.assertEqual(_model_records_from_payload({"models": [{"id": "a"}]}), [{"id": "a"}])
        self.assertEqual(_model_records_from_payload({"result": {"data": ["b"]}}), [{"id": "b"}])
        self.assertEqual(_model_records_from_payload([{"id": "c"}]), [{"id": "c"}])
        self.assertEqual(_model_records_from_payload(["d"]), [{"id": "d"}])
        self.assertEqual(
            _model_records_from_payload({"model_list": {"e": {"owned_by": "relay"}}}),
            [{"id": "e", "owned_by": "relay"}],
        )

    def test_full_endpoint_is_normalized_and_api_v1_is_supported(self) -> None:
        self.assertEqual(
            _models_endpoint_candidates("https://relay.example/api/v1/chat/completions"),
            ["https://relay.example/api/v1/models"],
        )
        self.assertEqual(
            completion_endpoint_candidates("https://relay.example/v1/models"),
            ["https://relay.example/v1/chat/completions"],
        )

    def test_discovery_tries_x_api_key_after_bearer_rejection(self) -> None:
        rejected = httpx.Response(401, request=httpx.Request("GET", "https://relay.example/models"))
        accepted = httpx.Response(
            200,
            request=httpx.Request("GET", "https://relay.example/models"),
            json={"data": [{"id": "model-a"}]},
        )
        client = AsyncMock()
        client.get.side_effect = [rejected, accepted]

        response, endpoint, attempts, auth_scheme = asyncio.run(
            _request_models_endpoint(client, "https://relay.example", "test-key")
        )

        self.assertTrue(response.is_success)
        self.assertEqual(endpoint, "https://relay.example/models")
        self.assertEqual(auth_scheme, "x-api-key")
        self.assertIn("x-api-key", attempts[-1])

    def test_html_success_is_not_accepted_as_model_catalog(self) -> None:
        responses = [
            httpx.Response(
                200,
                request=httpx.Request("GET", "https://relay.example/models"),
                headers={"content-type": "text/html"},
                text="<html>login</html>",
            )
        ]
        responses.extend(
            httpx.Response(404, request=httpx.Request("GET", f"https://relay.example/path-{index}"))
            for index in range(4)
        )
        client = AsyncMock()
        client.get.side_effect = responses

        response, _endpoint, attempts, auth_scheme = asyncio.run(
            _request_models_endpoint(client, "https://relay.example", "test-key")
        )

        self.assertFalse(response_is_json(response))
        self.assertEqual(auth_scheme, "")
        self.assertIn("非JSON", attempts[0])

    def test_unrecognized_json_falls_back_to_next_catalog_path(self) -> None:
        status_payload = httpx.Response(
            200,
            request=httpx.Request("GET", "https://relay.example/models"),
            json={"ok": True},
        )
        catalog_payload = httpx.Response(
            200,
            request=httpx.Request("GET", "https://relay.example/v1/models"),
            json={"data": [{"id": "model-a"}]},
        )
        client = AsyncMock()
        client.get.side_effect = [status_payload, catalog_payload]

        response, endpoint, attempts, auth_scheme = asyncio.run(
            _request_models_endpoint(client, "https://relay.example", "test-key")
        )

        self.assertTrue(response.is_success)
        self.assertEqual(endpoint, "https://relay.example/v1/models")
        self.assertEqual(auth_scheme, "bearer")
        self.assertIn("未识别到模型列表", attempts[0])

    def test_unauthorized_provider_is_reported_as_key_rejection(self) -> None:
        response = httpx.Response(
            401,
            request=httpx.Request("GET", "https://relay.example/v1/models"),
        )

        detail = _provider_http_error_detail(response, str(response.request.url))

        self.assertIn("供应商地址可以访问", detail)
        self.assertIn("API Key 被拒绝", detail)
        self.assertIn("https://relay.example/v1/models", detail)

    def test_chat_invalid_token_is_reported_as_local_key_reentry(self) -> None:
        profile = SimpleNamespace(
            id="model-id",
            provider_id="provider-id",
            provider_name="测试中转站",
            model="gpt-5.6-sol",
        )
        response = httpx.Response(
            401,
            request=httpx.Request("POST", "https://relay.example/v1/chat/completions"),
            json={
                "error": {
                    "code": "",
                    "message": "Invalid token (request id: 202608180639352390193978268d9d6DGatEdxk)",
                    "type": "new_api_error",
                }
            },
        )

        detail = _provider_chat_error_detail(
            response,
            profile=profile,
            endpoint=str(response.request.url),
            attempts=["直连：HTTP 401"],
            connection_route="直连",
        )

        self.assertEqual(detail["code"], "provider_api_key_rejected")
        self.assertTrue(detail["requires_local_key_reentry"])
        self.assertEqual(detail["request_id"], "202608180639352390193978268d9d6DGatEdxk")
        self.assertIn("这台电脑重新输入", detail["message"])
        self.assertNotIn("{\"error\"", detail["message"])

    def test_chat_fallback_succeeds_without_max_tokens(self) -> None:
        rejected = httpx.Response(
            400,
            request=httpx.Request("POST", "https://relay.example/chat/completions"),
            json={"error": "max_tokens is not supported"},
        )
        accepted = httpx.Response(
            200,
            request=httpx.Request("POST", "https://relay.example/chat/completions"),
            json={"choices": [{"message": {"content": "OK"}}]},
        )
        client = AsyncMock()
        client.post.side_effect = [rejected, accepted]

        response, endpoint, attempts, auth_scheme = asyncio.run(
            _request_chat_endpoint(client, "https://relay.example", "test-key", "model-a")
        )

        self.assertTrue(response.is_success)
        self.assertEqual(endpoint, "https://relay.example/chat/completions")
        self.assertEqual(auth_scheme, "bearer")
        self.assertIn("简化参数", attempts[-1])

    def test_html_success_is_not_accepted_as_chat_completion(self) -> None:
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://relay.example/v1/chat/completions"),
            headers={"content-type": "text/html"},
            text="<html>home page</html>",
        )

        self.assertFalse(_chat_response_is_valid(response))

    def test_json_without_choices_is_not_accepted_as_chat_completion(self) -> None:
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://relay.example/v1/chat/completions"),
            json={"ok": True},
        )

        self.assertFalse(_chat_response_is_valid(response))

    def test_responses_api_output_is_accepted(self) -> None:
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://opencode.ai/zen/go/v1/responses"),
            json={"output_text": "OK", "output": []},
        )

        self.assertTrue(_chat_response_is_valid(response, "responses"))

    def test_responses_request_uses_responses_endpoint_and_payload(self) -> None:
        accepted = httpx.Response(
            200,
            request=httpx.Request("POST", "https://chat.ekti.cc/v1/responses"),
            json={"output_text": "OK", "output": []},
        )
        client = AsyncMock()
        client.post.return_value = accepted

        response, endpoint, attempts, auth_scheme = asyncio.run(
            _request_chat_endpoint(
                client,
                "https://chat.ekti.cc/v1",
                "test-key",
                "gpt-5.6-sol",
                api_mode="responses",
            )
        )

        self.assertTrue(_chat_response_is_valid(response, "responses"))
        self.assertEqual(endpoint, "https://chat.ekti.cc/v1/responses")
        self.assertEqual(auth_scheme, "bearer")
        self.assertEqual(len(attempts), 1)
        request = client.post.await_args
        self.assertEqual(request.args[0], "https://chat.ekti.cc/v1/responses")
        self.assertEqual(
            request.kwargs["json"],
            {"model": "gpt-5.6-sol", "input": "只回复OK", "max_output_tokens": 4},
        )

    def test_responses_route_stops_after_first_valid_network_route(self) -> None:
        accepted = httpx.Response(
            200,
            request=httpx.Request("POST", "https://chat.ekti.cc/v1/responses"),
            json={"output_text": "OK", "output": []},
        )
        request = AsyncMock(return_value=(
            accepted,
            str(accepted.request.url),
            ["responses ok"],
            "bearer",
        ))
        with (
            patch(
                "app.routes.models._provider_client_routes",
                return_value=[("直连", ""), ("应用代理", "http://127.0.0.1:7897")],
            ),
            patch("app.routes.models._request_chat_endpoint", new=request),
        ):
            result = asyncio.run(
                _request_chat_via_routes(
                    "https://chat.ekti.cc/v1",
                    "test-key",
                    "gpt-5.6-sol",
                    api_mode="responses",
                )
            )

        self.assertEqual(result[4], "直连")
        self.assertEqual(request.await_count, 1)

    def test_provider_routes_follow_proxy_mode(self) -> None:
        with patch(
            "app.routes.models.settings",
            SimpleNamespace(
                openai_proxy_mode="auto",
                openai_proxy_url="http://127.0.0.1:7897",
            ),
        ):
            self.assertEqual(
                _provider_client_routes(),
                [("直连", ""), ("应用代理", "http://127.0.0.1:7897")],
            )

    def test_chat_route_falls_back_to_application_proxy(self) -> None:
        valid = httpx.Response(
            200,
            request=httpx.Request("POST", "https://relay.example/v1/chat/completions"),
            json={"choices": [{"message": {"content": "OK"}}]},
        )
        with patch(
            "app.routes.models._provider_client_routes",
            return_value=[("直连", ""), ("应用代理", "http://127.0.0.1:7897")],
        ), patch(
            "app.routes.models._request_chat_endpoint",
            new=AsyncMock(side_effect=[httpx.ConnectTimeout("timeout"), (valid, str(valid.request.url), ["ok"], "bearer")]),
        ):
            response, _endpoint, attempts, auth_scheme, route_label = asyncio.run(
                _request_chat_via_routes("https://relay.example/v1", "test-key", "model-a")
            )

        self.assertTrue(_chat_response_is_valid(response))
        self.assertEqual(auth_scheme, "bearer")
        self.assertEqual(route_label, "应用代理")
        self.assertEqual(attempts, ["应用代理：ok"])

    def test_failed_chat_diagnostics_include_attempted_paths(self) -> None:
        responses = [
            httpx.Response(
                404,
                request=httpx.Request("POST", f"https://relay.example/path-{index}"),
            )
            for index in range(5)
        ]
        client = AsyncMock()
        client.post.side_effect = responses

        response, endpoint, attempts, auth_scheme = asyncio.run(
            _request_chat_endpoint(client, "https://relay.example", "test-key", "model-a")
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(auth_scheme, "")
        self.assertEqual(len(attempts), 5)
        self.assertIn("openai/v1/chat/completions", attempts[-1])
        self.assertTrue(endpoint.endswith("path-4"))


if __name__ == "__main__":
    unittest.main()
