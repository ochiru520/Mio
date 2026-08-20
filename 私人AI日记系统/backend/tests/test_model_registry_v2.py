from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import db
from app.chat_service import ChatResult
from app.model_registry import (
    MODEL_REGISTRY_SCHEMA_VERSION,
    _custom_profiles,
    delete_provider,
    list_model_profiles,
    provider_model_api_mode,
    save_custom_provider,
)
from app.routes.agent import AgentChatRequest, agent_chat
from app.routes.models import remove_provider, router as model_router
from app.secret_store import protect_secret


class ModelRegistryV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_db_path = db.settings.db_path
        self.original_model_profiles_path = db.settings.model_profiles_path
        self.original_companion_config_path = db.settings.companion_config_path
        object.__setattr__(db.settings, "db_path", root / "test.db")
        object.__setattr__(db.settings, "model_profiles_path", root / "models.json")
        object.__setattr__(db.settings, "companion_config_path", root / "companion.json")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        object.__setattr__(db.settings, "model_profiles_path", self.original_model_profiles_path)
        object.__setattr__(db.settings, "companion_config_path", self.original_companion_config_path)
        self.temp_dir.cleanup()

    def _create_provider(self, index: int, *, name: str = "测试供应商") -> tuple[str, list[str]]:
        provider_id, profiles = save_custom_provider(
            {
                "provider_name": name,
                "provider_kind": "relay",
                "provider_protocol": "openai",
                "base_url": f"https://provider-{index}.example.test/v1",
                "api_key": f"secret-{index}",
                "auth_scheme": "auto",
            },
            [
                {"model": f"model-{index}-a", "display_name": f"用户名称 {index}A"},
                {"model": f"model-{index}-b", "display_name": f"用户名称 {index}B"},
            ],
        )
        return provider_id, [profile.id for profile in profiles]

    def test_legacy_json_migrates_deterministically_without_renaming(self) -> None:
        legacy = [
            {
                "id": "custom-legacy-a",
                "provider_name": "我命名的供应商",
                "display_name": "不要改这个名字",
                "model": "gpt-5.6-luna",
                "base_url": "https://legacy.example.test/v1",
                "api_key_protected": protect_secret("legacy-secret"),
            },
            {
                "id": "custom-legacy-b",
                "provider_name": "我命名的供应商",
                "display_name": "第二个自定义名",
                "model": "gpt-5.6-sol",
                "base_url": "https://legacy.example.test/v1",
                "api_key_protected": protect_secret("legacy-secret"),
            },
        ]
        db.settings.model_profiles_path.write_text(
            json.dumps(legacy, ensure_ascii=False),
            encoding="utf-8",
        )

        first = _custom_profiles()
        first_provider_ids = {profile.provider_id for profile in first}
        second = _custom_profiles()
        migrated = json.loads(db.settings.model_profiles_path.read_text(encoding="utf-8"))

        self.assertEqual(migrated["schema_version"], MODEL_REGISTRY_SCHEMA_VERSION)
        self.assertEqual([profile.id for profile in first], ["custom-legacy-a", "custom-legacy-b"])
        self.assertEqual([profile.display_name for profile in first], ["不要改这个名字", "第二个自定义名"])
        self.assertEqual(len(first_provider_ids), 1)
        self.assertEqual(first_provider_ids, {profile.provider_id for profile in second})
        self.assertNotIn("legacy-secret", db.settings.model_profiles_path.read_text(encoding="utf-8"))

    def test_ekti_uses_responses_while_explicit_chat_mode_is_preserved(self) -> None:
        self.assertEqual(
            provider_model_api_mode(
                "openai",
                "gpt-5.6-sol",
                base_url="https://chat.ekti.cc/v1",
            ),
            "responses",
        )
        self.assertEqual(
            provider_model_api_mode(
                "openai",
                "gpt-5.6-sol",
                base_url="https://chat.ekti.cc/v1",
                default_api_mode="chat_completions",
            ),
            "chat_completions",
        )

    def test_schema_three_ekti_models_migrate_without_changing_other_providers(self) -> None:
        ekti_provider_id = "provider-ekti"
        other_provider_id = "provider-other"
        db.settings.model_profiles_path.write_text(
            json.dumps({
                "schema_version": 3,
                "providers": [
                    {
                        "id": ekti_provider_id,
                        "display_name": "11111",
                        "provider_kind": "relay",
                        "provider_protocol": "openai",
                        "base_url": "https://chat.ekti.cc/v1",
                        "api_key_protected": protect_secret("ekti-secret"),
                        "auth_scheme": "auto",
                    },
                    {
                        "id": other_provider_id,
                        "display_name": "其他供应商",
                        "provider_kind": "relay",
                        "provider_protocol": "openai",
                        "base_url": "https://other.example.test/v1",
                        "api_key_protected": protect_secret("other-secret"),
                        "auth_scheme": "auto",
                    },
                ],
                "models": [
                    {
                        "id": "ekti-sol",
                        "provider_id": ekti_provider_id,
                        "model": "gpt-5.6-sol",
                        "display_name": "gpt-5.6-sol",
                        "api_mode": "chat_completions",
                    },
                    {
                        "id": "other-model",
                        "provider_id": other_provider_id,
                        "model": "other-model",
                        "display_name": "other-model",
                        "api_mode": "chat_completions",
                    },
                ],
                "hidden_default_provider_ids": [],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        profiles = {profile.id: profile for profile in _custom_profiles()}
        migrated = json.loads(db.settings.model_profiles_path.read_text(encoding="utf-8"))

        self.assertEqual(migrated["schema_version"], MODEL_REGISTRY_SCHEMA_VERSION)
        self.assertEqual(profiles["ekti-sol"].api_mode, "responses")
        self.assertEqual(profiles["other-model"].api_mode, "chat_completions")
        ekti_provider = next(item for item in migrated["providers"] if item["id"] == ekti_provider_id)
        self.assertEqual(ekti_provider["default_api_mode"], "responses")
        self.assertNotIn("ekti-secret", db.settings.model_profiles_path.read_text(encoding="utf-8"))

    def test_multi_model_create_is_all_or_nothing(self) -> None:
        self._create_provider(1)
        before = db.settings.model_profiles_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "模型ID不能为空"):
            save_custom_provider(
                {
                    "provider_name": "无效供应商",
                    "base_url": "https://invalid.example.test/v1",
                    "api_key": "secret",
                },
                [{"model": "valid"}, {"model": ""}],
            )

        self.assertEqual(db.settings.model_profiles_path.read_bytes(), before)

    def test_model_capabilities_persist_without_changing_display_name(self) -> None:
        provider_id, profiles = save_custom_provider(
            {
                "provider_name": "能力供应商",
                "provider_kind": "relay",
                "provider_protocol": "openai",
                "base_url": "https://capability.example.test/v1",
                "api_key": "secret",
            },
            [{
                "model": "capability-model",
                "display_name": "用户保留名",
                "supports_vision": True,
                "supports_tool_calls": True,
                "supports_structured_output": False,
                "context_window_tokens": 131072,
                "privacy_location": "local_device",
            }],
        )

        restored = next(item for item in _custom_profiles() if item.provider_id == provider_id)

        self.assertEqual(restored.display_name, "用户保留名")
        self.assertTrue(restored.supports_vision)
        self.assertTrue(restored.supports_tool_calls)
        self.assertFalse(restored.supports_structured_output)
        self.assertEqual(restored.context_window_tokens, 131072)
        self.assertEqual(restored.privacy_location, "local_device")
        self.assertEqual(profiles[0].id, restored.id)

    def test_old_inferred_label_is_restored_but_custom_label_is_preserved(self) -> None:
        provider_id = "provider-custom-label-test"
        db.settings.model_profiles_path.write_text(
            json.dumps({
                "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
                "providers": [{
                    "id": provider_id,
                    "display_name": "测试供应商",
                    "provider_kind": "relay",
                    "provider_protocol": "openai",
                    "base_url": "https://labels.example.test/v1",
                    "api_key_protected": protect_secret("secret"),
                    "auth_scheme": "auto",
                }],
                "models": [{
                    "id": "legacy-generated-label",
                    "provider_id": provider_id,
                    "model": "gpt-5.6-luna",
                    "display_name": "GPT-5.6 · Luna",
                    "family_name": "GPT-5.6",
                    "variant_name": "Luna",
                }, {
                    "id": "real-custom-label",
                    "provider_id": provider_id,
                    "model": "gpt-5.6-sol",
                    "display_name": "我自己的 Sol 名称",
                    "family_name": "GPT-5.6",
                    "variant_name": "Sol",
                }],
                "hidden_default_provider_ids": [],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        profiles = _custom_profiles()

        self.assertEqual(profiles[0].display_name, "gpt-5.6-luna")
        self.assertEqual(profiles[1].display_name, "我自己的 Sol 名称")

    def test_blank_registry_provider_lifecycle_passes_five_rounds(self) -> None:
        for index in range(5):
            provider_id, model_ids = self._create_provider(index)
            visible = [profile for profile in list_model_profiles() if profile.provider_id == provider_id]
            self.assertEqual({profile.id for profile in visible}, set(model_ids))

            deleted_ids = delete_provider(provider_id)

            self.assertEqual(set(deleted_ids), set(model_ids))
            self.assertFalse(any(profile.provider_id == provider_id for profile in list_model_profiles()))

    def test_provider_http_lifecycle_passes_five_rounds(self) -> None:
        app = FastAPI()
        app.include_router(model_router, prefix="/api/agent")
        client = TestClient(app)

        for index in range(5):
            created = client.post("/api/agent/providers", json={
                "provider_name": f"接口供应商 {index}",
                "provider_kind": "relay",
                "provider_protocol": "openai",
                "base_url": f"https://api-provider-{index}.example.test/v1",
                "api_key": f"secret-{index}",
                "auth_scheme": "auto",
                "models": [
                    {"model": f"api-model-{index}-a", "display_name": f"接口模型 {index}A"},
                    {"model": f"api-model-{index}-b", "display_name": f"接口模型 {index}B"},
                ],
            })
            self.assertEqual(created.status_code, 200, created.text)
            payload = created.json()
            provider_id = payload["provider"]["provider_id"]
            model_ids = {item["id"] for item in payload["models"]}
            self.assertEqual(len(model_ids), 2)

            deleted = client.delete(f"/api/agent/providers/{provider_id}")

            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(set(deleted.json()["deleted_model_ids"]), model_ids)

    def test_same_display_name_does_not_merge_distinct_provider_ids(self) -> None:
        first_id, _ = self._create_provider(11, name="同名供应商")
        second_id, _ = self._create_provider(12, name="同名供应商")

        self.assertNotEqual(first_id, second_id)
        delete_provider(first_id)
        self.assertTrue(any(profile.provider_id == second_id for profile in list_model_profiles()))

    def test_provider_delete_cleans_all_backend_model_references(self) -> None:
        provider_id, model_ids = self._create_provider(20)
        config = {
            "chat_model_id": model_ids[0],
            "pet_chat_model_id": model_ids[1],
            "screen_vision_model_id": model_ids[0],
        }
        saved_changes: dict[str, object] = {}

        def save_config(changes):
            saved_changes.update(changes)
            return {**config, **changes}

        with (
            patch("app.routes.models.companion_service.load_config", return_value=config),
            patch("app.routes.models.companion_service.save_config", side_effect=save_config),
        ):
            result = asyncio.run(remove_provider(provider_id))

        self.assertEqual(set(result["deleted_model_ids"]), set(model_ids))
        self.assertEqual(saved_changes["chat_model_id"], "auto")
        self.assertEqual(saved_changes["pet_chat_model_id"], "auto")
        self.assertEqual(saved_changes["screen_vision_model_id"], "auto-fast")

    def test_provider_delete_rolls_back_registry_when_reference_save_fails(self) -> None:
        provider_id, model_ids = self._create_provider(21)
        before = db.settings.model_profiles_path.read_bytes()
        with (
            patch("app.routes.models.companion_service.load_config", return_value={
                "chat_model_id": model_ids[0],
                "pet_chat_model_id": "auto",
                "screen_vision_model_id": "auto-fast",
            }),
            patch("app.routes.models.companion_service.save_config", side_effect=OSError("写入失败")),
        ):
            with self.assertRaises(HTTPException):
                asyncio.run(remove_provider(provider_id))

        self.assertEqual(db.settings.model_profiles_path.read_bytes(), before)
        self.assertTrue(any(profile.provider_id == provider_id for profile in list_model_profiles()))


class ChatIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_db_path = db.settings.db_path
        self.original_attachment_dir = db.settings.agent_attachment_dir
        object.__setattr__(db.settings, "db_path", root / "test.db")
        object.__setattr__(db.settings, "agent_attachment_dir", root / "attachments")
        db.init_db()
        db.create_agent_conversation("desktop_idempotency")

    def tearDown(self) -> None:
        object.__setattr__(db.settings, "db_path", self.original_db_path)
        object.__setattr__(db.settings, "agent_attachment_dir", self.original_attachment_dir)
        self.temp_dir.cleanup()

    def test_successful_request_replay_does_not_call_model_or_write_messages_twice(self) -> None:
        call_count = 0

        async def fake_chat(message: str, **kwargs):
            nonlocal call_count
            call_count += 1
            db.save_message(
                "user",
                message,
                source="desktop",
                conversation_id=kwargs["conversation_id"],
                request_id=kwargs["request_id"],
                model_id=kwargs["model_id"],
            )
            db.save_message(
                "assistant",
                "只生成一次",
                source="desktop",
                conversation_id=kwargs["conversation_id"],
                request_id=kwargs["request_id"],
                model_id=kwargs["model_id"],
                request_cost_yuan=0.01,
            )
            return ChatResult(
                reply="只生成一次",
                replies=["只生成一次"],
                request_id=kwargs["request_id"],
                model_id=kwargs["model_id"],
                request_cost_yuan=0.01,
            )

        payload = AgentChatRequest(
            message="幂等测试",
            model_id="test-model",
            conversation_id="desktop_idempotency",
            client_request_id="request-success-1",
        )
        with (
            patch("app.routes.agent.resolve_model_id", return_value="test-model"),
            patch("app.routes.agent.chat_with_ai", new=fake_chat),
        ):
            first = asyncio.run(agent_chat(payload))
            second = asyncio.run(agent_chat(payload))

        rows = db.get_recent_messages(limit=10, conversation_id="desktop_idempotency")
        self.assertEqual(call_count, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(first, second)
        self.assertEqual(first["client_request_id"], "request-success-1")

    def test_failed_request_replay_returns_same_error_without_second_call(self) -> None:
        fake_chat = AsyncMock(side_effect=RuntimeError("固定失败"))
        payload = AgentChatRequest(
            message="失败幂等测试",
            model_id="test-model",
            conversation_id="desktop_idempotency",
            client_request_id="request-failed-1",
        )
        with (
            patch("app.routes.agent.resolve_model_id", return_value="test-model"),
            patch("app.routes.agent.chat_with_ai", fake_chat),
        ):
            with self.assertRaises(HTTPException) as first:
                asyncio.run(agent_chat(payload))
            with self.assertRaises(HTTPException) as second:
                asyncio.run(agent_chat(payload))

        self.assertEqual(fake_chat.await_count, 1)
        self.assertEqual(first.exception.detail, second.exception.detail)
        self.assertEqual(first.exception.detail["request_id"], "request-failed-1")

    def test_reusing_request_id_for_different_payload_is_rejected(self) -> None:
        async def fake_chat(message: str, **kwargs):
            return ChatResult(
                reply="完成",
                replies=["完成"],
                request_id=kwargs["request_id"],
                model_id=kwargs["model_id"],
            )

        first_payload = AgentChatRequest(
            message="第一条",
            model_id="test-model",
            conversation_id="desktop_idempotency",
            client_request_id="request-conflict-1",
        )
        second_payload = first_payload.model_copy(update={"message": "不同内容"})
        with (
            patch("app.routes.agent.resolve_model_id", return_value="test-model"),
            patch("app.routes.agent.chat_with_ai", new=fake_chat),
        ):
            asyncio.run(agent_chat(first_payload))
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(agent_chat(second_payload))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("不能用于不同内容", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
