"""Expose complete public speech units without leaking split control tokens."""
from __future__ import annotations

import inspect
import re
from typing import Awaitable, Callable

ReplyDeltaCallback = Callable[[str, str], Awaitable[None] | None]

_HIDDEN_BLOCK = re.compile(r'<(think|analysis)\b[^>]*>.*?</\1\s*>', re.I | re.S)
_OPEN_BLOCK = re.compile(r'<(?:think|analysis)\b[^>]*>', re.I)
_CONTROL_PREFIXES = ('<think', '</think', '<analysis', '</analysis', '[[voice_emotion:',
                     '<｜｜DSML｜｜', '<｜DSML｜', '<||DSML||', '<|DSML|')
_TOOL_PREFIXES = _CONTROL_PREFIXES[-4:]
_FINAL_MARKER = re.compile(r'(?im)^\s*(?:natural(?:\s+(?:answer|response))?|final(?:\s+answer)?|answer|assistant\s+response|response)\s*[:：]\s*')
_DRAFT_LINE = re.compile(r'(?im)^\s*(?:we\s+need\b|need\s+(?:to\s+)?(?:answer|ask|respond|reply|say)\b|respond\s+(?:actual|in|with|naturally)\b|should\s+(?:answer|ask|respond|reply)\b|(?:the\s+)?user\s+(?:said|says|asked|asks|wants?|means?)\b|they\s+(?:already|said|want|wanted|mean)\b|perhaps\b|maybe\b|context\b)')


class PublicReplyStream:
    """Callback receives additive complete sentences/lines, plus public cumulative text.

    The final unpunctuated tail is held until finish. Ambiguous model-control
    prefixes and unclosed reasoning blocks are held, including on finish.
    """
    def __init__(self, callback: ReplyDeltaCallback, *, clean: Callable[[str], str],
                 is_placeholder: Callable[[str], bool]):
        self.callback = callback
        self.clean = clean
        self.is_placeholder = is_placeholder
        self.raw = ''
        self.emitted = ''
        self.delivered = False

    def _public(self) -> str:
        raw = _HIDDEN_BLOCK.sub('', self.raw)
        opening = _OPEN_BLOCK.search(raw)
        if opening:
            raw = raw[:opening.start()]
        control = re.search(r'\[\[voice_emotion:', raw, re.I)
        while control:
            closing = raw.find(']]', control.end())
            if closing < 0:
                raw = raw[:control.start()]
                break
            raw = raw[:control.start()] + raw[closing + 2:]
            control = re.search(r'\[\[voice_emotion:', raw, re.I)
        tool_starts = [raw.find(prefix) for prefix in _TOOL_PREFIXES if prefix in raw]
        if tool_starts:
            raw = raw[:min(tool_starts)]
        # A partial opener must never be spoken before the next token resolves it.
        held = max((n for prefix in _CONTROL_PREFIXES for n in range(1, len(prefix))
                    if raw.lower().endswith(prefix[:n].lower())), default=0)
        if held:
            raw = raw[:-held]
        if _DRAFT_LINE.search(raw):
            markers = list(_FINAL_MARKER.finditer(raw))
            raw = raw[markers[-1].end():] if markers else ''
        return self.clean(raw)

    async def _flush(self, *, final: bool = False):
        public = self._public()
        if not public.startswith(self.emitted):
            # A provider rewriting already published text cannot be replayed safely.
            raise ValueError('模型重写了已经播报的正文，已停止本轮语音。')
        end = len(public) if final else max(
            (match.end() for match in re.finditer(r'[。！？!?；;\n]', public)), default=0)
        candidate = public[:end]
        if end <= len(self.emitted) or self.is_placeholder(candidate):
            return
        piece = candidate[len(self.emitted):]
        self.emitted = candidate
        self.delivered = True
        result = self.callback(piece, candidate)
        if inspect.isawaitable(result):
            await result

    async def delta(self, piece: str, cumulative: str):
        self.raw += str(piece or '')
        await self._flush()

    async def finish(self):
        await self._flush(final=True)
