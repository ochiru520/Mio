from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image

from app import companion_runtime_service as runtime
from app import companion_service
from app.companion_observation_service import WindowObserver
from app.routes import companion as routes
from app.screen_observer_process import ScreenObserverProcess, _worker_result


class FakeCapture:
    def __init__(self, color="navy"):
        self.color = color

    def capture(self, **_kwargs):
        return Image.new("RGB", (640, 360), self.color)

    def release(self):
        pass


def observer():
    value = WindowObserver(prefer_native=False)
    value._screen_capture = FakeCapture()
    return value


def test_configuring_stopped_observer_does_not_capture_or_launch():
    value = ScreenObserverProcess()
    with patch.object(value, "_start_process_locked") as start:
        value.configure_watch("adaptive", "dense")
        status = value.status()
    start.assert_not_called()
    assert status["watch"]["mode"] == "adaptive"
    assert status["watch"]["pace"] == "dense"
    assert not status["running"]


def test_configuration_is_restored_before_restarting_capture():
    value = ScreenObserverProcess()
    value.configure_watch("adaptive", "calm")
    value._has_selection = True
    value._desired_running = True
    with patch.object(value, "_send_locked", return_value={}) as send:
        value._restore_state_locked()
    assert [call.args[0] for call in send.call_args_list] == ["configure_watch", "select_screen", "start"]
    assert send.call_args_list[0].args[1] == {"mode": "adaptive", "pace": "calm"}


def test_worker_force_is_optional_and_normal_stub_remains_compatible():
    normal = SimpleNamespace(claim_analysis_frame=lambda after: {"frame_id": after + 1})
    assert _worker_result(normal, "claim_analysis_frame", {"after_frame_id": 3}) == {"frame_id": 4}
    forced = SimpleNamespace(claim_analysis_frame=Mock(return_value={"frame_id": 4}))
    _worker_result(forced, "claim_analysis_frame", {"after_frame_id": 3, "force": True})
    forced.claim_analysis_frame.assert_called_once_with(3, force=True)


def test_static_screen_does_not_emit_again_but_manual_view_gets_latest():
    value = observer()
    value.configure_watch("adaptive", "normal")
    with patch("app.companion_observation_service.cleanup_legacy_preview"):
        value.capture()
        first = value.claim_analysis_frame()
        value.capture()
        assert value.claim_analysis_frame(first["frame_id"]) is None
        current = value.claim_analysis_frame(first["frame_id"], force=True)
    assert current["frame_id"] > first["frame_id"]
    assert current["watch_sheet"]["count"] == 1
    assert current["frame_age_seconds"] < 1


def test_held_frame_age_uses_selected_frame_timestamp_not_latest_capture():
    value = observer()
    value.configure_watch("adaptive", "normal")
    with patch("app.companion_observation_service.time.monotonic", return_value=100.0), \
         patch("app.companion_observation_service.cleanup_legacy_preview"):
        value.capture()
    with patch("app.companion_observation_service.time.monotonic", return_value=120.0):
        selected = value.claim_analysis_frame()
    assert selected["frame_age_seconds"] == 20.0


def test_changed_then_static_sheet_contains_real_current_tail_frame():
    value = observer()
    value.configure_watch("adaptive", "normal")
    with patch("app.companion_observation_service.cleanup_legacy_preview"):
        with patch("app.companion_observation_service.time.monotonic", return_value=100.0):
            value.capture()
            first = value.claim_analysis_frame()
        value._screen_capture.color = "red"
        with patch("app.companion_observation_service.time.monotonic", return_value=101.0):
            value.capture()
        with patch("app.companion_observation_service.time.monotonic", return_value=114.0):
            value.capture()
            newest_id = value.status()["frame_id"]
        with patch("app.companion_observation_service.time.monotonic", return_value=115.0):
            sheet = value.claim_analysis_frame(first["frame_id"])
    assert sheet["frame_id"] == newest_id
    assert sheet["watch_sheet"]["frame_ids"][-1] == newest_id
    assert sheet["watch_sheet"]["frame_times"][-1] == 114.0
    assert sheet["frame_age_seconds"] == 1.0


def test_stop_resets_buffer_and_invalidates_pending_capture():
    value = observer()
    value.configure_watch("adaptive", "normal")
    with patch("app.companion_observation_service.cleanup_legacy_preview"):
        value.capture()
        assert value.status()["watch"]["buffered_frames"] == 1
        value.stop()
    assert value.status()["watch"]["buffered_frames"] == 0
    assert value.claim_analysis_frame(force=True) is None


def test_stop_during_capture_cannot_repopulate_frame_buffer():
    value = observer()
    value.configure_watch("adaptive", "normal")
    entered, proceed = threading.Event(), threading.Event()

    def capture(**_kwargs):
        entered.set()
        assert proceed.wait(2)
        return Image.new("RGB", (640, 360), "navy")

    value._screen_capture.capture = capture
    with patch("app.companion_observation_service.cleanup_legacy_preview"):
        thread = threading.Thread(target=value.capture)
        thread.start()
        assert entered.wait(2)
        value.stop()
        proceed.set()
        thread.join(2)
    assert not thread.is_alive()
    assert not value.status()["capture_valid"]
    assert value.status()["watch"]["buffered_frames"] == 0


def test_capture_failure_clears_pending_sheet_without_exposing_old_pixels():
    value = observer()
    value.configure_watch("adaptive", "normal")
    with patch("app.companion_observation_service.cleanup_legacy_preview"):
        value.capture()
        value._screen_capture.capture = Mock(side_effect=OSError("test capture failed"))
        with patch("app.companion_observation_service.ImageGrab.grab", side_effect=OSError("unavailable")), \
             patch("app.companion_observation_service.create_native_capture", return_value=None):
            value.capture()
    assert value.claim_analysis_frame(force=True) is None
    assert value.status()["watch"]["buffered_frames"] == 0


def test_start_respects_disabled_system_audio_and_stop_preserves_preference():
    target = Mock()
    target.start.return_value = {"running": True}
    target.stop.return_value = {"running": False}
    config = {"screen_watch_mode": "adaptive", "screen_watch_pace": "dense", "screen_audio_enabled": False}
    with patch.object(runtime.companion_service, "window_observer", target), \
         patch.object(runtime.companion_service, "load_config", return_value=config), \
         patch.object(runtime.companion_service, "save_config") as save, \
         patch.object(runtime.system_audio_service, "start") as audio_start, \
         patch.object(runtime.system_audio_service, "stop") as audio_stop, \
         patch.object(runtime.screen_observation_service, "set_capture_only"), \
         patch.object(runtime.screen_observation_service, "end_session"), \
         patch.object(runtime, "_pet_call_resource_owner", ""):
        runtime.start_observation_runtime(interval_ms=1000, capture_only=False, screen_scope="primary")
        runtime.stop_observation_runtime()
    target.configure_watch.assert_called_once_with("adaptive", "dense")
    audio_start.assert_not_called()
    audio_stop.assert_called_once()
    save.assert_not_called()


def test_capture_only_does_not_start_audio_even_when_preference_enabled():
    target = Mock()
    with patch.object(runtime.companion_service, "window_observer", target), \
         patch.object(runtime.companion_service, "load_config", return_value={"screen_audio_enabled": True}), \
         patch.object(runtime.system_audio_service, "start") as start, \
         patch.object(runtime.screen_observation_service, "set_capture_only"):
        runtime.start_observation_runtime(interval_ms=1000, capture_only=True, screen_scope="primary")
    start.assert_not_called()


def test_stopping_observation_keeps_phone_transcriber_owned_by_call():
    target = Mock()
    with patch.object(runtime.companion_service, "window_observer", target), \
         patch.object(runtime.screen_observation_service, "end_session"), \
         patch.object(runtime.system_audio_service, "stop") as stop, \
         patch.object(runtime, "ensure_call_asr_ready") as ensure, \
         patch.object(runtime, "_pet_call_resource_owner", "active-call"):
        runtime.stop_observation_runtime()
    stop.assert_not_called()
    ensure.assert_called_once()


def test_apply_audio_preference_during_call_does_not_stop_phone_asr():
    with patch.object(companion_service, "pet_running", return_value=False), \
         patch.object(companion_service, "load_config", return_value={}), \
         patch.object(companion_service, "save_config") as save, \
         patch.object(companion_service.window_observer, "status", return_value={"running": True}), \
         patch.object(runtime, "_pet_call_resource_owner", "active-call"), \
         patch.object(runtime, "ensure_call_asr_ready") as ensure, \
         patch.object(runtime.system_audio_service, "stop") as stop:
        routes._apply_companion_settings({"screen_audio_enabled": False})
    save.assert_called_once_with({"screen_audio_enabled": False})
    ensure.assert_called_once()
    stop.assert_not_called()


def test_watch_config_validation_rejects_unknown_mode_and_pace():
    with pytest.raises(ValueError):
        observer().configure_watch("everything", "normal")
    with pytest.raises(ValueError):
        ScreenObserverProcess().configure_watch("adaptive", "instant")
    with pytest.raises(ValueError):
        routes.CompanionSettingsRequest(screen_watch_mode="everything")
    with pytest.raises(ValueError):
        routes.CompanionSettingsRequest(screen_watch_pace="instant")


def test_saving_watch_settings_updates_only_configuration_without_starting_capture():
    config = {"screen_watch_mode": "adaptive", "screen_watch_pace": "calm"}
    with patch.object(companion_service, "pet_running", return_value=False), \
         patch.object(companion_service, "load_config", return_value=config), \
         patch.object(companion_service, "save_config") as save, \
         patch.object(companion_service.window_observer, "configure_watch") as configure, \
         patch.object(companion_service.window_observer, "start") as start:
        routes._apply_companion_settings(dict(config))
    save.assert_called_once_with(config)
    configure.assert_called_once_with("adaptive", "calm")
    start.assert_not_called()


def test_stop_during_cold_vision_load_prevents_late_capture_start():
    async def exercise():
        loading, finish_loading = asyncio.Event(), asyncio.Event()

        async def delayed_ready():
            loading.set()
            await finish_loading.wait()

        target = Mock()
        target.stop.return_value = {"running": False}
        config = {"screen_vision_route": "local", "screen_audio_enabled": False}
        with patch.object(runtime.companion_service, "window_observer", target), \
             patch.object(runtime.companion_service, "load_config", return_value=config), \
             patch.object(routes.local_vision_service, "ensure_ready", side_effect=delayed_ready), \
             patch.object(runtime.screen_observation_service, "set_capture_only") as capture_mode, \
             patch.object(runtime.screen_observation_service, "end_session") as end_session, \
             patch.object(runtime.system_audio_service, "stop"), \
             patch.object(runtime, "_pet_call_resource_owner", ""):
            start = asyncio.create_task(routes.companion_screen_start(routes.ScreenObservationRequest()))
            await asyncio.wait_for(loading.wait(), timeout=2)
            await asyncio.to_thread(runtime.stop_observation_runtime)
            finish_loading.set()
            with pytest.raises(routes.HTTPException) as rejected:
                await asyncio.wait_for(start, timeout=2)
        assert rejected.value.status_code == 409
        target.stop.assert_called_once()
        target.start.assert_not_called()
        target.capture.assert_not_called()
        target.select_screen.assert_not_called()
        capture_mode.assert_not_called()
        end_session.assert_called_once()

    asyncio.run(exercise())


def test_new_start_invalidates_an_older_pending_ticket():
    old_ticket = runtime.begin_observation_start()
    current_ticket = runtime.begin_observation_start()
    target = Mock()
    with patch.object(runtime.companion_service, "window_observer", target), \
         patch.object(runtime.companion_service, "load_config", return_value={}), \
         patch.object(runtime.screen_observation_service, "set_capture_only"):
        with pytest.raises(runtime.ObservationStartCancelled):
            runtime.start_observation_runtime(interval_ms=1000, capture_only=True, start_ticket=old_ticket)
        target.start.assert_not_called()
        runtime.start_observation_runtime(interval_ms=1000, capture_only=True,
                                          screen_scope="primary", start_ticket=current_ticket)
    target.start.assert_called_once_with(1000)


@pytest.mark.parametrize("source", ["screen", "game", "desktop_pet_wake", "call", "desktop", "chat"])
def test_observation_speech_cancellation_respects_current_owner(source):
    screen_sources = {"screen", "game", "desktop_pet_wake"}
    with patch.object(companion_service, "_speech_generation", 10), \
         patch.object(companion_service, "_speech_owner_generation", 10), \
         patch.object(companion_service, "_speech_owner_priority", 75), \
         patch.object(companion_service, "_speech_owner_source", source):
        companion_service.cancel_speech_playback(sources=screen_sources)
        if source in screen_sources:
            assert companion_service._speech_generation == 11
            assert companion_service._speech_owner_generation == 0
            assert companion_service._speech_owner_source == ""
        else:
            assert companion_service._speech_generation == 10
            assert companion_service._speech_owner_generation == 10
            assert companion_service._speech_owner_source == source


def test_global_speech_cancellation_keeps_original_behavior():
    with patch.object(companion_service, "_speech_generation", 10), \
         patch.object(companion_service, "_speech_owner_generation", 10), \
         patch.object(companion_service, "_speech_owner_priority", 100), \
         patch.object(companion_service, "_speech_owner_source", "desktop"):
        companion_service.cancel_speech_playback()
        assert companion_service._speech_generation == 11
        assert companion_service._speech_owner_generation == 0
        assert companion_service._speech_owner_source == ""


def test_observation_stop_publishes_only_screen_sources_after_ending_session():
    actions = []
    target = Mock()
    with patch.object(runtime.companion_service, "window_observer", target), \
         patch.object(runtime.screen_observation_service, "end_session", side_effect=lambda: actions.append("end")), \
         patch.object(runtime.companion_service, "cancel_speech_playback", side_effect=lambda **_kw: actions.append("cancel")) as cancel, \
         patch.object(runtime.pet_event_service, "publish", side_effect=lambda *_args: actions.append("publish")) as publish, \
         patch.object(runtime.system_audio_service, "stop"), \
         patch.object(runtime, "_pet_call_resource_owner", ""):
        runtime.stop_observation_runtime()
    cancel.assert_called_once_with(sources={"screen", "game", "desktop_pet_wake"})
    publish.assert_called_once_with("speech_interrupt", {
        "sources": ["desktop_pet_wake", "game", "screen"], "reason": "observation_stopped",
    })
    assert actions == ["end", "cancel", "publish"]


@pytest.mark.parametrize("was_running", [False, True])
def test_stop_during_manual_analysis_cold_load_does_not_resume_capture(was_running):
    async def exercise():
        loading, finish_loading = asyncio.Event(), asyncio.Event()

        async def delayed_ready():
            loading.set()
            await finish_loading.wait()

        target = Mock()
        target.status.return_value = {"running": was_running}
        config = {"screen_vision_route": "local", "screen_ai_enabled": True}
        with patch.object(runtime.companion_service, "window_observer", target), \
             patch.object(runtime.companion_service, "load_config", return_value=config), \
             patch.object(routes.local_vision_service, "ensure_ready", side_effect=delayed_ready), \
             patch.object(runtime.screen_observation_service, "status", return_value={"capture_only": True}), \
             patch.object(runtime.screen_observation_service, "set_capture_only") as capture_mode, \
             patch.object(runtime.screen_observation_service, "analyze_once", new_callable=AsyncMock) as analyze, \
             patch.object(runtime.screen_observation_service, "end_session") as end_session, \
             patch.object(runtime.companion_service, "cancel_speech_playback"), \
             patch.object(runtime.pet_event_service, "publish"), \
             patch.object(runtime.system_audio_service, "stop"), \
             patch.object(runtime, "_pet_call_resource_owner", ""):
            request = asyncio.create_task(routes.companion_screen_analyze(routes.ScreenObservationRequest()))
            await asyncio.wait_for(loading.wait(), timeout=2)
            await asyncio.to_thread(runtime.stop_observation_runtime)
            finish_loading.set()
            with pytest.raises(routes.HTTPException) as rejected:
                await asyncio.wait_for(request, timeout=2)
        assert rejected.value.status_code == 409
        target.stop.assert_called_once()
        target.capture.assert_not_called()
        target.select_screen.assert_not_called()
        capture_mode.assert_not_called()
        analyze.assert_not_awaited()
        end_session.assert_called_once()

    asyncio.run(exercise())


def test_manual_analysis_ticket_does_not_cancel_a_valid_start():
    start_ticket = runtime.begin_observation_start()
    analysis_ticket = runtime.observation_ticket()
    assert analysis_ticket == start_ticket
    with runtime.observation_operation(analysis_ticket):
        pass
    assert runtime.observation_ticket() == start_ticket


def test_old_manual_game_analysis_cleanup_cannot_stop_a_new_session():
    async def exercise():
        analyzing, finish = asyncio.Event(), asyncio.Event()

        async def delayed_analyze(**_kwargs):
            analyzing.set()
            await finish.wait()
            return False

        target = Mock()
        target.status.return_value = {"running": False, "mode": "window", "hwnd": 123}
        with patch.object(companion_service, "game_observer", target), \
             patch.object(runtime.screen_observation_service, "analyze_once", side_effect=delayed_analyze), \
             patch.object(runtime.screen_observation_service, "status", return_value={}), \
             patch.object(runtime.screen_observation_service, "end_session") as end_session:
            request = asyncio.create_task(routes.companion_game_analyze())
            await asyncio.wait_for(analyzing.wait(), timeout=2)
            runtime.begin_observation_start()
            finish.set()
            await asyncio.wait_for(request, timeout=2)
        target.stop.assert_not_called()
        end_session.assert_not_called()

    asyncio.run(exercise())
