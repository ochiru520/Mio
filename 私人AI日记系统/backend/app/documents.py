from __future__ import annotations

from pathlib import Path

from .config import settings
from .mio_profile import render_mio_profile_for_prompt


def read_text_with_fallback(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _load_sources(
    sources: list[tuple[str, Path]],
    max_chars: int | None,
) -> list[dict[str, object]]:
    manuals: list[dict[str, object]] = []
    limit = settings.manual_max_chars if max_chars is None else max_chars

    for name, path in sources:
        item: dict[str, object] = {
            "name": name,
            "path": str(path),
            "exists": path.exists(),
            "content": "",
            "total_chars": 0,
            "used_chars": 0,
            "truncated": False,
            "error": "",
        }
        if path == settings.mio_profile_path:
            try:
                content = render_mio_profile_for_prompt()
                item["exists"] = True
                item["total_chars"] = len(content)
                if limit and limit > 0 and len(content) > limit:
                    item["content"] = content[:limit]
                    item["used_chars"] = limit
                    item["truncated"] = True
                else:
                    item["content"] = content
                    item["used_chars"] = len(content)
            except Exception as exc:
                item["error"] = str(exc)
            manuals.append(item)
            continue

        if not path.exists():
            item["error"] = "文件不存在"
            manuals.append(item)
            continue

        try:
            content = read_text_with_fallback(path)
            item["total_chars"] = len(content)
            if limit and limit > 0 and len(content) > limit:
                item["content"] = content[:limit]
                item["used_chars"] = limit
                item["truncated"] = True
            else:
                item["content"] = content
                item["used_chars"] = len(content)
        except OSError as exc:
            item["error"] = str(exc)
        manuals.append(item)

    return manuals


def load_manuals(max_chars: int | None = None) -> list[dict[str, object]]:
    """Load only the compact files that are sent on every model request."""
    return _load_sources(
        [
            ("Mio 运行时说明书", settings.runtime_summary_path),
            ("Mio 当前属性", settings.mio_profile_path),
        ],
        max_chars,
    )


def load_manual_statuses() -> list[dict[str, object]]:
    """Show runtime input and complete source documents without sending all of them to the model."""
    return _load_sources(
        [
            ("Mio 运行时说明书（每轮读取）", settings.runtime_summary_path),
            ("Mio 人格设定与提示词（完整原文）", settings.persona_prompt_path),
            ("个人说明书（完整原文）", settings.personal_manual_path),
            ("个人天赋使用说明书（完整原文）", settings.talent_manual_path),
            ("Mio 当前属性（每轮读取）", settings.mio_profile_path),
        ],
        0,
    )
