"""Synthetic HTTP regressions for cache usage across both supported protocols."""
import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from app import llm
from app.model_registry import ModelProfile


def profile(api_mode="chat_completions", priced=True):
    return ModelProfile(
        id="cache-test", provider_name="Synthetic", display_name="Synthetic",
        model="synthetic-model", base_urls=("https://cache.example.test/v1",),
        api_key="synthetic-not-a-key", api_mode=api_mode,
        cached_input_price_cny_per_million=0.1 if priced else 0,
        input_price_cny_per_million=1 if priced else 0,
        output_price_cny_per_million=2 if priced else 0,
    )


def usage(api_mode):
    if api_mode == "responses":
        return {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 800},
                "output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 20}}
    return {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens": 100, "completion_tokens_details": {"reasoning_tokens": 20}}


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider_cost", [None, 0.01234, 0.0])
def test_http_cache_usage_and_provider_cost_priority(api_mode, stream, provider_cost):
    selected = profile(api_mode)
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == selected.model
        assert bool(payload.get("stream")) == stream
        assert request.url.path.endswith("/responses" if api_mode == "responses" else "/chat/completions")
        if api_mode == "responses":
            body = {"status": "completed", "model": selected.model,
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "synthetic"}]}],
                    "usage": usage(api_mode)}
            events = [{"type": "response.output_text.delta", "delta": "synthetic"},
                      {"type": "response.completed", "response": body}]
        else:
            body = {"model": selected.model, "choices": [{"message": {"content": "synthetic"}}],
                    "usage": usage(api_mode)}
            events = [{"model": selected.model, "choices": [{"delta": {"content": "synthetic"}, "finish_reason": "stop"}]},
                      {"choices": [], "usage": usage(api_mode)}]
        headers = {"x-request-cost-cny": str(provider_cost)} if provider_cost is not None else {}
        if stream:
            headers["content-type"] = "text/event-stream"
            content = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
            return httpx.Response(200, content=content.encode(), headers=headers)
        return httpx.Response(200, json=body, headers=headers)

    client_class = httpx.AsyncClient

    def client(**_kwargs):
        return client_class(transport=httpx.MockTransport(handle), trust_env=False)

    async def run():
        callback = llm.call_chat_completion_stream_result if stream else llm.call_chat_completion_result
        # Skip only the runtime-ledger decorator, so this test never opens a DB.
        with (patch.object(llm, "require_configured"),
              patch.object(llm, "resolve_model_id", return_value=selected.id),
              patch.object(llm, "get_model_profile", return_value=selected),
              patch.object(llm, "_completion_routes", return_value=[llm.CompletionRoute(
                  selected.base_urls[0], "", "synthetic", api_mode=api_mode)]),
              patch.object(llm.httpx, "AsyncClient", side_effect=client)):
            return await callback.__wrapped__([{"role": "user", "content": "synthetic"}],
                                              model_id=selected.id, retry_attempts=1)

    result = asyncio.run(run())
    assert len(requests) == 1
    assert result.content == "synthetic"
    assert (result.prompt_tokens, result.cached_prompt_tokens, result.completion_tokens, result.reasoning_tokens) == (1000, 800, 100, 20)
    assert result.cost_yuan == pytest.approx(0.00048 if provider_cost is None else provider_cost)
    assert result.cost_source == ("configured_estimate" if provider_cost is None else "provider_reported")


@pytest.mark.parametrize("raw, expected", [
    ({"prompt_tokens": 0, "input_tokens": 1000, "completion_tokens": 0, "output_tokens": 100,
      "input_tokens_details": {"cached_tokens": 800}}, (0, 0, 0)),
    ({"input_tokens": -3, "output_tokens": -4, "input_tokens_details": {"cached_tokens": 800}}, (0, 0, 0)),
    ({"input_tokens": 100, "input_tokens_details": {"cached_tokens": 800}}, (100, 100, 0)),
    ({"input_tokens": 100, "input_tokens_details": {"cached_tokens": -1}}, (100, 0, 0)),
    ({"input_tokens": "100", "output_tokens": "4", "input_tokens_details": {"cached_tokens": "20"}}, (100, 20, 4)),
    ({"input_tokens": "invalid", "output_tokens": [], "input_tokens_details": None}, (0, 0, 0)),
    ({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200,
      "completion_tokens": 100}, (1000, 800, 100)),
])
def test_normalization_preserves_zero_and_bounds_cache(raw, expected):
    result = llm._normalize_usage(raw)
    assert (result["prompt_tokens"], result["prompt_cache_hit_tokens"], result["completion_tokens"]) == expected
    assert result["prompt_cache_hit_tokens"] + result["prompt_cache_miss_tokens"] <= result["prompt_tokens"]


def test_cache_usage_survives_missing_prices_and_does_not_infer_hits():
    assert llm._usage_cost_yuan(usage("responses"), profile(priced=False)) == (None, 800, "unavailable")
    cost, cached, _ = llm._usage_cost_yuan({"input_tokens": 1000, "output_tokens": 100}, profile())
    assert cached == 0
    assert cost == pytest.approx(0.0012)


def test_normalization_keeps_provider_metadata_and_original_payload():
    raw = {**usage("responses"), "cost_yuan": 0.02}
    normalized = llm._normalize_usage(raw)
    assert normalized["cost_yuan"] == 0.02
    assert "prompt_tokens" not in raw
    assert llm._normalize_usage(normalized) == normalized
