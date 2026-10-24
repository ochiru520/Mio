from __future__ import annotations

import os
from pathlib import Path

from .config import WORKSPACE_ROOT, settings
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
    *,
    optional_paths: set[Path] | None = None,
) -> list[dict[str, object]]:
    manuals: list[dict[str, object]] = []
    limit = settings.manual_max_chars if max_chars is None else max_chars

    for name, path in sources:
        optional = path in (optional_paths or set())
        item: dict[str, object] = {
            "name": name,
            "path": str(path),
            "exists": path.exists(),
            "content": "",
            "total_chars": 0,
            "used_chars": 0,
            "truncated": False,
            "error": "",
            "status": "ready",
            "source_kind": "file",
            "optional": optional,
            "description": "",
        }
        if path == settings.mio_profile_path:
            item["source_kind"] = "profile"
            item["description"] = "实际生效的人设来源为当前角色卡，每轮读取；不是完整人格原文。"
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
                item["status"] = "error"
            manuals.append(item)
            continue

        if not path.exists():
            if optional:
                item["status"] = "unconfigured"
                item["description"] = "未配置此可选文档，未读取该路径；当前角色卡仍用于提供人设。"
            else:
                item["status"] = "missing"
                item["error"] = "文件不存在"
                item["description"] = "配置的文件不存在，请检查路径；当前角色卡仍单独生效。"
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
            item["status"] = "error"
        manuals.append(item)

    return manuals


def _optional_default_paths() -> set[Path]:
    # Missing legacy defaults are optional inputs, never invented original text.
    # Explicitly chosen paths are left untouched and continue to report errors.
    definitions = (
        (settings.runtime_summary_path, "RUNTIME_SUMMARY_PATH", "澪运行时说明书.md"),
        (settings.persona_prompt_path, "PERSONA_PROMPT_PATH", "澪_私人AI人格设定与提示词.md"),
        (settings.personal_manual_path, "PERSONAL_MANUAL_PATH", "个人说明书.txt"),
        (settings.talent_manual_path, "TALENT_MANUAL_PATH", "个人天赋使用说明书.txt"),
    )
    return {
        path for path, env_name, filename in definitions
        if path == WORKSPACE_ROOT / filename and not (os.getenv(env_name) or os.getenv(f"\ufeff{env_name}"))
    }


def load_manuals(max_chars: int | None = None) -> list[dict[str, object]]:
    """Load only the compact files that are sent on every model request."""
    return _load_sources(
        [
            ("运行时说明书", settings.runtime_summary_path),
            ("当前角色卡", settings.mio_profile_path),
        ],
        max_chars,
        optional_paths=_optional_default_paths(),
    )


def load_manual_statuses() -> list[dict[str, object]]:
    """Show runtime input and complete source documents without sending all of them to the model."""
    return _load_sources(
        [
            ("运行时说明书（每轮读取）", settings.runtime_summary_path),
            ("人格设定与提示词（可选完整原文）", settings.persona_prompt_path),
            ("个人说明书（完整原文）", settings.personal_manual_path),
            ("个人天赋使用说明书（完整原文）", settings.talent_manual_path),
            ("当前角色卡（每轮读取）", settings.mio_profile_path),
        ],
        0,
        optional_paths=_optional_default_paths(),
    )
