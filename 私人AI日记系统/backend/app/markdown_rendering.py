from __future__ import annotations

import html
from html.parser import HTMLParser
from urllib.parse import urlparse

import markdown


_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_VOID_TAGS = {"br", "hr"}
_SAFE_LINK_SCHEMES = {"", "http", "https", "mailto"}


class _SafeMarkdownHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            return
        clean_attrs: list[tuple[str, str]] = []
        if tag == "a":
            for name, value in attrs:
                clean_value = str(value or "")
                if name == "href" and urlparse(clean_value).scheme.lower() in _SAFE_LINK_SCHEMES:
                    clean_attrs.append(("href", clean_value))
                elif name == "title":
                    clean_attrs.append(("title", clean_value))
            clean_attrs.append(("rel", "noreferrer noopener"))
        rendered_attrs = "".join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in clean_attrs
        )
        self.parts.append(f"<{tag}{rendered_attrs}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data, quote=False))


def render_safe_markdown(markdown_content: str) -> str:
    # Escape embedded HTML first, then keep only the small tag set emitted by Markdown.
    source = html.escape(str(markdown_content or ""), quote=False)
    rendered = markdown.markdown(
        source,
        extensions=["extra", "nl2br", "sane_lists"],
        output_format="html5",
    )
    parser = _SafeMarkdownHTMLParser()
    parser.feed(rendered)
    parser.close()
    return "".join(parser.parts)
