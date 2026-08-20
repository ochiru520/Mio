from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import re
import threading
import time
from typing import Any, Callable


DEFAULT_TRANSLATION_MODEL_ID = "deepseek-v4-flash"
QUICK_JAPANESE_TRANSLATIONS: dict[str, str] = {
    "嗯": "うんうん",
    "嗯?": "うん、どうしたの？",
    "嗯？": "うん、どうしたの？",
    "啊": "あ、そうなんだ",
    "啊?": "え？",
    "啊？": "え？",
    "哦": "そうなんだね",
    "好": "うん、わかったよ",
    "好啊": "うん、わかったよ",
    "好呀": "いいよ",
    "好的呀": "うん、わかったよ",
    "好吧": "うん、わかったよ",
    "好嘛": "うん、わかったよ",
    "好的": "うん、わかったよ",
    "嗯好": "うん、わかったよ",
    "嗯嗯": "うんうん、わかったよ",
    "行": "うん、いいよ",
    "行啊": "うん、いいよ",
    "可以": "うん、いいよ",
    "可以啊": "うん、いいよ",
    "对": "そうだね",
    "对啊": "そうだね",
    "对呀": "そうだね",
    "明白": "うん、わかったよ",
    "明白了": "うん、わかったよ",
    "知道了": "うん、わかったよ",
    "没事": "大丈夫",
    "谢谢": "ありがとう",
    "晚安": "おやすみ",
    "早上好": "おはよう",
    "大家好": "みんな、こんにちは",
    "哈喽大家好，我是澪~": "みんな、こんにちは。Mioだよ",
    "哈喽大家好，我是澪～": "みんな、こんにちは。Mioだよ",
    "哈喽大家好，我是Mio~": "みんな、こんにちは。Mioだよ",
    "哈喽大家好，我是Mio～": "みんな、こんにちは。Mioだよ",
}


class SpeechTranslationError(RuntimeError):
    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class SpeechTranslationResult:
    text: str
    model: str
    target_language: str
    cache_hit: bool = False


_lock = threading.RLock()
_cache: OrderedDict[tuple[str, str, str], SpeechTranslationResult] = OrderedDict()
_retry_after: dict[str, float] = {"ja": 0.0, "zh": 0.0}
_last: dict[str, Any] = {
    "target_language": "",
    "model": "",
    "error": "",
    "error_category": "",
    "cache_hit": False,
    "completed_at_monotonic": 0.0,
}


def _run_async_blocking(coroutine_factory: Callable[[], Any], timeout_seconds: float) -> Any:
    async def bounded() -> Any:
        return await asyncio.wait_for(
            coroutine_factory(),
            timeout=max(0.1, float(timeout_seconds)),
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(bounded())

    result: dict[str, Any] = {}
    finished = threading.Event()

    def worker() -> None:
        try:
            result["value"] = asyncio.run(bounded())
        except BaseException as exc:  # Re-raised in the caller thread.
            result["error"] = exc
        finally:
            finished.set()

    threading.Thread(target=worker, name="mio-speech-translation", daemon=True).start()
    finished.wait(timeout=max(1.0, float(timeout_seconds) + 1.0))
    if not finished.is_set():
        raise TimeoutError("语音朗读翻译超时。")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _normalize_translation(text: str, target_language: str, source_length: int) -> str:
    translated = " ".join(str(text or "").split()).strip()
    translated = re.sub(r"^(?:日本語|日语|中文|翻译)\s*[：:]\s*", "", translated)
    translated = translated.strip("` \t\r\n\"'“”‘’")
    if not translated or len(translated) > max(1200, source_length * 6):
        raise SpeechTranslationError("模型没有返回长度合理的译文。", category="invalid_translation")
    kana_count = len(re.findall(r"[\u3040-\u30ff]", translated))
    han_count = len(re.findall(r"[\u3400-\u9fff]", translated))
    if target_language == "ja" and kana_count == 0:
        raise SpeechTranslationError(
            "模型返回的文本没有日语假名，已拒绝把中文原文送入日语 TTS。",
            category="invalid_translation",
        )
    if target_language == "zh" and (han_count == 0 or kana_count >= 3):
        raise SpeechTranslationError("模型没有返回可用的中文译文。", category="invalid_translation")
    return translated


def _failure_category(exc: BaseException) -> str:
    if isinstance(exc, SpeechTranslationError):
        return exc.category
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    http_status = int(getattr(exc, "http_status", 0) or 0)
    if http_status in {401, 403}:
        return "authentication"
    return "model_error"


def _record_success(result: SpeechTranslationResult) -> None:
    with _lock:
        _last.update(
            {
                "target_language": result.target_language,
                "model": result.model,
                "error": "",
                "error_category": "",
                "cache_hit": result.cache_hit,
                "completed_at_monotonic": time.monotonic(),
            }
        )
        _retry_after[result.target_language] = 0.0


def _record_failure(target_language: str, exc: BaseException) -> SpeechTranslationError:
    category = _failure_category(exc)
    detail = str(exc).strip() or type(exc).__name__
    cooldown = 60.0 if category == "authentication" else 15.0 if category in {"timeout", "model_error"} else 0.0
    with _lock:
        _last.update(
            {
                "target_language": target_language,
                "model": "",
                "error": detail[:500],
                "error_category": category,
                "cache_hit": False,
                "completed_at_monotonic": time.monotonic(),
            }
        )
        _retry_after[target_language] = time.monotonic() + cooldown
    if isinstance(exc, SpeechTranslationError):
        return exc
    labels = {
        "authentication": "翻译模型鉴权失败",
        "timeout": "翻译模型请求超时",
        "model_error": "翻译模型请求失败",
    }
    return SpeechTranslationError(f"{labels.get(category, '语音翻译失败')}：{detail}", category=category)


def translate(
    text: str,
    *,
    target_language: str,
    model_id: str = DEFAULT_TRANSLATION_MODEL_ID,
    timeout_seconds: float = 4.0,
) -> SpeechTranslationResult:
    source = " ".join(str(text or "").split()).strip()
    target = str(target_language or "").strip().lower()
    if target not in {"ja", "zh"}:
        raise ValueError("语音翻译目标只支持 ja 或 zh。")
    if not source:
        raise ValueError("语音翻译原文不能为空。")

    quick_key = re.sub(r"\s+", "", source)
    # A model reply commonly ends a short acknowledgement with a full stop or
    # an exclamation mark. Those marks must not turn a local zero-cost phrase
    # into a cloud translation request. Question marks are intentionally kept
    # because "嗯？" and "嗯" have different meanings.
    exact_quick_key = quick_key
    quick_key = re.sub(r"[。.!！…~～]+$", "", quick_key)
    local_translation = (
        QUICK_JAPANESE_TRANSLATIONS.get(exact_quick_key)
        or QUICK_JAPANESE_TRANSLATIONS.get(quick_key)
    )
    if target == "ja" and local_translation:
        result = SpeechTranslationResult(
            text=local_translation,
            model="local-quick-translation",
            target_language=target,
        )
        _record_success(result)
        return result

    selected_model = str(model_id or DEFAULT_TRANSLATION_MODEL_ID).strip() or DEFAULT_TRANSLATION_MODEL_ID
    cache_key = (target, selected_model, source)
    with _lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            _cache.move_to_end(cache_key)
            result = SpeechTranslationResult(
                text=cached.text,
                model=cached.model,
                target_language=target,
                cache_hit=True,
            )
            _record_success(result)
            return result
        retry_seconds = _retry_after[target] - time.monotonic()
        if retry_seconds > 0:
            category = str(_last.get("error_category") or "model_error")
            raise SpeechTranslationError(
                f"翻译服务正在冷却，请在 {retry_seconds:.1f} 秒后重试：{_last.get('error') or '上次请求失败'}",
                category=category,
            )

    async def request_translation() -> tuple[str, str]:
        from .llm import call_chat_completion_result, resolve_model_id

        resolved_model = resolve_model_id(selected_model)
        if target == "ja":
            system_prompt = (
                "你是日语配音文本翻译器。把用户给出的中文完整翻译成自然、口语化的日语。"
                "保持原意、称呼、情绪和标点节奏；不要解释，不要加引号，不要输出中文或标签。"
                "结果必须包含自然日语假名。语气词使用日语假名，不要输出英文拟声词。"
            )
        else:
            system_prompt = (
                "你是中文配音文本翻译器。把用户给出的日语完整翻译成自然、口语化的中文。"
                "保持原意、称呼、情绪和标点节奏；不要解释，不要加引号，不要输出日语或标签。"
            )
        completion = await call_chat_completion_result(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": source},
            ],
            temperature=0.15,
            model_id=resolved_model,
            reasoning_level="off",
            retry_attempts=1,
        )
        return completion.content, completion.model

    try:
        translated, actual_model = _run_async_blocking(request_translation, timeout_seconds)
        normalized = _normalize_translation(translated, target, len(source))
        result = SpeechTranslationResult(
            text=normalized,
            model=str(actual_model or selected_model),
            target_language=target,
        )
        with _lock:
            _cache[cache_key] = result
            _cache.move_to_end(cache_key)
            while len(_cache) > 128:
                _cache.popitem(last=False)
        _record_success(result)
        return result
    except BaseException as exc:
        raise _record_failure(target, exc) from exc


def status() -> dict[str, Any]:
    with _lock:
        retry_after_seconds = max(
            0.0,
            float(_retry_after.get(str(_last.get("target_language") or ""), 0.0)) - time.monotonic(),
        )
        return {
            "cache_size": len(_cache),
            "last_target_language": str(_last.get("target_language") or ""),
            "last_model": str(_last.get("model") or ""),
            "last_error": str(_last.get("error") or ""),
            "last_error_category": str(_last.get("error_category") or ""),
            "last_cache_hit": bool(_last.get("cache_hit")),
            "retry_after_seconds": round(retry_after_seconds, 2),
        }


def reset_for_tests() -> None:
    with _lock:
        _cache.clear()
        _retry_after.update({"ja": 0.0, "zh": 0.0})
        _last.update(
            {
                "target_language": "",
                "model": "",
                "error": "",
                "error_category": "",
                "cache_hit": False,
                "completed_at_monotonic": 0.0,
            }
        )


__all__ = [
    "DEFAULT_TRANSLATION_MODEL_ID",
    "SpeechTranslationError",
    "SpeechTranslationResult",
    "reset_for_tests",
    "status",
    "translate",
]
