from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx


AUTH_SCHEMES = ("bearer", "x-api-key", "api-key")
_KNOWN_ENDPOINT_SUFFIXES = (
    "/chat/completions",
    "/models",
)


def normalize_api_base_url(base_url: str) -> str:
    clean = base_url.strip().rstrip("/")
    parsed = urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API 地址必须以 http:// 或 https:// 开头。")
    path = parsed.path.rstrip("/")
    lowered = path.lower()
    for suffix in _KNOWN_ENDPOINT_SUFFIXES:
        if lowered.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def models_endpoint_candidates(base_url: str) -> list[str]:
    clean = normalize_api_base_url(base_url)
    parsed = urlsplit(clean)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    if path:
        candidates = [f"{clean}/models"]
        if not path.lower().endswith("/v1"):
            candidates.extend((f"{clean}/v1/models", f"{origin}/v1/models"))
    else:
        candidates = [
            f"{origin}/models",
            f"{origin}/v1/models",
            f"{origin}/api/models",
            f"{origin}/api/v1/models",
            f"{origin}/openai/v1/models",
        ]
    return _dedupe(candidates)


def completion_endpoint_candidates(base_url: str) -> list[str]:
    return [
        endpoint[: -len("/models")] + "/chat/completions"
        for endpoint in models_endpoint_candidates(base_url)
    ]


def api_base_from_endpoint(endpoint: str) -> str:
    clean = endpoint.rstrip("/")
    for suffix in _KNOWN_ENDPOINT_SUFFIXES:
        if clean.lower().endswith(suffix):
            return clean[: -len(suffix)].rstrip("/")
    return normalize_api_base_url(clean)


def auth_headers(api_key: str, auth_scheme: str = "bearer") -> dict[str, str]:
    clean_key = api_key.strip()
    scheme = auth_scheme.strip().lower() or "bearer"
    if scheme == "x-api-key":
        return {"x-api-key": clean_key}
    if scheme == "api-key":
        return {"api-key": clean_key}
    return {"Authorization": f"Bearer {clean_key}"}


def auth_scheme_candidates(preferred: str = "") -> list[str]:
    clean = preferred.strip().lower()
    return _dedupe(([clean] if clean in AUTH_SCHEMES else []) + list(AUTH_SCHEMES))


def response_is_json(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        return True
    sample = response.text.lstrip()[:1]
    return sample in {"{", "["}

