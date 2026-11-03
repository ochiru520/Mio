"""Public model and voice operations use explicit targets and reviewed plans."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import model_management as routes


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_inventory_is_read_only_and_models_are_independent(client):
    with patch.object(routes.vision_models, 'inventory', return_value={'models': [
        {'id': 'qwen2.5vl:3b', 'selected': True}, {'id': 'qwen3.5:2b', 'selected': False},
    ]}), patch.object(routes.vision_models, 'select') as select:
        response = client.get('/api/model-management/vision/models')
    assert response.status_code == 200
    assert len(response.json()['models']) == 2
    select.assert_not_called()


@pytest.mark.parametrize('endpoint,operation', [
    ('download', 'start_download'), ('verify', 'verify'), ('select', 'select'),
    ('remove-preview', 'removal_preview'),
])
def test_vision_routes_forward_explicit_model(client, endpoint, operation):
    with patch.object(routes.vision_models, operation, return_value={'ok': True}) as call:
        response = client.post('/api/model-management/vision/models/' + endpoint,
                               json={'model_id': 'qwen2.5vl:3b'})
    assert response.status_code == 200
    call.assert_called_once_with('qwen2.5vl:3b')


def test_deleting_model_requires_review_token(client):
    with patch.object(routes.vision_models, 'remove', return_value={'ok': True}) as remove:
        invalid = client.post('/api/model-management/vision/models/remove', json={'model_id': 'qwen2.5vl:3b'})
        assert invalid.status_code == 422
        remove.assert_not_called()
        result = client.post('/api/model-management/vision/models/remove',
                             json={'model_id': 'qwen2.5vl:3b', 'token': 'a' * 64})
    assert result.status_code == 200
    remove.assert_called_once_with('qwen2.5vl:3b', 'a' * 64)


def test_voice_switch_preserves_engine_target_and_failure(client):
    with patch.object(routes.voice_assets, 'select', side_effect=ValueError('这个音色需要先转换')) as select:
        response = client.post('/api/model-management/voices/select', json={'profile_id': 'my-voice', 'engine': 'aniflive'})
    assert response.status_code == 409
    assert '先转换' in response.json()['detail']
    select.assert_called_once_with('my-voice', 'aniflive')
    with patch.object(routes.voice_assets, 'select') as select:
        response = client.post('/api/model-management/voices/select', json={'profile_id': 'my-voice', 'engine': 'unknown'})
        assert response.status_code == 422
        select.assert_not_called()


def test_model_download_cancel_is_separate_from_component_uninstall(client):
    with patch.object(routes.vision_models, 'cancel_download', return_value={'active': False}) as cancel:
        assert client.post('/api/model-management/vision/models/cancel-download').status_code == 200
    cancel.assert_called_once_with()


def test_unexpected_operation_error_does_not_expose_local_paths(client):
    with patch.object(routes.vision_models, 'verify', side_effect=OSError('PRIVATE PATH')):
        result = client.post('/api/model-management/vision/models/verify', json={'model_id': 'qwen3.5:2b'})
    assert result.status_code == 500
    assert 'PRIVATE PATH' not in result.text
