"""Keep real generated runtime time facts small; no provider/tokenizer required."""
from datetime import datetime
import unittest

from app import chat_service as chat
from app.config import settings


class TimeContextBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        for name, value in (("timezone", "Asia/Shanghai"), ("day_boundary_hour", 4)):
            original = getattr(settings, name)
            self.addCleanup(object.__setattr__, settings, name, original)
            object.__setattr__(settings, name, value)
        self.rows = [
            dict(id=1, role="user", content="我在搭建 AI 流水线。",
                 created_at="2026-07-17T23:45:00+08:00"),
            dict(id=2, role="user", content="有点难度。",
                 created_at="2026-07-18T00:47:00+08:00"),
            dict(id=3, role="user", content="还在弄流水线。",
                 created_at="2026-07-18T00:48:00+08:00"),
        ]
        self.current = datetime.fromisoformat(self.rows[-1]["created_at"])

    def blocks(self, current: datetime) -> list[str]:
        return [chat._build_current_time_context(current),
                chat._build_conversation_orientation_context(self.rows, 3, current),
                chat._build_user_message_gap_context(self.rows, 3)]

    def test_actual_generated_dynamic_text_is_under_forty_percent_of_baseline(self) -> None:
        # Measured before extraction for these exact synthetic rows: 1344 chars,
        # 3700 UTF-8 bytes in the actual runtime JSON. These are text budgets,
        # not a claim about any provider's billed tokens or cache hit rate.
        blocks = self.blocks(self.current)
        payload = chat._runtime_context_message(blocks)["content"]
        self.assertLess(sum(map(len, blocks)), 1344 * 0.4)
        self.assertLess(len(payload.encode("utf-8")), 3700 * 0.4)
        self.assertIn("当前记录日仍是 2026-07-17", payload)
        self.assertIn("23:45", payload)
        self.assertIn("1 小时 3 分钟", payload)

    def test_fixed_rules_stay_in_stable_system_prefix_and_out_of_dynamic_facts(self) -> None:
        system = chat._chat_system_message(["固定角色"])
        changed_time = self.current.replace(minute=49)
        first = chat._runtime_context_message(self.blocks(self.current))
        second = chat._runtime_context_message(self.blocks(changed_time))
        self.assertEqual(system, chat._chat_system_message(["固定角色"]))
        self.assertEqual(system["role"], "system")
        self.assertEqual(first["role"], "user")
        self.assertNotEqual(first["content"], second["content"])
        for rule in ("旧计划、作息和事件不能默认今天仍有效", "公开回复禁止", "活动时长须有"):
            self.assertIn(rule, system["content"])
            self.assertNotIn(rule, first["content"])
            self.assertNotIn(rule, second["content"])


if __name__ == "__main__":
    unittest.main()
