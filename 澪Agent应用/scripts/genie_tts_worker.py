from __future__ import annotations

import asyncio
from collections import OrderedDict
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import wave
import re


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _require_file(value: object, label: str) -> Path:
    path = Path(str(value or "")).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label}不存在：{path}")
    return path


def _require_dir(value: object, label: str) -> Path:
    path = Path(str(value or "")).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"{label}不存在：{path}")
    return path


def _language(value: object) -> str:
    normalized = str(value or "zh").strip().lower()
    return {
        "zh": "Chinese", "all_zh": "Chinese", "yue": "Chinese",
        "ja": "Japanese", "all_ja": "Japanese",
        "en": "English", "all_en": "English",
    }.get(normalized, "Chinese")


def _safe_g2p_text(value: object, language: str, *, fallback: str) -> str:
    """Keep Genie G2P away from empty punctuation and unsupported control text."""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    patterns = {
        "Chinese": r"[^\u3400-\u9fffA-Za-z0-9，。！？；：、,.!?;:…\-~ ]+",
        "Japanese": r"[^\u3040-\u30ff\u3400-\u9fffA-Za-z0-9，。！？；：、,.!?;:…\-~ ]+",
        "English": r"[^A-Za-z0-9 .,!?;:'\"()\-]+",
    }
    text = re.sub(patterns.get(language, patterns["Chinese"]), "", text)
    text = re.sub(r"\s+", " ", text).strip()
    content_pattern = {
        "Chinese": r"[\u3400-\u9fffA-Za-z0-9]",
        "Japanese": r"[\u3040-\u30ff\u3400-\u9fffA-Za-z0-9]",
        "English": r"[A-Za-z0-9]",
    }.get(language, r"[\u3400-\u9fffA-Za-z0-9]")
    return text if re.search(content_pattern, text) else fallback


def _set_reference_audio_safely(
    genie_tts,
    character: str,
    reference: Path,
    prompt_text: str,
    prompt_language: str,
    fallback_text: str,
) -> bool:
    """Set the reference cache and retry once after Genie G2P defects."""
    try:
        with contextlib.redirect_stdout(sys.stderr):
            genie_tts.set_reference_audio(character, reference, prompt_text, prompt_language)
        return False
    except Exception as prompt_error:
        if prompt_text == fallback_text:
            raise
        print(
            f"Genie 参考文本 G2P 失败，使用同语言安全短句重试：{prompt_error}",
            file=sys.stderr,
        )
        with contextlib.redirect_stdout(sys.stderr):
            genie_tts.set_reference_audio(character, reference, fallback_text, prompt_language)
        return True


async def _collect_pcm(genie_tts, character: str, text: str) -> tuple[bytes, float | None]:
    chunks: list[bytes] = []
    started = time.perf_counter()
    first_chunk_ms: float | None = None
    async for chunk in genie_tts.tts_async(
        character,
        text,
        play=False,
        split_sentence=False,
    ):
        if chunk and first_chunk_ms is None:
            first_chunk_ms = (time.perf_counter() - started) * 1000
        if chunk:
            chunks.append(bytes(chunk))
    return b"".join(chunks), first_chunk_ms


def _write_wav(path: Path, pcm: bytes) -> tuple[int, float]:
    if not pcm or len(pcm) % 2:
        raise OSError("Genie 没有生成有效的 16-bit PCM 音频。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with wave.open(str(temporary), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(32000)
        stream.writeframes(pcm)
    temporary.replace(path)
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getframerate() != 32000:
            raise OSError("Genie 输出的 WAV 格式不符合 32 kHz / 16-bit / 单声道要求。")
        frames = stream.getnframes()
    return frames, frames / 32000


def main() -> int:
    genie_data = _require_dir(os.environ.get("MIO_GENIE_DATA_DIR"), "GenieData")
    os.environ["GENIE_DATA_DIR"] = str(genie_data)
    os.environ["HUBERT_MODEL_DIR"] = str(genie_data / "chinese-hubert-base")
    os.environ["Chinese_G2P_DIR"] = str(genie_data / "G2P" / "ChineseG2P")
    os.environ["English_G2P_DIR"] = str(genie_data / "G2P" / "EnglishG2P")
    os.environ["SV_MODEL"] = str(genie_data / "speaker_encoder.onnx")
    os.environ.setdefault("Max_Cached_Character_Models", "1")
    os.environ.setdefault("Max_Cached_Reference_Audio", "4")

    try:
        with contextlib.redirect_stdout(sys.stderr):
            import genie_tts
    except Exception as exc:
        _emit({"ok": False, "fatal": True, "error": f"Genie 运行环境加载失败：{exc}"})
        return 1

    loaded_characters: OrderedDict[str, str] = OrderedDict()
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
            action = str(request.get("action") or "")
            if action == "shutdown":
                with contextlib.suppress(Exception):
                    genie_tts.stop()
                _emit({"ok": True})
                return 0
            if action == "probe":
                _emit({
                    "ok": True,
                    "runtime": "genie",
                    "sample_rate": 32000,
                    "providers": ["CPUExecutionProvider"],
                    "genie_data": str(genie_data),
                })
                continue
            if action != "synthesize":
                raise ValueError("未知的 Genie Worker 操作。")

            model_dir = _require_dir(request.get("model_dir"), "Genie ONNX 音色目录")
            reference = _require_file(request.get("reference_audio"), "参考音频")
            raw_prompt_text = str(request.get("prompt_text") or "").strip()
            raw_text = str(request.get("text") or "").strip()
            if not raw_prompt_text:
                raise ValueError("参考音频原文不能为空。")
            if not raw_text:
                raise ValueError("待合成文字不能为空。")

            model_language = _language(request.get("text_language"))
            prompt_language = _language(request.get("prompt_language"))
            fallback_texts = {"Chinese": "你好。", "Japanese": "こんにちは。", "English": "Hello."}
            prompt_text = _safe_g2p_text(
                raw_prompt_text,
                prompt_language,
                fallback=fallback_texts[prompt_language],
            )
            text = _safe_g2p_text(
                raw_text,
                model_language,
                fallback=fallback_texts[model_language],
            )
            # Genie uses the same ONNX sessions for Chinese and Japanese.  Only
            # its G2P language marker changes, so reloading the full character
            # on every language switch wastes seconds and several GB of memory.
            key_source = str(model_dir)
            model_key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
            character = loaded_characters.get(model_key, f"mio-{model_key[:16]}")
            if model_key not in loaded_characters:
                if len(loaded_characters) >= 1:
                    _, oldest_character = loaded_characters.popitem(last=False)
                    with contextlib.redirect_stdout(sys.stderr):
                        genie_tts.unload_character(oldest_character)
                with contextlib.redirect_stdout(sys.stderr):
                    genie_tts.load_character(character, model_dir, model_language)
                loaded_characters[model_key] = character
            else:
                loaded_characters.move_to_end(model_key)

            # Requests are processed serially by this worker. Updating the
            # character language here is therefore safe and avoids rebuilding
            # identical ONNX Runtime sessions just to select another G2P path.
            from genie_tts.ModelManager import model_manager
            model_manager.character_to_language[character.lower()] = model_language

            # Some Genie releases crash in ToneSandhi on otherwise valid short
            # prompts. Keep the audio and rebuild only its phoneme cache.
            _set_reference_audio_safely(
                genie_tts,
                character,
                reference,
                prompt_text,
                prompt_language,
                fallback_texts[prompt_language],
            )
            with contextlib.redirect_stdout(sys.stderr):
                started = time.perf_counter()
                pcm, first_chunk_ms = asyncio.run(_collect_pcm(genie_tts, character, text))
            output = Path(str(request.get("output_path") or "")).expanduser().resolve()
            frames, duration = _write_wav(output, pcm)
            _emit({
                "ok": True,
                "output_path": str(output),
                "sample_rate": 32000,
                "channels": 1,
                "sample_width": 2,
                "frames": frames,
                "duration_seconds": round(duration, 3),
                "first_audio_ms": round(first_chunk_ms, 1) if first_chunk_ms is not None else None,
                "total_ms": round((time.perf_counter() - started) * 1000, 1),
            })
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            _emit({"ok": False, "error": str(exc)[:1200]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
