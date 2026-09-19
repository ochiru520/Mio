"""Companion speech text; dependencies are explicitly injected."""
from __future__ import annotations

from typing import Any, Callable
import re


class SpeechTextService:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_ADAPTIVE_QQ_VOICE_RE: Callable[..., Any],
                 dep_SPEECH_CONTENT_RE: Callable[..., Any],
                 dep_SPEECH_EMOTION_LABELS: Callable[..., Any],
                 dep_SPEECH_EMOTION_PATTERNS: Callable[..., Any],
                 dep_SPEECH_EMOTION_PRIORITY: Callable[..., Any],
                 dep_SPEECH_HAN_RE: Callable[..., Any],
                 dep_SPEECH_JAPANESE_RE: Callable[..., Any],
                 dep_SPEECH_LATIN_RE: Callable[..., Any],
                 dep_SPEECH_LETTER_PRONUNCIATIONS: Callable[..., Any],
                 dep_SPEECH_META_RE: Callable[..., Any],
                 dep_SPEECH_PREFIX_RE: Callable[..., Any],
                 dep_SPEECH_REQUESTED_EMOTION_PATTERNS: Callable[..., Any],
                 dep_SPEECH_STAGE_DIRECTION_LINE_RE: Callable[..., Any],
                 dep_SPEECH_STAGE_DIRECTION_RE: Callable[..., Any],
                 dep_SPEECH_TERM_PRONUNCIATIONS: Callable[..., Any],
                 dep_load_config: Callable[..., Any],
                 ) -> None:
        self._dep_ADAPTIVE_QQ_VOICE_RE = dep_ADAPTIVE_QQ_VOICE_RE
        self._dep_SPEECH_CONTENT_RE = dep_SPEECH_CONTENT_RE
        self._dep_SPEECH_EMOTION_LABELS = dep_SPEECH_EMOTION_LABELS
        self._dep_SPEECH_EMOTION_PATTERNS = dep_SPEECH_EMOTION_PATTERNS
        self._dep_SPEECH_EMOTION_PRIORITY = dep_SPEECH_EMOTION_PRIORITY
        self._dep_SPEECH_HAN_RE = dep_SPEECH_HAN_RE
        self._dep_SPEECH_JAPANESE_RE = dep_SPEECH_JAPANESE_RE
        self._dep_SPEECH_LATIN_RE = dep_SPEECH_LATIN_RE
        self._dep_SPEECH_LETTER_PRONUNCIATIONS = dep_SPEECH_LETTER_PRONUNCIATIONS
        self._dep_SPEECH_META_RE = dep_SPEECH_META_RE
        self._dep_SPEECH_PREFIX_RE = dep_SPEECH_PREFIX_RE
        self._dep_SPEECH_REQUESTED_EMOTION_PATTERNS = dep_SPEECH_REQUESTED_EMOTION_PATTERNS
        self._dep_SPEECH_STAGE_DIRECTION_LINE_RE = dep_SPEECH_STAGE_DIRECTION_LINE_RE
        self._dep_SPEECH_STAGE_DIRECTION_RE = dep_SPEECH_STAGE_DIRECTION_RE
        self._dep_SPEECH_TERM_PRONUNCIATIONS = dep_SPEECH_TERM_PRONUNCIATIONS
        self._dep_load_config = dep_load_config

    def _speech_latin_pronunciation(self, match: re.Match[str]) -> str:
        word = match.group(0).lower()
        known = self._dep_SPEECH_TERM_PRONUNCIATIONS().get(word)
        if known:
            return known
        return "".join(self._dep_SPEECH_LETTER_PRONUNCIATIONS()[letter] for letter in word)


    def clean_speech_text(self, text: str) -> str:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not raw.strip():
            return ""
        lines: list[str] = []
        for raw_line in raw.split("\n"):
            clean = re.sub(r"[^\S\n]+", " ", raw_line).strip()
            if not clean or self._dep_SPEECH_META_RE().fullmatch(clean):
                continue
            clean = self._dep_SPEECH_STAGE_DIRECTION_RE().sub("", clean)
            if self._dep_SPEECH_STAGE_DIRECTION_LINE_RE().fullmatch(clean):
                continue
            previous = None
            while previous != clean:
                previous = clean
                clean = self._dep_SPEECH_PREFIX_RE().sub("", clean).strip()
            clean = clean.strip(" \t\r\n“”\"‘’'（）()")
            clean = clean.replace("——", "，").replace("—", "，").replace("……", "…")
            clean = re.sub(r"[，,]{2,}", "，", clean)
            clean = self._dep_SPEECH_LATIN_RE().sub(self._speech_latin_pronunciation, clean)
            if self._dep_SPEECH_CONTENT_RE().search(clean):
                lines.append(clean)
        return "\n".join(lines)


    def speech_text_language(self, text: str, configured_language: str = "auto") -> str:
        configured = str(configured_language or "auto").strip().lower()
        if configured != "auto":
            return configured
        kana_count = len(self._dep_SPEECH_JAPANESE_RE().findall(text))
        han_count = len(self._dep_SPEECH_HAN_RE().findall(text))
        if not kana_count and han_count:
            return "zh"
        if not kana_count:
            return "auto"
        chinese_markers = len(re.findall(r"[这那的了我你请说听给今晚今天明天可以现在]", text))
        if han_count and chinese_markers >= 2:
            return "auto"
        return "ja"


    def _naturalize_short_speech_text(self, text: str, language: str) -> str:
        """Expand isolated interjections before TTS so Genie cannot echo a reference clip."""
        raw = str(text or "").strip()
        if language in {"zh", "all_zh"}:
            replacements = {
                "好": "好的",
                "好啊": "好啊，我知道了",
                "好的": "好的，我明白了",
                "好的呀": "好的呀，我知道了",
                "好呀": "好呀，我知道了",
                "好吧": "好吧，我知道了",
                "好嘛": "好嘛，我知道了",
                "嗯": "嗯嗯",
                "嗯好": "嗯嗯，好的",
                "嗯嗯": "嗯嗯，我在听",
                "哦": "哦哦",
                "啊": "啊，我明白了",
                "行": "可以",
                "行啊": "可以啊，我知道了",
                "可以": "可以，我知道了",
                "可以啊": "可以啊，我知道了",
                "知道了": "知道了，我会记住的",
            }
        elif language in {"ja", "all_ja"}:
            replacements = {
                "はい": "うん、わかったよ",
                "うん": "うんうん、わかったよ",
                "うんうん": "うんうん、わかったよ",
                "そう": "そうだね、わかったよ",
                "そうだね": "そうだね、わかったよ",
                "いい": "いいよ、わかったよ",
                "いいよ": "いいよ、わかったよ",
                "あ": "あ、そうなんだ",
                "わかった": "うん、わかったよ",
                "了解": "うん、わかったよ",
                "大丈夫": "うん、大丈夫だよ",
            }
        else:
            return raw
        choices = "|".join(re.escape(item) for item in sorted(replacements, key=len, reverse=True))
        match = re.fullmatch(rf"({choices})([。.!！…~～]*)", raw)
        if match is None:
            return raw
        return replacements[match.group(1)] + match.group(2)


    def _requested_speech_emotion(self, context: str) -> str | None:
        clean = " ".join(str(context or "").split()).strip()
        latest: tuple[int, str] | None = None
        for emotion, patterns in self._dep_SPEECH_REQUESTED_EMOTION_PATTERNS().items():
            for pattern in patterns:
                for match in re.finditer(pattern, clean):
                    prefix = clean[max(0, match.start() - 4):match.start()]
                    if re.search(r"(?:不要|别|不用|不必)\s*$", prefix):
                        continue
                    candidate = (match.start(), emotion)
                    if latest is None or candidate[0] >= latest[0]:
                        latest = candidate
        return latest[1] if latest else None


    def infer_speech_emotion(self, text: str, context: str = "") -> str:
        clean = " ".join(str(text or "").split()).strip()
        context_clean = " ".join(str(context or "").split()).strip()
        requested = self._requested_speech_emotion(context_clean)
        if requested:
            return requested
        if not clean and not context_clean:
            return "neutral"
        scores = {
            emotion: (
                2 * sum(1 for pattern in patterns if re.search(pattern, clean))
                + sum(1 for pattern in patterns if re.search(pattern, context_clean))
            )
            for emotion, patterns in self._dep_SPEECH_EMOTION_PATTERNS().items()
        }
        if re.search(r"[！!]{1,}", clean):
            scores["cheerful"] += 1
        if re.search(r"[？?]$", clean) and re.search(r"(?:还好|怎么|是不是|要不要|可以吗)", clean):
            scores["concerned"] += 1
        if clean.startswith(("……", "...")):
            scores["shy"] += 1
        if re.search(r"(?:终于|成功|完成|做到了|太好了|开心)", context_clean):
            scores["cheerful"] += 1
        if re.search(r"(?:难受|疼|痛|害怕|焦虑|失眠|生病|受伤|撑不住)", context_clean):
            scores["concerned"] += 1
        highest = max(scores.values(), default=0)
        if highest <= 0:
            return "neutral"
        return next(emotion for emotion in self._dep_SPEECH_EMOTION_PRIORITY() if scores[emotion] == highest)


    def prepare_speech_prosody(self, text: str, emotion: str, language: str) -> str:
        clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if language not in {"zh", "all_zh"} or not clean:
            return clean
        output: list[str] = []
        for line in clean.split("\n"):
            line = re.sub(r"\s*([，。！？；：、…])\s*", r"\1", line.strip())
            if not line:
                continue
            line = re.sub(r"([，。！？；：、…])\1+", r"\1", line)
            # 只强化本身就是语气起句的短语，不把任意第一个逗号改成长停顿。
            if emotion == "cheerful":
                line = re.sub(r"^(太好了|太棒了|好耶|终于)[，,]", r"\1！", line, count=1)
            elif emotion == "serious":
                line = re.sub(r"^(先停一下|等一下|听我说|说真的)[，,]", r"\1。", line, count=1)
            elif emotion == "shy":
                line = re.sub(r"^[.…]+", "…", line)
            if line[-1] not in "。！？!?…":
                line += "！" if emotion == "cheerful" else "。"
            elif emotion == "cheerful" and line.endswith("。"):
                line = line[:-1] + "！"
            elif emotion == "shy" and line.endswith(("。", "！", "!")):
                line = line[:-1] + "……"
            output.append(line)
        return "\n".join(output)


    def speech_emotion_info(self, text: str, context: str = "") -> dict[str, int | str]:
        emotion = self.infer_speech_emotion(text, context)
        return {"id": emotion, "label": self._dep_SPEECH_EMOTION_LABELS()[emotion]}


    def should_use_qq_voice(self, user_message: str, *, explicitly_requested: bool = False) -> bool:
        mode = str(self._dep_load_config().get("qq_voice_mode") or "adaptive")
        if mode == "always":
            return True
        if explicitly_requested:
            return True
        if mode == "explicit":
            return False
        return bool(self._dep_ADAPTIVE_QQ_VOICE_RE().search(str(user_message or "")))
