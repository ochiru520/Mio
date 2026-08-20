from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.web_search_service import (
    WebLookup,
    WebSource,
    _extract_weather_location,
    _fetch_page,
    _is_blocked_url,
    _parse_china_weather_city_search,
    _parse_china_weather_index,
    _location_name_matches,
    _parse_domestic_search_results,
    _search_web,
    _source_matches_query,
    _weather_lookup,
    _validate_public_url,
    should_use_web_lookup,
)


class WebSearchRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_enabled = settings.web_search_enabled
        object.__setattr__(settings, "web_search_enabled", True)

    def tearDown(self) -> None:
        object.__setattr__(settings, "web_search_enabled", self.original_enabled)

    def test_personal_daily_state_does_not_trigger_web_search(self) -> None:
        self.assertFalse(should_use_web_lookup("你现在感觉怎么样？"))
        self.assertFalse(should_use_web_lookup("我最近状态怎么样"))

    def test_explicit_or_external_current_question_still_uses_web_search(self) -> None:
        self.assertTrue(should_use_web_lookup("帮我查一下 DeepSeek 最新价格"))
        self.assertTrue(should_use_web_lookup("现在重庆天气怎么样"))

    def test_weather_location_ignores_conversational_search_request(self) -> None:
        self.assertEqual(_extract_weather_location("嗯，现在帮我去网上查今天合川的天气吧"), "合川")
        self.assertEqual(
            _extract_weather_location("我测试联网功能，今天合川天气怎么样?"),
            "合川",
        )

    def test_weather_falls_back_after_primary_source_failure(self) -> None:
        expected = WebSource(title="合川天气", url="https://example.com/weather", snippet="晴，30°C")
        with (
            patch("app.web_search_service._fetch_weather_cn_api", new=AsyncMock(side_effect=TimeoutError("超时"))),
            patch("app.web_search_service._fetch_weather_china", new=AsyncMock(return_value=expected)),
        ):
            lookup = __import__("asyncio").run(_weather_lookup("查一下合川今天天气"))

        self.assertIsInstance(lookup, WebLookup)
        self.assertEqual(lookup.sources, [expected])
        self.assertEqual(lookup.engine, "中国天气网")
        self.assertIn("国内中文天气", lookup.attempts[0])

    def test_weather_resolves_administrative_area_and_retries_without_asking_user(self) -> None:
        expected = WebSource(
            title="合川区天气",
            url="https://example.com/hechuan-weather",
            snippet="地点：合川区，重庆市；当前：晴，34°C",
        )

        async def domestic_weather(location: str):
            return expected if location == "重庆市合川区" else None

        with (
            patch("app.web_search_service._fetch_weather_cn_api", new=AsyncMock(side_effect=domestic_weather)),
            patch("app.web_search_service._fetch_weather_china", new=AsyncMock(return_value=None)),
            patch(
                "app.web_search_service._resolve_china_weather_city",
                new=AsyncMock(return_value={"code": "101040300", "name": "合川", "province": "重庆"}),
            ),
        ):
            lookup = __import__("asyncio").run(
                _weather_lookup("我测试联网功能，今天合川天气怎么样?")
            )

        self.assertEqual(lookup.sources, [expected])
        self.assertEqual(lookup.query, "重庆市合川区 天气")
        self.assertIn("地点消歧：合川 -> 重庆市合川区", "；".join(lookup.attempts))

    def test_china_weather_city_search_prefers_exact_district(self) -> None:
        payload = '([{"ref":"101040300~chongqing~合川~Hechuan~合川~Hechuan~23~401520~HC~重庆"},' \
            '{"ref":"101040300001~chongqing~草街街道~caojie~合川~hechuan~023~401520~chongqing~重庆"}])'

        self.assertEqual(
            _parse_china_weather_city_search(payload, "合川"),
            {"code": "101040300", "name": "合川", "province": "重庆"},
        )

    def test_equal_city_candidates_are_not_chosen_arbitrarily(self) -> None:
        payload = '([{"ref":"101010100~a~长安~changan~长安~changan~1~1~CA~河北"},' \
            '{"ref":"101020100~b~长安~changan~长安~changan~2~2~CA~陕西"}])'

        self.assertIsNone(_parse_china_weather_city_search(payload, "长安"))

    def test_china_weather_index_parses_current_forecast(self) -> None:
        payload = 'var cityDZ ={"weatherinfo":{"city":"合川","temp":"31","tempn":"29","weather":"晴"}};var alarmDZ ={};'

        self.assertEqual(
            _parse_china_weather_index(payload),
            {"city": "合川", "temp": "31", "tempn": "29", "weather": "晴"},
        )

    def test_weather_location_match_rejects_unrelated_city(self) -> None:
        self.assertTrue(_location_name_matches("合川", "合川区", "重庆市"))
        self.assertFalse(_location_name_matches("合川", "北京", "北京市"))

    def test_search_falls_back_when_first_engine_fails(self) -> None:
        expected = [WebSource(title="测试结果", url="https://example.com", snippet="测试摘要")]
        with (
            patch("app.web_search_service._search_sogou", new=AsyncMock(side_effect=TimeoutError("超时"))),
            patch("app.web_search_service._search_360", new=AsyncMock(return_value=expected)),
        ):
            sources, engine, attempts = __import__("asyncio").run(_search_web("测试"))

        self.assertEqual(sources, expected)
        self.assertEqual(engine, "360搜索")
        self.assertIn("搜狗", attempts[0])
        self.assertIn("返回 1 条相关结果", attempts[1])

    def test_irrelevant_results_do_not_count_as_success(self) -> None:
        irrelevant = [WebSource(title="不字的搜索内容", url="https://example.com/no", snippet="无关摘要")]
        relevant = [WebSource(title="异环明日更新公告", url="https://example.com/update", snippet="版本更新")]
        with (
            patch("app.web_search_service._search_sogou", new=AsyncMock(return_value=irrelevant)),
            patch("app.web_search_service._search_360", new=AsyncMock(return_value=relevant)),
        ):
            sources, engine, attempts = __import__("asyncio").run(_search_web("明天异环更新"))

        self.assertEqual(sources, relevant)
        self.assertEqual(engine, "360搜索")
        self.assertIn("不相关", attempts[0])

    def test_query_relevance_uses_meaningful_terms(self) -> None:
        self.assertTrue(_source_matches_query(
            WebSource(title="DeepSeek V4 最新公告", url="https://example.com", snippet=""),
            "帮我查一下 DeepSeek 最新消息",
        ))
        self.assertFalse(_source_matches_query(
            WebSource(title="不字的搜索内容", url="https://example.com", snippet="没有相关内容"),
            "明天异环更新",
        ))

    def test_domestic_search_results_are_parsed(self) -> None:
        sources = _parse_domestic_search_results(
            '<h3 class="res-title"><a href="/link?id=1" data-mdurl="https://example.com/update">'
            '<em>异环</em>更新</a></h3>',
            "https://www.so.com",
        )
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].title, "异环更新")
        self.assertEqual(sources[0].url, "https://example.com/update")

    def test_literal_private_and_non_http_urls_are_blocked(self) -> None:
        self.assertTrue(_is_blocked_url("http://127.0.0.1/private"))
        self.assertTrue(_is_blocked_url("http://[::1]/private"))
        self.assertTrue(_is_blocked_url("file:///C:/private.txt"))
        self.assertTrue(_is_blocked_url("https://user:pass@example.com/private"))
        self.assertFalse(_is_blocked_url("https://example.com/page"))

    def test_domain_resolving_to_private_address_is_blocked(self) -> None:
        records = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with patch("app.web_search_service.socket.getaddrinfo", return_value=records):
            with self.assertRaisesRegex(ValueError, "非公网地址"):
                __import__("asyncio").run(_validate_public_url("http://example.test/private"))

    def test_each_redirect_target_is_validated_before_next_request(self) -> None:
        class FakeResponse:
            status_code = 302
            headers = {"location": "http://internal.test/private"}
            url = "https://public.test/start"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return False

        class FakeClient:
            requests = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def stream(self, method, url):
                self.requests.append((method, url))
                return FakeStream()

        client = FakeClient()
        validate = AsyncMock(side_effect=[None, ValueError("链接解析到了非公网地址")])
        with (
            patch("app.web_search_service.httpx.AsyncClient", return_value=client),
            patch("app.web_search_service._validate_public_url", new=validate),
        ):
            with self.assertRaisesRegex(ValueError, "非公网地址"):
                __import__("asyncio").run(_fetch_page("https://public.test/start"))

        self.assertEqual(client.requests, [("GET", "https://public.test/start")])
        self.assertEqual(validate.await_count, 2)

    def test_html_response_size_is_limited_before_body_read(self) -> None:
        class FakeResponse:
            status_code = 200
            headers = {"content-type": "text/html", "content-length": "99999999"}
            url = "https://public.test/large"
            charset_encoding = "utf-8"

            def raise_for_status(self):
                return None

            async def aiter_bytes(self):
                yield b"should-not-be-read"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return False

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def stream(self, _method, _url):
                return FakeStream()

        with (
            patch("app.web_search_service.httpx.AsyncClient", return_value=FakeClient()),
            patch("app.web_search_service._validate_public_url", new=AsyncMock()),
        ):
            with self.assertRaisesRegex(ValueError, "网页响应超过"):
                __import__("asyncio").run(_fetch_page("https://public.test/large"))


if __name__ == "__main__":
    unittest.main()
