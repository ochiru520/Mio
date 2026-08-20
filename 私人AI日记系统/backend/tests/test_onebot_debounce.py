from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import companion_service
from app.chat_service import ChatResult
from app.config import settings
from app.routes.onebot import (
    OneBotConnection,
    OneBotEventDebouncer,
    _debounce_seconds,
    _active_connections,
    disconnect_all_connections,
    _extract_image_sources,
    _extract_text_message,
    _is_profile_update_command,
    _is_today_diary_command,
    _diary_edit_date,
    _merge_onebot_events,
    _requests_voice_reply,
    _enqueue_connection,
    _fail_connection_pending,
    _outbox_worker,
    _resolve_delivery_ack,
    runtime_health_status,
    send_private_message,
    send_private_message_receipt,
    _shared_agent_route,
    _voice_reply_text,
    _authorization_result,
    process_onebot_event,
)


def private_event(text: str = "", *, user_id: str = "42", image_url: str = "") -> dict:
    message: list[dict] = []
    if text:
        message.append({"type": "text", "data": {"text": text}})
    if image_url:
        message.append({"type": "image", "data": {"url": image_url}})
    return {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10000,
        "user_id": int(user_id),
        "message": message,
        "raw_message": text,
    }


def group_event(
    text: str = "",
    *,
    user_id: str = "99",
    group_id: str = "12345",
    mentioned: bool = True,
) -> dict:
    message: list[dict] = []
    if mentioned:
        message.append({"type": "at", "data": {"qq": "10000"}})
    if text:
        message.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 10000,
        "user_id": int(user_id),
        "group_id": int(group_id),
        "sender": {"card": "测试成员", "nickname": "成员昵称"},
        "message": message,
        "raw_message": text,
    }


class OneBotMergeTests(unittest.TestCase):
    def test_qq_route_follows_agent_model_and_reasoning(self) -> None:
        automatic = SimpleNamespace(model_id="automatic-model", reasoning_level="low")
        with (
            patch("app.routes.onebot.select_auto_route", return_value=automatic),
            patch(
                "app.routes.onebot.companion_service.load_config",
                return_value={"chat_model_id": "shared-model", "chat_reasoning_level": "high"},
            ),
        ):
            selected = _shared_agent_route("测试", history_rows=[], image_count=0)

        self.assertEqual(selected, ("shared-model", "high", None))

    def test_qq_route_keeps_automatic_agent_mode(self) -> None:
        automatic = SimpleNamespace(model_id="automatic-model", reasoning_level="medium")
        with (
            patch("app.routes.onebot.select_auto_route", return_value=automatic),
            patch(
                "app.routes.onebot.companion_service.load_config",
                return_value={"chat_model_id": "auto", "chat_reasoning_level": "auto"},
            ),
        ):
            selected = _shared_agent_route("测试", history_rows=[], image_count=0)

        self.assertEqual(selected, ("automatic-model", "medium", automatic))

    def test_three_text_events_merge_in_order(self) -> None:
        merged = _merge_onebot_events(
            [private_event("我刚刚想了下"), private_event("其实还有一点"), private_event("就是没说完")]
        )

        self.assertEqual(_extract_text_message(merged), "我刚刚想了下\n其实还有一点\n就是没说完")

    def test_image_segment_is_preserved(self) -> None:
        merged = _merge_onebot_events(
            [private_event("你看看这个"), private_event(image_url="https://example.test/a.jpg")]
        )

        self.assertEqual(_extract_text_message(merged), "你看看这个")
        self.assertEqual(_extract_image_sources(merged), ["https://example.test/a.jpg"])

    def test_incomplete_sentence_uses_longer_window(self) -> None:
        original_normal = settings.qq_message_debounce_seconds
        original_incomplete = settings.qq_message_incomplete_debounce_seconds
        try:
            object.__setattr__(settings, "qq_message_debounce_seconds", 3.5)
            object.__setattr__(settings, "qq_message_incomplete_debounce_seconds", 7.0)
            for text in ("因为", "但是", "我还想说，", "其实"):
                self.assertEqual(_debounce_seconds(text), 7.0)
            self.assertEqual(_debounce_seconds("我说完了。"), 3.5)
        finally:
            object.__setattr__(settings, "qq_message_debounce_seconds", original_normal)
            object.__setattr__(settings, "qq_message_incomplete_debounce_seconds", original_incomplete)

    def test_diary_command_recognizes_requests_not_statements(self) -> None:
        self.assertTrue(_is_today_diary_command("生成今天的日记吧"))
        self.assertTrue(_is_today_diary_command("日终整理"))
        # 陈述、否定和疑问不是命令。
        self.assertFalse(_is_today_diary_command("我今天还没写日记"))
        self.assertFalse(_is_today_diary_command("我把日记写好了"))
        self.assertFalse(_is_today_diary_command("忘了写日记了"))

    def test_diary_edit_ignores_completed_statements(self) -> None:
        self.assertIsNotNone(_diary_edit_date("把今天日记里那句改成开心一点"))
        self.assertIsNone(_diary_edit_date("我把日记补上了"))
        self.assertIsNone(_diary_edit_date("已经改过日记了"))

    def test_profile_command_requires_addressing_mio(self) -> None:
        self.assertTrue(_is_profile_update_command("把这个写进你的属性"))
        self.assertTrue(_is_profile_update_command("以后你说话方式改成这样"))
        # 用户说自己的事，不是在改澪的设定。
        self.assertFalse(_is_profile_update_command("我想调整一下说话方式"))

    def test_group_authorization_accepts_other_members_only_when_mentioned(self) -> None:
        with (
            patch("app.routes.onebot.group_is_allowed", return_value=True),
            patch("app.routes.onebot.group_mention_required", return_value=True),
        ):
            self.assertEqual(_authorization_result(group_event("你好", user_id="777")), (True, ""))
            allowed, reason = _authorization_result(group_event("你好", user_id="777", mentioned=False))
        self.assertFalse(allowed)
        self.assertEqual(reason, "group_mention_required")

    def test_napcat_plain_text_mio_mention_is_authorized_and_removed(self) -> None:
        event = group_event("@Mio 用语音说句日语", user_id="777", mentioned=False)
        with (
            patch("app.routes.onebot.group_is_allowed", return_value=True),
            patch("app.routes.onebot.group_mention_required", return_value=True),
        ):
            self.assertEqual(_authorization_result(event), (True, ""))

        self.assertEqual(_extract_text_message(event), "用语音说句日语")

    def test_napcat_text_at_other_member_does_not_trigger_mio(self) -> None:
        event = group_event("@其他成员 用语音说句日语", user_id="777", mentioned=False)
        with (
            patch("app.routes.onebot.group_is_allowed", return_value=True),
            patch("app.routes.onebot.group_mention_required", return_value=True),
        ):
            allowed, reason = _authorization_result(event)

        self.assertFalse(allowed)
        self.assertEqual(reason, "group_mention_required")

    def test_napcat_plain_text_mention_survives_event_merge(self) -> None:
        event = group_event("@澪 用语音说句日语", user_id="777", mentioned=False)
        merged = _merge_onebot_events([event])
        with (
            patch("app.routes.onebot.group_is_allowed", return_value=True),
            patch("app.routes.onebot.group_mention_required", return_value=True),
        ):
            self.assertEqual(_authorization_result(merged), (True, ""))

        self.assertEqual(_extract_text_message(merged), "用语音说句日语")

    def test_napcat_text_at_type_six_with_target_is_authorized(self) -> None:
        event = group_event("用语音说句日语", user_id="777", mentioned=False)
        event["message"] = [{
            "type": "text",
            "data": {"text": "用语音说句日语", "atType": 6, "qq": "10000"},
        }]
        with (
            patch("app.routes.onebot.group_is_allowed", return_value=True),
            patch("app.routes.onebot.group_mention_required", return_value=True),
        ):
            self.assertEqual(_authorization_result(event), (True, ""))

    def test_voice_reply_request_requires_clear_intent(self) -> None:
        self.assertTrue(_requests_voice_reply("发条语音给我听"))
        self.assertTrue(_requests_voice_reply("给我发一句语音"))
        self.assertTrue(_requests_voice_reply("这次用语音回复我"))
        self.assertTrue(_requests_voice_reply("用语音介绍自己"))
        self.assertTrue(_requests_voice_reply("用语音自我介绍一下"))
        self.assertTrue(_requests_voice_reply("语音介绍一下你自己"))
        self.assertTrue(_requests_voice_reply("用你的声音回答我"))
        self.assertTrue(_requests_voice_reply("再用语音接受一下你自己"))
        self.assertTrue(_requests_voice_reply("可以来一段日语的语音吗"))
        self.assertTrue(_requests_voice_reply("让我听听你的声音"))
        self.assertFalse(_requests_voice_reply("我刚听了一条语音"))

    def test_voice_replies_are_joined_into_one_utterance(self) -> None:
        self.assertEqual(_voice_reply_text(["嗯，我在", "怎么突然想听我说话了"]), "嗯，我在。怎么突然想听我说话了。")

    def test_voice_reply_drops_fake_duration_and_stage_directions(self) -> None:
        self.assertEqual(
            _voice_reply_text([
                "语音消息约 5 秒",
                "语音里说：",
                "我是澪",
                "然后轻轻笑了一声：\"很高兴认识你\"",
                "（这次应该能听到了吧？）",
            ]),
            "我是澪。很高兴认识你。",
        )


class OneBotDebouncerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_allowed_users = settings.qq_allowed_user_ids
        self.original_normal = settings.qq_message_debounce_seconds
        self.original_incomplete = settings.qq_message_incomplete_debounce_seconds
        self.original_initial_delay = settings.qq_reply_initial_delay_seconds
        object.__setattr__(settings, "qq_allowed_user_ids", ("42",))
        object.__setattr__(settings, "qq_message_debounce_seconds", 0.01)
        object.__setattr__(settings, "qq_message_incomplete_debounce_seconds", 0.02)
        object.__setattr__(settings, "qq_reply_initial_delay_seconds", 0.0)

    def tearDown(self) -> None:
        object.__setattr__(settings, "qq_allowed_user_ids", self.original_allowed_users)
        object.__setattr__(settings, "qq_message_debounce_seconds", self.original_normal)
        object.__setattr__(settings, "qq_message_incomplete_debounce_seconds", self.original_incomplete)
        object.__setattr__(settings, "qq_reply_initial_delay_seconds", self.original_initial_delay)

    async def test_three_enqueues_process_only_one_merged_event(self) -> None:
        send_json = AsyncMock()
        debouncer = OneBotEventDebouncer(send_json)
        with patch("app.routes.onebot.process_onebot_event", new_callable=AsyncMock) as process_mock:
            debouncer.enqueue(private_event("第一句"))
            debouncer.enqueue(private_event("第二句"))
            debouncer.enqueue(private_event("补充完了。"))
            await asyncio.gather(*list(debouncer.tasks))

            process_mock.assert_awaited_once()
            merged_event = process_mock.await_args.args[0]
            self.assertEqual(_extract_text_message(merged_event), "第一句\n第二句\n补充完了。")
            self.assertFalse(process_mock.await_args.kwargs["record_debug"])
        await debouncer.close()

    async def test_unauthorized_message_never_enters_batch(self) -> None:
        debouncer = OneBotEventDebouncer(AsyncMock())
        with patch("app.routes.onebot.process_onebot_event", new_callable=AsyncMock) as process_mock:
            self.assertTrue(debouncer.enqueue(private_event("不该处理", user_id="99")))
            await asyncio.sleep(0.03)
            process_mock.assert_not_awaited()
            self.assertEqual(debouncer.batches, {})
        await debouncer.close()

    async def test_diary_command_saves_user_message_and_confirmation(self) -> None:
        send_json = AsyncMock()
        event = private_event("今天做了一下午 demo\n生成今天的日记吧")
        with (
            patch("app.db.save_message") as save_message,
            patch("app.routes.onebot._generate_today_diary_replies", new=AsyncMock(return_value=["生成完毕。"])),
        ):
            handled = await process_onebot_event(event, send_json)

        self.assertTrue(handled)
        calls = [call.args for call in save_message.call_args_list]
        # 用户原话（含命令）先入库，日记生成后再入库确认语。
        self.assertEqual(calls[0][:2], ("user", "今天做了一下午 demo\n生成今天的日记吧"))
        self.assertEqual(calls[1][:2], ("assistant", "生成完毕。"))
        send_json.assert_awaited_once()

    async def test_qq_chat_uses_automatic_model_and_reasoning_route(self) -> None:
        send_json = AsyncMock()
        event = private_event("帮我分析一下这两个选择")
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="high")
        with (
            patch("app.routes.onebot.database.get_recent_messages", return_value=[]) as recent_messages,
            patch("app.routes.onebot.select_auto_route", return_value=route) as auto_route,
            patch(
                "app.routes.onebot.companion_service.load_config",
                return_value={"chat_model_id": "auto", "chat_reasoning_level": "auto"},
            ),
            patch(
                "app.routes.onebot.chat_with_ai",
                new=AsyncMock(return_value=ChatResult(reply="我想想。", replies=["我想想。"])),
            ) as chat_with_ai,
        ):
            handled = await process_onebot_event(event, send_json)

        self.assertTrue(handled)
        recent_messages.assert_called_once_with(limit=12, conversation_id="qq_private_42")
        auto_route.assert_called_once_with(
            "帮我分析一下这两个选择",
            history_rows=[],
            image_count=0,
        )
        self.assertEqual(chat_with_ai.await_args.kwargs["model_id"], "deepseek-v4-flash")
        self.assertEqual(chat_with_ai.await_args.kwargs["reasoning_level"], "high")

    async def test_private_voice_request_sends_record_message(self) -> None:
        send_json = AsyncMock()
        event = private_event("用语音介绍自己")
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="high")
        with (
            patch("app.routes.onebot.database.get_recent_messages", return_value=[]),
            patch("app.routes.onebot.select_auto_route", return_value=route),
            patch(
                "app.routes.onebot.companion_service.load_config",
                return_value={"chat_model_id": "auto", "chat_reasoning_level": "auto"},
            ),
            patch(
                "app.routes.onebot.chat_with_ai",
                new=AsyncMock(
                    return_value=ChatResult(
                        reply="我在这里",
                        replies=["我在这里"],
                        speech_emotion="gentle",
                    )
                ),
            ) as chat_with_ai,
            patch(
                "app.routes.onebot.companion_service.synthesize_speech_wav",
                return_value=b"RIFFvoice",
            ) as synthesize,
            patch(
                "app.routes.onebot.companion_service.should_use_qq_voice",
                wraps=companion_service.should_use_qq_voice,
            ) as should_use_voice,
        ):
            handled = await process_onebot_event(event, send_json)

        self.assertTrue(handled)
        payload = send_json.await_args.args[0]
        self.assertEqual(payload["action"], "send_private_msg")
        self.assertEqual(payload["params"]["message"][0]["type"], "record")
        self.assertTrue(payload["params"]["message"][0]["data"]["file"].startswith("base64://"))
        should_use_voice.assert_called_once_with("用语音介绍自己", explicitly_requested=True)
        self.assertTrue(chat_with_ai.await_args.kwargs["voice_reply_requested"])
        synthesize.assert_called_once_with(
            "我在这里。",
            context="用语音介绍自己",
            emotion="gentle",
            require_configured_engine=True,
            model_id="deepseek-v4-flash",
        )

    async def test_private_voice_failure_falls_back_to_text(self) -> None:
        send_json = AsyncMock()
        event = private_event("用语音回复我")
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="high")
        with (
            patch("app.routes.onebot.database.get_recent_messages", return_value=[]),
            patch("app.routes.onebot.select_auto_route", return_value=route),
            patch(
                "app.routes.onebot.chat_with_ai",
                new=AsyncMock(return_value=ChatResult(reply="现在不行", replies=["现在不行"])),
            ),
            patch(
                "app.routes.onebot.companion_service.synthesize_speech_wav",
                side_effect=OSError("voice failed"),
            ),
            patch("app.routes.onebot.companion_service.should_use_qq_voice", return_value=True),
        ):
            handled = await process_onebot_event(event, send_json)

        self.assertTrue(handled)
        payload = send_json.await_args.args[0]
        self.assertEqual(payload["params"]["message"], "语音刚刚没发出来，我先打字告诉你：现在不行。")

    async def test_group_chat_uses_temporary_context_without_database_writes(self) -> None:
        send_json = AsyncMock()
        event = group_event("你们觉得这个游戏怎么样", user_id="777")
        history = [{"role": "user", "content": "另一个成员：刚开了一局"}]
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="high")
        with (
            patch("app.routes.onebot.group_is_allowed", return_value=True),
            patch("app.routes.onebot.group_mention_required", return_value=True),
            patch("app.routes.onebot.get_group_history", return_value=history),
            patch("app.routes.onebot.select_auto_route", return_value=route),
            patch(
                "app.routes.onebot.chat_in_qq_group",
                new=AsyncMock(return_value=ChatResult(reply="看起来挺有意思", replies=["看起来挺有意思"])),
            ) as group_chat,
            patch("app.routes.onebot.append_group_exchange") as append_exchange,
            patch("app.db.save_message") as save_message,
            patch("app.routes.onebot.companion_service.should_use_qq_voice", return_value=False),
        ):
            handled = await process_onebot_event(event, send_json)

        self.assertTrue(handled)
        save_message.assert_not_called()
        group_chat.assert_awaited_once()
        self.assertEqual(group_chat.await_args.kwargs["sender_name"], "测试成员")
        self.assertEqual(group_chat.await_args.kwargs["history"], history)
        self.assertFalse(group_chat.await_args.kwargs["voice_reply_requested"])
        append_exchange.assert_called_once_with(
            "12345",
            "测试成员",
            "你们觉得这个游戏怎么样",
            ["看起来挺有意思"],
        )
        payload = send_json.await_args.args[0]
        self.assertEqual(payload["action"], "send_group_msg")
        self.assertEqual(payload["params"]["group_id"], 12345)

    async def test_group_voice_request_sends_record_message(self) -> None:
        send_json = AsyncMock()
        event = group_event("用语音介绍一下自己", user_id="777")
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="high")
        with (
            patch("app.routes.onebot.group_is_allowed", return_value=True),
            patch("app.routes.onebot.group_mention_required", return_value=True),
             patch("app.routes.onebot.get_group_history", return_value=[]),
             patch("app.routes.onebot.select_auto_route", return_value=route),
             patch(
                 "app.routes.onebot.companion_service.load_config",
                 return_value={"chat_model_id": "auto", "chat_reasoning_level": "auto"},
             ),
             patch(
                 "app.routes.onebot.chat_in_qq_group",
                 new=AsyncMock(
                     return_value=ChatResult(
                         reply="我是澪",
                         replies=["我是澪"],
                         speech_emotion="cheerful",
                     )
                 ),
             ) as group_chat,
            patch("app.routes.onebot.append_group_exchange"),
            patch(
                "app.routes.onebot.companion_service.synthesize_speech_wav",
                return_value=b"RIFFgroupvoice",
            ) as synthesize,
            patch(
                "app.routes.onebot.companion_service.should_use_qq_voice",
                return_value=True,
            ) as should_use_voice,
        ):
            handled = await process_onebot_event(event, send_json)

        self.assertTrue(handled)
        payload = send_json.await_args.args[0]
        self.assertEqual(payload["action"], "send_group_msg")
        self.assertEqual(payload["params"]["group_id"], 12345)
        self.assertEqual(payload["params"]["message"][0]["type"], "record")
        self.assertTrue(payload["params"]["message"][0]["data"]["file"].startswith("base64://"))
        self.assertTrue(group_chat.await_args.kwargs["voice_reply_requested"])
        should_use_voice.assert_called_once_with("用语音介绍一下自己", explicitly_requested=True)
        synthesize.assert_called_once_with(
            "我是澪。",
            context="用语音介绍一下自己",
            emotion="cheerful",
            require_configured_engine=True,
            model_id="deepseek-v4-flash",
        )

    async def test_group_voice_failure_falls_back_to_group_text(self) -> None:
        send_json = AsyncMock()
        event = group_event("用语音回复我", user_id="777")
        route = SimpleNamespace(model_id="deepseek-v4-flash", reasoning_level="high")
        with (
            patch("app.routes.onebot.group_is_allowed", return_value=True),
            patch("app.routes.onebot.group_mention_required", return_value=True),
            patch("app.routes.onebot.get_group_history", return_value=[]),
            patch("app.routes.onebot.select_auto_route", return_value=route),
            patch(
                "app.routes.onebot.chat_in_qq_group",
                new=AsyncMock(return_value=ChatResult(reply="我在这里", replies=["我在这里"])),
            ),
            patch("app.routes.onebot.append_group_exchange"),
            patch(
                "app.routes.onebot.companion_service.synthesize_speech_wav",
                side_effect=OSError("voice failed"),
            ),
            patch("app.routes.onebot.companion_service.should_use_qq_voice", return_value=True),
        ):
            handled = await process_onebot_event(event, send_json)

        self.assertTrue(handled)
        payload = send_json.await_args.args[0]
        self.assertEqual(payload["action"], "send_group_msg")
        self.assertEqual(payload["params"]["group_id"], 12345)
        self.assertEqual(payload["params"]["message"], "语音刚刚没发出来，我先打字告诉你：我在这里。")


class OneBotDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.websocket = SimpleNamespace(send_json=AsyncMock())
        self.connection = OneBotConnection(
            websocket=self.websocket,
            lock=asyncio.Lock(),
        )
        self.worker = asyncio.create_task(_outbox_worker(self.connection))

    async def asyncTearDown(self) -> None:
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)

    async def _ack_next_payload(self) -> dict:
        for _ in range(20):
            if self.websocket.send_json.await_args_list:
                payload = self.websocket.send_json.await_args_list[-1].args[0]
                _resolve_delivery_ack(
                    self.connection,
                    {"echo": payload["echo"], "status": "ok", "retcode": 0},
                )
                return payload
            await asyncio.sleep(0)
        self.fail("发送队列没有调用 WebSocket")

    async def test_outbox_sends_in_order_and_waits_for_ack(self) -> None:
        first = asyncio.create_task(_enqueue_connection(self.connection, {"echo": "first"}))
        second = asyncio.create_task(_enqueue_connection(self.connection, {"echo": "second"}))

        first_payload = await self._ack_next_payload()
        await first
        self.assertEqual(first_payload["echo"], "first")

        second_payload = await self._ack_next_payload()
        await second
        self.assertEqual(second_payload["echo"], "second")
        self.assertEqual(
            [call.args[0]["echo"] for call in self.websocket.send_json.await_args_list],
            ["first", "second"],
        )

    async def test_ack_timeout_does_not_retry_or_duplicate_message(self) -> None:
        test_settings = replace(
            settings,
            qq_delivery_ack_timeout_seconds=0.01,
            qq_delivery_max_retries=2,
        )
        with patch("app.routes.onebot.settings", test_settings):
            with self.assertRaises(asyncio.TimeoutError):
                await _enqueue_connection(self.connection, {"echo": "timeout"})

        self.assertEqual(self.websocket.send_json.await_count, 1)
        self.assertEqual(self.connection.pending_acks, {})

    async def test_delivery_ack_routes_to_pending_request(self) -> None:
        future = asyncio.get_running_loop().create_future()
        self.connection.pending_acks["request-1"] = future

        handled = _resolve_delivery_ack(
            self.connection,
            {"echo": "request-1", "status": "ok", "retcode": 0},
        )

        self.assertTrue(handled)
        self.assertEqual((await future)["retcode"], 0)

    async def test_closing_connection_rejects_new_delivery(self) -> None:
        self.connection.closing = True
        with self.assertRaisesRegex(ConnectionError, "正在关闭"):
            await _enqueue_connection(self.connection, {"echo": "late"})
        self.assertEqual(self.connection.outbox.qsize(), 0)

    async def test_disconnect_failure_finishes_queued_requests(self) -> None:
        test_settings = replace(settings, qq_delivery_max_retries=0)
        with patch("app.routes.onebot.settings", test_settings):
            first = asyncio.create_task(
                _enqueue_connection(self.connection, {"echo": "first"})
            )
            second = asyncio.create_task(
                _enqueue_connection(self.connection, {"echo": "second"})
            )
            await self._ack_next_payload()
            await first

            # The second request is allowed to enter the worker, but no ACK arrives.
            await asyncio.sleep(0)
            self.assertIn("second", self.connection.pending_acks)
            _fail_connection_pending(self.connection, RuntimeError("断线"))
            with self.assertRaises(RuntimeError):
                await second

    async def test_privacy_disconnect_closes_connection_and_cancels_work(self) -> None:
        self.websocket.close = AsyncMock()
        self.connection.worker = self.worker
        pending_ack = asyncio.get_running_loop().create_future()
        self.connection.pending_acks["pending"] = pending_ack
        processing_task = asyncio.create_task(asyncio.Event().wait())
        self.connection.processing_tasks.add(processing_task)
        connection_key = id(self.websocket)
        _active_connections[connection_key] = self.connection

        with patch("app.proactive_service.note_qq_connection_state") as note_state:
            disconnected = await disconnect_all_connections("隐私暂停")

        self.assertEqual(disconnected, 1)
        self.assertNotIn(connection_key, _active_connections)
        self.assertTrue(self.connection.closing)
        self.assertTrue(self.worker.cancelled())
        self.assertTrue(processing_task.cancelled())
        self.websocket.close.assert_awaited_once_with(code=1001, reason="隐私暂停")
        note_state.assert_called_once_with(False)
        with self.assertRaisesRegex(RuntimeError, "隐私暂停"):
            await pending_ack

    async def test_send_failure_closes_connection_and_marks_delivery_unknown(self) -> None:
        self.websocket.close = AsyncMock()
        self.connection.worker = self.worker
        connection_key = id(self.websocket)
        _active_connections[connection_key] = self.connection

        with (
            patch("app.routes.onebot._enqueue_connection", new=AsyncMock(side_effect=asyncio.TimeoutError)),
            patch("app.proactive_service.note_qq_connection_state") as note_state,
        ):
            sent = await send_private_message("42", "测试消息")

        self.assertFalse(sent)
        self.assertNotIn(connection_key, _active_connections)
        self.assertTrue(self.connection.closing)
        self.assertTrue(self.worker.cancelled())
        self.websocket.close.assert_awaited_once()
        self.assertEqual(runtime_health_status()["delivery"]["last_status"], "delivery_unknown")
        note_state.assert_called_once_with(False)

    async def test_test_delivery_returns_message_id_from_ack(self) -> None:
        self.connection.worker = self.worker
        connection_key = id(self.websocket)
        _active_connections[connection_key] = self.connection
        try:
            task = asyncio.create_task(send_private_message_receipt("42", "测试消息"))
            for _ in range(20):
                if self.websocket.send_json.await_args_list:
                    payload = self.websocket.send_json.await_args_list[-1].args[0]
                    _resolve_delivery_ack(
                        self.connection,
                        {
                            "echo": payload["echo"],
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": 9876},
                        },
                    )
                    break
                await asyncio.sleep(0)
            receipt = await task
        finally:
            _active_connections.pop(connection_key, None)

        self.assertTrue(receipt["acknowledged"])
        self.assertEqual(receipt["message_id"], 9876)


if __name__ == "__main__":
    unittest.main()
