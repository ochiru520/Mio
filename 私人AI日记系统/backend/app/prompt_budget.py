"""Local prompt estimates and whole-entry selection; never a billing tokenizer."""
from __future__ import annotations

import re
from math import ceil


def estimate_tokens(text: object) -> int:
    """Estimate mixed Chinese/English text without a tokenizer dependency."""
    value = str(text or "")
    if not value:
        return 0
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", value))
    latin_runs = re.findall(r"[A-Za-z0-9_]+", value)
    latin_tokens = sum(max(1, ceil(len(run) / 4)) for run in latin_runs)
    punctuation = len(re.findall(r"[^\sA-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", value))
    whitespace = len(re.findall(r"\s+", value))
    return max(1, int(ceil(cjk * 1.1 + latin_tokens + punctuation * 0.35 + whitespace * 0.1)))


def fact_key(value: object) -> str:
    # Punctuation and word boundaries can change a fact (1-2 vs 12, etc.).
    return ' '.join(str(value or '').split()).casefold()


def query_terms(query: str) -> set[str]:
    # Retrieval features, not an intent gate: the model still chooses tools.
    noise = {'什么', '怎么', '怎么样', '今天', '昨天', '前天', '一下', '之前', '现在',
             '可以', '这个', '那个', '我们', '一个', '还有', '事情', '帮我', '记得'}
    terms = set(re.findall(r"[a-z0-9][a-z0-9_.+-]+", query.casefold()))
    for chunk in re.findall(r"[\u3400-\u9fff]+", query):
        terms.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return terms - noise


def relevance(text: object, terms: set[str]) -> int:
    value = str(text or '').casefold()
    return sum(len(term) for term in terms if term in value)


def complete_text(text: object, max_tokens: int) -> str:
    """Admit a complete record, or require readback of the original.

    Even complete sentences can lose a preceding denial or a following
    qualification. A local selector cannot safely summarize that relationship.
    """
    clean = str(text or '').strip()
    if estimate_tokens(clean) <= max_tokens:
        return clean
    return ''


def select_sections(sections: list[tuple[str, list[str]]], *, max_tokens: int,
                    max_chars: int | None = None, prefix: str = '', notice: str = '') -> str:
    """Pack priority-ordered entries without slicing a fact or its provenance."""
    chosen: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    omitted = False

    def render(items):
        return '\n\n'.join([prefix] * bool(prefix) + [title + '\n' + '\n'.join(lines)
                                                    for title, lines in items]).strip()

    for title, lines in sections:
        selected = []
        for line in lines:
            key = fact_key(line)
            if not key or key in seen:
                continue
            candidate = render([*chosen, (title, [*selected, line])])
            reserved = candidate + ('\n\n' + notice if notice else '')
            if estimate_tokens(reserved) > max_tokens or (max_chars is not None and len(reserved) > max_chars):
                omitted = True
                continue
            selected.append(line)
            seen.add(key)
        if selected:
            chosen.append((title, selected))
    result = render(chosen) if chosen else ''
    if omitted and notice:
        candidate = result + ('\n\n' if result else '') + notice
        if estimate_tokens(candidate) <= max_tokens and (max_chars is None or len(candidate) <= max_chars):
            result = candidate
    return result
