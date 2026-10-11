"""Settings reads must not expose stored secrets or trigger cloud synthesis."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import companion_service
from app.routes.companion import router
from test_runtime_safety_v2 import isolated


@pytest.mark.parametrize('engine,enabled', [('cloud', True), ('gpt_sovits', False)])
def test_language_warmup_does_not_start_cloud_or_disabled_voice(engine, enabled):
    companion_service.save_config({'voice_engine': engine, 'voice_enabled': enabled})
    with patch('app.companion_service.threading.Thread') as thread:
        assert companion_service.warm_voice_language_async('ja') is False
    thread.assert_not_called()


def test_status_and_settings_receipts_hide_secret_while_blank_save_preserves_it():
    fake_key = 'MIO-SYNTHETIC-NOT-A-REAL-CLOUD-KEY'
    companion_service.save_config({'voice_engine': 'cloud', 'voice_enabled': True, 'cloud_tts_api_key': fake_key})
    stored = companion_service.load_config()['cloud_tts_api_key']
    assert stored and stored != fake_key
    app = FastAPI(); app.include_router(router)
    with patch('app.cloud_tts.synthesize_wav') as synthesize, TestClient(app) as client:
        response = client.get('/api/companion/status')
        assert response.status_code == 200
        assert response.json()['pet']['settings']['cloud_tts_api_key'] == ''
        assert response.json()['voice_runtime']['cloud_tts']['configured'] is True
        assert stored not in response.text and fake_key not in response.text
        saved = client.patch('/api/companion/settings', json={'cloud_tts_api_key': '', 'pet_speech_language': 'ja', 'cloud_tts_speech_rate': 5})
        assert saved.status_code == 200
        assert saved.json()['pet']['settings']['cloud_tts_api_key'] == ''
        assert saved.json()['voice_runtime']['cloud_tts']['configured'] is True
        assert stored not in saved.text and fake_key not in saved.text
        assert companion_service.load_config()['cloud_tts_api_key'] == stored
        synthesize.assert_not_called()
