from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    def test_shortened_time_labels_do_not_become_qq_messages(self) -> None:
        reply = "[今天 21:21]\n我在。\n[今天 21:23]\n慢慢说。"
        self.assertEqual(replies_for_source(reply, "qq"), ["我在", "慢慢说"])
        self.assertEqual(replies_for_source("[今天 21:21]", "qq"), [])
        self.assertEqual(replies_for_source("**[今天 21:21]**\n我在。", "qq"), ["我在"])
        self.assertEqual(
            replies_for_source("[内部消息时间：今天 21:21][今天 21:21] 我在。", "qq"),
            ["我在"],
        )

    def test_shortened_time_prefixes_keep_the_message_body(self) -> None:
        for marker in (
            "[今天 21:21]", "[今天21:21]", "【昨天 09:03】", "[今天（昨晚）23:59]",
            "[2026-10-22 21:21:30]", "[2026-10-20 21:21（2 天前）]",
            "[今天 21:21（本轮新消息）]",
        ):
            with self.subTest(marker=marker):
                self.assertEqual(replies_for_source(f"{marker} 我在。", "qq"), ["我在"])

    def test_time_answers_and_explicit_quotations_survive_cleaning(self) -> None:
        for reply in (
            "现在是今天 21:21。", "21:21", "今天 21:21", "2026-10-22 21:21",
            "你看到的 [今天 21:21] 是时间标签。", '"[今天 21:21]"',
            "“[今天 21:21]”", "> [今天 21:21]", "`[今天 21:21]`",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(clean_chat_reply(reply), reply)

    def test_timestamp_examples_inside_markdown_code_survive(self) -> None:
        for reply in (
            "```text\n[今天 21:21]\n```", "~~~~text\n[昨天 21:23]\n~~~~",
            "示例：\n    [今天 21:21]", "示例：\n\t[今天 21:21]",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(clean_chat_reply(reply), reply)

    def test_time_prefix_cleanup_resumes_after_code_fence(self) -> None:
        reply = "```text\n[今天 21:21]\n```\n[今天 21:23]\n我在。"
        self.assertEqual(clean_chat_reply(reply), "```text\n[今天 21:21]\n```\n我在。")

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


class QqTimeLabelDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_spoken_bodies_reach_onebot_send_callback(self) -> None:
        from app.routes import onebot

        replies = replies_for_source("[今天 21:21]\n我在。\n[今天 21:23] 慢慢说。", "qq")
        for event, action in (
            ({"message_type": "private", "user_id": 42}, "send_private_msg"),
            ({"message_type": "group", "group_id": 123, "user_id": 42}, "send_group_msg"),
        ):
            with self.subTest(action=action):
                send = AsyncMock()
                with patch.object(onebot, "settings", SimpleNamespace(
                    qq_reply_initial_delay_seconds=0, qq_reply_delay_seconds=0,
                )):
                    await onebot._send_replies(event, replies, send)
                self.assertEqual(send.await_count, 2)
                actions = [call.args[0] for call in send.await_args_list]
                self.assertEqual([item["action"] for item in actions], [action, action])
                self.assertEqual([item["params"]["message"] for item in actions], ["我在", "慢慢说"])


if __name__ == "__main__":
    unittest.main()
