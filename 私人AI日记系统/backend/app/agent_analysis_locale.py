"""Language gate for provider analysis; never translates or invents reasoning."""
import re

from .agent_progress_service import FinalTextRecorder


ANALYSIS_LANGUAGE_NOTICE = '模型返回的分析不是中文，以下显示中文执行进度。'
_HAN = re.compile(r'[\u3400-\u9fff]')
_FENCE_START = re.compile(r'(?m)^[ \t]{0,3}(`{3,}|~{3,})')
_FENCED = re.compile(r'(?ms)^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n.*?^[ \t]{0,3}\1[ \t]*\n')
_TECHNICAL = re.compile(
    r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`]*`|https?://\S+|[A-Za-z]:[\\/][^\s，。；！？]*|'
    r'\b[A-Za-z][\w-]*[._/][\w./-]+\b|\b[A-Z][A-Z0-9_-]+\b|'
    r'(?<![A-Za-z0-9_])(?:Python|JavaScript|TypeScript|Node\.js|Vue|React|'
    r'pytest|npm|pnpm|node|git|pip|ruff|mypy|vitest|eslint|Docker|PowerShell|bash)'
    r'(?![A-Za-z0-9_])'
)
_BOUNDARY = re.compile(r'[。！？\n]+')


def is_chinese_analysis(text: str) -> bool:
    """Accept Chinese narrative with technical identifiers, not English prose.

    This is deliberately a display check, not a claim about a model's internal
    reasoning language. English paragraphs containing a Chinese quote or place
    name still fail. Source text, paths and acronyms embedded in Chinese remain.
    """
    text = str(text or '')
    narrative = _TECHNICAL.sub('', text)
    chinese = len(_HAN.findall(narrative))
    latin = len(re.findall(r'[A-Za-z]', narrative))
    return (chinese > 0 and chinese >= latin * .6) or (
        chinese == latin == 0 and _TECHNICAL.search(text) is not None
    )


class ChineseAnalysisRecorder(FinalTextRecorder):
    """Buffer complete spans so partial English tokens never reach the UI."""
    def __init__(self, run_id, *, round_index=None):
        self.language_pending = ''
        self.suppressed_chars = 0
        self.notice_sent = False
        super().__init__(run_id, stream_kind='analysis', round_index=round_index)

    def _accept(self, text, *, code=False):
        if not text.strip():
            return
        if code or is_chinese_analysis(text):
            self.pending += text
            self.total += len(text)
            self.flush()
        else:
            self.suppressed_chars += len(text)
            if not self.notice_sent:
                self.event('analysis_language_notice', {
                    'reason': 'non_chinese_analysis',
                    'text': ANALYSIS_LANGUAGE_NOTICE,
                    'suppressed_chars': self.suppressed_chars,
                })
                self.notice_sent = True

    async def delta(self, piece, cumulative):
        self.language_pending += piece
        while self.language_pending:
            fence = _FENCE_START.search(self.language_pending)
            boundary = _BOUNDARY.search(self.language_pending)
            if fence and (not boundary or fence.start() < boundary.start()):
                if fence.start():
                    self._accept(self.language_pending[:fence.start()])
                    self.language_pending = self.language_pending[fence.start():]
                complete = _FENCED.match(self.language_pending)
                if not complete:
                    # A fence arriving across SSE chunks remains one code block;
                    # its line breaks must not be treated as narrative boundaries.
                    return
                self._accept(complete.group(), code=True)
                self.language_pending = self.language_pending[complete.end():]
            elif boundary:
                self._accept(self.language_pending[:boundary.end()])
                self.language_pending = self.language_pending[boundary.end():]
            else:
                break
        # Bound memory even when a supplier emits a very long unbroken span.
        if len(self.language_pending) >= 4096:
            self._accept(self.language_pending)
            self.language_pending = ''

    def finish(self, status):
        # CommonMark permits an unclosed fence at EOF. Preserve interrupted
        # code verbatim, including its opening fence; do not invent missing code.
        self._accept(self.language_pending, code=bool(_FENCE_START.match(self.language_pending)))
        self.language_pending = ''
        self.flush()
        self.event('analysis_stream_finished', {
            'status': status, 'total_chars': self.total, 'draft': True,
            'suppressed_chars': self.suppressed_chars, 'language': 'zh-CN',
        })
