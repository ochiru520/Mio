"""豆包 Realtime 二进制协议客户端测试（只测帧编解码，不连网）。"""
from __future__ import annotations

import json
import struct
import unittest

from app import cloud_realtime


class FrameBuildTests(unittest.TestCase):
    def test_json_frame_header(self) -> None:
        frame = cloud_realtime._json_frame(100, {"a": 1})
        self.assertEqual(len(frame) >= 4, True)
        self.assertEqual(frame[0], 0x11)
        # byte1 高4位 = 客户端文本请求 0b0001
        self.assertEqual((frame[1] >> 4) & 0x0F, 0b0001)
        # byte2 高4位 = JSON 序列化 0b0001
        self.assertEqual((frame[2] >> 4) & 0x0F, 0b0001)
        # event 号在 4 字节头之后（大端 100）
        event = struct.unpack(">I", frame[4:8])[0]
        self.assertEqual(event, 100)
        payload = json.loads(frame[8:].decode("utf-8"))
        self.assertEqual(payload, {"a": 1})

    def test_audio_frame_is_raw_binary(self) -> None:
        pcm = b"\x01\x02" * 320  # 640 字节 = 20ms@16kHz mono s16le
        frame = cloud_realtime._frame(cloud_realtime.MSG_CLIENT_AUDIO, payload=pcm)
        self.assertEqual(frame[0], 0x11)
        self.assertEqual((frame[1] >> 4) & 0x0F, 0b0010)
        self.assertEqual(frame[4:], pcm)


class FrameParseTests(unittest.TestCase):
    def test_parse_server_text_frame(self) -> None:
        payload = json.dumps({"text": "你好"}).encode("utf-8")
        frame = cloud_realtime._frame(
            cloud_realtime.MSG_SERVER_TEXT,
            event=cloud_realtime.EVENT_ASR_RESPONSE,
            payload=payload,
            serialization=0b0001,
        )
        parsed = cloud_realtime._parse_frame(frame)
        self.assertEqual(parsed["message_type"], cloud_realtime.MSG_SERVER_TEXT)
        self.assertEqual(parsed["event"], cloud_realtime.EVENT_ASR_RESPONSE)
        self.assertEqual(json.loads(parsed["payload"]), {"text": "你好"})

    def test_parse_server_audio_frame(self) -> None:
        pcm = b"\x00\x01\x00\x02"
        frame = cloud_realtime._frame(cloud_realtime.MSG_SERVER_AUDIO, payload=pcm)
        parsed = cloud_realtime._parse_frame(frame)
        self.assertEqual(parsed["message_type"], cloud_realtime.MSG_SERVER_AUDIO)
        self.assertEqual(parsed["payload"], pcm)

    def test_parse_error_frame_has_code(self) -> None:
        header = bytes([0x11, (cloud_realtime.MSG_ERROR << 4) & 0xF0, 0x00, 0x00])
        code = struct.pack(">I", 45000003)
        frame = header + code + b'{"error":"idle"}'
        parsed = cloud_realtime._parse_frame(frame)
        self.assertEqual(parsed["message_type"], cloud_realtime.MSG_ERROR)
        self.assertEqual(parsed["code"], 45000003)

    def test_parse_frame_rejects_too_short(self) -> None:
        with self.assertRaises(ValueError):
            cloud_realtime._parse_frame(b"\x11")


class RealtimeSessionConfigTests(unittest.TestCase):
    def test_session_requires_app_id_and_key(self) -> None:
        session = cloud_realtime.RealtimeSession("app", "key", speaker="zh_female_vv_jupiter_bigtts")
        self.assertEqual(session.app_id, "app")
        self.assertEqual(session.api_key, "key")
        self.assertEqual(session.model, cloud_realtime.REALTIME_DEFAULT_MODEL)

    def test_session_generates_unique_ids(self) -> None:
        first = cloud_realtime.RealtimeSession("app", "key", speaker="zh_female_vv_jupiter_bigtts")
        second = cloud_realtime.RealtimeSession("app", "key", speaker="zh_female_vv_jupiter_bigtts")
        self.assertNotEqual(first.connect_id, second.connect_id)
        self.assertNotEqual(first.session_id, second.session_id)


if __name__ == "__main__":
    unittest.main()
