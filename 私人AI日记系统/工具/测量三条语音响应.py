from __future__ import annotations

import argparse
import base64
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx


def _round(value: float | int | None) -> float | None:
    return None if value is None else round(float(value), 1)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "vad_ms",
        "asr_ms",
        "request_ms",
        "model_first_token_ms",
        "model_provider_ms",
        "tts_first_chunk_ms",
        "estimated_first_audio_ms",
    )
    result: dict[str, Any] = {"rounds": len(rows)}
    for key in keys:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        if values:
            result[key] = {
                "average": _round(statistics.fmean(values)),
                "fastest": _round(min(values)),
                "slowest": _round(max(values)),
            }
    return result


def _stream_first_audio(
    client: httpx.Client,
    base_url: str,
    text: str,
    *,
    model_id: str,
    context: str,
) -> float:
    started = time.perf_counter()
    first_chunk_ms: float | None = None
    with client.stream(
        "POST",
        f"{base_url}/api/companion/voice/stream",
        json={
            "text": text,
            "context": context,
            "emotion": "gentle",
            "model_id": model_id,
            "language": "zh",
        },
        timeout=120,
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if chunk and first_chunk_ms is None:
                first_chunk_ms = (time.perf_counter() - started) * 1000
    if first_chunk_ms is None:
        raise RuntimeError("流式语音没有返回音频数据")
    return first_chunk_ms


def _chat_round(
    client: httpx.Client,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.post(f"{base_url}{endpoint}", json=payload, timeout=120)
    response.raise_for_status()
    result = response.json()
    request_ms = (time.perf_counter() - started) * 1000
    reply = " ".join(result.get("replies") or [result.get("reply") or ""]).strip()
    tts_ms = _stream_first_audio(
        client,
        base_url,
        reply,
        model_id=str(result.get("model_id") or ""),
        context=context,
    )
    return {
        "request_ms": _round(request_ms),
        "model_first_token_ms": _round(result.get("first_token_latency_ms")),
        "model_provider_ms": _round(result.get("total_latency_ms")),
        "tts_first_chunk_ms": _round(tts_ms),
        "estimated_first_audio_ms": _round(request_ms + tts_ms + 35),
        "model_id": str(result.get("model_id") or ""),
        "reasoning_level": str(result.get("reasoning_level") or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="测量 Agent、桌宠和电话的首音频等待")
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    wav_base64 = base64.b64encode(args.wav.read_bytes()).decode("ascii")
    report: dict[str, Any] = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rounds": args.rounds,
        "agent": [],
        "desktop_pet": [],
        "call": [],
    }

    with httpx.Client(trust_env=False) as client:
        health = client.get(f"{base_url}/health", timeout=10)
        health.raise_for_status()
        companion_status = client.get(f"{base_url}/api/companion/status", timeout=10)
        companion_status.raise_for_status()
        original_voice_enabled = bool(
            companion_status.json().get("pet", {}).get("settings", {}).get("voice_enabled", True)
        )
        conversation = client.post(
            f"{base_url}/api/agent/conversations",
            json={"title": "0.5.7响应延迟临时测试"},
            timeout=10,
        )
        conversation.raise_for_status()
        conversation_data = conversation.json()
        conversation_id = str(conversation_data.get("conversation_id") or conversation_data["id"])
        try:
            client.post(f"{base_url}/api/companion/voice/runtime/warmup", timeout=120).raise_for_status()
            start_call = client.post(f"{base_url}/api/companion/call/start", timeout=30)
            start_call.raise_for_status()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                status = client.get(f"{base_url}/api/companion/status", timeout=10).json()
                if status.get("system_audio", {}).get("ready"):
                    break
                error = str(status.get("system_audio", {}).get("last_error") or "")
                if error:
                    raise RuntimeError(error)
                time.sleep(0.25)
            else:
                raise TimeoutError("本地 Whisper 在 60 秒内没有准备好")

            for index in range(args.rounds):
                report["agent"].append(_chat_round(
                    client,
                    base_url,
                    "/api/agent/chat",
                    {
                        "message": f"日常延迟测试第{index + 1}轮，只用一句自然短句回应我",
                        "model_id": "auto",
                        "reasoning_level": "auto",
                        "conversation_id": conversation_id,
                        "attachments": [],
                    },
                    context="Agent文字对话延迟测试",
                ))
                time.sleep(0.4)

            # Without a connected desktop renderer, /companion/chat starts local
            # playback itself. Disable that temporary playback so this benchmark
            # measures one model request plus one streamed TTS request per round.
            client.patch(
                f"{base_url}/api/companion/settings",
                json={"voice_enabled": False},
                timeout=15,
            ).raise_for_status()
            for index in range(args.rounds):
                report["desktop_pet"].append(_chat_round(
                    client,
                    base_url,
                    "/api/companion/chat",
                    {"message": f"桌宠延迟测试第{index + 1}轮，只说一句短话", "images": []},
                    context="桌宠文字对话延迟测试",
                ))
                time.sleep(0.4)
            client.patch(
                f"{base_url}/api/companion/settings",
                json={"voice_enabled": original_voice_enabled},
                timeout=15,
            ).raise_for_status()

            for _index in range(args.rounds):
                started = time.perf_counter()
                response = client.post(
                    f"{base_url}/api/companion/call/turn",
                    json={"wav_base64": wav_base64, "language": "zh"},
                    timeout=120,
                )
                response.raise_for_status()
                result = response.json()
                request_ms = (time.perf_counter() - started) * 1000
                reply = " ".join(result.get("replies") or [result.get("reply") or ""]).strip()
                tts_ms = _stream_first_audio(
                    client,
                    base_url,
                    reply,
                    model_id=str(result.get("model_id") or ""),
                    context="电话模式响应延迟测试",
                )
                timings = result.get("timings") or {}
                vad_ms = 650.0
                report["call"].append({
                    "vad_ms": vad_ms,
                    "asr_ms": _round(timings.get("asr_ms")),
                    "request_ms": _round(request_ms),
                    "model_first_token_ms": _round(timings.get("model_first_token_ms")),
                    "model_provider_ms": _round(timings.get("model_ms")),
                    "tts_first_chunk_ms": _round(tts_ms),
                    "estimated_first_audio_ms": _round(vad_ms + request_ms + tts_ms + 35),
                    "model_id": str(result.get("model_id") or ""),
                    "reasoning_level": str(result.get("reasoning_level") or ""),
                })
                time.sleep(0.4)
        finally:
            client.patch(
                f"{base_url}/api/companion/settings",
                json={"voice_enabled": original_voice_enabled},
                timeout=15,
            )
            client.post(f"{base_url}/api/companion/call/stop", timeout=15)
            client.delete(f"{base_url}/api/agent/conversations/{conversation_id}", timeout=15)

    report["summary"] = {
        "agent": _summary(report["agent"]),
        "desktop_pet": _summary(report["desktop_pet"]),
        "call": _summary(report["call"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
