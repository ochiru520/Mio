import re
from datetime import date as _date
from urllib.parse import quote

from fastapi.templating import Jinja2Templates

from .config import settings


templates = Jinja2Templates(directory=str(settings.templates_dir))

WEEKDAY_LABELS = "一二三四五六日"


def date_parts(value: str) -> dict[str, str]:
    try:
        parsed = _date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return {"day": str(value), "month": "", "weekday": "", "display": str(value)}
    return {
        "day": f"{parsed.day:02d}",
        "month": f"{parsed.month}月",
        "weekday": f"周{WEEKDAY_LABELS[parsed.weekday()]}",
        "display": f"{parsed.month} 月 {parsed.day} 日",
    }


_EXCERPT_META_RE = re.compile(r"^(状态|原因|生成于)[：:]")


def diary_excerpt(markdown_content: str, max_chars: int = 72) -> str:
    for line in str(markdown_content or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        text = re.sub(r"^[-*]\s*", "", text)
        text = re.sub(r"^\d+[.、]\s*", "", text)
        text = text.replace("**", "").strip()
        if not text or _EXCERPT_META_RE.match(text) or text in {"未确认", "无", "..."}:
            continue
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "…"
        return text
    return ""


def _versioned_url(prefix: str, base_dir, path: str) -> str:
    normalized_path = path.replace("\\", "/").lstrip("/")
    file_path = base_dir / normalized_path
    try:
        version = str(int(file_path.stat().st_mtime))
    except OSError:
        version = "0"
    quoted_path = quote(normalized_path, safe="/")
    return f"{prefix}/{quoted_path}?v={version}"


def asset_url(path: str) -> str:
    return _versioned_url("/static", settings.static_dir, path)


def local_site_url(path: str) -> str:
    return _versioned_url("/local-site", settings.site_custom_dir, path)


def local_asset_url(path: str, fallback_path: str = "") -> str:
    normalized_path = path.replace("\\", "/").lstrip("/")
    if (settings.site_custom_dir / normalized_path).exists():
        return local_site_url(normalized_path)
    return asset_url(fallback_path or normalized_path)


def optional_local_css(path: str = "自定义.css") -> str:
    normalized_path = path.replace("\\", "/").lstrip("/")
    if (settings.site_custom_dir / normalized_path).exists():
        return local_site_url(normalized_path)
    return ""


def status_label(value: str) -> str:
    labels = {
        "done": "完成",
        "partial": "部分完成",
        "missed": "未完成",
        "unknown": "未判定",
        "未判定": "未判定",
        "未生成": "未生成",
    }
    return labels.get(value, value or "未判定")


templates.env.filters["status_label"] = status_label
templates.env.filters["date_parts"] = date_parts
templates.env.filters["diary_excerpt"] = diary_excerpt
templates.env.globals["asset_url"] = asset_url
templates.env.globals["local_asset_url"] = local_asset_url
templates.env.globals["optional_local_css"] = optional_local_css
