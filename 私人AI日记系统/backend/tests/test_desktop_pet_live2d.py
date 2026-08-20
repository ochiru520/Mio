from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.desktop_pet_live2d import Live2DBridge


class Live2DBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = Mock()
        self.bridge = Live2DBridge(x=100, y=200, width=420, height=600, size_percent=150)
        self.bridge._bind_window(self.window)

    @patch("app.desktop_pet_live2d.threading.Thread")
    def test_drag_uses_window_origin_and_persists_position(self, thread: Mock) -> None:
        with patch("app.desktop_pet_live2d._request_json", return_value={}) as request:
            self.bridge.begin_drag(300, 400)
            result = self.bridge.drag_to(330, 445)
            self.bridge.end_drag()

        self.assertTrue(result["ok"])
        self.window.move.assert_called_with(130, 245)
        request.assert_called_with(
            "/api/companion/position",
            method="PATCH",
            payload={"x": 130, "y": 245},
        )
        thread.return_value.start.assert_called_once_with()

    def test_set_size_resizes_and_persists_size_and_position(self) -> None:
        with patch("app.desktop_pet_live2d._request_json", return_value={}) as request:
            result = self.bridge.set_size(180)

        self.assertEqual(result, {"ok": True, "percent": 180})
        self.window.resize.assert_called_once_with(504, 720)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0], "/api/companion/size")
        self.assertEqual(request.call_args_list[0].kwargs["payload"], {"percent": 180})
        self.assertEqual(request.call_args_list[1].args[0], "/api/companion/position")


if __name__ == "__main__":
    unittest.main()
