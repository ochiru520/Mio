from __future__ import annotations

from io import BytesIO
import unittest

from PIL import Image, ImageDraw

from app.screen_watch_pipeline import AdaptiveScreenWatch


def frame(number: int, color: str | tuple[int, int, int], *, title: str = "test", size=(1280, 720), cursor=False):
    image = Image.new("RGB", size, color)
    if cursor:
        ImageDraw.Draw(image).rectangle((100, 100, 102, 108), fill="white")
    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return {
        "content": output.getvalue(), "frame_id": number,
        "captured_at": f"2026-11-02T00:00:{number:02d}",
        "mode": "window", "hwnd": 123, "title": title,
    }


class AdaptiveScreenWatchTests(unittest.TestCase):
    def test_first_frame_is_available_immediately(self):
        pipeline = AdaptiveScreenWatch()
        original = frame(1, "red")
        pipeline.push(original, now=100)
        result = pipeline.take(now=100)
        self.assertEqual(result["watch_sheet"]["frame_ids"], [1])
        self.assertEqual(Image.open(BytesIO(result["content"])).size, (1280, 768))
        self.assertNotIn("watch_sheet", original)
        self.assertIsNone(pipeline.take(now=150))

    def test_static_screen_and_tiny_cursor_never_generate_duplicate_sheets(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(1, (90, 90, 90)), now=0)
        pipeline.take(now=0)
        for number in range(2, 70):
            pipeline.push(frame(number, (90, 90, 90), cursor=bool(number % 2)), now=number)
            self.assertIsNone(pipeline.take(now=number))
        self.assertEqual(pipeline.status()["sheets"], 1)
        self.assertEqual(pipeline.status()["skipped_frames"], 68)

    def test_motion_accumulates_without_a_large_single_change(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(0, (40, 40, 40)), now=0)
        pipeline.take(now=0)
        for number in range(1, 10):
            shade = 40 + number * 7
            pipeline.push(frame(number, (shade, shade, shade)), now=number)
        self.assertGreater(pipeline.status()["selected_frames"], 1)
        self.assertIsNotNone(pipeline.take(now=9))

    def test_all_sensitivities_emit_new_changes_without_a_waiting_window(self):
        for pace in ("calm", "normal", "dense"):
            with self.subTest(pace=pace):
                pipeline = AdaptiveScreenWatch(pace)
                pipeline.push(frame(1, "red"), now=0)
                pipeline.take(now=0)
                pipeline.push(frame(2, "blue"), now=1)
                self.assertEqual(pipeline.take(now=1)["watch_sheet"]["frame_ids"], [2])
                self.assertEqual(pipeline.status()["gap_seconds"], 0)
                self.assertIsNone(pipeline.take(now=2))

    def test_fast_scene_changes_keep_four_latest_in_temporal_order(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(1, "black"), now=0)
        pipeline.take(now=0)
        colors = ["red", "blue", "green", "yellow", "purple", "white"]
        for number, color in enumerate(colors, start=2):
            pipeline.push(frame(number, color), now=number)
        self.assertEqual(pipeline.status()["buffered_frames"], 4)
        result = pipeline.take(now=15)
        self.assertEqual(result["watch_sheet"]["frame_ids"], [4, 5, 6, 7])
        self.assertEqual(result["frame_id"], 7)
        self.assertEqual(result["watch_sheet"]["order"], "row-major")
        self.assertEqual(pipeline.status()["replaced_frames"], 2)
        image = Image.open(BytesIO(result["content"]))
        expected = [(0, 128, 0), (255, 255, 0), (128, 0, 128), (255, 255, 255)]
        for point, color in zip([(320, 204), (960, 204), (320, 588), (960, 588)], expected):
            self.assertLess(max(abs(actual - wanted) for actual, wanted in zip(image.getpixel(point), color)), 8)

    def test_returning_to_retained_frame_does_not_duplicate_it(self):
        pipeline = AdaptiveScreenWatch("calm")
        pipeline.push(frame(1, (100, 100, 100)), now=0)
        pipeline.take(now=0)
        for number in range(2, 8):
            pipeline.push(frame(number, (104 if number % 2 else 100,) * 3), now=number)
        self.assertEqual(pipeline.status()["selected_frames"], 1)
        self.assertIsNone(pipeline.take(now=31))

    def test_force_uses_latest_full_frame_and_discards_old_sheet(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(1, "red"), now=0)
        pipeline.take(now=0)
        pipeline.push(frame(2, "blue"), now=1)
        latest = frame(3, (0, 0, 253))
        pipeline.push(latest, now=2)
        result = pipeline.take(now=2, force=True)
        self.assertEqual(result["watch_sheet"]["frame_ids"], [3])
        self.assertEqual(result["content"], latest["content"])
        self.assertTrue(result["watch_sheet"]["forced"])
        self.assertIsNone(pipeline.take(now=30))

    def test_delayed_sheet_contains_latest_real_pixels_and_matching_capture_identity(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(0, "red"), now=0)
        pipeline.take(now=0)
        changed = frame(1, "blue")
        changed["captured_monotonic"] = 1.0
        pipeline.push(changed, now=1)
        # The tiny marker survives rendering but remains below the selection
        # threshold, so only appending the actual latest image can include it.
        latest = frame(14, "blue")
        image = Image.open(BytesIO(latest["content"]))
        ImageDraw.Draw(image).rectangle((600, 330, 624, 354), fill="white")
        output = BytesIO()
        image.save(output, format="JPEG", quality=95)
        latest["content"] = output.getvalue()
        latest["captured_monotonic"] = 14.0
        pipeline.push(latest, now=14)
        self.assertEqual(pipeline.status()["buffered_frames"], 1)
        result = pipeline.take(now=15)
        self.assertEqual(result["watch_sheet"]["frame_ids"], [1, 14])
        self.assertEqual(result["frame_id"], 14)
        self.assertEqual(result["captured_at"], latest["captured_at"])
        self.assertEqual(result["captured_monotonic"], 14.0)
        self.assertEqual(result["watch_sheet"]["captured_monotonic"], [1.0, 14.0])
        self.assertEqual(result["watch_sheet"]["frame_times"][-1], 14)
        sheet = Image.open(BytesIO(result["content"]))
        # Both cells are 640px wide and vertically centered. The historical
        # frame is blue at this position; the current frame has a white mark.
        self.assertLess(sheet.getpixel((306, 387))[0], 20)
        self.assertGreater(sheet.getpixel((946, 387))[0], 200)
        self.assertIsNone(pipeline.take(now=60))

    def test_latest_tail_refresh_never_exceeds_four_cells(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(0, "black"), now=0)
        pipeline.take(now=0)
        for number, color in enumerate(["red", "green", "yellow", "blue"], start=1):
            pipeline.push(frame(number, color), now=number)
        pipeline.push(frame(14, "blue", cursor=True), now=14)
        result = pipeline.take(now=15)
        self.assertEqual(result["watch_sheet"]["frame_ids"], [2, 3, 4, 14])
        self.assertEqual(result["watch_sheet"]["count"], 4)
        self.assertEqual(result["frame_id"], 14)

    def test_source_changes_clear_previous_frames(self):
        for field, value in [("title", "other"), ("hwnd", 999), ("mode", "screen"), ("screen_scope", "all")]:
            with self.subTest(field=field):
                pipeline = AdaptiveScreenWatch()
                pipeline.push(frame(1, "red"), now=0)
                pipeline.take(now=0)
                pipeline.push(frame(2, "blue"), now=1)
                new_source = frame(3, "green")
                new_source[field] = value
                pipeline.push(new_source, now=2)
                self.assertEqual(pipeline.take(now=2)["watch_sheet"]["frame_ids"], [3])

    def test_resize_also_discards_old_source(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(1, "red"), now=0)
        pipeline.take(now=0)
        pipeline.push(frame(2, "blue", size=(800, 600)), now=1)
        result = pipeline.take(now=1)
        self.assertEqual(result["watch_sheet"]["frame_ids"], [2])

    def test_portrait_frame_is_letterboxed_without_cropping(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(1, "red", size=(360, 720)), now=0)
        result = pipeline.take(now=0)
        image = Image.open(BytesIO(result["content"]))
        self.assertLess(image.getpixel((10, 500))[0], 40)
        self.assertGreater(image.getpixel((640, 500))[0], 240)

    def test_sensitivity_change_retains_selected_frames_without_waiting(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(1, "red"), now=0)
        pipeline.take(now=0)
        pipeline.push(frame(2, "blue"), now=1)
        pipeline.configure("dense")
        self.assertEqual(pipeline.status()["buffered_frames"], 1)
        self.assertIsNotNone(pipeline.take(now=1))

    def test_reset_removes_latest_frame_and_counters(self):
        pipeline = AdaptiveScreenWatch("dense")
        pipeline.push(frame(1, "red"), now=0)
        pipeline.take(now=0)
        pipeline.reset()
        self.assertIsNone(pipeline.take(now=2, force=True))
        self.assertEqual(pipeline.status()["sheets"], 0)
        self.assertEqual(pipeline.status()["selected_frames"], 0)
        self.assertEqual(pipeline.status()["pace"], "dense")

    def test_bad_input_does_not_poison_existing_buffer(self):
        pipeline = AdaptiveScreenWatch()
        pipeline.push(frame(1, "red"), now=0)
        with self.assertRaises(ValueError):
            pipeline.push({"content": b""}, now=1)
        with self.assertRaises(ValueError):
            pipeline.push(frame(2, "blue"), now=float("nan"))
        with self.assertRaises(ValueError):
            pipeline.configure("invalid")
        self.assertEqual(pipeline.take(now=0)["watch_sheet"]["frame_ids"], [1])


if __name__ == "__main__":
    unittest.main()
