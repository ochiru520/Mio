"""豆包 Realtime 实时语音对话客户端（二进制协议）。

协议依据：
- https://www.volcengine.com/docs/6561/1594356
- GitHub GizClaw/doubao-speech-go docs/realtime_speech.md（帧格式与事件号）

帧结构：4 字节头 + 可选字段 + payload
- byte0: 高4位=协议版本(0b0001)，低4位=头长度(0b0001 => 4字节)
- byte1: 高4位=消息类型，低4位=类型专用标志
- byte2: 高4位=序列化(0b0000 raw / 0b0001 JSON)，低4位=压缩(0b0000 无)
- byte3: 保留 0x00

消息类型：0b0001 客户端文本请求，0b1001 服务端文本响应，
0b0010 客户端音频，0b1011 服务端音频，0b1111 错误帧。

可选字段顺序：code(4B) / sequence(4B) / event(4B) /
connect id size(4B)+id / session id size(4B)+id。
"""

from __future__ import annotations

import json
import struct
import threading
import time
import uuid
from typing import Any, Callable

import websockets
import websockets.sync.client

REALTIME_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
REALTIME_RESOURCE_ID = "volc.speech.dialog"
REALTIME_DEFAULT_MODEL = "1.2.1.1"  # O 2.0

# 消息类型
MSG_CLIENT_TEXT = 0b0001
MSG_SERVER_TEXT = 0b1001
MSG_CLIENT_AUDIO = 0b0010
MSG_SERVER_AUDIO = 0b1011
MSG_ERROR = 0b1111

# 客户端事件
EVENT_START_CONNECTION = 1
EVENT_FINISH_CONNECTION = 2
EVENT_START_SESSION = 100
EVENT_FINISH_SESSION = 102
EVENT_TASK_REQUEST = 200
EVENT_END_ASR = 400
EVENT_CHAT_TTS_TEXT = 500
EVENT_CHAT_TEXT_QUERY = 501

# 服务端事件
EVENT_CONNECTION_STARTED = 50
EVENT_CONNECTION_FAILED = 51
EVENT_CONNECTION_FINISHED = 52
EVENT_SESSION_STARTED = 150
EVENT_SESSION_FINISHED = 152
EVENT_SESSION_FAILED = 153
EVENT_USAGE = 154
EVENT_TTS_SENTENCE_START = 350
EVENT_TTS_SENTENCE_END = 351
EVENT_TTS_RESPONSE = 352
EVENT_TTS_ENDED = 359
EVENT_ASR_INFO = 450
EVENT_ASR_RESPONSE = 451
EVENT_ASR_ENDED = 459
EVENT_CHAT_RESPONSE = 550
EVENT_CHAT_ENDED = 559
EVENT_DIALOG_ERROR = 599


def _frame(message_type: int, *, event: int = 0, payload: bytes = b"", serialization: int = 0b0000) -> bytes:
    header = bytes(
        [
            0x11,
            (message_type << 4) & 0xF0,
            (serialization << 4) & 0xF0,
            0x00,
        ]
    )
    optional = b""
    if event:
        optional += struct.pack(">I", event)
    return header + optional + payload


def _json_frame(event: int, payload: dict[str, Any]) -> bytes:
    return _frame(
        MSG_CLIENT_TEXT,
        event=event,
        payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        serialization=0b0001,
    )


def _parse_frame(data: bytes) -> dict[str, Any]:
    """解析一帧（可能在字节流中连续多帧；调用方自行切分）。"""
    if len(data) < 4:
        raise ValueError("帧太短。")
    header_size = data[0] & 0x0F
    if header_size < 1:
        raise ValueError("非法帧头长度。")
    message_type = (data[1] >> 4) & 0x0F
    serialization = (data[2] >> 4) & 0x0F
    compression = data[2] & 0x0F
    offset = header_size * 4
    result: dict[str, Any] = {
        "message_type": message_type,
        "serialization": serialization,
        "compression": compression,
        "event": 0,
        "code": 0,
        "payload": b"",
    }
    if message_type == MSG_ERROR:
        if len(data) < offset + 4:
            raise ValueError("错误帧缺少错误码。")
        result["code"] = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
    if offset + 4 <= len(data) and message_type in {MSG_CLIENT_TEXT, MSG_SERVER_TEXT}:
        # 尝试按 event 字段解析：文本事件帧带 event 号。
        result["event"] = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
    result["payload"] = data[offset:]
    return result


class RealtimeClientError(RuntimeError):
    pass


class RealtimeSession:
    """一个豆包 Realtime 会话：连接、收发、回调。"""

    def __init__(
        self,
        app_id: str,
        api_key: str,
        *,
        speaker: str,
        instructions: str = "",
        model: str = REALTIME_DEFAULT_MODEL,
        on_asr: Callable[[str, bool], None] | None = None,
        on_tts_audio: Callable[[bytes], None] | None = None,
        on_chat_text: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_session_started: Callable[[str], None] | None = None,
    ) -> None:
        self.app_id = app_id.strip()
        self.api_key = api_key.strip()
        self.speaker = speaker
        self.instructions = instructions
        self.model = model
        self.on_asr = on_asr
        self.on_tts_audio = on_tts_audio
        self.on_chat_text = on_chat_text
        self.on_error = on_error
        self.on_session_started = on_session_started
        self.connect_id = uuid.uuid4().hex
        self.session_id = uuid.uuid4().hex
        self.dialog_id = ""
        self._socket: Any = None
        self._receive_thread: threading.Thread | None = None
        self._closed = False
        self._send_lock = threading.Lock()

    def start(self) -> None:
        headers = {
            "X-Api-App-Id": self.app_id,
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": REALTIME_RESOURCE_ID,
        }
        try:
            self._socket = websockets.sync.client.connect(
                REALTIME_ENDPOINT,
                additional_headers=headers,
                open_timeout=15,
                max_size=8 * 1024 * 1024,
            )
            # StartConnection
            self._socket.send(_json_frame(EVENT_START_CONNECTION, {}))
            # StartSession
            session_payload: dict[str, Any] = {
                "asr": {
                    "audio_info": {"format": "pcm", "sample_rate": 16000, "channel": 1},
                },
                "tts": {
                    "speaker": self.speaker,
                    "audio_config": {"channel": 1, "format": "pcm_s16le", "sample_rate": 24000, "bits": 16},
                },
                "dialog": {
                    "extra": {"model": self.model},
                },
            }
            if self.instructions:
                session_payload["dialog"]["system_role"] = self.instructions
            self._socket.send(_json_frame(EVENT_START_SESSION, session_payload))
            self._receive_thread = threading.Thread(
                target=self._receive_loop,
                name="doubao-realtime-recv",
                daemon=True,
            )
            self._receive_thread.start()
        except Exception:
            # 连接或握手失败：标记关闭并安全清理 socket，避免半初始化对象残留。
            self._closed = True
            socket_obj = getattr(self, "_socket", None)
            if socket_obj is not None:
                try:
                    socket_obj.close()
                except Exception:
                    pass
                self._socket = None
            raise

    def send_audio(self, pcm: bytes) -> None:
        if not pcm or self._closed or self._socket is None:
            return
        with self._send_lock:
            self._socket.send(_frame(MSG_CLIENT_AUDIO, payload=pcm))

    def send_end_asr(self) -> None:
        if self._closed or self._socket is None:
            return
        with self._send_lock:
            self._socket.send(_json_frame(EVENT_END_ASR, {}))

    def send_text(self, text: str) -> None:
        if self._closed or self._socket is None:
            return
        with self._send_lock:
            self._socket.send(_json_frame(EVENT_CHAT_TEXT_QUERY, {"text": text}))

    def send_tts_text(self, text: str) -> None:
        """注入要朗读的文本（用于屏幕观察等外部来源）。"""
        if self._closed or not text.strip() or self._socket is None:
            return
        with self._send_lock:
            self._socket.send(_json_frame(EVENT_CHAT_TTS_TEXT, {"text": text.strip()}))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        socket_obj = self._socket
        if socket_obj is None:
            return
        try:
            socket_obj.send(_json_frame(EVENT_FINISH_SESSION, {}))
            socket_obj.send(_json_frame(EVENT_FINISH_CONNECTION, {}))
        except Exception:
            pass
        try:
            socket_obj.close()
        except Exception:
            pass

    def _receive_loop(self) -> None:
        try:
            for raw in self._socket:
                if self._closed:
                    break
                if isinstance(raw, str):
                    continue
                self._handle(raw)
        except websockets.ConnectionClosed:
            pass
        except Exception as exc:
            self._notify_error(f"实时语音连接中断：{exc}")
        finally:
            self._closed = True

    def _handle(self, data: bytes) -> None:
        try:
            frame = _parse_frame(data)
        except ValueError:
            return
        if frame["message_type"] == MSG_SERVER_AUDIO:
            if self.on_tts_audio and frame["payload"]:
                self.on_tts_audio(frame["payload"])
            return
        if frame["message_type"] == MSG_ERROR:
            self._notify_error(f"实时语音错误（{frame['code']}）")
            return
        if frame["message_type"] != MSG_SERVER_TEXT:
            return
        try:
            payload = json.loads(frame["payload"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        event = frame["event"]
        if event == EVENT_SESSION_STARTED:
            self.dialog_id = str(payload.get("dialog_id") or "")
            if self.on_session_started:
                self.on_session_started(self.dialog_id)
        elif event in {EVENT_SESSION_FAILED, EVENT_CONNECTION_FAILED, EVENT_DIALOG_ERROR}:
            message = str(payload.get("error") or payload.get("message") or payload.get("status_code") or "服务端错误")
            self._notify_error(f"实时语音服务错误：{message}")
        elif event == EVENT_ASR_RESPONSE:
            text = str(payload.get("text") or "")
            final = bool(payload.get("final") or payload.get("is_final") or False)
            if text and self.on_asr:
                self.on_asr(text, final)
        elif event == EVENT_ASR_INFO:
            text = str(payload.get("text") or "")
            if text and self.on_asr:
                self.on_asr(text, False)
        elif event == EVENT_CHAT_RESPONSE:
            text = str(payload.get("text") or "")
            if text and self.on_chat_text:
                self.on_chat_text(text)
        elif event == EVENT_USAGE:
            pass

    def _notify_error(self, message: str) -> None:
        if self.on_error:
            try:
                self.on_error(message)
            except Exception:
                pass


__all__ = [
    "RealtimeSession",
    "RealtimeClientError",
    "REALTIME_ENDPOINT",
    "REALTIME_DEFAULT_MODEL",
    "_parse_frame",
    "_frame",
]
