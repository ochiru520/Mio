from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import settings
from .secret_store import protect_secret, unprotect_secret


MODEL_REGISTRY_SCHEMA_VERSION = 4
_RESPONSES_ONLY_HOSTS = {"aihub.top", "chat.ekti.cc"}
SUPPORTED_API_MODES = {"auto", "chat_completions", "responses", "messages"}


@dataclass(frozen=True)
class ModelProfile:
    id: str
    provider_name: str
    display_name: str
    model: str
    base_urls: tuple[str, ...]
    api_key: str
    api_key_error: str = ""
    api_mode: str = "chat_completions"
    provider_id: str = ""
    family_name: str = ""
    variant_name: str = ""
    supports_vision: bool = False
    cached_input_price_cny_per_million: float = 0.0
    input_price_cny_per_million: float = 0.0
    output_price_cny_per_million: float = 0.0
    pricing_source: str = "unconfigured"
    provider_kind: str = "relay"
    provider_protocol: str = "openai"
    auth_scheme: str = "bearer"
    supports_tool_calls: bool = False
    supports_structured_output: bool = True
    context_window_tokens: int = 32768
    privacy_location: str = "external_provider"
    is_default: bool = False
    is_custom: bool = False


MODEL_CATALOG: dict[str, dict[str, object]] = {
    "deepseek-v4-flash": {
        "provider_name": "DeepSeek",
        "family_name": "DeepSeek V4",
        "variant_name": "Flash",
        "display_name": "DeepSeek V4 · Flash",
        "cached_input_price_cny_per_million": 0.02,
        "input_price_cny_per_million": 1.0,
        "output_price_cny_per_million": 2.0,
        "pricing_source": "official_catalog",
    },
    "deepseek-v4-pro": {
        "provider_name": "DeepSeek",
        "family_name": "DeepSeek V4",
        "variant_name": "Pro",
        "display_name": "DeepSeek V4 · Pro",
        "cached_input_price_cny_per_million": 0.025,
        "input_price_cny_per_million": 3.0,
        "output_price_cny_per_million": 6.0,
        "pricing_source": "official_catalog",
    },
}


def model_catalog_entry(model: str) -> dict[str, object]:
    return dict(MODEL_CATALOG.get(model.strip().lower(), {}))


def suggest_model_metadata(model: str) -> dict[str, str]:
    clean = model.strip()
    catalog = model_catalog_entry(clean)
    if catalog:
        return {
            "family_name": str(catalog["family_name"]),
            "variant_name": str(catalog["variant_name"]),
            "display_name": clean,
        }

    match = re.match(r"^(.*?)[-_](flash|pro|sol|luna)$", clean, flags=re.IGNORECASE)
    if match:
        family_raw, variant_raw = match.groups()
        family = family_raw.replace("_", "-")
        family = re.sub(r"^deepseek", "DeepSeek", family, flags=re.IGNORECASE)
        family = re.sub(r"^gpt", "GPT", family, flags=re.IGNORECASE)
        family = re.sub(r"-v(?=\d)", " V", family, flags=re.IGNORECASE)
        variant = variant_raw.capitalize()
        return {
            "family_name": family,
            "variant_name": variant,
            "display_name": clean,
        }
    return {"family_name": clean, "variant_name": "", "display_name": clean}


def _legacy_inferred_display_name(model: str) -> str:
    clean = model.strip()
    catalog = model_catalog_entry(clean)
    if catalog:
        return str(catalog["display_name"])
    match = re.match(r"^(.*?)[-_](flash|pro|sol|luna)$", clean, flags=re.IGNORECASE)
    if not match:
        return clean
    family_raw, variant_raw = match.groups()
    family = family_raw.replace("_", "-")
    family = re.sub(r"^deepseek", "DeepSeek", family, flags=re.IGNORECASE)
    family = re.sub(r"^gpt", "GPT", family, flags=re.IGNORECASE)
    family = re.sub(r"-v(?=\d)", " V", family, flags=re.IGNORECASE)
    return f"{family} · {variant_raw.capitalize()}"


def model_reasoning_config(model: str) -> dict[str, object]:
    lower = model.strip().lower()
    if any(marker in lower for marker in ("gpt-5", "o1", "o3", "o4")):
        return {
            "parameter": "reasoning_effort",
            "default": "medium",
            "options": [
                {"id": "low", "label": "低", "description": "较少推理，更快回复"},
                {"id": "medium", "label": "中", "description": "模型默认的平衡强度"},
                {"id": "high", "label": "高", "description": "增加推理和检查"},
            ],
        }
    if "deepseek" in lower:
        low_description = "较少推理，优先响应速度"
        if "pro" in lower:
            low_description = "请求 low；DeepSeek V4 Pro 当前会映射为 high"
        # Flash 定位快速响应，默认低思考；Pro 保持 high 深度思考。
        default_thinking = "high" if "pro" in lower else "low"
        return {
            "parameter": "deepseek_thinking",
            "default": default_thinking,
            "options": [
                {"id": "off", "label": "关闭", "description": "关闭 DeepSeek 思考模式"},
                {"id": "low", "label": "低", "description": low_description},
                {"id": "high", "label": "高", "description": "DeepSeek 默认思考强度"},
                {"id": "max", "label": "最大", "description": "使用最大思考强度"},
            ],
        }
    return {
        "parameter": "default",
        "default": "default",
        "options": [{"id": "default", "label": "模型默认", "description": "由模型自行决定"}],
    }


def normalize_model_reasoning(model: str, requested: str) -> str:
    config = model_reasoning_config(model)
    option_ids = {str(option["id"]) for option in config["options"]}
    legacy = {"fast": "low", "standard": "medium", "deep": "high"}
    normalized = legacy.get(requested, requested)
    if str(config["parameter"]) == "deepseek_thinking" and normalized in {"medium", "thinking"}:
        normalized = "high"
    return normalized if normalized in option_ids else str(config["default"])


def _pricing_values(record: dict[str, object], model: str) -> tuple[float, float, float, str]:
    catalog = model_catalog_entry(model)
    cached = max(0.0, float(record.get("cached_input_price_cny_per_million") or 0))
    input_price = max(0.0, float(record.get("input_price_cny_per_million") or 0))
    output_price = max(0.0, float(record.get("output_price_cny_per_million") or 0))
    manually_configured = cached > 0 or input_price > 0 or output_price > 0
    saved_source = str(record.get("pricing_source") or "").strip()
    if not manually_configured and catalog:
        return (
            float(catalog.get("cached_input_price_cny_per_million") or 0),
            float(catalog.get("input_price_cny_per_million") or 0),
            float(catalog.get("output_price_cny_per_million") or 0),
            str(catalog.get("pricing_source") or "official_catalog"),
        )
    if manually_configured and saved_source in {"official_catalog", "provider_catalog", "manual"}:
        return cached, input_price, output_price, saved_source
    return cached, input_price, output_price, "manual" if manually_configured else "unconfigured"


def _inferred_vision_support(model: str) -> bool:
    if model in settings.openai_vision_models:
        return True
    lower = model.lower()
    return any(marker in lower for marker in ("gpt-4o", "gpt-4.1", "gpt-5", "vision", "gemini", "claude"))


def normalize_provider_api_mode(value: object, *, base_url: str = "") -> str:
    mode = str(value or "auto").strip().lower() or "auto"
    if mode not in SUPPORTED_API_MODES:
        raise ValueError("接口模式必须是自动、Chat Completions 或 Responses。")
    if mode != "auto":
        return mode
    try:
        hostname = (urlsplit(str(base_url or "").strip()).hostname or "").lower()
    except ValueError:
        hostname = ""
    return "responses" if hostname in _RESPONSES_ONLY_HOSTS else "auto"


def provider_model_api_mode(
    provider_protocol: str,
    model: str,
    *,
    base_url: str = "",
    default_api_mode: str = "auto",
) -> str:
    protocol = str(provider_protocol or "openai").strip().lower()
    lower = str(model or "").strip().lower()
    configured_mode = normalize_provider_api_mode(default_api_mode, base_url=base_url)
    if configured_mode != "auto":
        return configured_mode
    if protocol != "opencode_go":
        return "chat_completions"
    if "claude" in lower:
        return "messages"
    if lower.startswith("gpt-") or "grok" in lower or "luna" in lower:
        return "responses"
    return "chat_completions"


def _stable_id(prefix: str, *parts: object) -> str:
    normalized = "\x1f".join(str(part or "").strip().casefold() for part in parts)
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]}"


def _provider_id(
    provider_name: str,
    provider_kind: str,
    provider_protocol: str,
    base_urls: tuple[str, ...] | list[str],
    *,
    origin: str,
) -> str:
    return _stable_id(
        f"provider-{origin}",
        provider_name,
        provider_kind,
        provider_protocol,
        *(str(url).strip().rstrip("/") for url in base_urls),
    )


def _model_id(provider_id: str, provider_model: str) -> str:
    return _stable_id("model", provider_id, provider_model)


def _default_profiles() -> list[ModelProfile]:
    profiles: list[ModelProfile] = []
    models = list(settings.openai_models)
    if any("api.deepseek.com" in url.lower() for url in settings.openai_base_urls):
        for model in MODEL_CATALOG:
            if model not in models:
                models.append(model)
    provider_kind = "official" if any(
        host in url.lower()
        for url in settings.openai_base_urls
        for host in ("api.openai.com", "api.deepseek.com")
    ) else "relay"
    provider_protocol = "deepseek" if any(
        "api.deepseek.com" in url.lower() for url in settings.openai_base_urls
    ) else "openai"
    for model in models:
        catalog = model_catalog_entry(model)
        metadata = suggest_model_metadata(model)
        provider = str(catalog.get("provider_name") or ("DeepSeek" if "deepseek" in model.lower() else "当前供应商"))
        stable_provider_id = _provider_id(
            provider,
            provider_kind,
            provider_protocol,
            settings.openai_base_urls,
            origin="builtin",
        )
        configured_prices = {
            "cached_input_price_cny_per_million": 0.0,
            "input_price_cny_per_million": settings.openai_input_price_cny_per_million,
            "output_price_cny_per_million": settings.openai_output_price_cny_per_million,
        }
        cached_price, input_price, output_price, pricing_source = _pricing_values(configured_prices, model)
        profiles.append(
            ModelProfile(
                id=model,
                provider_id=stable_provider_id,
                provider_name=provider,
                display_name=metadata["display_name"],
                model=model,
                base_urls=settings.openai_base_urls,
                api_key=settings.openai_api_key,
                family_name=metadata["family_name"],
                variant_name=metadata["variant_name"],
                supports_vision=_inferred_vision_support(model),
                cached_input_price_cny_per_million=cached_price,
                input_price_cny_per_million=input_price,
                output_price_cny_per_million=output_price,
                pricing_source=pricing_source,
                provider_kind=provider_kind,
                provider_protocol=provider_protocol,
                supports_tool_calls=provider_kind == "official",
                is_default=model == settings.openai_model,
            )
        )
    return profiles


def _empty_registry() -> dict[str, object]:
    return {
        "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
        "providers": [],
        "models": [],
        "hidden_default_provider_ids": [],
    }


def _restored_legacy_display_name(record: dict[str, object], model: str) -> str:
    saved = str(record.get("display_name") or model).strip() or model
    inferred = _legacy_inferred_display_name(model)
    metadata = suggest_model_metadata(model)
    if (
        inferred != model
        and saved == inferred
        and str(record.get("family_name") or metadata["family_name"]).strip() == metadata["family_name"]
        and str(record.get("variant_name") or metadata["variant_name"]).strip() == metadata["variant_name"]
    ):
        return model
    return saved


def _write_registry(document: dict[str, object]) -> None:
    settings.model_profiles_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.model_profiles_path.with_suffix(settings.model_profiles_path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(settings.model_profiles_path)


def _legacy_registry(records: list[dict[str, object]]) -> dict[str, object]:
    document = _empty_registry()
    providers: list[dict[str, object]] = []
    models: list[dict[str, object]] = []
    provider_indexes: dict[str, int] = {}
    hidden_ids: set[str] = set()
    default_profiles = _default_profiles()

    for record in records:
        if str(record.get("record_type") or "") != "hidden_default_provider":
            continue
        provider_name = str(record.get("provider_name") or "").strip()
        hidden_ids.update(
            profile.provider_id for profile in default_profiles if profile.provider_name == provider_name
        )

    for record in records:
        if str(record.get("record_type") or ""):
            continue
        provider_name = str(record.get("provider_name") or "自定义供应商").strip() or "自定义供应商"
        provider_kind = str(record.get("provider_kind") or "relay").strip().lower() or "relay"
        provider_protocol = str(record.get("provider_protocol") or "openai").strip().lower() or "openai"
        base_url = str(record.get("base_url") or "").strip().rstrip("/")
        stable_provider_id = _provider_id(
            provider_name,
            provider_kind,
            provider_protocol,
            (base_url,),
            origin="custom",
        )
        protected_key = str(record.get("api_key_protected") or "").strip()
        plain_key = str(record.get("api_key") or "").strip()
        if not protected_key and plain_key:
            protected_key = protect_secret(plain_key)
        if stable_provider_id not in provider_indexes:
            provider_indexes[stable_provider_id] = len(providers)
            providers.append({
                "id": stable_provider_id,
                "display_name": provider_name,
                "provider_kind": provider_kind,
                "provider_protocol": provider_protocol,
                "base_url": base_url,
                "api_key_protected": protected_key,
                "auth_scheme": str(record.get("auth_scheme") or "auto").strip().lower() or "auto",
                "default_api_mode": normalize_provider_api_mode(
                    record.get("default_api_mode"),
                    base_url=base_url,
                ),
            })
        model = str(record.get("model") or "").strip()
        profile_id = str(record.get("id") or "").strip() or _model_id(stable_provider_id, model)
        models.append({
            "id": profile_id,
            "provider_id": stable_provider_id,
            "model": model,
            # Undo only the exact formatting produced by the old inference code.
            # Any genuinely custom label remains untouched.
            "display_name": _restored_legacy_display_name(record, model),
            "family_name": str(record.get("family_name") or model).strip() or model,
            "variant_name": str(record.get("variant_name") or "").strip(),
            "supports_vision": bool(record.get("supports_vision")),
            "supports_tool_calls": bool(record.get("supports_tool_calls")),
            "supports_structured_output": bool(record.get("supports_structured_output", True)),
            "context_window_tokens": max(4096, int(record.get("context_window_tokens") or 32768)),
            "privacy_location": str(record.get("privacy_location") or "external_provider"),
            "api_mode": str(
                record.get("api_mode")
                or provider_model_api_mode(
                    provider_protocol,
                    model,
                    base_url=base_url,
                    default_api_mode=record.get("default_api_mode") or "auto",
                )
            ).strip().lower(),
            "pricing_source": str(record.get("pricing_source") or "").strip(),
            "cached_input_price_cny_per_million": record.get("cached_input_price_cny_per_million") or 0,
            "input_price_cny_per_million": record.get("input_price_cny_per_million") or 0,
            "output_price_cny_per_million": record.get("output_price_cny_per_million") or 0,
        })

    document["providers"] = providers
    document["models"] = models
    document["hidden_default_provider_ids"] = sorted(hidden_ids)
    return document


def _normalize_registry(document: dict[str, object]) -> tuple[dict[str, object], bool]:
    normalized = _empty_registry()
    source_schema_version = int(document.get("schema_version") or 0)
    changed = source_schema_version != MODEL_REGISTRY_SCHEMA_VERSION
    providers = [dict(item) for item in document.get("providers", []) if isinstance(item, dict)]
    models = [dict(item) for item in document.get("models", []) if isinstance(item, dict)]
    hidden = [str(item).strip() for item in document.get("hidden_default_provider_ids", []) if str(item).strip()]
    for provider in providers:
        plain_key = str(provider.get("api_key") or "").strip()
        if plain_key:
            provider["api_key_protected"] = protect_secret(plain_key)
            provider.pop("api_key", None)
            changed = True
        base_url = str(provider.get("base_url") or "").strip().rstrip("/")
        default_api_mode = normalize_provider_api_mode(
            provider.get("default_api_mode"),
            base_url=base_url,
        )
        if default_api_mode != str(provider.get("default_api_mode") or "").strip().lower():
            provider["default_api_mode"] = default_api_mode
            changed = True
        # AIHub and Ekti publish Codex-compatible Responses routes. Older
        # registries stored auto as Chat Completions and otherwise never
        # re-evaluated the provider after an upgrade.
        try:
            hostname = (urlsplit(base_url).hostname or "").lower()
        except ValueError:
            hostname = ""
        if hostname in _RESPONSES_ONLY_HOSTS and str(provider.get("default_api_mode") or "auto").strip().lower() == "auto":
            provider["default_api_mode"] = "responses"
            changed = True
    providers_by_id = {
        str(provider.get("id") or "").strip(): provider
        for provider in providers
        if str(provider.get("id") or "").strip()
    }
    for model_record in models:
        provider_model = str(model_record.get("model") or "").strip()
        provider = providers_by_id.get(str(model_record.get("provider_id") or "").strip(), {})
        provider_protocol = str(provider.get("provider_protocol") or "openai")
        base_url = str(provider.get("base_url") or "").strip().rstrip("/")
        inferred_api_mode = provider_model_api_mode(
            provider_protocol,
            provider_model,
            base_url=base_url,
            default_api_mode=str(provider.get("default_api_mode") or "auto"),
        )
        restored_name = _restored_legacy_display_name(model_record, provider_model)
        if restored_name != str(model_record.get("display_name") or "").strip():
            model_record["display_name"] = restored_name
            changed = True
        defaults = {
            "supports_tool_calls": False,
            "supports_structured_output": True,
            "context_window_tokens": 32768,
            "privacy_location": "external_provider",
            "api_mode": inferred_api_mode,
        }
        for key, value in defaults.items():
            if key not in model_record:
                model_record[key] = value
                changed = True
        if (
            str(provider.get("default_api_mode") or "auto").strip().lower() == "responses"
            and str(model_record.get("api_mode") or "chat_completions").strip().lower() == "chat_completions"
            and provider.get("base_url")
            and (urlsplit(str(provider.get("base_url") or "")).hostname or "").lower() in _RESPONSES_ONLY_HOSTS
        ):
            model_record["api_mode"] = "responses"
            changed = True
        if (
            source_schema_version < 4
            and inferred_api_mode == "responses"
            and str(model_record.get("api_mode") or "chat_completions").strip().lower() == "chat_completions"
        ):
            model_record["api_mode"] = "responses"
            changed = True
    normalized["providers"] = providers
    normalized["models"] = models
    normalized["hidden_default_provider_ids"] = sorted(set(hidden))
    return normalized, changed


def _read_registry() -> dict[str, object]:
    path = settings.model_profiles_path
    if not path.exists():
        return _empty_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_registry()
    if isinstance(data, list):
        migrated = _legacy_registry([dict(item) for item in data if isinstance(item, dict)])
        _write_registry(migrated)
        return migrated
    if not isinstance(data, dict):
        return _empty_registry()
    normalized, changed = _normalize_registry(data)
    if changed:
        _write_registry(normalized)
    return normalized


def model_registry_snapshot() -> bytes | None:
    try:
        return settings.model_profiles_path.read_bytes()
    except FileNotFoundError:
        return None


def restore_model_registry_snapshot(snapshot: bytes | None) -> None:
    path = settings.model_profiles_path
    if snapshot is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".rollback")
    temporary.write_bytes(snapshot)
    temporary.replace(path)


def _custom_profiles() -> list[ModelProfile]:
    document = _read_registry()
    providers = {
        str(item.get("id") or "").strip(): item
        for item in document["providers"]
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    profiles: list[ModelProfile] = []
    for record in document["models"]:
        if not isinstance(record, dict):
            continue
        try:
            provider_id = str(record.get("provider_id") or "").strip()
            provider = providers.get(provider_id)
            if provider is None:
                continue
            base_url = str(provider.get("base_url") or "").strip().rstrip("/")
            model = str(record.get("model") or "").strip()
            protected_key = str(provider.get("api_key_protected") or "").strip()
            api_key_error = ""
            try:
                api_key = unprotect_secret(protected_key) if protected_key else ""
            except (OSError, RuntimeError, TypeError, ValueError):
                api_key = ""
                api_key_error = "这个密钥由另一台电脑或另一个 Windows 用户加密，请在本机重新输入。"
            profile_id = str(record.get("id") or "").strip()
            if not profile_id or not base_url or not model:
                continue
            cached_price, input_price, output_price, pricing_source = _pricing_values(record, model)
            profiles.append(
                ModelProfile(
                    id=profile_id,
                    provider_id=provider_id,
                    provider_name=str(provider.get("display_name") or "自定义供应商").strip(),
                    display_name=str(record.get("display_name") or model).strip() or model,
                    model=model,
                    base_urls=(base_url,),
                    api_key=api_key,
                    api_key_error=api_key_error,
                    api_mode=str(
                        record.get("api_mode")
                        or provider_model_api_mode(
                            str(provider.get("provider_protocol") or "openai"),
                            model,
                            base_url=base_url,
                            default_api_mode=str(provider.get("default_api_mode") or "auto"),
                        )
                    ).strip().lower(),
                    family_name=str(record.get("family_name") or model).strip() or model,
                    variant_name=str(record.get("variant_name") or "").strip(),
                    supports_vision=bool(record.get("supports_vision")),
                    cached_input_price_cny_per_million=cached_price,
                    input_price_cny_per_million=input_price,
                    output_price_cny_per_million=output_price,
                    pricing_source=pricing_source,
                    provider_kind=str(provider.get("provider_kind") or "relay").strip().lower(),
                    provider_protocol=str(provider.get("provider_protocol") or "openai").strip().lower(),
                    auth_scheme=str(provider.get("auth_scheme") or "auto").strip().lower(),
                    supports_tool_calls=bool(record.get("supports_tool_calls")),
                    supports_structured_output=bool(record.get("supports_structured_output", True)),
                    context_window_tokens=max(4096, int(record.get("context_window_tokens") or 32768)),
                    privacy_location=str(record.get("privacy_location") or "external_provider")[:40],
                    is_custom=True,
                )
            )
        except (OSError, TypeError, ValueError):
            continue
    return profiles


def hidden_default_provider_records() -> list[dict[str, str]]:
    hidden_ids = set(_read_registry()["hidden_default_provider_ids"])
    records: dict[str, dict[str, str]] = {}
    for profile in _default_profiles():
        if profile.provider_id in hidden_ids:
            records[profile.provider_id] = {
                "provider_id": profile.provider_id,
                "display_name": profile.provider_name,
            }
    return sorted(records.values(), key=lambda item: (item["display_name"].casefold(), item["provider_id"]))


def hidden_default_providers() -> list[str]:
    return [item["display_name"] for item in hidden_default_provider_records()]


def list_model_profiles() -> list[ModelProfile]:
    hidden_ids = {item["provider_id"] for item in hidden_default_provider_records()}
    profiles = [profile for profile in _default_profiles() if profile.provider_id not in hidden_ids]
    known_ids = {profile.id for profile in profiles}
    profiles.extend(profile for profile in _custom_profiles() if profile.id not in known_ids)
    return profiles


def get_model_profile(model_id: str = "") -> ModelProfile:
    requested = model_id.strip()
    profiles = list_model_profiles()
    automatic = not requested or requested == "auto"
    if automatic:
        requested = settings.openai_model
    for profile in profiles:
        if profile.id == requested:
            return profile
    if automatic and profiles:
        return profiles[0]
    raise ValueError(f"模型未配置：{requested}")


def public_model_profile(profile: ModelProfile) -> dict[str, object]:
    reasoning = model_reasoning_config(profile.model)
    return {
        "id": profile.id,
        "provider_id": profile.provider_id,
        "provider_name": profile.provider_name,
        "display_name": profile.display_name,
        "model": profile.model,
        "family_name": profile.family_name,
        "variant_name": profile.variant_name,
        "supports_vision": profile.supports_vision,
        "supports_tool_calls": profile.supports_tool_calls,
        "supports_structured_output": profile.supports_structured_output,
        "context_window_tokens": profile.context_window_tokens,
        "privacy_location": profile.privacy_location,
        "cached_input_price_cny_per_million": profile.cached_input_price_cny_per_million,
        "input_price_cny_per_million": profile.input_price_cny_per_million,
        "output_price_cny_per_million": profile.output_price_cny_per_million,
        "pricing_source": profile.pricing_source,
        "provider_kind": profile.provider_kind,
        "provider_protocol": profile.provider_protocol,
        "auth_scheme": profile.auth_scheme,
        "pricing_configured": profile.pricing_source != "unconfigured",
        "api_key_configured": bool(profile.api_key),
        "api_key_error": profile.api_key_error,
        "requires_key_reentry": bool(profile.api_key_error),
        "api_mode": profile.api_mode,
        "api_supported": profile.api_mode != "messages",
        "is_default": profile.is_default,
        "is_custom": profile.is_custom,
        "reasoning_parameter": reasoning["parameter"],
        "default_reasoning_level": reasoning["default"],
        "reasoning_options": reasoning["options"],
    }


def save_custom_provider(
    provider_record: dict[str, object],
    model_records: list[dict[str, object]],
) -> tuple[str, list[ModelProfile]]:
    provider_name = str(provider_record.get("provider_name") or "").strip()
    provider_kind = str(provider_record.get("provider_kind") or "relay").strip().lower()
    provider_protocol = str(provider_record.get("provider_protocol") or "openai").strip().lower()
    base_url = str(provider_record.get("base_url") or "").strip().rstrip("/")
    api_key = str(provider_record.get("api_key") or "").strip()
    auth_scheme = str(provider_record.get("auth_scheme") or "auto").strip().lower()
    default_api_mode = normalize_provider_api_mode(
        provider_record.get("default_api_mode"),
        base_url=base_url,
    )
    if not provider_name or not base_url or not api_key:
        raise ValueError("供应商名称、API地址和API Key不能为空。")
    if provider_kind not in {"official", "relay"}:
        raise ValueError("供应商类型必须是官方 API 或中转站。")
    if provider_protocol not in {"openai", "deepseek", "opencode_go"}:
        raise ValueError("当前只支持 OpenAI 兼容、DeepSeek 官方和 OpenCode Go 协议。")
    if auth_scheme not in {"auto", "bearer", "x-api-key", "api-key"}:
        raise ValueError("不支持这个 API 鉴权方式。")
    if not model_records:
        raise ValueError("至少选择一个模型。")

    provider_id = str(provider_record.get("provider_id") or "").strip() or _provider_id(
        provider_name,
        provider_kind,
        provider_protocol,
        (base_url,),
        origin="custom",
    )
    clean_models: list[dict[str, object]] = []
    seen_provider_models: set[str] = set()
    for record in model_records:
        model = str(record.get("model") or "").strip()
        if not model:
            raise ValueError("模型ID不能为空。")
        folded_model = model.casefold()
        if folded_model in seen_provider_models:
            raise ValueError(f"模型ID重复：{model}")
        seen_provider_models.add(folded_model)
        clean_models.append({
            "id": str(record.get("id") or "").strip(),
            "provider_id": provider_id,
            "model": model,
            # Only use a supplied label or the literal provider model name.
            "display_name": str(record.get("display_name") or model).strip() or model,
            "family_name": str(record.get("family_name") or model).strip() or model,
            "variant_name": str(record.get("variant_name") or "").strip(),
            "supports_vision": bool(record.get("supports_vision")),
            "supports_tool_calls": bool(record.get("supports_tool_calls")),
            "supports_structured_output": bool(record.get("supports_structured_output", True)),
            "context_window_tokens": max(4096, int(record.get("context_window_tokens") or 32768)),
            "privacy_location": str(record.get("privacy_location") or "external_provider")[:40],
            "api_mode": str(
                record.get("api_mode")
                or provider_model_api_mode(
                    provider_protocol,
                    model,
                    base_url=base_url,
                    default_api_mode=default_api_mode,
                )
            ).strip().lower(),
            "pricing_source": str(record.get("pricing_source") or "").strip(),
            "cached_input_price_cny_per_million": max(0.0, float(record.get("cached_input_price_cny_per_million") or 0)),
            "input_price_cny_per_million": max(0.0, float(record.get("input_price_cny_per_million") or 0)),
            "output_price_cny_per_million": max(0.0, float(record.get("output_price_cny_per_million") or 0)),
        })
        if clean_models[-1]["api_mode"] == "messages":
            raise ValueError(f"模型 {model} 使用 Anthropic /messages；当前版本尚未接入，不能保存为可用模型。")
        if clean_models[-1]["api_mode"] not in SUPPORTED_API_MODES - {"auto"}:
            raise ValueError(f"模型 {model} 的接口模式无效。")

    document = _read_registry()
    providers = [dict(item) for item in document["providers"] if isinstance(item, dict)]
    existing_provider = next(
        (item for item in providers if str(item.get("id") or "") == provider_id),
        None,
    )
    clean_provider = {
        "id": provider_id,
        "display_name": provider_name,
        "provider_kind": provider_kind,
        "provider_protocol": provider_protocol,
        "base_url": base_url,
        "api_key_protected": protect_secret(api_key),
        "auth_scheme": auth_scheme,
        "default_api_mode": default_api_mode,
    }
    if existing_provider is None:
        providers.append(clean_provider)
    else:
        providers[providers.index(existing_provider)] = clean_provider

    models = [dict(item) for item in document["models"] if isinstance(item, dict)]
    existing_by_provider_model = {
        str(item.get("model") or "").strip().casefold(): item
        for item in models
        if str(item.get("provider_id") or "") == provider_id
    }
    created_ids: list[str] = []
    for clean in clean_models:
        existing = existing_by_provider_model.get(str(clean["model"]).casefold())
        profile_id = str(clean.get("id") or "").strip()
        if not profile_id and existing is not None:
            profile_id = str(existing.get("id") or "").strip()
        clean["id"] = profile_id or _model_id(provider_id, str(clean["model"]))
        models = [item for item in models if str(item.get("id") or "") != clean["id"]]
        models.append(clean)
        created_ids.append(str(clean["id"]))

    next_document = dict(document)
    next_document["providers"] = providers
    next_document["models"] = models
    _write_registry(next_document)
    return provider_id, [get_model_profile(profile_id) for profile_id in created_ids]


def save_custom_profile(record: dict[str, object]) -> ModelProfile:
    provider_id, profiles = save_custom_provider(record, [record])
    if not profiles:
        raise ValueError(f"供应商 {provider_id} 没有保存任何模型。")
    return profiles[0]


def delete_custom_profile(profile_id: str) -> None:
    if profile_id in settings.openai_models:
        raise ValueError("不能删除由 .env 配置的默认模型。")
    document = _read_registry()
    models = [dict(item) for item in document["models"] if isinstance(item, dict)]
    deleted = next((item for item in models if str(item.get("id") or "") == profile_id), None)
    if deleted is None:
        raise ValueError("没有找到这个自定义模型。")
    models = [item for item in models if str(item.get("id") or "") != profile_id]
    provider_id = str(deleted.get("provider_id") or "")
    providers = [dict(item) for item in document["providers"] if isinstance(item, dict)]
    if not any(str(item.get("provider_id") or "") == provider_id for item in models):
        providers = [item for item in providers if str(item.get("id") or "") != provider_id]
    document["models"] = models
    document["providers"] = providers
    _write_registry(document)


def delete_provider(provider_id: str) -> list[str]:
    clean_id = str(provider_id or "").strip()
    if not clean_id:
        raise ValueError("供应商ID不能为空。")
    visible_profiles = list_model_profiles()
    matching = [profile for profile in visible_profiles if profile.provider_id == clean_id]
    if not matching:
        raise ValueError("没有找到这个供应商。")
    document = _read_registry()
    document["models"] = [
        item for item in document["models"]
        if isinstance(item, dict) and str(item.get("provider_id") or "") != clean_id
    ]
    document["providers"] = [
        item for item in document["providers"]
        if isinstance(item, dict) and str(item.get("id") or "") != clean_id
    ]
    hidden_ids = {str(item) for item in document["hidden_default_provider_ids"]}
    if any(profile.provider_id == clean_id for profile in _default_profiles()):
        hidden_ids.add(clean_id)
    document["hidden_default_provider_ids"] = sorted(hidden_ids)
    _write_registry(document)
    return [profile.id for profile in matching]


def restore_default_provider(provider_id: str) -> list[str]:
    clean_id = str(provider_id or "").strip()
    document = _read_registry()
    hidden_ids = {str(item) for item in document["hidden_default_provider_ids"]}
    if clean_id not in hidden_ids:
        raise ValueError("这个内置供应商没有被隐藏。")
    hidden_ids.remove(clean_id)
    document["hidden_default_provider_ids"] = sorted(hidden_ids)
    _write_registry(document)
    return [profile.id for profile in _default_profiles() if profile.provider_id == clean_id]
