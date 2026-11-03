"""A local model change cannot publish a comment produced for the old model."""
import asyncio
from unittest.mock import Mock

from app import companion_service, local_vision_models, screen_observation_service as watch


def test_local_model_revision_rejects_old_and_away_back_result(monkeypatch):
    config = {'screen_ai_enabled': True, 'screen_vision_route': 'local',
              '_local_vision_selection': ('qwen2.5vl:3b', 'old-revision')}
    monkeypatch.setattr(watch, '_session_generation', 5)
    monkeypatch.setattr(watch, '_capture_only', False)
    monkeypatch.setattr(companion_service, 'load_config', lambda: config.copy())
    monkeypatch.setattr(local_vision_models, 'selection_identity', lambda: ('qwen2.5vl:3b', 'old-revision'))
    assert watch._observation_still_current(5, config)
    monkeypatch.setattr(local_vision_models, 'selection_identity', lambda: ('qwen3.5:2b', 'new-revision'))
    assert not watch._observation_still_current(5, config)
    monkeypatch.setattr(local_vision_models, 'selection_identity', lambda: ('qwen2.5vl:3b', 'returned-revision'))
    assert not watch._observation_still_current(5, config)


def test_next_pass_discards_prior_model_context_without_stopping_capture(monkeypatch):
    monkeypatch.setattr(watch, '_local_vision_selection', ('qwen2.5vl:3b', 'before'))
    monkeypatch.setattr(watch, '_capture_only', False)
    monkeypatch.setattr(local_vision_models, 'selection_identity', lambda: ('qwen3.5:2b', 'after'))
    monkeypatch.setattr(companion_service, 'load_config', lambda: {'screen_ai_enabled': True, 'screen_vision_route': 'local'})
    monkeypatch.setattr(companion_service.window_observer, 'status', lambda: {'running': False})
    stop = Mock(); reset = Mock(); cancel = Mock(); publish = Mock()
    monkeypatch.setattr(companion_service.window_observer, 'stop', stop)
    monkeypatch.setattr(watch, 'end_session', reset)
    monkeypatch.setattr(companion_service, 'cancel_speech_playback', cancel)
    monkeypatch.setattr(watch.pet_event_service, 'publish', publish)
    assert asyncio.run(watch.analyze_once()) is False
    reset.assert_called_once()
    stop.assert_not_called()
    cancel.assert_called_once_with(sources={'screen', 'game', 'desktop_pet_wake'})
    assert publish.call_args.args[1]['reason'] == 'vision_model_changed'
