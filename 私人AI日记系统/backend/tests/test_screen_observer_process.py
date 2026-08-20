from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO

from PIL import Image, ImageDraw
from unittest.mock import patch

from app.screen_behavior_service import (
    BehaviorDecision,
    Observation,
    decide_behavior,
    is_duplicate_summary,
    is_new_event_occurrence,
    update_game_state,
)
from app.screen_frame_processor import calculate_change_metrics, calculate_change_percent, process_frame
from app.screen_observation_service import (
    _analysis_messages,
    _cursor_prompt_context,
    _effective_change_threshold,
    _game_event_can_open,
    _is_self_ui_foreground,
    _newer_frame_supersedes,
    _parse_observation,
    is_screen_chat_follow_up,
)
from app.screen_observer_process import ScreenObserverProcess, _worker_result
from app.companion_observation_service import WindowObserver


class StubObserver:
    def take_preview(self):
        return b"preview"

    def claim_analysis_frame(self, _after_frame_id):
        return {
            "frame_id": 2,
            "content": b"frame",
            "change_percent": 12.0,
            "captured_at": "now",
            "title": "test",
            "mode": "window",
        }


class ScreenObserverProcessTests(unittest.TestCase):
    def test_game_window_uses_more_sensitive_change_gate(self):
        self.assertEqual(
            _effective_change_threshold({"screen_change_threshold": 4.0}, {"mode": "window"}),
            2.4,
        )
        self.assertEqual(
            _effective_change_threshold({"screen_change_threshold": 4.0}, {"mode": "screen"}),
            4.0,
        )

    def test_new_game_dialogue_can_open_when_model_is_conservative(self):
        observation = Observation(
            "dialogue_choice",
            "画面出现新的对白选项",
            0.9,
            "测试游戏",
            {},
            (),
        )
        decision = BehaviorDecision(True, 0.76, "neutral", "dialogue_choice 事件达到回应阈值", 1)
        self.assertTrue(
            _game_event_can_open(
                observation,
                decision,
                event_is_new=True,
                recent_summaries=[],
                seconds_since_last_speech=20,
                cooldown_seconds=5,
            )
        )

    def test_full_screen_observation_detects_mio_foreground(self):
        with patch(
            "app.screen_observation_service._foreground_details",
            return_value={"process_name": "Mio.exe", "title": "对话", "process_id": 1},
        ):
            self.assertTrue(_is_self_ui_foreground({"mode": "screen"}))

    def test_full_screen_observation_does_not_block_other_app(self):
        with patch(
            "app.screen_observation_service._foreground_details",
            return_value={"process_name": "game.exe", "title": "测试游戏", "process_id": 2},
        ):
            self.assertFalse(_is_self_ui_foreground({"mode": "screen"}))

    def test_vision_prompt_warns_against_mio_history(self):
        with patch(
            "app.screen_observation_service._foreground_details",
            return_value={"process_name": "Mio.exe", "title": "对话", "process_id": 1},
        ):
            messages = _analysis_messages({
                "content": b"frame",
                "mode": "screen",
                "title": "主屏幕",
                "captured_at": "now",
                "change_percent": 12.0,
            })
            prompt = messages[1]["content"][0]["text"]
            self.assertIn("old chat text", prompt)

    def test_screen_follow_up_uses_recent_successful_observation(self):
        now = datetime(2026, 8, 19, 20, 0, 0)
        self.assertTrue(is_screen_chat_follow_up(
            "现在呢？",
            observation_status={"last_analyzed_at": (now - timedelta(minutes=2)).isoformat()},
            now=now,
        ))

    def test_screen_follow_up_keeps_explicit_conversation_context_after_waiting(self):
        now = datetime(2026, 8, 19, 20, 0, 0)
        history = [
            {"role": "user", "source": "desktop", "content": "你看看现在的画面"},
            {"role": "assistant", "source": "screen", "content": "现在是游戏主菜单。"},
        ]
        self.assertTrue(is_screen_chat_follow_up(
            "现在呢",
            history,
            observation_status={"last_analyzed_at": (now - timedelta(minutes=30)).isoformat()},
            now=now,
        ))

    def test_screen_follow_up_accepts_database_history_ordered_newest_first(self):
        history = [
            {"id": 4, "role": "assistant", "source": "screen", "content": "现在是设置页。"},
            {"id": 3, "role": "user", "source": "desktop", "content": "你看一下当前屏幕"},
        ]
        self.assertTrue(is_screen_chat_follow_up(
            "现在呢",
            history,
            observation_status={"last_analyzed_at": ""},
        ))

    def test_screen_follow_up_accepts_punctuation_and_explicit_visual_diagnostic(self):
        history = [
            {"role": "assistant", "source": "game", "content": "画面刚刚变化了。"},
        ]
        self.assertTrue(is_screen_chat_follow_up(
            "现在呢/",
            history,
            observation_status={"last_analyzed_at": ""},
        ))
        self.assertTrue(is_screen_chat_follow_up(
            "不可能啊，我们现在在谈视觉功能",
            history,
            observation_status={"last_analyzed_at": ""},
        ))

    def test_screen_follow_up_does_not_hijack_ordinary_conversation(self):
        history = [
            {"role": "user", "source": "desktop", "content": "晚饭吃什么"},
            {"role": "assistant", "source": "desktop", "content": "可以吃点清淡的。"},
        ]
        self.assertFalse(is_screen_chat_follow_up(
            "现在呢",
            history,
            observation_status={"last_analyzed_at": ""},
        ))

    def test_structured_observation_is_normalized(self):
        result = _parse_observation(
            '{"event": "player_dead", "summary": "角色倒下了", "confidence": 0.9, '
            '"game": "测试游戏", "state": {"boss": "测试 Boss"}, "tags": ["战斗"]}'
        )
        self.assertEqual(result.event_type, "death")
        self.assertEqual(result.game_name, "测试游戏")
        self.assertEqual(result.details["boss"], "测试 Boss")
        self.assertEqual(result.confidence, 0.9)

    def test_old_plain_reply_remains_compatible(self):
        result = _parse_observation("NO_REPLY")
        self.assertEqual(result.event_type, "idle")
        result = _parse_observation("刚刚差一点")
        self.assertEqual(result.event_type, "notable_scene")
        self.assertEqual(result.summary, "刚刚差一点")

    def test_duplicate_events_are_filtered_by_similarity(self):
        self.assertTrue(is_duplicate_summary("刚刚又失败了一次", ["刚刚又失败了一次"]))
        self.assertTrue(is_duplicate_summary("刚刚又失败了一次", ["刚刚又失败一次了"]))
        self.assertFalse(is_duplicate_summary("成功完成了关卡", ["刚刚又失败了一次"]))

    def test_behavior_layer_decides_when_to_speak(self):
        observation = Observation("death", "角色被 Boss 击败", 0.92, "测试游戏", {"boss": "测试 Boss"}, ())
        state = update_game_state({}, observation)
        decision = decide_behavior(
            observation,
            state,
            recent_summaries=[],
            seconds_since_last_speech=999,
            cooldown_seconds=60,
            minimum_priority=0.62,
        )
        self.assertTrue(decision.should_speak)
        self.assertEqual(decision.emotion, "concerned")
        self.assertEqual(state["death_count"], 1)
        idle = decide_behavior(
            Observation("movement", "角色正常移动", 0.99, "测试游戏", {}, ()),
            state,
            recent_summaries=[],
            seconds_since_last_speech=999,
            cooldown_seconds=60,
            minimum_priority=0.62,
        )
        self.assertFalse(idle.should_speak)

    def test_normal_gameplay_can_speak_without_waiting_for_a_major_event(self):
        observation = Observation("gameplay", "玩家进入新的战斗区域", 0.92, "测试游戏", {}, ())
        state = update_game_state({}, observation)
        decision = decide_behavior(
            observation,
            state,
            recent_summaries=[],
            seconds_since_last_speech=999,
            cooldown_seconds=15,
            minimum_priority=0.62,
        )
        self.assertTrue(decision.should_speak)

        cooling_down = decide_behavior(
            observation,
            state,
            recent_summaries=[],
            seconds_since_last_speech=4,
            cooldown_seconds=5,
            minimum_priority=0.62,
        )
        self.assertFalse(cooling_down.should_speak)
        self.assertEqual(cooling_down.reason, "仍在回应冷却时间内")

    def test_repeated_gameplay_can_resume_after_quiet_rhythm_window(self):
        observation = Observation("gameplay", "玩家正在新的战斗区域中移动", 0.92, "测试游戏", {}, ())
        state = update_game_state({"event_counts": {"gameplay": 1}}, observation)
        decision = decide_behavior(
            observation,
            state,
            recent_summaries=["玩家正在新的战斗区域中移动"],
            seconds_since_last_speech=25,
            cooldown_seconds=5,
            minimum_priority=0.5,
        )
        self.assertTrue(decision.should_speak)

    def test_full_screen_analysis_keeps_game_and_activity_events(self):
        messages = _analysis_messages({
            "content": b"frame",
            "mode": "screen",
            "title": "主屏幕",
            "captured_at": "now",
            "change_percent": 12.0,
        })
        prompt = messages[1]["content"][0]["text"]
        self.assertIn("gameplay", prompt)
        self.assertIn("death", prompt)
        self.assertIn("activity_change", prompt)
        self.assertIn("整个屏幕", prompt)

    def test_newer_significantly_changed_frame_supersedes_old_reaction(self):
        self.assertTrue(_newer_frame_supersedes(
            10,
            {"frame_id": 14, "pending_change_percent": 12.0},
            change_threshold=8.0,
        ))
        self.assertFalse(_newer_frame_supersedes(
            10,
            {"frame_id": 14, "pending_change_percent": 2.0},
            change_threshold=8.0,
        ))

    def test_cursor_context_describes_position_inside_selected_window(self):
        context = _cursor_prompt_context({
            "mode": "window",
            "cursor": {
                "available": True,
                "inside_capture": True,
                "relative_x": 0.52,
                "relative_y": 0.41,
            },
        })
        self.assertIn("横向约 52%", context)
        self.assertIn("纵向约 41%", context)
        self.assertIn("弱证据", context)

    def test_cursor_context_does_not_guess_when_cursor_is_outside_window(self):
        context = _cursor_prompt_context({
            "mode": "window",
            "cursor": {"available": True, "inside_capture": False},
        })
        self.assertEqual(context, "当前鼠标位置：在所选窗口外")

    @unittest.skipUnless(os.name == "nt", "鼠标坐标采样只在 Windows 可用")
    def test_cursor_metadata_samples_current_position_without_history(self):
        import ctypes
        from ctypes import wintypes

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            self.skipTest("当前测试进程没有可用的交互式 Windows 桌面")
        region = (point.x - 100, point.y - 80, point.x + 100, point.y + 120)
        cursor = WindowObserver._cursor_metadata(region)
        self.assertTrue(cursor["available"])
        self.assertTrue(cursor["inside_capture"])
        self.assertAlmostEqual(cursor["relative_x"], 0.5, places=2)
        self.assertAlmostEqual(cursor["relative_y"], 0.4, places=2)
        self.assertNotIn("history", cursor)

    def test_same_death_screen_is_not_counted_or_spoken_twice(self):
        observation = Observation("death", "角色被 Boss 击败", 0.92, "测试游戏", {}, ())
        recent = [{
            "event_type": "death",
            "summary": "角色倒下了",
            "occurred_at": "2026-08-07T14:00:00+08:00",
        }]
        event_is_new = is_new_event_occurrence(
            observation,
            recent,
            occurred_at="2026-08-07T14:00:30+08:00",
        )
        state = update_game_state(
            {"event_counts": {"death": 1}, "death_count": 1, "attempt": 2},
            observation,
            count_occurrence=event_is_new,
        )
        decision = decide_behavior(
            observation,
            state,
            recent_summaries=[],
            seconds_since_last_speech=999,
            cooldown_seconds=60,
            minimum_priority=0.62,
            event_is_new=event_is_new,
        )
        self.assertFalse(event_is_new)
        self.assertEqual(state["death_count"], 1)
        self.assertEqual(state["attempt"], 2)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reason, "同一事件仍在持续")

    def test_event_transition_allows_a_new_death(self):
        observation = Observation("death", "角色再次倒下", 0.94, "测试游戏", {}, ())
        recent = [{
            "event_type": "boss_battle",
            "summary": "玩家重新进入战斗",
            "occurred_at": "2026-08-07T14:01:00+08:00",
        }]
        self.assertTrue(is_new_event_occurrence(
            observation,
            recent,
            occurred_at="2026-08-07T14:01:20+08:00",
        ))

    def test_frame_processor_reduces_model_input_without_writing_file(self):
        source = BytesIO()
        Image.new("RGB", (1920, 1080), "navy").save(source, format="JPEG", quality=95)
        processed = process_frame(source.getvalue())
        self.assertEqual((processed.width, processed.height), (960, 540))
        self.assertLess(len(processed.content), len(source.getvalue()))

    def test_frame_change_percent_stays_in_memory_and_detects_scene_difference(self):
        previous = Image.new("RGB", (1920, 1080), "black")
        current = Image.new("RGB", (1920, 1080), "white")
        self.assertEqual(calculate_change_percent(None, previous), 0.0)
        self.assertGreater(calculate_change_percent(previous, current), 95.0)

    def test_local_dialogue_change_triggers_when_global_average_is_small(self):
        previous = Image.new("RGB", (1920, 1080), "#20242a")
        current = previous.copy()
        draw = ImageDraw.Draw(current)
        for y in range(820, 945, 24):
            draw.rectangle((180, y, 1420, y + 8), fill="white")

        change = calculate_change_metrics(previous, current)

        self.assertLess(change.global_percent, 8.0)
        self.assertGreater(change.local_percent, 8.0)
        self.assertEqual(change.effective_percent, change.local_percent)

    def test_single_tiny_cursor_change_does_not_trigger_local_threshold(self):
        previous = Image.new("RGB", (1920, 1080), "#20242a")
        current = previous.copy()
        ImageDraw.Draw(current).rectangle((950, 535, 970, 555), fill="white")

        change = calculate_change_metrics(previous, current)

        self.assertLess(change.effective_percent, 8.0)

    def test_worker_payload_keeps_frames_in_memory(self):
        observer = StubObserver()
        preview = _worker_result(observer, "take_preview", {})
        frame = _worker_result(observer, "claim_analysis_frame", {"after_frame_id": 0})
        self.assertEqual(preview["content"], b"preview")
        self.assertEqual(frame["content"], b"frame")

    def test_stale_window_selection_is_cleared_during_process_restore(self):
        observer = ScreenObserverProcess()
        observer._has_selection = True
        observer._desired_mode = "window"
        observer._desired_hwnd = 123456
        observer._desired_running = True
        observer._cached_preview = b"old-preview"

        with patch.object(observer, "_send_locked", side_effect=RuntimeError("窗口已经关闭")):
            observer._restore_state_locked()

        self.assertFalse(observer._has_selection)
        self.assertFalse(observer._desired_running)
        self.assertEqual(observer._desired_hwnd, 0)
        self.assertIsNone(observer._cached_preview)

    @unittest.skipUnless(os.name == "nt", "独立观察器只在 Windows 桌面环境运行")
    def test_child_process_can_be_restarted_without_restarting_parent(self):
        with tempfile.TemporaryDirectory() as runtime_root, patch.dict(
            os.environ,
            {"MIO_RUNTIME_ROOT": runtime_root},
        ):
            observer = ScreenObserverProcess()
            try:
                observer.select_screen("primary")
                observer.start(1000)
                first_pid = observer._process.pid
                observer._process.kill()
                observer._process.wait(timeout=3)
                status = observer.status()
                self.assertTrue(status["process_alive"])
                self.assertTrue(status["running"])
                self.assertEqual(status["process_pid"], observer._process.pid)
                self.assertGreaterEqual(status["process_restarts"], 1)
                self.assertNotEqual(observer._process.pid, first_pid)
            finally:
                observer.stop()

    @unittest.skipUnless(os.name == "nt", "独立观察器只在 Windows 桌面环境运行")
    def test_status_does_not_restart_an_observer_after_stop(self):
        with tempfile.TemporaryDirectory() as runtime_root, patch.dict(
            os.environ,
            {"MIO_RUNTIME_ROOT": runtime_root},
        ):
            observer = ScreenObserverProcess()
            observer.select_screen("primary")
            observer.start(1000)
            observer.stop()

            status = observer.status()
            preview = observer.take_preview()

            self.assertFalse(status["running"])
            self.assertFalse(status["process_alive"])
            self.assertEqual(status["process_pid"], 0)
            self.assertEqual(status["preview_available"], preview is not None)
            self.assertIsNone(observer._process)


if __name__ == "__main__":
    unittest.main()
