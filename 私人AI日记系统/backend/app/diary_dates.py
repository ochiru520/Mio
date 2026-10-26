"""Canonical logical diary dates, independent of HTTP and storage."""
from datetime import date as date_cls
import re

DIARY_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_diary_date(value: str) -> str:
    if not DIARY_DATE_RE.fullmatch(str(value or "")):
        raise ValueError("日记日期格式应为 YYYY-MM-DD。")
    try:
        return date_cls.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("日记日期无效。") from exc
