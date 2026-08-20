from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.mio_profile import DEFAULT_PROFILE
from app.prompts import build_system_prompt
from app.screen_observation_service import _screen_persona_context


class PersonaPromptTests(unittest.TestCase):
    def test_distribution_default_profile_matches_package_kind(self) -> None:
        identity = DEFAULT_PROFILE["identity"]
        preferences = DEFAULT_PROFILE["preferences"]
        encoded = json.dumps(DEFAULT_PROFILE, ensure_ascii=False)

        self.assertIn("默认称呼", preferences["user_address"])
        self.assertIn("直接说明", DEFAULT_PROFILE["behavior"]["mood_quirks"])
        self.assertNotIn("青梅竹马", encoded)
        self.assertNotIn("小女友", encoded)
        try:
            from app.public_distribution_defaults import PUBLIC_DEFAULT_PROFILE
        except ImportError:
            self.assertEqual(identity["name"], "Mio")
            self.assertTrue(identity["age_feel"])
        else:
            self.assertEqual(DEFAULT_PROFILE, PUBLIC_DEFAULT_PROFILE)
            self.assertEqual(identity["name"], "Mio")
            self.assertEqual(identity["age_feel"], "")
            self.assertIn("可配置", identity["core"])
            self.assertNotIn("高中女生", encoded)
            self.assertNotIn("恋爱", encoded)
            self.assertNotIn("吃醋", encoded)

    def test_custom_relationship_still_reaches_final_prompt(self) -> None:
        prompt = build_system_prompt(
            [
                {
                    "name": "澪运行时说明书",
                    "path": "runtime.md",
                    "content": "澪是示例用户的青梅竹马和小女友。",
                },
                {
                    "name": "澪当前属性",
                    "path": "profile.json",
                    "content": '{"identity": {"core": "示例用户的青梅竹马小女友"}}',
                },
            ]
        )

        self.assertIn("示例用户的青梅竹马和小女友", prompt)
        self.assertIn("示例用户的青梅竹马小女友", prompt)
        self.assertNotIn("固定用户从小一起长大的青梅竹马", prompt)
        self.assertNotIn("你是用户的私人 AI", prompt)
        self.assertNotIn("不要假装自己是真人", prompt)
        self.assertNotIn("不替代现实关系", prompt)

    def test_base_prompt_does_not_hardcode_private_relationship(self) -> None:
        prompt = build_system_prompt([])

        self.assertNotIn("青梅竹马", prompt)
        self.assertNotIn("小女友", prompt)
        self.assertNotIn("固定用户从小", prompt)

    def test_compact_chat_prompt_keeps_identity_without_full_agent_rules(self) -> None:
        prompt = build_system_prompt(
            [{"name": "Mio 当前属性", "path": "profile.json", "content": "保持温柔直接"}],
            compact=True,
        )

        self.assertIn("保持温柔直接", prompt)
        self.assertIn("普通聊天", prompt)
        self.assertNotIn("每日三十识别", prompt)

    def test_low_risk_ambiguity_is_verified_before_asking(self) -> None:
        prompt = build_system_prompt([])

        self.assertIn("可以低成本查证的歧义", prompt)
        self.assertIn("合川天气", prompt)
        self.assertIn("多个候选查证后仍无法区分", prompt)
        self.assertIn("不可逆", prompt)

    def test_screen_persona_context_uses_current_profile(self) -> None:
        profile = {
            "identity": {"core": "示例用户的长期伙伴"},
            "speaking_style": {"tone": "明快而直接"},
            "preferences": {
                "user_address": "称呼示例用户",
                "relationship_distance": "自然亲近",
            },
        }
        with patch("app.screen_observation_service.load_mio_profile", return_value=profile):
            context = _screen_persona_context()

        self.assertIn("示例用户的长期伙伴", context)
        self.assertIn("明快而直接", context)
        self.assertIn("称呼示例用户", context)
        self.assertNotIn("落", context)


if __name__ == "__main__":
    unittest.main()
