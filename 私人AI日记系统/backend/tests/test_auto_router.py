from __future__ import annotations

import unittest

from app.auto_router import build_task_profile, classify_difficulty, select_auto_route
from app.model_latency_service import record_latency, reset_latency_stats
from app.model_registry import ModelProfile


def _profile(
    model: str,
    *,
    price: float,
    vision: bool = False,
    default: bool = False,
    tool_calls: bool = False,
    structured_output: bool = True,
    context_window_tokens: int = 32768,
) -> ModelProfile:
    return ModelProfile(
        id=model,
        provider_name="test",
        display_name=model,
        model=model,
        base_urls=("https://example.test/v1",),
        api_key="configured",
        supports_vision=vision,
        supports_tool_calls=tool_calls,
        supports_structured_output=structured_output,
        context_window_tokens=context_window_tokens,
        input_price_cny_per_million=price,
        output_price_cny_per_million=price * 2,
        pricing_source="test",
        is_default=default,
    )


class AutoRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_latency_stats()
        self.profiles = [
            _profile("deepseek-v4-flash", price=1, default=True),
            _profile("gpt-5.6-luna", price=3, vision=True),
            _profile("gpt-5.6-sol", price=12, vision=True),
        ]

    def tearDown(self) -> None:
        reset_latency_stats()

    def test_simple_chat_uses_cheap_model_with_low_thinking(self) -> None:
        route = select_auto_route("嗯，刚吃过饭。", profiles=self.profiles)

        self.assertEqual(route.difficulty, "simple")
        self.assertEqual(route.model, "deepseek-v4-flash")
        self.assertEqual(route.reasoning_level, "low")

    def test_standard_analysis_uses_default_model_with_thinking(self) -> None:
        route = select_auto_route("帮我分析一下这两个方案的区别。", profiles=self.profiles)

        self.assertEqual(route.difficulty, "standard")
        self.assertEqual(route.model, "deepseek-v4-flash")
        self.assertEqual(route.reasoning_level, "high")

    def test_open_ended_feature_request_is_not_treated_as_idle_chat(self) -> None:
        route = select_auto_route("你后续有什么想让我做的功能嘛？", profiles=self.profiles)

        self.assertEqual(route.difficulty, "standard")
        self.assertEqual(route.reasoning_level, "high")

    def test_complex_implementation_uses_strong_model_and_high_reasoning(self) -> None:
        route = select_auto_route(
            "请给出完整方案，设计数据库和接口，并实现代码、测试方案与风险分析。",
            profiles=self.profiles,
            performance={
                "deepseek-v4-flash": {
                    "sample_count": 5,
                    "success_rate": 0.6,
                    "first_token_p95_ms": 1800,
                    "average_cost_yuan": 0.002,
                },
                "gpt-5.6-luna": {
                    "sample_count": 5,
                    "success_rate": 0.8,
                    "first_token_p95_ms": 2600,
                    "average_cost_yuan": 0.008,
                },
                "gpt-5.6-sol": {
                    "sample_count": 5,
                    "success_rate": 1.0,
                    "first_token_p95_ms": 4200,
                    "average_cost_yuan": 0.025,
                },
            },
        )

        self.assertEqual(route.difficulty, "complex")
        self.assertEqual(route.model, "gpt-5.6-sol")
        self.assertEqual(route.reasoning_level, "high")
        self.assertEqual(route.fallback_model_id, "gpt-5.6-luna")

    def test_deepseek_only_complex_task_uses_max_reasoning(self) -> None:
        route = select_auto_route(
            "请设计数据库和接口，并实现代码、测试方案与风险分析。",
            profiles=[self.profiles[0]],
        )

        self.assertEqual(route.model, "deepseek-v4-flash")
        self.assertEqual(route.reasoning_level, "max")

    def test_image_uses_cheaper_vision_model(self) -> None:
        route = select_auto_route("看看这张图。", image_count=1, profiles=self.profiles)

        self.assertEqual(route.model, "gpt-5.6-luna")
        self.assertEqual(route.reasoning_level, "medium")

    def test_short_continuation_inherits_previous_topic_difficulty(self) -> None:
        difficulty, _ = classify_difficulty(
            "继续",
            history_rows=[{"role": "user", "content": "请实现代码并设计数据库接口和完整测试方案。"}],
        )

        self.assertEqual(difficulty, "complex")

    def test_slow_simple_chat_model_is_avoided_when_latency_is_known(self) -> None:
        record_latency(
            "deepseek-v4-flash",
            first_token_latency_ms=9200,
            total_latency_ms=12000,
        )
        record_latency(
            "gpt-5.6-luna",
            first_token_latency_ms=1200,
            total_latency_ms=2600,
        )

        route = select_auto_route("今天有点累。", profiles=self.profiles)

        self.assertEqual(route.model, "gpt-5.6-luna")
        self.assertEqual(route.latency_budget_ms, 2800)
        self.assertEqual(route.reasoning_level, "low")

    def test_slow_standard_deepseek_reduces_reasoning_when_no_alternative_exists(self) -> None:
        record_latency(
            "deepseek-v4-flash",
            first_token_latency_ms=9000,
            total_latency_ms=14000,
        )

        route = select_auto_route(
            "帮我分析一下这两个方案的区别。",
            profiles=[self.profiles[0]],
        )

        self.assertEqual(route.model, "deepseek-v4-flash")
        self.assertEqual(route.reasoning_level, "low")

    def test_slow_simple_deepseek_never_disables_thinking_in_auto_mode(self) -> None:
        record_latency(
            "deepseek-v4-flash",
            first_token_latency_ms=12000,
            total_latency_ms=18000,
        )

        route = select_auto_route("今天有点累。", profiles=[self.profiles[0]])

        self.assertEqual(route.reasoning_level, "low")

    def test_tool_task_requires_declared_structured_capability(self) -> None:
        incapable = _profile(
            "plain-chat",
            price=0.1,
            structured_output=False,
        )
        capable = _profile(
            "tool-model",
            price=1,
            tool_calls=True,
            structured_output=False,
        )

        route = select_auto_route(
            "帮我创建一个今晚复测语音的提醒。",
            profiles=[incapable, capable],
        )

        self.assertEqual(route.model_id, "tool-model")
        rejected = next(item for item in route.candidates if item["model_id"] == "plain-chat")
        self.assertFalse(rejected["eligible"])

    def test_explicit_web_lookup_is_not_classified_as_plain_chat(self) -> None:
        profile = build_task_profile("帮我联网查一下今天合川天气")

        self.assertTrue(profile.requires_tools)

    def test_voice_fault_report_requires_service_diagnostics(self) -> None:
        profile = build_task_profile("为什么语音有时候没声音，是不是转写故障了")

        self.assertTrue(profile.requires_tools)
        self.assertEqual(profile.task_type, "agent_tool")

    def test_ordinary_analysis_does_not_require_agent_tools(self) -> None:
        profile = build_task_profile("你觉得这两个想法有什么区别")

        self.assertFalse(profile.requires_tools)
        self.assertEqual(profile.task_type, "analysis")

    def test_low_success_model_is_penalized_after_five_samples(self) -> None:
        route = select_auto_route(
            "今天有点累。",
            profiles=self.profiles[:2],
            performance={
                "deepseek-v4-flash": {
                    "sample_count": 5,
                    "success_rate": 0.4,
                    "first_token_p95_ms": 500,
                    "average_cost_yuan": 0.001,
                },
                "gpt-5.6-luna": {
                    "sample_count": 5,
                    "success_rate": 0.8,
                    "first_token_p95_ms": 1400,
                    "average_cost_yuan": 0.006,
                },
            },
        )

        self.assertEqual(route.model_id, "gpt-5.6-luna")

    def test_task_profile_exposes_decision_inputs_without_model_guessing(self) -> None:
        profile = build_task_profile(
            "看看这张图，再帮我分析问题。",
            image_count=1,
            text_attachment_chars=3000,
        )

        self.assertEqual(profile.task_type, "vision")
        self.assertEqual(profile.modalities, ("text", "image", "document"))
        self.assertTrue(profile.requires_vision)
        self.assertGreater(profile.estimated_context_tokens, 900)

    def test_context_capacity_filter_includes_conversation_history(self) -> None:
        small = _profile(
            "small-context",
            price=0.1,
            context_window_tokens=4096,
        )
        large = _profile(
            "large-context",
            price=1,
            context_window_tokens=32768,
        )

        route = select_auto_route(
            "继续",
            history_rows=[{"role": "assistant", "content": "x" * 9000}],
            profiles=[small, large],
        )

        self.assertEqual(route.model_id, "large-context")
        rejected = next(item for item in route.candidates if item["model_id"] == "small-context")
        self.assertFalse(rejected["eligible"])
        self.assertIn("上下文容量不足", rejected["capability_reasons"])


if __name__ == "__main__":
    unittest.main()
