from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .config import settings


URL_RE = re.compile(r"https?://[^\s<>()\"'，。！？；、]+", re.I)
WEB_TRIGGER_RE = re.compile(
    r"(查一下|查查|帮我查|搜一下|搜索|上网|联网|网上|网页|浏览|看看最新)"
)
TIME_SENSITIVE_RE = re.compile(
    r"(最新|实时|新闻|热搜|天气|汇率|股价|价格|票价|政策|法规|版本|更新|官网|下载|赛程|比分|上映|发布|"
    r"(现在|今天|最近|目前|当前|今年).{0,20}(是谁|多少|怎么样|有哪些|什么时候|情况|消息|新闻|天气|价格|政策|版本|开了吗|还能用吗))"
)
PERSONAL_STATE_RE = re.compile(
    r"(?:你|我|Mio|澪|咱们).{0,16}(?:感觉|心情|状态|身体|累|困|饿|开心|难过|在做什么|过得怎么样)"
    r"|(?:感觉|心情|状态|身体).{0,12}(?:怎么样|如何|还好吗)",
    re.IGNORECASE,
)
WEATHER_RE = re.compile(r"(天气|气温|温度|下雨|降雨|带伞|穿什么|适合出门|热不热|冷不冷)")
LOCATION_HINT_RE = re.compile(r"(我在|在|重庆|北京|上海|天津|合川|区|县|市|省|镇|路|附近)")
WEATHER_LOOKUP_NOISE_RE = re.compile(
    r"(?:嗯+|唔+|好(?:的|呀|啊)?|行(?:吧)?|那(?:就)?|然后)[，,、\s]*"
    r"|(?:帮我|给我|请|麻烦|可以)?(?:去)?(?:网上|上网|联网)(?:帮我)?"
    r"(?:查(?:一下|查)?|搜(?:索|一下)?|看(?:一下|看)?)?(?:一下)?"
    r"|(?:帮我|给我|请|麻烦|可以)?(?:查(?:一下|查)?|搜(?:索|一下)?|看(?:一下|看)?)(?:一下)?"
)
SEARCH_PREFIX_RE = re.compile(
    r"^(帮我|请|麻烦|Mio|澪|你|给我|可以)?\s*(查一下|查查|帮我查|搜一下|搜索一下|搜索|上网看看|联网看看|网上看看|看一下|看看)\s*", re.IGNORECASE
)
SEARCH_TERM_NOISE_RE = re.compile(
    r"(帮我|请|麻烦|Mio|澪|你|给我|可以|查一下|查查|搜索一下|搜索|搜一下|上网看看|联网看看|"
    r"网上看看|看一下|看看|今天|明天|现在|目前|当前|最近|最新|实时|一下|消息|新闻|情况|"
    r"怎么样|是什么|有哪些|什么时候|多少|官网|更新|版本|发布)"
)


@dataclass(frozen=True)
class WebSource:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class WebLookup:
    query: str
    sources: list[WebSource]
    error: str = ""
    engine: str = ""
    attempts: tuple[str, ...] = ()


def extract_urls(message: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.finditer(message):
        url = match.group(0).rstrip(".,;:!?)]}，。！？；：）】")
        if url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def should_use_web_lookup(message: str) -> bool:
    if not settings.web_search_enabled:
        return False
    text = message.strip()
    if not text:
        return False
    if extract_urls(text):
        return True
    if WEB_TRIGGER_RE.search(text) is not None:
        return True
    if PERSONAL_STATE_RE.search(text) is not None and WEATHER_RE.search(text) is None:
        return False
    return TIME_SENSITIVE_RE.search(text) is not None


def _mentions_weather(text: str) -> bool:
    return WEATHER_RE.search(text) is not None


def _looks_like_location_followup(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 40:
        return False
    if _mentions_weather(stripped):
        return False
    return LOCATION_HINT_RE.search(stripped) is not None


def _row_content(row: object) -> str:
    try:
        return str(row["content"])  # type: ignore[index]
    except Exception:
        if isinstance(row, dict):
            return str(row.get("content") or "")
    return ""


def build_contextual_lookup_message(message: str, recent_messages: list[object]) -> str:
    if should_use_web_lookup(message):
        return message
    if not _looks_like_location_followup(message):
        return message

    recent_text = "\n".join(_row_content(row) for row in recent_messages[-8:])
    if _mentions_weather(recent_text):
        location = _extract_weather_location(message)
        if location:
            return f"{location} 今天 天气"
    return message


def extract_search_query(message: str) -> str:
    text = URL_RE.sub(" ", message).strip()
    text = SEARCH_PREFIX_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)
    text = text.strip("：:，,。！？!?. ")
    if not text:
        text = message.strip()
    return text[:160]


def _search_terms(query: str) -> list[str]:
    normalized = unquote(str(query or "")).lower()
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9._+-]{1,}", normalized)
    cjk_text = SEARCH_TERM_NOISE_RE.sub(" ", normalized)
    cjk_runs = re.findall(r"[\u3400-\u9fff]{2,}", cjk_text)
    terms: list[str] = []
    for term in [*ascii_terms, *cjk_runs]:
        if term not in terms:
            terms.append(term)
    return terms


def _source_matches_query(source: WebSource, query: str) -> bool:
    terms = _search_terms(query)
    if not terms:
        return True
    haystack = unquote(f"{source.title} {source.snippet} {source.url}").lower()
    return any(term in haystack for term in terms)


def _relevant_sources(sources: list[WebSource], query: str) -> list[WebSource]:
    return [source for source in sources if _source_matches_query(source, query)]


def _extract_weather_location(message: str) -> str:
    text = extract_search_query(message)
    clauses = [
        item.strip()
        for item in re.split(r"[，,。！？!?；;\n]+", text)
        if item.strip()
    ]
    weather_clauses = [item for item in clauses if _mentions_weather(item)]
    if weather_clauses:
        text = weather_clauses[-1]
    text = WEATHER_LOOKUP_NOISE_RE.sub(" ", text)
    text = re.sub(r"(今天|现在|实时|最新|当前|目前|天气|气温|温度|下雨|降雨|带伞|穿什么|适合出门|热不热|冷不冷)", " ", text)
    text = re.sub(r"(我在的城市|所在城市|这个城市|这里|这边|那边|天气吧|天气怎么样|怎么样|如何|情况)", " ", text)
    text = re.sub(r"^(我在|我现在在|现在在|在)", "", text.strip())
    text = re.sub(
        "(?:"
        "\u67e5\u4e00\u4e0b|\u67e5\u67e5|\u5e2e\u6211\u67e5|\u641c\u4e00\u4e0b|\u641c\u7d22|"
        "\u4e0a\u7f51\u770b\u770b|\u8054\u7f51\u770b\u770b|\u7f51\u4e0a\u770b\u770b|\u770b\u4e00\u4e0b|\u770b\u770b|"
        "\u5e2e\u6211|\u8bf7|\u9ebb\u70e6|\u7ed9\u6211|\u53ef\u4ee5|"
        "\u6211\u5728\u7684\u57ce\u5e02|\u6240\u5728\u57ce\u5e02|\u8fd9\u4e2a\u57ce\u5e02|\u672c\u5730|\u5f53\u5730|"
        "\u57ce\u5e02|\u5730\u65b9|\u5730\u70b9|\u4f4d\u7f6e|\u4e00\u4e0b|\u7684|\u5427|\u5462|\u5417|\u5440|\u554a"
        ")",
        "",
        text,
    )
    text = re.sub(r"\s+", "", text)
    text = text.strip("：:，,。！？!?. ")
    if len(text) < 2:
        return ""
    return text[:40]


def _weather_location_candidates(location: str) -> list[str]:
    clean = re.sub(r"\s+", "", location)
    clean = clean.strip("：:，,。！？!?. ")
    if not clean:
        return []

    candidates = [clean]
    for municipality in ("重庆", "重庆市", "北京", "北京市", "上海", "上海市", "天津", "天津市"):
        if clean.startswith(municipality) and len(clean) > len(municipality):
            candidates.append(clean[len(municipality) :])
            break

    without_suffix = re.sub(r"(市|区|县)$", "", clean)
    if without_suffix and without_suffix != clean:
        candidates.append(without_suffix)

    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _weather_description(current: dict[str, object]) -> str:
    descriptions = current.get("lang_zh") or current.get("weatherDesc") or []
    if isinstance(descriptions, list) and descriptions:
        first = descriptions[0]
        if isinstance(first, dict):
            return str(first.get("value") or "")
    return ""


def _area_name(data: dict[str, object], fallback: str) -> str:
    nearest = data.get("nearest_area") or []
    if isinstance(nearest, list) and nearest:
        area = nearest[0]
        if isinstance(area, dict):
            names: list[str] = []
            for key in ("areaName", "region", "country"):
                values = area.get(key) or []
                if isinstance(values, list) and values:
                    value = values[0]
                    if isinstance(value, dict) and value.get("value"):
                        names.append(str(value["value"]))
            if names:
                return "，".join(names)
    return fallback


def _weather_number(value: object, suffix: str = "") -> str:
    if value in {None, ""}:
        return "未提供"
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return f"{value}{suffix}"
    formatted = str(int(number)) if number.is_integer() else f"{number:.1f}"
    return f"{formatted}{suffix}"


def _parse_china_weather_city_search(payload: str, location: str) -> dict[str, str] | None:
    document = payload.strip()
    if document.startswith("(") and document.endswith(")"):
        document = document[1:-1]
    try:
        rows = __import__("json").loads(document)
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None

    expected = re.sub(r"(?:省|市|区|县)$", "", location.strip())
    matches: list[tuple[int, dict[str, str]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = str(row.get("ref") or "").split("~")
        if len(fields) < 3 or not fields[0].isdigit():
            continue
        name = fields[2].strip()
        parent = fields[4].strip() if len(fields) > 4 else ""
        province = fields[9].strip() if len(fields) > 9 else ""
        normalized_name = re.sub(r"(?:省|市|区|县)$", "", name)
        score = 0
        if normalized_name == expected:
            score += 100
        elif expected and expected in normalized_name:
            score += 40
        if len(fields[0]) == 9:
            score += 20
        if parent and parent != name:
            score -= 5
        matches.append((score, {"code": fields[0], "name": name, "province": province}))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    best_score = matches[0][0]
    best = [item for score, item in matches if score == best_score]
    identities = {
        (item.get("code", ""), item.get("name", ""), item.get("province", ""))
        for item in best
    }
    if len(identities) != 1:
        return None
    return best[0]


def _parse_china_weather_index(payload: str) -> dict[str, object] | None:
    match = re.search(r"var\s+cityDZ\s*=\s*(\{.*?\});\s*var\s+", payload, flags=re.S)
    if match is None:
        return None
    try:
        data = __import__("json").loads(match.group(1))
    except (TypeError, ValueError):
        return None
    weather = data.get("weatherinfo") if isinstance(data, dict) else None
    return weather if isinstance(weather, dict) else None


async def _fetch_weather_china(location: str) -> WebSource | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.weather.com.cn/",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(
        timeout=settings.web_search_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        city_response = await client.get(
            "https://toy1.weather.com.cn/search",
            params={"cityname": location},
        )
        city_response.raise_for_status()
        city = _parse_china_weather_city_search(city_response.text, location)
        if city is None:
            return None
        weather_response = await client.get(
            f"https://d1.weather.com.cn/weather_index/{city['code']}.html"
        )
        weather_response.raise_for_status()
        current = _parse_china_weather_index(weather_response.text)
        if current is None:
            return None

    area = "，".join(item for item in (city.get("name"), city.get("province")) if item)
    parts = [
        f"地点：{area or location}",
        f"当前：{current.get('weather') or '未说明'}，"
        f"{_weather_number(current.get('temp'), '°C')}",
    ]
    if current.get("tempn") not in {None, ""}:
        parts.append(
            f"今日温度：{_weather_number(current.get('tempn'), '°C')} - "
            f"{_weather_number(current.get('temp'), '°C')}"
        )
    wind = "".join(
        str(current.get(key) or "") for key in ("wd", "ws")
    ).strip()
    if wind:
        parts.append(f"风力：{wind}")
    if current.get("fctime"):
        parts.append(f"发布时间：{current['fctime']}")
    return WebSource(
        title=f"{city.get('name') or location} 天气",
        url=f"https://www.weather.com.cn/weather/{city['code']}.shtml",
        snippet="；".join(parts),
    )


async def _resolve_china_weather_city(location: str) -> dict[str, str] | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.weather.com.cn/",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(
        timeout=settings.web_search_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get(
            "https://toy1.weather.com.cn/search",
            params={"cityname": location},
        )
        response.raise_for_status()
    return _parse_china_weather_city_search(response.text, location)


def _qualified_weather_candidates(location: str, city: dict[str, str] | None) -> list[str]:
    if not city:
        return []
    name = str(city.get("name") or location).strip()
    province = str(city.get("province") or "").strip()
    if not name or not province:
        return []

    municipality = re.sub(r"市$", "", province)
    candidates: list[str]
    if municipality in {"重庆", "北京", "上海", "天津"}:
        district = name if re.search(r"(?:区|县)$", name) else f"{name}区"
        candidates = [f"{municipality}市{district}", f"{municipality}{name}", district]
    else:
        candidates = [f"{province}{name}"]

    existing = set(_weather_location_candidates(location))
    return [candidate for candidate in candidates if candidate and candidate not in existing]


def _location_name_matches(query: str, *names: object) -> bool:
    expected = re.sub(r"(?:省|市|区|县)$", "", query.strip())
    if not expected:
        return False
    for value in names:
        normalized = re.sub(r"(?:省|市|区|县)$", "", str(value or "").strip())
        if normalized and (expected in normalized or normalized in expected):
            return True
    return False


async def _fetch_weather_cn_api(location: str) -> WebSource | None:
    headers = {
        "User-Agent": "MioAgent/0.5.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(
        timeout=settings.web_search_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get(
            "https://uapis.cn/api/v1/misc/weather",
            params={"city": location},
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict) or not _location_name_matches(
        location,
        data.get("district"),
        data.get("city"),
        data.get("province"),
    ):
        return None
    area = "，".join(
        dict.fromkeys(
            str(data.get(key) or "").strip()
            for key in ("district", "city", "province")
            if data.get(key)
        )
    )
    parts = [
        f"地点：{area or location}",
        f"当前：{data.get('weather') or '未说明'}，"
        f"{_weather_number(data.get('temperature'), '°C')}",
        f"湿度：{_weather_number(data.get('humidity'), '%')}，"
        f"风力：{data.get('wind_direction') or ''}{data.get('wind_power') or ''}",
    ]
    if data.get("report_time"):
        parts.append(f"发布时间：{data['report_time']}")
    return WebSource(
        title=f"{data.get('district') or location} 天气",
        url="https://uapis.cn/api/v1/misc/weather",
        snippet="；".join(parts),
    )


async def _weather_lookup(message: str) -> WebLookup | None:
    if not _mentions_weather(message):
        return None
    location = _extract_weather_location(message)
    if not location:
        return WebLookup(query=message, sources=[], error="缺少城市或地区名")

    attempts: list[str] = []
    candidates = _weather_location_candidates(location)
    resolved = False
    candidate_index = 0
    while candidate_index < len(candidates):
        candidate = candidates[candidate_index]
        candidate_index += 1
        for engine, fetch in (
            ("国内中文天气", _fetch_weather_cn_api),
            ("中国天气网", _fetch_weather_china),
        ):
            try:
                source = await fetch(candidate)
            except Exception as exc:
                attempts.append(f"{engine}：{type(exc).__name__} {str(exc)[:120]}".strip())
                continue
            if source is not None:
                attempts.append(f"{engine}：返回实时天气")
                return WebLookup(
                    query=f"{candidate} 天气",
                    sources=[source],
                    engine=engine,
                    attempts=tuple(attempts),
                )
            attempts.append(f"{engine}：没有查询到地点")
        if not resolved and candidate_index >= len(candidates):
            resolved = True
            try:
                city = await _resolve_china_weather_city(location)
            except Exception as exc:
                attempts.append(f"地点消歧：{type(exc).__name__} {str(exc)[:120]}".strip())
                city = None
            expanded = _qualified_weather_candidates(location, city)
            if expanded:
                attempts.append(f"地点消歧：{location} -> {' / '.join(expanded)}")
                candidates.extend(expanded)
            elif city is None:
                attempts.append("地点消歧：没有找到可信的上级行政区")
    return WebLookup(
        query=f"{location} 天气",
        sources=[],
        error="；".join(attempts) or "没有查询到这个地点的天气",
        attempts=tuple(attempts),
    )


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean_text(text)


def _is_blocked_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return True
    if parsed.username or parsed.password:
        return True
    try:
        parsed.port
    except ValueError:
        return True
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


async def _validate_public_url(url: str) -> None:
    if _is_blocked_url(url):
        raise ValueError("链接不是可访问的公网 HTTP 地址")
    parsed = urlparse(url)
    host = str(parsed.hostname or "")
    port = int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError(f"链接域名无法解析：{host}") from exc
    addresses = {
        str(record[4][0]).split("%", 1)[0]
        for record in records
        if record and len(record) > 4 and record[4]
    }
    if not addresses:
        raise ValueError(f"链接域名没有可用地址：{host}")
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise ValueError(f"链接域名返回了无效地址：{raw_address}") from exc
        if not address.is_global:
            raise ValueError(f"链接解析到了非公网地址：{raw_address}")


def _page_response_limit_bytes() -> int:
    return max(512_000, min(2_000_000, int(settings.web_page_max_chars) * 64))


async def _read_limited_response(response: httpx.Response, limit_bytes: int) -> bytes:
    content_length = str(response.headers.get("content-length") or "").strip()
    if content_length:
        try:
            if int(content_length) > limit_bytes:
                raise ValueError(f"网页响应超过 {limit_bytes // 1024}KB 上限")
        except ValueError as exc:
            if "超过" in str(exc):
                raise
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit_bytes:
            raise ValueError(f"网页响应超过 {limit_bytes // 1024}KB 上限")
        chunks.append(chunk)
    return b"".join(chunks)


class _PageParser(HTMLParser):
    TEXT_TAGS = {"p", "h1", "h2", "h3", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.text_parts: list[str] = []
        self._current_tag = ""
        self._buffer: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return

        if tag == "meta":
            attrs_map = {key.lower(): value or "" for key, value in attrs}
            name = attrs_map.get("name", "").lower() or attrs_map.get("property", "").lower()
            if name in {"description", "og:description", "twitter:description"} and attrs_map.get("content"):
                self.description = _clean_text(attrs_map["content"])
            return

        if self._skip_depth:
            return

        if tag == "title" or tag in self.TEXT_TAGS:
            self._current_tag = tag
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._current_tag:
            return
        self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag != self._current_tag:
            return

        text = _clean_text("".join(self._buffer))
        if text:
            if tag == "title" and not self.title:
                self.title = text
            elif tag in self.TEXT_TAGS and len(text) >= 12:
                self.text_parts.append(text)
        self._current_tag = ""
        self._buffer = []


def _dedupe_sources(sources: list[WebSource], limit: int) -> list[WebSource]:
    deduped: list[WebSource] = []
    seen: set[str] = set()
    for source in sources:
        if not source.url or source.url in seen:
            continue
        deduped.append(source)
        seen.add(source.url)
        if len(deduped) >= limit:
            break
    return deduped


def _parse_domestic_search_results(html: str, origin: str) -> list[WebSource]:
    sources: list[WebSource] = []
    for block in re.findall(r"<h3\b[^>]*>(.*?)</h3>", html, flags=re.I | re.S):
        anchor = re.search(r"<a\b([^>]*)>(.*?)</a>", block, flags=re.I | re.S)
        if anchor is None:
            continue
        attrs = anchor.group(1)
        href_match = re.search(r'\bhref=["\']([^"\']+)', attrs, flags=re.I)
        direct_match = re.search(r'\bdata-mdurl=["\']([^"\']+)', attrs, flags=re.I)
        href = unescape((direct_match or href_match).group(1)) if (direct_match or href_match) else ""
        if not href or href.startswith(("javascript:", "#")):
            continue
        url = urljoin(origin, href)
        title = _clean_text(unescape(re.sub(r"<[^>]+>", "", anchor.group(2))))
        if title and url.startswith(("http://", "https://")):
            sources.append(WebSource(title=title, url=url))
    return sources


async def _search_sogou(query: str) -> list[WebSource]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(
        timeout=settings.web_search_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get("https://www.sogou.com/web", params={"query": query})
        response.raise_for_status()
    return _dedupe_sources(
        _parse_domestic_search_results(response.text, "https://www.sogou.com"),
        settings.web_search_max_results,
    )


async def _search_360(query: str) -> list[WebSource]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(
        timeout=settings.web_search_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get("https://www.so.com/s", params={"q": query})
        response.raise_for_status()
    return _dedupe_sources(
        _parse_domestic_search_results(response.text, "https://www.so.com"),
        settings.web_search_max_results,
    )


async def _search_web(query: str) -> tuple[list[WebSource], str, tuple[str, ...]]:
    attempts: list[str] = []
    for engine, search in (("搜狗", _search_sogou), ("360搜索", _search_360)):
        try:
            sources = await search(query)
        except Exception as exc:
            attempts.append(f"{engine}：{type(exc).__name__} {str(exc)[:120]}".strip())
            continue
        if sources:
            relevant = _relevant_sources(sources, query)
            if relevant:
                attempts.append(f"{engine}：返回 {len(relevant)} 条相关结果")
                return relevant, engine, tuple(attempts)
            attempts.append(f"{engine}：解析到 {len(sources)} 条，但与查询词不相关")
            continue
        attempts.append(f"{engine}：没有解析到结果")
    return [], "", tuple(attempts)


async def _fetch_page(url: str) -> WebSource:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(
        timeout=settings.web_search_timeout_seconds,
        follow_redirects=False,
        headers=headers,
    ) as client:
        current_url = url
        for redirect_count in range(6):
            await _validate_public_url(current_url)
            async with client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = str(response.headers.get("location") or "").strip()
                    if not location:
                        raise ValueError("网页重定向缺少目标地址")
                    if redirect_count >= 5:
                        raise ValueError("网页重定向次数超过 5 次")
                    current_url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                final_url = str(response.url)
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    title = urlparse(final_url).netloc or final_url
                    return WebSource(title=title, url=final_url, snippet=f"页面类型：{content_type or '未知'}")
                body = await _read_limited_response(response, _page_response_limit_bytes())
                encoding = response.charset_encoding or "utf-8"
                html = body.decode(encoding, errors="replace")
                parser = _PageParser()
                parser.feed(html[: settings.web_page_max_chars * 12])
                title = parser.title or urlparse(final_url).netloc or final_url
                snippet_parts = [parser.description] if parser.description else []
                snippet_parts.extend(parser.text_parts[:8])
                snippet = _clean_text(" ".join(snippet_parts))[: settings.web_page_max_chars]
                return WebSource(title=title[:120], url=final_url, snippet=snippet)
    raise ValueError("网页重定向没有得到最终响应")


async def perform_web_lookup(message: str) -> WebLookup | None:
    if not should_use_web_lookup(message):
        return None

    urls = extract_urls(message)
    query = extract_search_query(message)
    try:
        weather_lookup = await _weather_lookup(message)
        if weather_lookup is not None:
            return weather_lookup

        if urls:
            sources: list[WebSource] = []
            for url in urls[: settings.web_search_max_results]:
                if _is_blocked_url(url):
                    continue
                sources.append(await _fetch_page(url))
            return WebLookup(query=query or "用户提供的链接", sources=sources)

        sources, engine, attempts = await _search_web(query)
        error = "" if sources else "；".join(attempts) or "所有搜索源都没有返回结果"
        return WebLookup(query=query, sources=sources, error=error, engine=engine, attempts=attempts)
    except Exception as exc:
        return WebLookup(query=query, sources=[], error=str(exc)[:240])


async def lookup_web_query(query: str) -> WebLookup:
    """Run a deliberate read-only lookup without requiring conversational trigger words."""
    normalized = extract_search_query(query)
    if not settings.web_search_enabled:
        return WebLookup(query=normalized, sources=[], error="联网搜索已关闭")
    if not normalized:
        return WebLookup(query="", sources=[], error="查询词为空")
    try:
        weather_lookup = await _weather_lookup(normalized)
        if weather_lookup is not None:
            return weather_lookup
        sources, engine, attempts = await _search_web(normalized)
        return WebLookup(
            query=normalized,
            sources=sources,
            engine=engine,
            attempts=attempts,
            error="" if sources else "；".join(attempts) or "所有搜索源都没有返回结果",
        )
    except Exception as exc:
        return WebLookup(query=normalized, sources=[], error=str(exc)[:240])


def build_web_context_message(lookup: WebLookup) -> str:
    if lookup.error == "\u7f3a\u5c11\u57ce\u5e02\u6216\u5730\u533a\u540d":
        return (
            "\u672c\u8f6e\u95ee\u9898\u9700\u8981\u5929\u6c14\u7b49\u5b9e\u65f6\u4fe1\u606f\uff0c\u4f46\u7528\u6237\u6ca1\u6709\u7ed9\u51fa\u660e\u786e\u57ce\u5e02\u6216\u5730\u533a\u540d\u3002\n"
            "\u4e0d\u8981\u731c\u6d4b\u5730\u70b9\uff0c\u4e0d\u8981\u8bf4\u81ea\u5df1\u4e0d\u80fd\u8054\u7f51\u6216\u4e0d\u80fd\u5b9e\u65f6\u67e5\u8be2\u3002\n"
            "\u7528 QQ \u77ed\u6d88\u606f\u7684\u53e3\u543b\uff0c\u8f7b\u8f7b\u8ffd\u95ee\u4ed6\u73b0\u5728\u5728\u54ea\u4e2a\u57ce\u5e02\u6216\u533a\u53bf\u3002"
        )

    if lookup.error:
        return (
            f"本轮问题需要外部或实时信息，但查询失败：{lookup.error}\n"
            "如果回答依赖最新信息，请直接说明现在没查到，不要编造。"
        )

    if not lookup.sources:
        return (
            f"本轮问题需要外部或实时信息。查询词：{lookup.query}\n"
            "没有拿到可用搜索结果。如果回答依赖最新信息，请说明没查到，不要编造。"
        )

    lines = [
        "本轮问题需要外部或实时信息，已联网查询。",
        f"查询词：{lookup.query}",
        "你已经拿到了下面的联网结果。不要说自己不能联网、不能实时查询或需要用户截图。",
        "只把下面结果当作外部来源；不要编造未出现的来源。",
        "用户不想在聊天里看到来源链接，不要列链接或来源清单。",
        "",
        "联网结果：",
    ]
    for index, source in enumerate(lookup.sources, start=1):
        snippet = source.snippet or "无摘要"
        lines.append(f"{index}. 标题：{source.title}\n   链接：{source.url}\n   摘要：{snippet}")
    return "\n".join(lines)
