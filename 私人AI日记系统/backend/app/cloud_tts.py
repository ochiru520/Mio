"""火山引擎豆包语音合成（标准 TTS）客户端。

用途：公开版默认的"云端语音"引擎。用户在语音设置里填入
火山引擎控制台的语音 API Key（https://console.volcengine.com/speech/new/setting/apikeys），
回复朗读与试听即可直接出声，不需要本地 GPT-SoVITS 环境或显卡。

接入依据（官方示例）：
- https://www.volcengine.com/docs/6561/1598757 （HTTP 单向流式 V3）
- bytedance/agentkit-samples skills/byted-text-to-speech/scripts/text_to_speech.py
"""

from __future__ import annotations

import base64
import json
import struct
import uuid
from typing import Any

import httpx

CLOUD_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
CLOUD_TTS_RESOURCE_ID = "seed-tts-2.0"
CLOUD_TTS_DEFAULT_SPEAKER = "zh_female_vv_uranus_bigtts"
CLOUD_TTS_SAMPLE_RATE = 24000

# 中文音色（seed-tts-2.0 官方音色，友好名只用于设置界面展示）
CLOUD_TTS_SPEAKERS: tuple[dict[str, str], ...] = (
    {"id": "zh_female_vv_uranus_bigtts", "name": "薇薇（活泼女声）"},
    {"id": "zh_female_xiaohe_uranus_bigtts", "name": "小荷（清甜女声）"},
    {"id": "zh_male_yunzhou_uranus_bigtts", "name": "云舟（沉稳男声）"},
    {"id": "zh_male_xiaotian_uranus_bigtts", "name": "小天（清亮男声）"},
)


def _decode_api_key(config: dict[str, Any]) -> str:
    """从配置里取出云端语音 API Key 并解密。

    Key 在 save_config 时用 DPAPI 加密保存；本函数只负责解密使用。
    """
    from .secret_store import unprotect_secret

    value = str(config.get("cloud_tts_api_key") or "").strip()
    if not value:
        raise ValueError("还没有配置云端语音 API Key，请先在语音设置中填写。")
    try:
        return unprotect_secret(value)
    except (ValueError, RuntimeError, OSError) as exc:
        raise ValueError("云端语音 API Key 无法解密，请重新填写。") from exc


def cloud_tts_configured(config: dict[str, Any]) -> bool:
    return bool(str(config.get("cloud_tts_api_key") or "").strip())


def synthesize_pcm(
    text: str,
    api_key: str,
    *,
    speaker: str = CLOUD_TTS_DEFAULT_SPEAKER,
    speech_rate: int = 0,
    timeout: float = 60.0,
) -> bytes:
    """调用豆包 TTS，返回 PCM 16kHz/24kHz 单声道 s16le 音频数据。"""
    if not text or not text.strip():
        raise ValueError("这条消息没有可朗读的正文。")
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("云端语音 API Key 为空。")
    speech_rate = max(-50, min(100, int(speech_rate or 0)))
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": CLOUD_TTS_RESOURCE_ID,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Key": api_key,
    }
    additions = {
        "disable_markdown_filter": True,
        "enable_latex_tn": False,
    }
    body: dict[str, Any] = {
        "user": {"uid": "mio-agent"},
        "req_params": {
            "text": text.strip(),
            "speaker": speaker,
            "sample_rate": CLOUD_TTS_SAMPLE_RATE,
            "audio_params": {
                "format": "pcm",
                "speech_rate": speech_rate,
                "loudness_rate": 0,
            },
            "additions": json.dumps(additions, ensure_ascii=False),
        },
    }
    chunks: list[bytes] = []
    last_code = 0
    last_message = ""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            with client.stream("POST", CLOUD_TTS_ENDPOINT, headers=headers, json=body) as response:
                if not response.is_success:
                    detail = response.read().decode("utf-8", errors="replace")[:400]
                    raise OSError(f"云端语音服务返回 {response.status_code}：{detail}")
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    last_code = int(event.get("code") or 0)
                    last_message = str(event.get("message") or "")
                    if last_code not in (0, 20000000):
                        raise OSError(
                            f"云端语音合成失败（{last_code}）：{last_message or '服务端错误'}"
                        )
                    data = event.get("data")
                    if data:
                        try:
                            chunks.append(base64.b64decode(data, validate=False))
                        except (ValueError, TypeError):
                            continue
    except httpx.HTTPError as exc:
        raise OSError(f"云端语音请求失败：{exc}") from exc
    if last_code not in (0, 20000000):
        raise OSError(f"云端语音合成失败（{last_code}）：{last_message or '服务端错误'}")
    content = b"".join(chunks)
    if not content:
        raise OSError("云端语音服务没有返回音频数据，请检查 API Key 与音色。")
    return content


def pcm_to_wav(pcm: bytes, *, sample_rate: int = CLOUD_TTS_SAMPLE_RATE, channels: int = 1, bits: int = 16) -> bytes:
    """把 PCM 裸数据封装成可播放的 WAV（RIFF 头 + fmt + data）。"""
    if not pcm:
        raise ValueError("没有可封装的音频数据。")
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
        + b"data"
        + struct.pack("<I", len(pcm))
    )
    return header + pcm


def synthesize_wav(text: str, config: dict[str, Any]) -> bytes:
    """云端引擎的完整合成：文本 -> 豆包 PCM -> WAV。"""
    api_key = _decode_api_key(config)
    speaker = str(config.get("cloud_tts_speaker") or CLOUD_TTS_DEFAULT_SPEAKER)
    speech_rate = int(config.get("cloud_tts_speech_rate") or 0)
    pcm = synthesize_pcm(text, api_key, speaker=speaker, speech_rate=speech_rate)
    return pcm_to_wav(pcm)


__all__ = [
    "CLOUD_TTS_ENDPOINT",
    "CLOUD_TTS_RESOURCE_ID",
    "CLOUD_TTS_DEFAULT_SPEAKER",
    "CLOUD_TTS_SPEAKERS",
    "CLOUD_TTS_SAMPLE_RATE",
    "cloud_tts_configured",
    "synthesize_pcm",
    "pcm_to_wav",
    "synthesize_wav",
]
