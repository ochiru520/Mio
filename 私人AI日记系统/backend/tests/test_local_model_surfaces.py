"""Local no-key models must remain available beyond the provider settings panel."""
from types import SimpleNamespace
from unittest.mock import patch

from app import screen_observation_service, self_snapshot_service
from app.routes import diary
from app.model_registry import ModelProfile


def profiles():
    return [
        ModelProfile(id='local-fixture', provider_name='LM Studio', display_name='local', model='qwen',
                     base_urls=('http://127.0.0.1:1234/v1',), api_key='', provider_kind='local', is_default=True),
        ModelProfile(id='external-empty', provider_name='relay', display_name='external', model='remote',
                     base_urls=('https://example.invalid/v1',), api_key=''),
        ModelProfile(id='broken-secret', provider_name='relay', display_name='broken', model='remote',
                     base_urls=('https://example.invalid/v1',), api_key='bad-fixture', api_key_error='unreadable'),
    ]


def test_diary_default_and_fixed_selection_accept_no_key_local_model():
    with patch.object(diary, 'list_model_profiles', return_value=profiles()):
        for selected in ('', 'local-fixture'):
            with patch('app.model_runtime.model_selection', return_value=(selected, 'default')):
                assert diary._diary_model_id() == 'local-fixture'


def test_screen_model_inventory_keeps_local_model_and_rejects_unconfigured_cloud():
    with patch.object(screen_observation_service, 'list_model_profiles', return_value=profiles()):
        assert [p.id for p in screen_observation_service._configured_profiles()] == ['local-fixture']


def test_self_snapshot_reports_local_as_configured_without_claiming_cloud_credentials():
    with patch.object(self_snapshot_service, 'get_latency_stats', return_value=SimpleNamespace(public_dict=lambda: {})):
        public = self_snapshot_service._public_models(profiles())
    assert [p['configured'] for p in public] == [True, False, False]
    with patch.object(self_snapshot_service, 'get_model_profile', return_value=profiles()[0]):
        overview = self_snapshot_service._overview({}, {}, profiles())
    assert overview['configured_model_count'] == 1
