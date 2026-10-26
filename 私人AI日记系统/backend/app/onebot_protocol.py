"""Pure OneBot/CQ message parsing used by chat and file intake."""
from html import unescape as html_unescape
from urllib.parse import unquote


def parse_cq_params(segment_text: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in segment_text.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        decoded = html_unescape(value.strip())
        params[key.strip()] = unquote(decoded)
    return params
