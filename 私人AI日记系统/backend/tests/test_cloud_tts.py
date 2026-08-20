"""云端语音（豆包 TTS）模块测试。"""
from __future__ import annotations

import base64
import json
import unittest
from unittest import mock

from app import cloud_tts


def _sse_payload(pcm_chunks: list[bytes], code: int = 0, message: str = "") -> bytes:
    lines = []
    for chunk in pcm_chunks:
        event = {"code": code, "message": message, "data": base64.b64encode(chunk).decode("ascii")}
        lines.append(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
    return b"".join(lines)


class _FakeResponse:
    def __init__(self, body: bytes, is_success: bool = True, status_code: int = 200):
        self._body = body
        self.is_success = is_success
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self):
        return self._body.decode("utf-8").splitlines()

    def read(self) -> bytes:
        return self._body


class _FakeClient:
    captured: dict = {}

    def __init__(self, *args, **kwargs):
        self.response_body = kwargs.pop("_response_body", b"")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method, url, headers=None, json=None):
        type(self).captured = {"method": method, "url": url, "headers": headers, "json": json}
        return _FakeResponse(self.response_body)


def _patch_client(response_body: bytes):
    def factory(*args, **kwargs):
        return _FakeClient(_response_body=response_body)

    return mock.patch.object(cloud_tts.httpx, "Client", factory)


class CloudTtsPcmToWavTests(unittest.TestCase):
    def test_pcm_to_wav_produces_valid_riff_header(self) -> None:
        pcm = b"\x00\x00" * 480
        wav = cloud_tts.pcm_to_wav(pcm)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(wav[12:16], b"fmt ")
        data_size = int.from_bytes(wav[-len(pcm) - 4 : -len(pcm)], "little")
        self.assertEqual(data_size, len(pcm))
        self.assertEqual(len(wav), 44 + len(pcm))

    def test_pcm_to_wav_header_fields(self) -> None:
        pcm = b"\x01\x02\x03\x04"
        wav = cloud_tts.pcm_to_wav(pcm, sample_rate=24000, channels=1, bits=16)
        audio_format = int.from_bytes(wav[20:22], "little")
        channels = int.from_bytes(wav[22:24], "little")
        sample_rate = int.from_bytes(wav[24:28], "little")
        byte_rate = int.from_bytes(wav[28:32], "little")
        bits = int.from_bytes(wav[34:36], "little")
        self.assertEqual(audio_format, 1)
        self.assertEqual(channels, 1)
        self.assertEqual(sample_rate, 24000)
        self.assertEqual(byte_rate, 48000)
        self.assertEqual(bits, 16)

    def test_pcm_to_wav_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            cloud_tts.pcm_to_wav(b"")


class CloudTtsSynthesizePcmTests(unittest.TestCase):
    def test_synthesize_pcm_joins_sse_audio_chunks(self) -> None:
        chunks = [b"\x01\x02\x03", b"\x04\x05\x06"]
        with _patch_client(_sse_payload(chunks)):
            pcm = cloud_tts.synthesize_pcm("你好", "test-key", speaker="zh_male_yunzhou_uranus_bigtts")
        self.assertEqual(pcm, b"\x01\x02\x03\x04\x05\x06")

    def test_synthesize_pcm_passes_expected_headers_and_body(self) -> None:
        with _patch_client(_sse_payload([b"\x00\x01"])):
            cloud_tts.synthesize_pcm("你好", "test-key", speaker="zh_female_vv_uranus_bigtts", speech_rate=5)
        captured = _FakeClient.captured
        self.assertEqual(captured["url"], cloud_tts.CLOUD_TTS_ENDPOINT)
        self.assertEqual(captured["headers"]["X-Api-Key"], "test-key")
        self.assertEqual(captured["headers"]["X-Api-Resource-Id"], cloud_tts.CLOUD_TTS_RESOURCE_ID)
        self.assertEqual(captured["json"]["req_params"]["speaker"], "zh_female_vv_uranus_bigtts")
        self.assertEqual(captured["json"]["req_params"]["audio_params"]["format"], "pcm")
        self.assertEqual(captured["json"]["req_params"]["audio_params"]["speech_rate"], 5)

    def test_synthesize_pcm_raises_on_service_error_code(self) -> None:
        with _patch_client(_sse_payload([], code=3000, message="no permission")):
            with self.assertRaises(OSError):
                cloud_tts.synthesize_pcm("你好", "test-key")

    def test_synthesize_pcm_raises_when_no_audio(self) -> None:
        with _patch_client(b""):
            with self.assertRaises(OSError):
                cloud_tts.synthesize_pcm("你好", "test-key")

    def test_synthesize_pcm_requires_api_key(self) -> None:
        with self.assertRaises(ValueError):
            cloud_tts.synthesize_pcm("你好", "")

    def test_cloud_tts_configured_requires_dpapi_value(self) -> None:
        self.assertTrue(cloud_tts.cloud_tts_configured({"cloud_tts_api_key": "dpapi:abc"}))
        self.assertFalse(cloud_tts.cloud_tts_configured({"cloud_tts_api_key": ""}))
        self.assertFalse(cloud_tts.cloud_tts_configured({}))


if __name__ == "__main__":
    unittest.main()
