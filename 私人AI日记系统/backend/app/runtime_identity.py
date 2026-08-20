from __future__ import annotations

import hashlib
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import BUNDLE_ROOT, SOURCE_PROJECT_ROOT, settings


BUILD_MANIFEST_FILENAME = "构建清单.json"
EMBEDDED_IDENTITY_FILENAME = "build_identity.json"


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:
        return False


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        return None
    return payload


def _manifest_candidates(exe_path: Path) -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv("MIO_BUILD_MANIFEST", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            exe_path.parent / BUILD_MANIFEST_FILENAME,
            BUNDLE_ROOT / EMBEDDED_IDENTITY_FILENAME,
        ]
    )
    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _find_manifest(exe_path: Path) -> tuple[Path | None, dict[str, Any] | None]:
    for candidate in _manifest_candidates(exe_path):
        manifest = _read_manifest(candidate)
        if manifest is not None:
            return candidate, manifest
    return None, None


def _verify_artifacts(manifest_path: Path | None, manifest: dict[str, Any] | None) -> dict[str, Any]:
    artifacts = list((manifest or {}).get("artifacts") or [])
    if manifest_path is None or not artifacts:
        return {"checked": 0, "ok": False, "errors": ["构建清单没有可校验的发布文件。"]}
    errors: list[str] = []
    checked = 0
    for item in artifacts:
        if not isinstance(item, dict):
            errors.append("构建清单包含无效的发布文件记录。")
            continue
        relative = str(item.get("path") or "").strip()
        expected_hash = str(item.get("sha256") or "").strip().upper()
        if not relative or not expected_hash:
            errors.append("构建清单包含缺少路径或哈希的发布文件记录。")
            continue
        target = (manifest_path.parent / Path(relative)).resolve()
        if not _is_within(target, manifest_path.parent):
            errors.append(f"发布文件路径越界：{relative}")
            continue
        if not target.is_file():
            errors.append(f"发布文件缺失：{relative}")
            continue
        checked += 1
        if _sha256(target) != expected_hash:
            errors.append(f"发布文件哈希不匹配：{relative}")
    return {"checked": checked, "ok": bool(artifacts) and not errors, "errors": errors}


def build_runtime_identity(
    *,
    manifest_path: Path | None = None,
    exe_path: Path | None = None,
    runtime_root: Path | None = None,
    state_root: Path | None = None,
    database_path: Path | None = None,
    frozen: bool | None = None,
) -> dict[str, Any]:
    executable = (exe_path or Path(sys.executable)).resolve()
    runtime = (runtime_root or settings.project_root).resolve()
    configured_state = os.getenv("MIO_DESKTOP_STATE_DIR", "").strip()
    state = (
        state_root
        or (Path(configured_state).expanduser() if configured_state else runtime / "桌面状态")
    ).resolve()
    database = (database_path or settings.db_path).resolve()
    is_frozen = bool(getattr(sys, "frozen", False) if frozen is None else frozen)

    selected_path = manifest_path.resolve() if manifest_path else None
    manifest = _read_manifest(selected_path) if selected_path else None
    if selected_path is None:
        selected_path, manifest = _find_manifest(executable)

    artifact_verification = _verify_artifacts(selected_path, manifest)
    warnings: list[str] = []
    if manifest is None:
        warnings.append("正式程序缺少构建清单。" if is_frozen else "开发运行未关联构建清单。")
    elif manifest.get("artifacts") and not artifact_verification["ok"]:
        warnings.extend(str(item) for item in artifact_verification["errors"])

    expected_build_id = os.getenv("MIO_EXPECTED_BUILD_ID", "").strip()
    build_id = str((manifest or {}).get("build_id") or "development-unmanifested")
    if expected_build_id and expected_build_id != build_id:
        warnings.append(f"构建身份不匹配：期望 {expected_build_id}，实际 {build_id}。")
    supported_bundled_data_layout = (
        _same_path(state, executable.parent / "Data")
        and _is_within(runtime, state)
        and _is_within(database, runtime)
    )
    if is_frozen and _is_within(database, executable.parent) and not supported_bundled_data_layout:
        warnings.append("业务数据库位于程序目录内，程序与数据边界发生冲突。")
    if _same_path(state, runtime):
        warnings.append("桌面状态目录与业务运行根相同，状态和业务数据尚未隔离。")
    if not _is_within(database, runtime):
        warnings.append("数据库不在当前业务运行根内。")
    if is_frozen and (
        _same_path(runtime, SOURCE_PROJECT_ROOT)
        or (runtime / ".git").exists()
        or (runtime / "backend" / "app").is_dir()
    ):
        warnings.append("正式程序正在使用源码目录作为业务运行根。")

    sources = dict((manifest or {}).get("sources") or {})
    source_revisions = {
        name: {
            "commit": str((entry or {}).get("commit") or "unknown"),
            "dirty": bool((entry or {}).get("dirty")),
            "dirty_hash": str((entry or {}).get("dirty_hash") or ""),
        }
        for name, entry in sources.items()
        if isinstance(entry, dict)
    }
    return {
        "status": "warning" if warnings else "ok",
        "exe_path": str(executable),
        "program_root": str(executable.parent),
        "build_id": build_id,
        "app_version": str((manifest or {}).get("app_version") or "development"),
        "built_at_utc": str((manifest or {}).get("built_at_utc") or ""),
        "manifest_path": str(selected_path) if selected_path else "",
        "runtime_root": str(runtime),
        "state_root": str(state),
        "database_path": str(database),
        "source_mode": not is_frozen,
        "source_revisions": source_revisions,
        "artifact_verification": artifact_verification,
        "warnings": warnings,
    }


@lru_cache(maxsize=1)
def runtime_identity() -> dict[str, Any]:
    return build_runtime_identity()
