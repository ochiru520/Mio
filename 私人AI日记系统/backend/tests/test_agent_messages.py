from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.auto_router import AutoRoute
from app.chat_service import ChatResult
from app.model_registry import (
    ModelProfile,
    _custom_profiles,
    _default_profiles,
    delete_provider,
    hidden_default_providers,
    list_model_profiles,
    public_model_profile,
    restore_default_provider,
)
from app.secret_store import protect_secret
from app.routes.agent import (
    AgentAttachmentRequest,
    AgentChatRequest,
    ConversationUpdateRequest,
    ModelProfileRequest,
    _agent_stats_payload,
    _day_dashboard_payload,
    _conversation_id,
    _conversation_list,
    _message_dict,
    _prepare_attachments,
    agent_chat,
    agent_tasks,
    approve_agent_task,
    cancel_agent_task,
    context_usage,
    create_model_profile,
    delete_conversation,
    rename_conversation,
    remove_model_profile,
)
from app.routes import agent as agent_routes


class AgentMessageMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        self.original_attachment_dir = db.settings.agent_attachment_dir
        self.original_model_profiles_path = db.settings.model_profiles_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "test.db")
        object.__setattr__(db.settings, "agent_attachment_dir", Path(self.temp_dir.name) / "attachments")
        object.__setattr__(db.settings, "model_profiles_path", Path(self.temp_dir.name) / "models.json")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        object.__setattr__(db.settings, "agent_attachment_dir", self.original_attachment_dir)
        object.__setattr__(db.settings, "model_profiles_path", self.original_model_profiles_path)
        self.temp_dir.cleanup()

    def test_message_metadata_is_persisted_for_agent_bubbles(self) -> None:
        db.save_message(
            "assistant",
            "测试回复",
            source="desktop",
            conversation_id="qq_private_test",
            request_id="request-1",
            model_id="deepseek-v4-flash",
            provider_model="deepseek-chat-202608",
            reasoning_level="standard",
            prompt_tokens=120,
            cached_prompt_tokens=40,
            completion_tokens=24,
            reasoning_tokens=8,
            request_cost_yuan=0.00125,
            request_cost_source="official_estimate",
            first_token_latency_ms=321.5,
            total_latency_ms=987.25,
        )

        row = db.get_recent_messages(10, "qq_private_test")[0]
        self.assertEqual(row["request_id"], "request-1")
        self.assertEqual(row["model_id"], "deepseek-v4-flash")
        self.assertEqual(row["provider_model"], "deepseek-chat-202608")
        self.assertEqual(row["reasoning_level"], "standard")
        self.assertEqual(row["prompt_tokens"], 120)
        self.assertEqual(row["cached_prompt_tokens"], 40)
        self.assertEqual(row["completion_tokens"], 24)
        self.assertEqual(row["reasoning_tokens"], 8)
        self.assertAlmostEqual(row["request_cost_yuan"], 0.00125)
        self.assertEqual(row["request_cost_source"], "official_estimate")
        self.assertAlmostEqual(row["first_token_latency_ms"], 321.5)
        self.assertAlmostEqual(row["total_latency_ms"], 987.25)

    def test_message_latency_survives_public_mapping_and_reload(self) -> None:
        message_id = db.save_message(
            "assistant",
            "持久化耗时",
            source="desktop",
            conversation_id="desktop_latency",
            first_token_latency_ms=111.25,
            total_latency_ms=222.5,
        )

        row = db.get_recent_messages(10, "desktop_latency")[0]
        self.assertEqual(row["id"], message_id)
        mapped = _message_dict(row)
        self.assertEqual(mapped["first_token_latency_ms"], 111.25)
        self.assertEqual(mapped["total_latency_ms"], 222.5)

    def test_token_usage_combines_chat_and_screen_by_logical_day(self) -> None:
        message_id = db.save_message(
            "assistant",
            "跨日统计",
            prompt_tokens=120,
            cached_prompt_tokens=20,
            completion_tokens=30,
            reasoning_tokens=10,
        )
        logical_date = db.today_string()
        started_at, _ = db.logical_day_bounds(logical_date)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                (started_at, message_id),
            )
        db.record_screen_analysis_usage(
            prompt_tokens=40,
            completion_tokens=10,
            date=logical_date,
        )

        usage = db.get_token_usage_summary(days=2)

        self.assertEqual(usage["today"]["chat_tokens"], 150)
        self.assertEqual(usage["today"]["screen_tokens"], 50)
        self.assertEqual(usage["today"]["total_tokens"], 200)
        self.assertEqual(usage["today"]["reasoning_tokens"], 10)
        self.assertEqual(usage["total"]["total_tokens"], 200)
        self.assertEqual(len(usage["days"]), 2)

    def test_assistant_messages_can_be_polled_after_id(self) -> None:
        first_id = db.save_message("assistant", "第一条", conversation_id="desktop_notice")
        db.save_message("user", "用户消息", conversation_id="desktop_notice")
        second_id = db.save_message("assistant", "第二条", conversation_id="desktop_notice")

        rows = db.get_messages_after_id(first_id, role="assistant")

        self.assertEqual(db.get_latest_message_id(role="assistant"), second_id)
        self.assertEqual([row["content"] for row in rows], ["第二条"])

    def test_desktop_conversations_can_be_created_listed_and_titled(self) -> None:
        conversation_id = "desktop_test"
        db.create_agent_conversation(conversation_id)
        db.touch_agent_conversation(conversation_id, "这是第一条桌面对话消息，应该成为标题")
        db.save_message(
            "user",
            "这是第一条桌面对话消息，应该成为标题",
            source="desktop",
            conversation_id=conversation_id,
        )

        rows = db.list_agent_conversations()
        self.assertEqual(rows[0]["id"], conversation_id)
        self.assertEqual(rows[0]["title"], "这是第一条桌面对话消息，应该成为标题"[:28])
        self.assertEqual(rows[0]["preview"], "这是第一条桌面对话消息，应该成为标题")

    def test_desktop_conversation_can_be_created_through_http_api(self) -> None:
        test_app = FastAPI()
        test_app.include_router(agent_routes.router)

        with TestClient(test_app) as client:
            response = client.post("/api/agent/conversations", json={"title": "新对话"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["id"].startswith("desktop_"))
        self.assertEqual(payload["title"], "新对话")
        self.assertEqual(payload["kind"], "desktop")
        self.assertIsNotNone(db.get_agent_conversation(payload["id"]))

    def test_desktop_pet_is_a_fixed_conversation_window(self) -> None:
        db.save_message(
            "assistant",
            "我在这里",
            source="desktop_pet",
            conversation_id="desktop_pet",
        )

        conversations = _conversation_list()
        pet = next(item for item in conversations if item["id"] == "desktop_pet")

        self.assertEqual(_conversation_id("desktop_pet"), "desktop_pet")
        self.assertEqual(pet["kind"], "pet")
        self.assertEqual(pet["title"], "桌宠Mio")
        self.assertEqual(pet["preview"], "我在这里")

    def test_desktop_pet_is_not_duplicated_by_legacy_agent_conversation_row(self) -> None:
        db.create_agent_conversation("desktop_pet", "错误的自动标题")

        conversations = _conversation_list()
        matching = [item for item in conversations if item["id"] == "desktop_pet"]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["kind"], "pet")
        self.assertEqual(matching[0]["title"], "桌宠Mio")

    def test_desktop_conversation_can_be_renamed_and_deleted(self) -> None:
        conversation_id = "desktop_manage_test"
        attachment = Path(self.temp_dir.name) / "attachments" / "2026-08-01" / "test.png"
        attachment.parent.mkdir(parents=True)
        attachment.write_bytes(b"test")
        db.create_agent_conversation(conversation_id)
        db.save_message(
            "user",
            "需要删除的消息",
            source="desktop",
            conversation_id=conversation_id,
            attachments_json=json.dumps([{"url": "/agent-files/2026-08-01/test.png"}]),
        )
        db.remember_pending_thread(conversation_id, "需要删除的待跟进事项")
        db.replace_memory("conversation_summary", "需要删除的摘要", tags=conversation_id)
        db.log_companion_action(conversation_id, "test", "{}", "done")

        renamed = asyncio.run(
            rename_conversation(conversation_id, ConversationUpdateRequest(title="  新的 对话名称  "))
        )
        self.assertEqual(renamed["title"], "新的 对话名称")

        result = asyncio.run(delete_conversation(conversation_id))
        self.assertTrue(result["ok"])
        self.assertEqual(result["attachment_cleanup"]["deleted"], 1)
        self.assertIsNone(db.get_agent_conversation(conversation_id))
        self.assertEqual(db.get_recent_messages(10, conversation_id), [])
        self.assertEqual(db.list_open_pending_threads(conversation_id), [])
        self.assertIsNone(db.get_latest_memory("conversation_summary", tags=conversation_id))
        self.assertFalse(attachment.exists())
        with db.get_conn() as conn:
            action_count = conn.execute(
                "SELECT COUNT(*) FROM companion_actions WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
        self.assertEqual(action_count, 0)

    def test_conversation_is_not_deleted_when_attachment_cleanup_fails(self) -> None:
        conversation_id = "desktop_attachment_busy"
        attachment = Path(self.temp_dir.name) / "attachments" / "2026-08-01" / "busy.png"
        attachment.parent.mkdir(parents=True)
        attachment.write_bytes(b"busy")
        db.create_agent_conversation(conversation_id)
        db.save_message(
            "user",
            "附件仍被占用",
            source="desktop",
            conversation_id=conversation_id,
            attachments_json=json.dumps([{"url": "/agent-files/2026-08-01/busy.png"}]),
        )

        with patch("app.conversation_service._move_attachment", side_effect=OSError("file locked")):
            with self.assertRaisesRegex(Exception, "对话未删除"):
                asyncio.run(delete_conversation(conversation_id))

        self.assertIsNotNone(db.get_agent_conversation(conversation_id))
        self.assertEqual(len(db.get_recent_messages(10, conversation_id)), 1)
        self.assertTrue(attachment.exists())

    def test_conversation_delete_restores_attachments_when_database_delete_fails(self) -> None:
        conversation_id = "desktop_db_locked"
        attachment = Path(self.temp_dir.name) / "attachments" / "2026-08-01" / "restore.png"
        attachment.parent.mkdir(parents=True)
        attachment.write_bytes(b"restore")
        db.create_agent_conversation(conversation_id)
        db.save_message(
            "user",
            "数据库锁定",
            source="desktop",
            conversation_id=conversation_id,
            attachments_json=json.dumps([{"url": "/agent-files/2026-08-01/restore.png"}]),
        )

        with patch("app.routes.conversations.db.delete_agent_conversation", side_effect=OSError("database locked")):
            with self.assertRaisesRegex(Exception, "附件已恢复"):
                asyncio.run(delete_conversation(conversation_id))

        self.assertTrue(attachment.exists())
        self.assertIsNotNone(db.get_agent_conversation(conversation_id))

    def test_attachment_cleanup_rejects_paths_outside_attachment_root(self) -> None:
        outside = Path(self.temp_dir.name) / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        result = agent_routes._delete_archived_attachments(
            [json.dumps([{"url": "/agent-files/../outside.txt"}])]
        )

        self.assertEqual(result["rejected"], 1)
        self.assertTrue(outside.exists())

    def test_attachment_rollback_failure_preserves_staging_copy(self) -> None:
        from app import conversation_service

        first = Path(self.temp_dir.name) / "attachments" / "2026-08-01" / "first.png"
        second = Path(self.temp_dir.name) / "attachments" / "2026-08-01" / "second.png"
        first.parent.mkdir(parents=True)
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        records = [json.dumps([
            {"url": "/agent-files/2026-08-01/first.png"},
            {"url": "/agent-files/2026-08-01/second.png"},
        ])]
        real_move = conversation_service._move_attachment
        calls = 0

        def fail_after_first_move(source, target):
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise OSError("move locked")
            return real_move(source, target)

        with patch("app.conversation_service._move_attachment", side_effect=fail_after_first_move):
            with self.assertRaisesRegex(OSError, "暂存文件已保留") as raised:
                conversation_service.stage_archived_attachments(records, strict=True)

        staging_path = Path(str(raised.exception).split("暂存文件已保留在 ", 1)[1].split("：", 1)[0])
        self.assertTrue(staging_path.is_dir())
        self.assertEqual((staging_path / "0").read_bytes(), b"first")
        self.assertTrue(second.exists())

    def test_agent_task_list_and_cancel_keep_audit_record(self) -> None:
        task_id = db.log_companion_action(
            "desktop_test",
            "remember_thread",
            json.dumps({"type": "remember_thread", "content": "明天继续测试"}, ensure_ascii=False),
            "needs_confirmation",
            source_message_id=17,
            requires_confirmation=True,
        )

        rows = asyncio.run(agent_tasks(limit=20, conversation_id="desktop_test"))
        self.assertEqual(rows[0]["id"], task_id)
        self.assertEqual(rows[0]["title"], "记录待跟进话题")
        self.assertTrue(rows[0]["requires_confirmation"])

        cancelled = asyncio.run(cancel_agent_task(task_id))
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIn("用户已取消", cancelled["result"])
        self.assertTrue(cancelled["finished_at"])

    def test_agent_task_can_be_approved_and_executed(self) -> None:
        source_message_id = db.save_message(
            "user",
            "明天继续测试 Agent",
            source="desktop",
            conversation_id="desktop_test",
        )
        task_id = db.log_companion_action(
            "desktop_test",
            "remember_thread",
            json.dumps({
                "type": "remember_thread",
                "content": "明天继续测试 Agent",
                "follow_up_after": "2026-08-07T10:00:00",
                "confidence": 0.99,
            }, ensure_ascii=False),
            "needs_confirmation",
            source_message_id=source_message_id,
            requires_confirmation=True,
        )
        executor = AsyncMock(return_value="thread:9")
        with (
            patch.dict("app.companion_action_service.ACTION_POLICIES", {"remember_thread": "confirmation"}),
            patch("app.companion_action_service.execute_companion_action_primitive", executor),
        ):
            approved = asyncio.run(approve_agent_task(task_id))

        self.assertEqual(approved["status"], "executed")
        self.assertEqual(approved["result"], "thread:9")
        self.assertTrue(approved["approved_at"])
        self.assertTrue(approved["finished_at"])
        executor.assert_awaited_once()

    def test_attachment_metadata_is_persisted_and_returned(self) -> None:
        attachments = [{
            "kind": "text",
            "name": "说明.txt",
            "mime_type": "text/plain",
            "size": 6,
            "url": "/agent-files/2026-08-01/test.txt",
        }]
        db.save_message(
            "user",
            "看这个文件",
            source="desktop",
            conversation_id="qq_private_test",
            attachments_json=json.dumps(attachments, ensure_ascii=False),
        )

        row = db.get_recent_messages(10, "qq_private_test")[0]
        result = _message_dict(row)
        self.assertEqual(result["attachments"], attachments)
        self.assertNotIn("attachments_json", result)

    def test_context_usage_reports_real_character_budget(self) -> None:
        conversation_id = "desktop_context_test"
        db.create_agent_conversation(conversation_id)
        db.save_message("user", "这是上下文内容", source="desktop", conversation_id=conversation_id)

        result = asyncio.run(context_usage(conversation_id))

        self.assertGreater(result["used_chars"], 0)
        self.assertEqual(result["max_chars"], db.settings.chat_context_max_chars)
        self.assertGreater(result["percent"], 0)

    def test_day_dashboard_exposes_detailed_state_and_history(self) -> None:
        logical_date = db.today_string()
        db.upsert_daily_state(
            logical_date,
            "done",
            "满足",
            "完成了 Agent 的界面调整",
            "中途有些分心",
            "确认构建结果",
            daily_thirty_reason="持续开发超过三十分钟",
            mood_score=4,
        )

        result = _day_dashboard_payload()

        self.assertEqual(result["logical_date"], logical_date)
        self.assertEqual(result["today_state"]["mood_score"], 4)
        self.assertEqual(result["today_state"]["daily_thirty_reason"], "持续开发超过三十分钟")
        self.assertEqual(result["state_history"][-1]["date"], logical_date)
        self.assertIn("auto_diary", result)

    def test_agent_stats_exposes_summary_calendar_and_mood_trend(self) -> None:
        logical_date = db.today_string()
        logical_day = date.fromisoformat(logical_date)
        db.upsert_diary(logical_date, "测试日记", "# 测试日记", daily_thirty_status="done")
        db.upsert_daily_state(
            logical_date,
            "done",
            "平稳",
            "完成测试",
            "",
            "继续验证",
            mood_score=3,
        )

        result = _agent_stats_payload(logical_day.year, logical_day.month)

        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["by_status"]["done"], 1)
        self.assertEqual(result["calendar"][0]["date"], logical_date)
        self.assertEqual(result["mood_trend"][-1]["mood_score"], 3)

    def test_agent_chat_passes_model_reasoning_and_text_attachment(self) -> None:
        captured: dict[str, object] = {}

        async def fake_chat(message: str, **kwargs):
            captured["message"] = message
            captured.update(kwargs)
            return ChatResult(reply="收到", replies=["收到"], model_id=kwargs["model_id"])

        payload = AgentChatRequest(
            message="读一下",
            model_id="test-model",
            reasoning_level="deep",
            conversation_id="desktop_agent_test",
            attachments=[
                AgentAttachmentRequest(
                    kind="text",
                    name="计划.md",
                    mime_type="text/markdown",
                    text="# 第一阶段",
                    size=12,
                )
            ],
        )
        db.create_agent_conversation("desktop_agent_test")
        with (
            patch("app.routes.agent.resolve_model_id", return_value="test-model"),
            patch("app.routes.agent.chat_with_ai", new=fake_chat),
        ):
            result = asyncio.run(agent_chat(payload))

        self.assertEqual(captured["reasoning_level"], "deep")
        self.assertEqual(captured["model_id"], "test-model")
        self.assertEqual(captured["conversation_id"], "desktop_agent_test")
        self.assertEqual(captured["text_attachments"][0].name, "计划.md")
        self.assertEqual(result["reply"], "收到")

    def test_agent_chat_auto_mode_applies_router_model_and_reasoning(self) -> None:
        captured: dict[str, object] = {}

        async def fake_chat(message: str, **kwargs):
            captured.update(kwargs)
            return ChatResult(
                reply="收到",
                replies=["收到"],
                model_id=kwargs["model_id"],
                reasoning_level=kwargs["reasoning_level"],
            )

        route = AutoRoute(
            model_id="deepseek-v4-flash",
            model="deepseek-v4-flash",
            reasoning_level="off",
            difficulty="simple",
            reason="普通对话",
        )
        payload = AgentChatRequest(
            message="晚上好",
            model_id="auto",
            reasoning_level="auto",
            conversation_id="desktop_auto_test",
        )
        db.create_agent_conversation("desktop_auto_test")

        with (
            patch("app.routes.agent.select_auto_route", return_value=route),
            patch("app.routes.agent.chat_with_ai", new=fake_chat),
            patch(
                "app.routes.agent.route_observation_service.record_completed_route"
            ) as record_route,
        ):
            result = asyncio.run(agent_chat(payload))

        self.assertEqual(captured["model_id"], "deepseek-v4-flash")
        self.assertEqual(captured["reasoning_level"], "off")
        self.assertEqual(result["auto_routing"]["difficulty"], "simple")
        record_route.assert_called_once()
        self.assertEqual(record_route.call_args.kwargs["mode"], "automatic")
        self.assertEqual(record_route.call_args.kwargs["selected_model_id"], "deepseek-v4-flash")

    def test_agent_chat_continues_screen_context_with_fresh_analysis(self) -> None:
        conversation_id = "desktop_screen_follow_up"
        db.create_agent_conversation(conversation_id)
        db.save_message(
            "assistant",
            "刚才还是游戏主菜单。",
            source="screen",
            conversation_id=conversation_id,
        )
        screen_result = ChatResult(
            reply="现在已经进入存档选择画面了。",
            replies=["现在已经进入存档选择画面了。"],
            request_id="screen-follow-up",
            model_id="vision-model",
            reasoning_level="low",
        )
        with (
            patch(
                "app.routes.agent.screen_observation_service.analyze_screen_chat_follow_up",
                new=AsyncMock(return_value=screen_result),
            ) as analyze,
            patch("app.routes.agent.chat_with_ai", new=AsyncMock()) as chat,
        ):
            response = asyncio.run(agent_chat(AgentChatRequest(
                message="现在呢",
                conversation_id=conversation_id,
            )))

        analyze.assert_awaited_once()
        chat.assert_not_awaited()
        self.assertEqual(response["reply"], "现在已经进入存档选择画面了。")
        self.assertIsNone(response["auto_routing"])

    def test_prepare_image_attachment_archives_local_copy(self) -> None:
        image_data = "data:image/png;base64,iVBORw0KGgo="
        images, text_files, metadata = _prepare_attachments([
            AgentAttachmentRequest(
                kind="image",
                name="截图.png",
                mime_type="image/png",
                data_url=image_data,
                size=8,
            )
        ])

        self.assertEqual(len(images), 1)
        self.assertEqual(text_files, [])
        self.assertEqual(metadata[0]["kind"], "image")
        archived = Path(self.temp_dir.name) / "attachments" / db.today_string() / Path(metadata[0]["url"]).name
        self.assertTrue(archived.exists())

    def test_prepare_ephemeral_image_does_not_archive_local_copy(self) -> None:
        image_data = "data:image/png;base64,iVBORw0KGgo="
        attachment_root = Path(self.temp_dir.name) / "attachments"
        files_before = list(attachment_root.rglob("*")) if attachment_root.exists() else []
        images, text_files, metadata = _prepare_attachments([
            AgentAttachmentRequest(
                kind="image",
                name="screen.jpg",
                mime_type="image/png",
                data_url=image_data,
                size=8,
                ephemeral=True,
            )
        ])

        self.assertEqual(len(images), 1)
        self.assertEqual(text_files, [])
        self.assertEqual(metadata, [])
        files_after = list(attachment_root.rglob("*")) if attachment_root.exists() else []
        self.assertEqual(files_after, files_before)

    def test_prepare_attachment_failure_removes_files_archived_earlier_in_batch(self) -> None:
        attachment_root = Path(self.temp_dir.name) / "attachments"
        requested = [
            AgentAttachmentRequest(
                kind="text",
                name="说明.txt",
                mime_type="text/plain",
                text="先归档的内容",
                size=18,
            ),
            AgentAttachmentRequest(
                kind="unsupported",
                name="坏附件.bin",
                mime_type="application/octet-stream",
                size=4,
            ),
        ]

        with self.assertRaisesRegex(ValueError, "暂不支持"):
            _prepare_attachments(requested)

        archived_files = [path for path in attachment_root.rglob("*") if path.is_file()]
        self.assertEqual(archived_files, [])

    def test_custom_model_profile_can_be_added_without_echoing_key(self) -> None:
        payload = ModelProfileRequest(
            provider_name="测试供应商",
            display_name="视觉模型",
            model="vision-test",
            base_url="https://example.test/v1",
            api_key="secret-test-key",
            supports_vision=True,
            input_price_cny_per_million=1.5,
            output_price_cny_per_million=6,
        )
        result = asyncio.run(create_model_profile(payload))

        self.assertTrue(result["id"].startswith("model-"))
        self.assertTrue(result["supports_vision"])
        self.assertTrue(result["api_key_configured"])
        self.assertEqual(result["auth_scheme"], "bearer")
        self.assertNotIn("api_key", result)
        saved_text = db.settings.model_profiles_path.read_text(encoding="utf-8")
        self.assertNotIn("secret-test-key", saved_text)
        self.assertIn("api_key_protected", saved_text)
        asyncio.run(remove_model_profile(result["id"]))
        saved = json.loads(db.settings.model_profiles_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["providers"], [])
        self.assertEqual(saved["models"], [])

    def test_custom_provider_group_can_be_deleted_at_once(self) -> None:
        created_ids = []
        for model in ("group-model-a", "group-model-b"):
            result = asyncio.run(create_model_profile(ModelProfileRequest(
                provider_name="待删除供应商",
                display_name=model,
                model=model,
                base_url="https://example.test/v1",
                api_key="secret-test-key",
            )))
            created_ids.append(result["id"])
        asyncio.run(create_model_profile(ModelProfileRequest(
            provider_name="保留供应商",
            display_name="保留模型",
            model="fallback-model",
            base_url="https://fallback.example.test/v1",
            api_key="fallback-secret-key",
        )))

        provider_id = next(
            profile.provider_id
            for profile in list_model_profiles()
            if profile.provider_name == "待删除供应商"
        )
        deleted_ids = delete_provider(provider_id)

        self.assertEqual(set(deleted_ids), set(created_ids))
        self.assertFalse(any(profile.provider_name == "待删除供应商" for profile in list_model_profiles()))

    def test_default_provider_can_be_hidden_and_restored_without_changing_env(self) -> None:
        default_profile = ModelProfile(
            id="fixture-default-model",
            provider_id="provider-builtin-fixture",
            provider_name="夹具内置供应商",
            display_name="夹具默认模型",
            model="fixture-default-model",
            base_urls=("https://fixture.example.test/v1",),
            api_key="fixture-secret-key",
            is_default=True,
        )
        provider_name = default_profile.provider_name
        asyncio.run(create_model_profile(ModelProfileRequest(
            provider_name="保留供应商",
            display_name="保留模型",
            model="fallback-model",
            base_url="https://fallback.example.test/v1",
            api_key="fallback-secret-key",
        )))

        with patch("app.model_registry._default_profiles", return_value=[default_profile]):
            deleted_ids = delete_provider(default_profile.provider_id)

            self.assertEqual(deleted_ids, [default_profile.id])
            self.assertIn(provider_name, hidden_default_providers())
            self.assertFalse(any(profile.provider_name == provider_name for profile in list_model_profiles()))

            restored_ids = restore_default_provider(default_profile.provider_id)

            self.assertEqual(restored_ids, [default_profile.id])
            self.assertNotIn(provider_name, hidden_default_providers())
            self.assertTrue(any(profile.provider_name == provider_name for profile in list_model_profiles()))

    def test_legacy_custom_model_uses_automatic_auth_discovery(self) -> None:
        db.settings.model_profiles_path.write_text(
            json.dumps([
                {
                    "id": "custom-legacy",
                    "provider_name": "旧供应商",
                    "display_name": "旧模型",
                    "model": "legacy-model",
                    "base_url": "https://example.test/v1",
                    "api_key_protected": protect_secret("secret-test-key"),
                }
            ]),
            encoding="utf-8",
        )

        profile = next(item for item in _custom_profiles() if item.id == "custom-legacy")

        self.assertEqual(profile.auth_scheme, "auto")

    def test_unreadable_protected_model_key_remains_visible_for_local_reentry(self) -> None:
        db.settings.model_profiles_path.write_text(
            json.dumps([
                {
                    "id": "custom-unreadable",
                    "provider_name": "测试供应商",
                    "display_name": "无法解密的模型",
                    "model": "test-model",
                    "base_url": "https://example.test/v1",
                    "api_key_protected": "dpapi:broken",
                }
            ]),
            encoding="utf-8",
        )

        with patch("app.model_registry.unprotect_secret", side_effect=OSError("DPAPI failed")):
            profiles = _custom_profiles()

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].id, "custom-unreadable")
        self.assertEqual(profiles[0].api_key, "")
        self.assertIn("本机重新输入", profiles[0].api_key_error)
        public = public_model_profile(profiles[0])
        self.assertFalse(public["api_key_configured"])
        self.assertTrue(public["requires_key_reentry"])


class TodayStateAnalysisRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.settings.db_path
        object.__setattr__(db.settings, "db_path", Path(self.temp_dir.name) / "state-retry.db")
        db.init_db()
        db.update_daily_thirty("unknown", "")
        db.save_message("user", "今天继续测试", conversation_id="state_retry")
        agent_routes._analyzed_state_days.clear()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        agent_routes._analyzed_state_days.clear()
        self.temp_dir.cleanup()

    async def test_failed_analysis_can_retry_on_next_chat(self) -> None:
        today = db.today_string()
        with patch("app.routes.chat.analyze_today_state", new=AsyncMock(side_effect=RuntimeError("temporary"))):
            agent_routes._schedule_today_state_analysis()
            await asyncio.gather(*list(agent_routes._background_state_analysis_tasks))

        self.assertNotIn(today, agent_routes._analyzed_state_days)

        with patch("app.routes.chat.analyze_today_state", new=AsyncMock(return_value={"ok": True})) as analyze:
            agent_routes._schedule_today_state_analysis()
            await asyncio.gather(*list(agent_routes._background_state_analysis_tasks))

        analyze.assert_awaited_once()
        self.assertIn(today, agent_routes._analyzed_state_days)


if __name__ == "__main__":
    unittest.main()
