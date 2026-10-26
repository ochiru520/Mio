from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import model_provider_service
from ..http_service_errors import service_errors_to_http
from ..model_registry import list_model_profiles, public_model_profile
from ..provider_presets import public_provider_presets


router = APIRouter()


@router.get('/models/catalog')
async def model_catalog():
    from ..model_registry import hidden_default_provider_records
    return {'models': [public_model_profile(p) for p in list_model_profiles()],
            'provider_presets': public_provider_presets(),
            'hidden_model_providers': hidden_default_provider_records()}


class ModelProfileRequest(BaseModel):
    provider_name: str
    display_name: str = ""
    family_name: str = ""
    variant_name: str = ""
    model: str
    base_url: str
    api_key: str = ""
    supports_vision: bool = True
    supports_tool_calls: bool | None = None
    supports_structured_output: bool = True
    context_window_tokens: int = Field(default=32768, ge=4096, le=10_000_000)
    context_window_source: str = Field(default='', pattern='^(|configured|provider_catalog|unverified_default)$')
    privacy_location: str = Field(default="external_provider", pattern="^(external_provider|local_device)$")
    cached_input_price_cny_per_million: float = 0.0
    input_price_cny_per_million: float = 0.0
    output_price_cny_per_million: float = 0.0
    pricing_source: str = ""
    provider_kind: str = "relay"
    provider_protocol: str = "openai"
    auth_scheme: str = "bearer"
    preset_id: str = ""
    default_api_mode: str = "auto"


class ProviderModelRequest(BaseModel):
    model: str
    display_name: str = ""
    family_name: str = ""
    variant_name: str = ""
    supports_vision: bool = True
    supports_tool_calls: bool | None = None
    supports_structured_output: bool = True
    context_window_tokens: int = Field(default=32768, ge=4096, le=10_000_000)
    context_window_source: str = Field(default='', pattern='^(|configured|provider_catalog|unverified_default)$')
    privacy_location: str = Field(default="external_provider", pattern="^(external_provider|local_device)$")
    cached_input_price_cny_per_million: float = 0.0
    input_price_cny_per_million: float = 0.0
    output_price_cny_per_million: float = 0.0
    pricing_source: str = ""
    api_mode: str = ""


class ProviderCreateRequest(BaseModel):
    provider_name: str
    base_url: str
    api_key: str = ""
    provider_kind: str = "relay"
    provider_protocol: str = "openai"
    auth_scheme: str = "auto"
    preset_id: str = ""
    default_api_mode: str = "auto"
    models: list[ProviderModelRequest] = Field(min_length=1, max_length=200)


class ModelDiscoveryRequest(BaseModel):
    provider_kind: str = "relay"
    provider_protocol: str = "openai"
    base_url: str = ""
    api_key: str = ""
    preset_id: str = ""
    default_api_mode: str = "auto"


@router.get("/models/provider-presets")
async def provider_presets():
    return {"presets": public_provider_presets()}


@router.post("/models")
@service_errors_to_http
async def create_model_profile(payload: ModelProfileRequest):
    return await model_provider_service.create_model_profile(payload.model_dump())


@router.post("/providers")
@service_errors_to_http
async def create_provider(payload: ProviderCreateRequest):
    return await model_provider_service.create_provider(payload.model_dump())


@router.post("/models/discover")
@service_errors_to_http
async def discover_models(payload: ModelDiscoveryRequest):
    return await model_provider_service.discover_models(payload.model_dump())


@router.post("/models/{profile_id}/test")
@service_errors_to_http
async def test_model_profile(profile_id: str):
    return await model_provider_service.test_model_profile(profile_id)


@router.delete("/models/{profile_id}")
@service_errors_to_http
async def remove_model_profile(profile_id: str):
    return await model_provider_service.remove_model_profile(profile_id)


@router.delete("/providers/{provider_id}")
@service_errors_to_http
async def remove_provider(provider_id: str):
    return await model_provider_service.remove_provider(provider_id)


@router.post("/providers/{provider_id}/restore")
@service_errors_to_http
async def restore_provider(provider_id: str):
    return await model_provider_service.restore_provider(provider_id)
