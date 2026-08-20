"""实时语音对话：前端 WebSocket 代理到豆包 Realtime。"""
from __future__ import annotations

import asyncio
import base64
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import cloud_realtime, companion_service
from ..cloud_tts import _decode_api_key

router = APIRouter()


def _realtime_credentials() -> tuple[str, str, str, str]:
    """从伴生配置取 App ID / API Key / 音色 / 语速。"""
    config = companion_service.load_config()
    api_key = _decode_api_key(config)
    app_id = str(config.get("cloud_tts_app_id") or "").strip()
    speaker = str(config.get("cloud_tts_speaker") or "zh_female_vv_uranus_bigtts")
    speech_rate = int(config.get("cloud_tts_speech_rate") or 0)
    return app_id, api_key, speaker, speech_rate


@router.websocket("/api/realtime/voice")
async def realtime_voice(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_running_loop()
    session: cloud_realtime.RealtimeSession | None = None
    stopped = asyncio.Event()

    def notify(message: dict) -> None:
        if websocket.client_state.name != "CONNECTED":
            return
        future = asyncio.run_coroutine_threadsafe(
            websocket.send_text(json.dumps(message, ensure_ascii=False)),
            loop,
        )
        # 消费结果，避免“Future exception was never retrieved”泄漏告警（前端已断开时静默）。
        future.add_done_callback(
            lambda f: f.exception() if not f.cancelled() else None
        )

    def on_asr(text: str, final: bool) -> None:
        notify({"type": "asr", "text": text, "final": bool(final)})

    def on_tts_audio(pcm: bytes) -> None:
        notify({"type": "tts_audio", "data": base64.b64encode(pcm).decode("ascii")})

    def on_chat_text(text: str) -> None:
        notify({"type": "chat", "text": text})

    def on_error(message: str) -> None:
        notify({"type": "error", "message": message})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                notify({"type": "error", "message": "消息格式不正确。"})
                continue
            kind = str(message.get("type") or "")
            if kind == "start":
                # 重复 start 守卫：先停掉旧会话，避免连接泄漏。
                if session is not None:
                    try:
                        await asyncio.to_thread(session.close)
                    except Exception:
                        pass
                    session = None
                try:
                    app_id, api_key, speaker, _ = await asyncio.to_thread(_realtime_credentials)
                    instructions = str(message.get("instructions") or "").strip()[:2000]
                    session = cloud_realtime.RealtimeSession(
                        app_id,
                        api_key,
                        speaker=speaker,
                        instructions=instructions,
                        on_asr=on_asr,
                        on_tts_audio=on_tts_audio,
                        on_chat_text=on_chat_text,
                        on_error=on_error,
                    )
                    await asyncio.to_thread(session.start)
                    # 实时会话期间，屏幕观察等产生的朗读文本交给豆包实时语音出声
                    def bridge(text: str) -> None:
                        current = session
                        if current is not None:
                            current.send_tts_text(text)

                    companion_service.register_realtime_text_bridge(bridge)
                    notify({"type": "started"})
                except Exception as exc:
                    # 启动失败：确保残留会话已清理。
                    failed_session = session
                    session = None
                    if failed_session is not None:
                        try:
                            await asyncio.to_thread(failed_session.close)
                        except Exception:
                            pass
                    notify({"type": "error", "message": f"实时语音启动失败：{exc}"})
            elif kind == "audio":
                if session is None:
                    notify({"type": "error", "message": "请先开始实时对话。"})
                    continue
                try:
                    pcm = base64.b64decode(str(message.get("data") or ""))
                except (ValueError, TypeError):
                    continue
                await asyncio.to_thread(session.send_audio, pcm)
            elif kind == "end_asr":
                if session is not None:
                    await asyncio.to_thread(session.send_end_asr)
            elif kind == "text":
                if session is None:
                    notify({"type": "error", "message": "请先开始实时对话。"})
                    continue
                await asyncio.to_thread(session.send_text, str(message.get("text") or "")[:4000])
            elif kind == "tts_text":
                if session is not None:
                    await asyncio.to_thread(session.send_tts_text, str(message.get("text") or "")[:4000])
            elif kind == "stop":
                stopped.set()
                companion_service.register_realtime_text_bridge(None)
                if session is not None:
                    await asyncio.to_thread(session.close)
                    session = None
                notify({"type": "stopped"})
                break
            else:
                notify({"type": "error", "message": f"不支持的消息类型：{kind}"})
    except WebSocketDisconnect:
        pass
    finally:
        companion_service.register_realtime_text_bridge(None)
        if session is not None:
            try:
                await asyncio.to_thread(session.close)
            except Exception:
                pass
