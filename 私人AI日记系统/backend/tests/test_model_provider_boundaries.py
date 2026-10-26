"""Provider operations expose domain failures; HTTP adapters preserve metadata."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import model_provider_service as providers
from app.routes import models
from app.service_errors import ServiceOperationError


def test_remote_discovery_rejects_missing_key_in_service_and_http(monkeypatch):
    request = models.ModelDiscoveryRequest(base_url='https://synthetic.invalid/v1')
    catalog = AsyncMock()
    monkeypatch.setattr(providers, 'request_model_catalog', catalog)
    with pytest.raises(ServiceOperationError) as failure:
        asyncio.run(providers.discover_models(request.model_dump()))
    assert failure.value.status_code == 400
    assert failure.value.detail == '请先填写API Key。'
    app = FastAPI()
    app.include_router(models.router)
    with TestClient(app) as client:
        response = client.post('/models/discover', json=request.model_dump())
    assert response.status_code == 400
    assert response.json() == {'detail': '请先填写API Key。'}
    catalog.assert_not_awaited()


def test_provider_body_validation_keeps_nested_model_schema(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(providers, 'create_provider', create)
    app = FastAPI()
    app.include_router(models.router)
    with TestClient(app) as client:
        for body in ({}, {'provider_name': '合成供应商', 'base_url': 'https://synthetic.invalid/v1', 'models': []},
                     {'provider_name': '合成供应商', 'base_url': 'https://synthetic.invalid/v1',
                      'models': [{'model': 'fixture', 'context_window_tokens': 1}]}):
            assert client.post('/providers', json=body).status_code == 422
    create.assert_not_awaited()


def test_model_verification_keeps_structured_credential_error(monkeypatch):
    profile = SimpleNamespace(id='fixture-model', provider_id='fixture-provider',
        provider_name='合成供应商', model='fixture', api_key='unreadable',
        api_key_error='此电脑无法解密已保存的 Key。', base_urls=['https://synthetic.invalid/v1'])
    monkeypatch.setattr(providers, 'get_model_profile', lambda identifier: profile)
    catalog = AsyncMock()
    monkeypatch.setattr(providers, 'request_model_catalog', catalog)
    with pytest.raises(ServiceOperationError) as failure:
        asyncio.run(providers.test_model_profile(profile.id))
    app = FastAPI()
    app.include_router(models.router)
    with TestClient(app) as client:
        response = client.post('/models/fixture-model/test')
    assert response.status_code == failure.value.status_code == 400
    assert response.json()['detail'] == failure.value.detail
    assert response.json()['detail']['code'] == 'provider_key_requires_reentry'
    assert response.json()['detail']['requires_local_key_reentry'] is True
    assert 'unreadable' not in response.text
    catalog.assert_not_awaited()


def test_cancelled_network_probe_never_falls_back_or_records_verification(monkeypatch):
    request = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(providers, '_request_chat_endpoint', request)
    monkeypatch.setattr(providers, '_provider_client_routes', lambda base='': [('直连', ''), ('代理', '')])
    verified = AsyncMock()
    monkeypatch.setattr(providers.onboarding_service, 'record_model_verification', verified)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(providers.request_chat_probe('https://synthetic.invalid/v1', 'synthetic-key', 'fixture'))
    assert request.await_count == 1
    verified.assert_not_called()
