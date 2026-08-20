from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import WebSocketDisconnect

from app import companion_service, db
from app.pet_event_service import PetEventHub
from app.routes.companion import (
    CompanionChatRequest,
    Live2DMotionPreviewRequest,
    SpeechRequest,
    companion_chat,
    companion_live2d_motion_preview,
    companion_voice_stream,
)


class FakeWebSocket:
    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages = list(messages or [])
        self.accepted = False
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive_json(self) -> dict:
        if self.messages:
            return self.messages.pop(0)
        raise WebSocketDisconnect()


class PetEventServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_db_path = db.settings.db_path
        self.original_config_path = companion_service.settings.companion_config_path
        object.__setattr__(db.settings, "db_path", root / "test.db")
        object.__setattr__(
            companion_service.settings,
            "companion_config_path",
            root / "companion-settings.json",
        )
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        object.__setattr__(
            companion_service.settings,
            "companion_config_path",
            self.original_config_path,
        )
        self.temp_dir.cleanup()

    def test_websocket_sends_ready_and_tracks_voice_lifecycle(self) -> None:
        websocket = FakeWebSocket(
            [
                {"type": "voice_started", "payload": {"emotion": "cheerful"}},
                {"type": "voice_ended", "payload": {}},
            ]
        )
        hub = PetEventHub()

        with (
            patch(
                "app.companion_service.pet_activity_status",
                return_value={"state": "idle", "emotion": "neutral"},
            ),
            patch("app.companion_service.set_pet_activity") as set_activity,
            patch("app.screen_observation_service.status", return_value={"running": False}),
        ):
            asyncio.run(hub.serve(websocket))

        self.assertTrue(websocket.accepted)
        self.assertEqual(websocket.sent[0]["type"], "ready")
        self.assertEqual(websocket.sent[0]["payload"]["protocol_version"], 3)
        self.assertEqual(set_activity.call_count, 2)
        self.assertEqual(set_activity.call_args_list[0].args[0], "speaking")
        self.assertEqual(set_activity.call_args_list[0].kwargs["emotion"], "cheerful")
        self.assertEqual(set_activity.call_args_list[1].args[0], "idle")
        self.assertFalse(hub.has_clients())

    def test_renderer_capabilities_are_recorded(self) -> None:
        websocket = FakeWebSocket(
            [
                {
                    "type": "renderer_ready",
                    "payload": {
                        "runtime": "electron",
                        "model_id": "mio-live2d",
                        "model_name": "澪",
                        "capabilities": {
                            "motions": [{"name": "Idle", "count": 2}],
                            "expressions": [{"name": "Smile"}],
                            "physics": True,
                        },
                    },
                }
            ]
        )
        hub = PetEventHub()

        with (
            patch("app.companion_service.pet_activity_status", return_value={"state": "idle"}),
            patch("app.screen_observation_service.status", return_value={"running": False}),
        ):
            asyncio.run(hub.serve(websocket))

        renderer = hub.status()["renderer"]
        self.assertEqual(renderer["runtime"], "electron")
        self.assertEqual(renderer["model_id"], "mio-live2d")
        self.assertEqual(renderer["model_name"], "澪")
        self.assertTrue(renderer["capabilities"]["physics"])
        self.assertEqual(renderer["capabilities"]["motions"][0]["name"], "Idle")

    def test_desktop_renderer_count_ignores_browser_previews(self) -> None:
        electron = FakeWebSocket()
        browser = FakeWebSocket()
        hub = PetEventHub()
        hub._clients.update({electron, browser})
        hub._client_info[electron] = {"runtime": "electron"}
        hub._client_info[browser] = {"runtime": "browser"}

        self.assertTrue(hub.has_desktop_renderer())
        self.assertEqual(hub.desktop_renderer_count(), 1)
        self.assertEqual(hub.status()["desktop_renderer_count"], 1)

    def test_start_pet_reuses_connected_external_renderer(self) -> None:
        original_process = companion_service._pet_process
        original_runtime = companion_service._pet_runtime_kind
        companion_service._pet_process = None
        companion_service._pet_runtime_kind = ""
        try:
            with (
                patch("app.companion_service.load_config", return_value={"pet_renderer": "live2d"}),
                patch("app.companion_service._external_pet_renderer_connected", return_value=True),
                patch("app.companion_service.pet_status", return_value={"running": True}) as status,
                patch("app.companion_service.subprocess.Popen") as popen,
            ):
                result = companion_service.start_pet()
        finally:
            companion_service._pet_process = original_process
            companion_service._pet_runtime_kind = original_runtime

        self.assertEqual(result, {"running": True})
        status.assert_called_once_with()
        popen.assert_not_called()

    def test_foreground_window_changes_are_published(self) -> None:
        async def scenario() -> list[dict]:
            hub = PetEventHub()
            hub._clients.add(FakeWebSocket())
            delivered: list[dict] = []
            ready = asyncio.Event()

            async def capture(event_type: str, payload: dict) -> None:
                if event_type == "foreground_changed":
                    delivered.append(payload)
                    if len(delivered) >= 2:
                        ready.set()

            hub.broadcast = capture  # type: ignore[method-assign]
            snapshots = iter(
                [
                    {"hwnd": 11, "title": "游戏 A", "process_id": 101},
                    {"hwnd": 11, "title": "游戏 A", "process_id": 101},
                    {"hwnd": 22, "title": "游戏 B", "process_id": 202},
                ]
            )
            with (
                patch("app.pet_event_service.FOREGROUND_INTERVAL_SECONDS", 0.001),
                patch(
                    "app.pet_event_service.foreground_window_snapshot",
                    side_effect=lambda: next(snapshots, {"hwnd": 22, "title": "游戏 B", "process_id": 202}),
                ),
            ):
                task = asyncio.create_task(hub.foreground_loop())
                try:
                    await asyncio.wait_for(ready.wait(), timeout=1)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
            return delivered

        delivered = asyncio.run(scenario())
        self.assertEqual([item["title"] for item in delivered], ["游戏 A", "游戏 B"])

    def test_message_loop_merges_reply_fragments_with_same_request_id(self) -> None:
        async def scenario() -> dict:
            hub = PetEventHub()
            hub._clients.add(FakeWebSocket())
            hub._cursor = db.get_latest_message_id(
                role="assistant",
                conversation_id="desktop_pet",
            )
            db.save_message(
                "assistant",
                "第一句",
                source="desktop_pet",
                conversation_id="desktop_pet",
                request_id="request-1",
                model_id="deepseek-v4-flash",
                emotion="gentle",
            )
            db.save_message(
                "assistant",
                "第二句",
                source="desktop_pet",
                conversation_id="desktop_pet",
                request_id="request-1",
                model_id="deepseek-v4-flash",
                emotion="gentle",
            )
            delivered = asyncio.Event()
            payload: dict = {}

            async def capture(event_type: str, event_payload: dict) -> None:
                if event_type == "speak":
                    payload.update(event_payload)
                    delivered.set()

            hub.broadcast = capture  # type: ignore[method-assign]
            with patch("app.companion_service.load_config", return_value={"voice_enabled": True}):
                task = asyncio.create_task(hub.message_loop())
                try:
                    await asyncio.wait_for(delivered.wait(), timeout=2)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
            return payload

        payload = asyncio.run(scenario())

        self.assertEqual(payload["request_id"], "request-1")
        self.assertEqual(payload["response_id"], "request-1")
        self.assertEqual(payload["priority"], 100)
        self.assertTrue(payload["interruptible"])
        self.assertEqual(payload["text"], "第一句 第二句")
        self.assertEqual(payload["emotion"], "gentle")
        self.assertEqual(payload["model_id"], "deepseek-v4-flash")
        self.assertTrue(payload["should_speak"])
        self.assertEqual([item["text"] for item in payload["timeline"]], ["第一句", "第二句"])
        self.assertEqual(payload["timeline"][0]["offset_ms"], 0)
        self.assertGreater(payload["timeline"][1]["offset_ms"], 0)

    def test_speech_priorities_keep_direct_replies_above_background_events(self) -> None:
        from app.pet_event_service import speech_priority

        self.assertGreater(speech_priority("desktop_pet"), speech_priority("game"))
        self.assertGreater(speech_priority("game"), speech_priority("proactive"))
        self.assertEqual(speech_priority("qq_group_123"), 90)

    def test_motion_preview_is_published_to_desktop_renderer(self) -> None:
        with (
            patch("app.routes.companion.pet_event_service.has_desktop_renderer", return_value=True),
            patch("app.routes.companion.pet_event_service.publish") as publish,
        ):
            result = asyncio.run(
                companion_live2d_motion_preview(
                    Live2DMotionPreviewRequest(group="TapBody", index=0)
                )
            )

        self.assertTrue(result["ok"])
        publish.assert_called_once_with(
            "motion_preview",
            {"group": "TapBody", "index": 0},
        )

    def test_companion_chat_delegates_voice_to_connected_renderer(self) -> None:
        result = SimpleNamespace(
            reply="我在",
            replies=["我在", "慢慢说"],
            request_id="pet-chat-delegated",
            model_id="deepseek-v4-flash",
            reasoning_level="standard",
            speech_emotion="gentle",
        )

        with (
            patch("app.routes.companion.resolve_model_id", return_value="deepseek-v4-flash"),
            patch("app.routes.companion.chat_with_ai", new=AsyncMock(return_value=result)),
            patch("app.routes.companion.pet_event_service.has_clients", return_value=True),
            patch(
                "app.routes.companion.companion_service.load_config",
                return_value={"voice_enabled": True},
            ),
            patch("app.routes.companion.companion_service.speak_text") as speak,
        ):
            response = asyncio.run(
                companion_chat(
                    CompanionChatRequest(
                        message="陪我说会儿话",
                        model_id="deepseek-v4-flash",
                        reasoning_level="standard",
                    )
                )
            )

        self.assertTrue(response["voice_attempted"])
        self.assertTrue(response["voice_delegated"])
        self.assertFalse(response["spoken"])
        self.assertEqual(response["speech_emotion"], "gentle")
        speak.assert_not_called()

    def test_live2d_renderer_uses_electron_command_when_available(self) -> None:
        with (
            patch("app.companion_service.load_config", return_value={"pet_renderer": "live2d"}),
            patch(
                "app.companion_service._electron_pet_command",
                return_value=["D:/澪Agent/澪Live2D桌宠.exe"],
            ),
        ):
            command, runtime_kind = companion_service._pet_command()

        self.assertEqual(command, ["D:/澪Agent/澪Live2D桌宠.exe"])
        self.assertEqual(runtime_kind, "electron_live2d")

    def test_voice_stream_route_returns_chunked_wav(self) -> None:
        async def scenario() -> tuple[bytes, str, str]:
            with patch(
                "app.routes.companion.companion_service.iter_speech_wav_stream",
                return_value=iter([b"RIFF", b"stream"]),
            ):
                response = await companion_voice_stream(
                    SpeechRequest(text="晚上好", context="晚上好", emotion="gentle")
                )
                chunks = [chunk async for chunk in response.body_iterator]
                return (
                    b"".join(chunks),
                    str(response.media_type),
                    str(response.headers.get("x-mio-streaming") or ""),
                )

        content, media_type, streaming = asyncio.run(scenario())
        self.assertEqual(content, b"RIFFstream")
        self.assertEqual(media_type, "audio/wav")
        self.assertEqual(streaming, "1")


if __name__ == "__main__":
    unittest.main()
