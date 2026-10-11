"""Keep provider tool syntax out of final text, including split stream tokens."""
import re


PREFIXES = ('<｜｜DSML｜｜', '<｜DSML｜', '<||DSML||', '<|DSML|')
BLOCK = re.compile(
    r'<[｜|]{1,2}DSML[｜|]{1,2}\s*(calls|function_calls)\s*>.*?'
    r'</[｜|]{1,2}DSML[｜|]{1,2}\s*\1\s*>', re.DOTALL)


def has_tool_protocol(text):
    return any(prefix in str(text or '') for prefix in PREFIXES)


def strip_tool_protocol(text):
    text = BLOCK.sub('', text)
    # Incomplete protocol has no trustworthy boundary after its opener.
    starts = [text.find(prefix) for prefix in PREFIXES if prefix in text]
    return text[:min(starts)] if starts else text


class FinalReplyTextFilter:
    def __init__(self, recorder):
        self.recorder = recorder
        self.pending = ''
        self.blocked = False

    async def delta(self, piece, cumulative):
        if self.blocked:
            return
        self.pending += piece
        starts = [self.pending.find(prefix) for prefix in PREFIXES if prefix in self.pending]
        if starts:
            text = self.pending[:min(starts)]
            self.pending = ''
            self.blocked = True
        else:
            # Hold just a possible partial opener, so normal text stays live.
            held = max((length for prefix in PREFIXES for length in range(1, len(prefix))
                        if self.pending.endswith(prefix[:length])), default=0)
            text = self.pending[:-held] if held else self.pending
            self.pending = self.pending[-held:] if held else ''
        if text:
            await self.recorder.delta(text, '')

    async def finish(self):
        if self.pending and not self.blocked:
            await self.recorder.delta(self.pending, '')
        self.pending = ''
