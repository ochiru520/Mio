"""Synthetic HTTP and isolated ledger checks; no provider calls or private data."""
import asyncio
from contextlib import closing
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, llm, model_runtime as runtime, prompt_metrics
from app.model_registry import ModelProfile
from app.routes.runtime import router
from test_runtime_safety_v2 import isolated


PRIVATE = "PRIVATE_BODY_D:/synthetic/private/path sk-synthetic-secret"


def sample_messages():
    return [
        {"role": "system", "content": "固定规则 " + PRIVATE},
        {"role": "user", "content": "过去问题 " + PRIVATE},
        {"role": "assistant", "content": "过去回复 " + PRIVATE},
        {"role": "user", "content": json.dumps({"应用运行资料": [
            "【当前状态板】\n" + PRIVATE + "\n\n---\n\n【日记摘录】\n" + PRIVATE,
            "当前本地时间：2026-10-24", "【私密未知标题】\n" + PRIVATE,
        ]}, ensure_ascii=False)},
        {"role": "user", "content": "实际问题 " + PRIVATE},
    ]


def profile(api_mode):
    return ModelProfile(id="synthetic", provider_id="synthetic-provider", provider_name="Synthetic",
                        display_name="Synthetic", model="synthetic-model", base_urls=("https://invalid.example/v1",),
                        api_key="sk-synthetic-secret", api_mode=api_mode)


def test_estimate_is_ordered_additive_and_returns_no_input_or_unknown_titles():
    metrics = prompt_metrics.estimate_request(sample_messages(), [{"type": "function", "function": {
        "name": "private_tool", "description": PRIVATE, "parameters": {"type": "object"}}}])
    categories = [block["category"] for block in metrics["blocks"]]
    assert categories == ["fixed_instructions", "history", "history", "runtime_framing",
                          "runtime_current_state", "runtime_diary", "runtime_current_time", "runtime_other",
                          "current_request", "tool_schema"]
    assert metrics["estimated_input_tokens"] == sum(block["estimated_tokens"] for block in metrics["blocks"])
    assert metrics["is_billing_usage"] is False
    assert PRIVATE not in json.dumps(metrics) and "私密未知标题" not in json.dumps(metrics, ensure_ascii=False)
    changed = sample_messages()
    changed[-1]["content"] += "实际新增输入" * 100
    changed_metrics = prompt_metrics.estimate_request(changed)
    assert metrics["blocks"][:-2] == changed_metrics["blocks"][:-1]
    assert changed_metrics["blocks"][-1]["estimated_tokens"] > metrics["blocks"][-2]["estimated_tokens"]


@pytest.mark.parametrize(("heading", "category"), [
    ("相关历史状态", "runtime_historical_state"), ("相关AI回顾", "runtime_review"),
    ("分层私人记忆", "runtime_structured_memory"), ("压缩记忆", "runtime_compressed_memory"),
    ("已到期约定", "runtime_due_followups"),
    ("用户发言间隔判断", "runtime_message_gap"), ("会话开场时间定位", "runtime_conversation_orientation"),
])
def test_only_fixed_heading_labels_are_retained(heading, category):
    metrics = prompt_metrics.estimate_request([{"role": "user", "content": json.dumps({
        "应用运行资料": [f"{heading}：\n{PRIVATE}"]}, ensure_ascii=False)}])
    assert metrics["blocks"][1]["category"] == category
    assert PRIVATE not in json.dumps(metrics)


def test_multimodal_images_are_not_counted_as_base64_text_or_persisted():
    message = {"role": "user", "content": [
        {"type": "text", "text": "请看图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100000}},
    ], "_mio_step_ids": [123]}
    metrics = prompt_metrics.estimate_request([message])
    assert metrics["unestimated_image_count"] == 1
    assert metrics["estimated_input_tokens"] < 100
    assert "base64" not in json.dumps(metrics)


def test_nested_runtime_sections_split_whitelisted_line_headings_without_reading_user_titles():
    text = ("以下是按当前话题筛选的周期记忆资料。\n\n当前状态板：\n" + PRIVATE
            + "\n\n相关历史状态：\n过去\n\n日记摘录（资料）：\n日记"
            + "\n\n现实行动回访（尊重真实结果与调整，不重复原建议）：\n回访"
            + "\n\n已到期约定（当前）：\n约定")
    metrics = prompt_metrics.estimate_request([
        {"role": "user", "content": "当前状态板：这是用户原话，不是运行资料"},
        {"role": "user", "content": json.dumps({"应用运行资料": [text,
            "以下是较早聊天的压缩记忆。它用于延续关系和事实。\n合成摘要"]}, ensure_ascii=False)},
        {"role": "user", "content": "日记摘录：实际请求仍只是用户正文"},
    ])
    assert [part["category"] for part in metrics["blocks"]] == [
        "history", "runtime_framing", "runtime_other", "runtime_current_state", "runtime_historical_state",
        "runtime_diary", "runtime_action_followup", "runtime_due_followups", "runtime_compressed_memory", "current_request"]
    assert PRIVATE not in json.dumps(metrics)


@pytest.mark.parametrize(("text", "category"), [
    ("当前时间（本地）：2026-10-24", "runtime_current_time"),
    ("上一条可见消息：固定时间", "runtime_conversation_orientation"),
    ("当前会话可见记录：第一条", "runtime_conversation_orientation"),
    ("上一条用户消息：固定时间", "runtime_message_gap"),
    ("本轮用户消息未定位，不推测。", "runtime_message_gap"),
    ("这是当前可见记录中的第一条用户消息，没有可计算间隔。", "runtime_message_gap"),
    ("最近两条用户消息的时间不完整，不推测。", "runtime_message_gap"),
])
def test_time_template_prefixes_are_classified_only_in_runtime_envelope(text, category):
    metrics = prompt_metrics.estimate_request([{"role": "user", "content": json.dumps({
        "应用运行资料": [text]}, ensure_ascii=False)}, {"role": "user", "content": text}])
    assert [block["category"] for block in metrics["blocks"]] == ["runtime_framing", category, "current_request"]


@pytest.mark.parametrize(("mode", "purpose", "request_id", "automatic", "expected"), [
    ("chat", "chat_with_ai", "r", False, "chat"),
    ("agent", "run_agent_loop", "r", False, "agent_planner"),
    ("agent", "final_model_call", "r", False, "agent_reply"),
    ("agent", "run_agent_loop", "r:context-compress:1", False, "context_compression"),
    ("memory", "_compress_old_messages", "r", True, "context_compression"),
    ("memory", "consolidate", "r", True, "memory"),
    ("proactive", "generate", "r", True, "background"),
])
def test_request_category_uses_operation_metadata(mode, purpose, request_id, automatic, expected):
    assert prompt_metrics.request_category(mode, purpose, request_id, automatic) == expected


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
def test_http_result_ledger_and_diagnostic_api_keep_estimates_separate_from_usage(api_mode, streaming):
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        usage = ({"input_tokens": 1234, "input_tokens_details": {"cached_tokens": 1000}, "output_tokens": 7}
                 if api_mode == "responses" else
                 {"prompt_tokens": 1234, "prompt_tokens_details": {"cached_tokens": 1000}, "completion_tokens": 7})
        result = ({"model": "synthetic-model", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "合成回复"}]}], "usage": usage}
            if api_mode == "responses" else {"model": "synthetic-model", "choices": [
                {"message": {"content": "合成回复"}}], "usage": usage})
        if streaming:
            event = ({"type": "response.completed", "response": result} if api_mode == "responses" else
                     {"model": "synthetic-model", "choices": [{"delta": {"content": "合成回复"}, "finish_reason": "stop"}], "usage": usage})
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream", "x-request-cost-cny": "0.01"})
        return httpx.Response(200, json=result, headers={"x-request-cost-cny": "0.01"})
    constructor = httpx.AsyncClient
    @runtime.operation("chat")
    async def chat_with_ai():
        callback = llm.call_chat_completion_stream_result if streaming else llm.call_chat_completion_result
        return await callback(sample_messages(), model_id="synthetic", request_id="metrics-request", tools=[
            {"type": "function", "function": {"name": "synthetic_tool", "description": PRIVATE,
                                               "parameters": {"type": "object"}}}])
    with patch.object(llm, "get_model_profile", return_value=profile(api_mode)), patch.object(
        llm.httpx, "AsyncClient", side_effect=lambda **kwargs: constructor(transport=httpx.MockTransport(handler), trust_env=False)):
        completion = asyncio.run(chat_with_ai())
    assert completion.content == "合成回复" and len(requests) == 1
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/runtime/operations").json()
    calls = response["operations"][0]["calls"]
    assert len(calls) == 1
    call = calls[0]
    assert call["request_category"] == "chat" and call["is_streaming"] == int(streaming)
    assert call["protocol_mode"] == api_mode
    assert call["prompt_tokens"] == 1234 and call["cached_prompt_tokens"] == 1000
    assert call["cached_tokens_reporting"] == "reported_positive"
    assert call["cost_yuan"] == pytest.approx(.01)
    metrics = json.loads(call["estimated_input_json"])
    assert metrics["estimated_input_tokens"] != call["prompt_tokens"]
    assert metrics["is_billing_usage"] is False
    assert PRIVATE not in json.dumps(response) and "sk-synthetic-secret" not in json.dumps(response)
    assert "私密未知标题" not in json.dumps(response, ensure_ascii=False)


def test_additive_migration_preserves_old_rows_and_keeps_missing_cache_unknown():
    from app.repositories import prompt_metrics_repository as repository
    import sqlite3
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE runtime_model_calls(id TEXT PRIMARY KEY,prompt_tokens INTEGER)")
        conn.execute("INSERT INTO runtime_model_calls VALUES('old',123)")
        repository.initialize(conn)
        repository.initialize(conn)
        assert conn.execute("SELECT prompt_tokens,cached_prompt_tokens,estimated_input_json FROM runtime_model_calls").fetchone() == (123, None, "{}")


def test_uninitialized_old_schema_skips_only_additional_metrics_without_migrating():
    from app.repositories import prompt_metrics_repository as repository
    from types import SimpleNamespace
    import sqlite3
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE runtime_model_calls(id TEXT PRIMARY KEY,prompt_tokens INTEGER)")
        conn.execute("INSERT INTO runtime_model_calls VALUES('old',123)")
        repository.record_estimate(conn, "old", category="chat", streaming=False,
                                   metrics=prompt_metrics.estimate_request(sample_messages()))
        repository.record_usage(conn, "old", SimpleNamespace(cached_prompt_tokens=100, prompt_tokens=123,
                                                              protocol_mode="responses"))
        repository.finalize_attempts(conn, 'old', 'synthetic_failure')
        assert conn.execute("SELECT * FROM runtime_model_calls").fetchone() == ("old", 123)
        assert [row[1] for row in conn.execute("PRAGMA table_info(runtime_model_calls)")] == ["id", "prompt_tokens"]


def test_failed_call_retains_local_estimate_without_fabricated_provider_usage():
    @runtime.model_call
    async def fail(messages, model_id="", reasoning_level="", request_id=""):
        raise ValueError(PRIVATE)
    with pytest.raises(ValueError):
        asyncio.run(fail(sample_messages(), model_id="synthetic"))
    with closing(db.get_conn()) as conn:
        row = dict(conn.execute("SELECT * FROM runtime_model_calls").fetchone())
    assert row["status"] == "failed" and row["cached_prompt_tokens"] is None
    assert json.loads(row["estimated_input_json"])["estimated_input_tokens"] > 0
    assert PRIVATE not in json.dumps(row)


@pytest.mark.parametrize("cached", [0, None, -1, 999])
def test_cache_reporting_preserves_zero_ambiguity_and_bounds_actual_input(cached):
    from app.repositories import prompt_metrics_repository as repository
    from types import SimpleNamespace
    import sqlite3
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE runtime_model_calls(id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO runtime_model_calls VALUES('call')")
        repository.initialize(conn)
        repository.record_usage(conn, "call", SimpleNamespace(cached_prompt_tokens=cached, prompt_tokens=123,
                                                               protocol_mode="responses"))
        value, reporting = conn.execute("SELECT cached_prompt_tokens,cached_tokens_reporting FROM runtime_model_calls").fetchone()
    assert value == (123 if cached == 999 else None)
    assert reporting == ("reported_positive" if value else "unknown")
