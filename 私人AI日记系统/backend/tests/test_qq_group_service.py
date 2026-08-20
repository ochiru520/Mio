from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import settings
from app.qq_group_service import (
    append_group_exchange,
    clear_group_histories,
    get_group_history,
    load_group_config,
    save_group_config,
)


class QqGroupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = settings.qq_group_config_path
        object.__setattr__(
            settings,
            "qq_group_config_path",
            Path(self.temp_dir.name) / "QQ群聊设置.json",
        )
        clear_group_histories()

    def tearDown(self) -> None:
        clear_group_histories()
        object.__setattr__(settings, "qq_group_config_path", self.original_path)
        self.temp_dir.cleanup()

    def test_settings_are_saved_and_group_ids_are_normalised(self) -> None:
        saved = save_group_config(
            {
                "enabled": True,
                "group_ids": ["123", " 456 ", "123", ""],
                "mention_required": True,
            }
        )

        self.assertEqual(saved["group_ids"], ["123", "456"])
        self.assertTrue(load_group_config()["enabled"])

    def test_group_history_is_memory_only_and_bounded(self) -> None:
        save_group_config(
            {
                "enabled": True,
                "group_ids": ["123"],
                "mention_required": True,
                "context_messages": 4,
            }
        )
        for index in range(4):
            append_group_exchange("123", "成员", f"消息{index}", [f"回复{index}"])

        history = get_group_history("123")
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["content"], "成员：消息2")
        self.assertEqual(history[-1]["content"], "回复3")

        clear_group_histories()
        self.assertEqual(get_group_history("123"), [])


if __name__ == "__main__":
    unittest.main()
