from __future__ import annotations

import unittest

from app.chat_service import (
    _remove_replayed_previous_turn,
    _replies_for_storage,
    clean_chat_reply,
    deterministic_recitation_target,
    extract_speech_emotion,
    replies_for_source,
)
from app.companion_action_service import _sync_diary_markdown_status


class ReplySanitizeTests(unittest.TestCase):
    def test_voice_reply_is_saved_as_one_agent_bubble(self) -> None:
        replies = ["那个……", "你突然让我说这种话……", "我会害羞的啦……"]
        self.assertEqual(
            _replies_for_storage(replies, True),
            ["那个…… 你突然让我说这种话…… 我会害羞的啦……"],
        )
        self.assertEqual(_replies_for_storage(replies, False), replies)

    def test_voice_emotion_tag_is_extracted_without_leaking_to_reply(self) -> None:
        emotion, reply = extract_speech_emotion(
            "[[voice_emotion:concerned]]\n先别硬撑。\n休息一会儿，好吗？"
        )

        self.assertEqual(emotion, "concerned")
        self.assertEqual(reply, "先别硬撑。\n休息一会儿，好吗？")
        self.assertEqual(
            replies_for_source("[[voice_emotion:concerned]]\n先别硬撑。", "qq"),
            ["先别硬撑"],
        )

    def test_invalid_voice_emotion_tag_is_not_treated_as_control_data(self) -> None:
        emotion, _ = extract_speech_emotion("[[voice_emotion:angry]]\n别这样")
        self.assertIsNone(emotion)

    def test_fake_voice_note_is_removed(self) -> None:
        reply = "[语音消息长度约 3 秒]\n嗯……\n怎么突然想听我说话了"
        self.assertEqual(clean_chat_reply(reply), "嗯……\n怎么突然想听我说话了")

    def test_unbracketed_voice_note_and_stage_direction_are_removed(self) -> None:
        reply = "语音消息约 5 秒\n（发送语音）\n我是澪"
        self.assertEqual(clean_chat_reply(reply), "我是澪")

    def test_voice_stage_directions_are_removed_without_dropping_spoken_lines(self) -> None:
        reply = (
            "“那个……\n"
            "你、你突然让我说这种话……\n"
            "”（声音越来越小）\n"
            "“我……\n"
            "我会害羞的啦……\n"
            "”（停顿了一下）"
        )
        self.assertEqual(
            replies_for_source(reply, "qq"),
            ["“那个……", "你、你突然让我说这种话……", "“我……", "我会害羞的啦……"],
        )

    def test_voice_prefix_and_performance_direction_are_removed(self) -> None:
        reply = "语音里说：\n语音消息（约3秒）：‘我是澪’\n然后轻轻笑了一声：\"很高兴认识你\"\n（这次应该能听到了吧？）"
        self.assertEqual(clean_chat_reply(reply), "‘我是澪’\n\"很高兴认识你\"")

    def test_internal_time_note_lines_are_dropped(self) -> None:
        reply = "游戏玩了几天，也该玩得差不多了。\n[内部消息时间：今天 15:58]\n澪不是不让你玩。"
        self.assertEqual(
            replies_for_source(reply, "qq"),
            ["游戏玩了几天，也该玩得差不多了", "澪不是不让你玩"],
        )

    def test_inline_markers_are_stripped(self) -> None:
        reply = "[内部消息时间：今天 08:10（本轮新消息）]\n醒了就好。"
        self.assertEqual(clean_chat_reply(reply), "醒了就好。")
        self.assertEqual(clean_chat_reply("我看到了[图片 1 张]这个。"), "我看到了这个。")

    def test_reasoning_block_with_alternate_labels_is_dropped(self) -> None:
        reply = """[本轮消息时间：2026-07-28 17:48]
[内部判断：用户还在消化情绪。
这不是第一次吐槽。
先接住情绪，不急着讲道理。
]
是啊。
用正式岗的标准筛实习生，确实让人火大。"""
        self.assertEqual(
            replies_for_source(reply, "qq"),
            ["是啊", "用正式岗的标准筛实习生，确实让人火大"],
        )

    def test_qq_bubble_drops_terminal_full_stop_only(self) -> None:
        self.assertEqual(
            replies_for_source("第一句。\n第二句？\n“第三句。”", "qq"),
            ["第一句", "第二句？", "“第三句”"],
        )

    def test_stray_internal_block_closing_bracket_is_dropped(self) -> None:
        self.assertEqual(clean_chat_reply("]\n我在。"), "我在。")

    def test_think_block_is_dropped(self) -> None:
        self.assertEqual(clean_chat_reply("<think>内部分析</think>\n嗯。"), "嗯。")

    def test_untagged_english_reasoning_keeps_only_natural_answer(self) -> None:
        leaked = '''We need answer Chinese natural. User says Japanese voice did not work.
respond actual Japanese maybe says it could not speak.
Perhaps they want another test.
Need ask maybe?

Natural: "嗯，刚才还是没能顺利说出来"'''

        self.assertEqual(clean_chat_reply(leaked), "嗯，刚才还是没能顺利说出来")

    def test_web_paragraph_split_survives_cleaning(self) -> None:
        reply = "第一段。\n\n第二段。"
        self.assertEqual(replies_for_source(reply, "web"), ["第一段", "第二段"])

    def test_web_sentences_become_separate_bubbles(self) -> None:
        reply = "先歇了一天。听起来也不算坏事。你现在是放松，还是有点空？"
        self.assertEqual(
            replies_for_source(reply, "web"),
            ["先歇了一天", "听起来也不算坏事", "你现在是放松，还是有点空？"],
        )

    def test_isolated_acknowledgements_are_naturalized_without_rewriting_sentences(self) -> None:
        self.assertEqual(replies_for_source("好。\n嗯……\n行", "web"), ["好的", "嗯嗯……", "可以"])
        self.assertEqual(replies_for_source("好消息。\n嗯？", "web"), ["好消息", "嗯？"])

    def test_fragmentary_question_tail_stays_with_previous_bubble(self) -> None:
        self.assertEqual(
            replies_for_source("我重新念了一遍。\n对不？", "web"),
            ["我重新念了一遍 对不？"],
        )

    def test_explicit_recitation_and_repeat_are_deterministic(self) -> None:
        history = [
            {"role": "user", "content": "来吧，跟我说，1 2 3 3 2 1 啊 啊"},
            {"role": "assistant", "content": "1 2 3 3 2 1 啊 啊"},
        ]
        self.assertEqual(
            deterministic_recitation_target(history[0]["content"], []),
            "1 2 3 3 2 1 啊 啊",
        )
        self.assertEqual(
            deterministic_recitation_target("再来一遍", history),
            "1 2 3 3 2 1 啊 啊",
        )
        self.assertEqual(
            deterministic_recitation_target("再来，123321啊，啊", history),
            "123321啊，啊",
        )
        self.assertEqual(deterministic_recitation_target("再来一遍", []), "")
        self.assertEqual(deterministic_recitation_target("念念不忘是什么意思", []), "")

    def test_previous_assistant_turn_replay_is_removed(self) -> None:
        rows = [
            {"id": 1, "role": "user", "content": "胡萝卜怎么做"},
            {"id": 2, "role": "assistant", "content": "切块跟鸡一起。"},
            {"id": 3, "role": "assistant", "content": "溃疡还在，胡萝卜炖软了比炒脆片好嚼。"},
            {"id": 4, "role": "assistant", "content": "鸡肉炒香之后加水焖一会儿。"},
            {"id": 5, "role": "user", "content": "用高压锅一起放进去吗？"},
        ]
        replies = [
            "切块跟鸡一起。",
            "溃疡还在，胡萝卜炖软了比炒脆片好嚼。",
            "鸡肉炒香之后加水焖一会儿。",
            "高压锅的话，胡萝卜别一开始就放进去。",
        ]
        self.assertEqual(
            _remove_replayed_previous_turn(replies, rows, 5),
            ["高压锅的话，胡萝卜别一开始就放进去。"],
        )

    def test_short_natural_repeat_is_not_removed(self) -> None:
        rows = [
            {"id": 1, "role": "assistant", "content": "是啊。"},
            {"id": 2, "role": "user", "content": "真的很烦。"},
        ]
        self.assertEqual(_remove_replayed_previous_turn(["是啊。"], rows, 2), ["是啊。"])


class DiaryStatusSyncTests(unittest.TestCase):
    def test_status_line_and_reason_are_rewritten(self) -> None:
        markdown = "# 2026-07-26\n\n## 每日三十\n状态：未确认\n原因：暂无信息\n\n## AI 观察\n- 无"
        updated = _sync_diary_markdown_status(markdown, "done", "跑步 40 分钟")
        self.assertIn("状态：完成", updated)
        self.assertIn("原因：跑步 40 分钟", updated)
        self.assertIn("## AI 观察", updated)

    def test_markdown_without_section_is_untouched(self) -> None:
        markdown = "# 2026-07-26\n\n## 今日事件\n- 无"
        self.assertEqual(_sync_diary_markdown_status(markdown, "done", "x"), markdown)

    def test_today_growth_heading_is_rewritten(self) -> None:
        markdown = "# 2026-08-11\n\n## 今日成长\n状态：未确认\n原因：暂无信息\n\n## 做得不错\n- 完成了排版"
        updated = _sync_diary_markdown_status(markdown, "done", "持续推进两小时")
        self.assertIn("## 今日成长", updated)
        self.assertIn("状态：完成", updated)
        self.assertIn("原因：持续推进两小时", updated)


if __name__ == "__main__":
    unittest.main()
