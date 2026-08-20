from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MANIFEST_FILENAME = "构建清单.json"
SOURCE_SNAPSHOT_EXCLUDES = {
    ".desktop-cache",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "release",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _git_bytes(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
    ).stdout


def _source_snapshot_state(repository: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    for current_root, directories, filenames in os.walk(repository, topdown=True, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in SOURCE_SNAPSHOT_EXCLUDES)
        current = Path(current_root)
        for filename in sorted(filenames):
            target = current / filename
            relative = target.relative_to(repository).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(_sha256(target)))
    snapshot_hash = digest.hexdigest().upper()
    return {
        "commit": f"source-{snapshot_hash[:12].lower()}",
        "dirty": False,
        "dirty_hash": snapshot_hash,
    }


def repository_state(repository: Path) -> dict[str, object]:
    if not (repository / ".git").exists():
        return _source_snapshot_state(repository)
    commit = _git_bytes(repository, "rev-parse", "HEAD").decode("ascii").strip()
    status = _git_bytes(repository, "status", "--porcelain=v1", "-z")
    dirty = bool(status)
    digest = hashlib.sha256()
    digest.update(status)
    digest.update(_git_bytes(repository, "diff", "--binary", "HEAD", "--"))
    untracked = _git_bytes(repository, "ls-files", "--others", "--exclude-standard", "-z")
    for raw_path in (item for item in untracked.split(b"\0") if item):
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        target = repository / relative
        digest.update(raw_path)
        if target.is_file():
            digest.update(bytes.fromhex(_sha256(target)))
    return {
        "commit": commit,
        "dirty": dirty,
        "dirty_hash": digest.hexdigest().upper() if dirty else "",
    }


def _app_version(desktop_root: Path) -> str:
    version_file = desktop_root / "desktop" / "version_info.txt"
    match = re.search(r"FileVersion',\s*u'([^']+)'", version_file.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError("无法从 desktop/version_info.txt 读取应用版本。")
    return match.group(1)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare_manifest(desktop_root: Path, backend_project_root: Path, output: Path) -> dict[str, Any]:
    built_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "app_version": _app_version(desktop_root),
        "built_at_utc": built_at,
        "sources": {
            "backend": repository_state(backend_project_root),
            "desktop": repository_state(desktop_root),
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload["build_id"] = f"mio-{payload['app_version']}-{stamp}-{hashlib.sha256(canonical).hexdigest()[:10]}"
    _write_json(output, payload)
    return payload


def _artifact_paths(release_root: Path) -> list[tuple[str, Path]]:
    frontend_root = release_root / "_internal" / "agent_frontend"
    artifacts: list[tuple[str, Path]] = [
        ("windows_executable", release_root / "Mio.exe"),
        ("live2d_app_asar", release_root / "_internal" / "live2d_desktop" / "resources" / "app.asar"),
        ("frontend_index", frontend_root / "index.html"),
        ("mio_voice_installer", release_root / "_internal" / "agent_scripts" / "deps" / "install-gpt-sovits.ps1"),
        ("mio_voice_package_installer", release_root / "_internal" / "agent_scripts" / "deps" / "install-mio-voice-package.py"),
    ]
    default_voice_reference = release_root / "_internal" / "default_voice" / "mio_v2_00.wav"
    if default_voice_reference.is_file():
        artifacts.append(("mio_default_voice_reference", default_voice_reference))
    for target in sorted((frontend_root / "assets").glob("index-*.*")):
        if target.suffix.lower() in {".js", ".css"}:
            artifacts.append((f"frontend_bundle_{target.suffix.lower()[1:]}", target))
    return artifacts


def finalize_manifest(identity_path: Path, release_root: Path) -> dict[str, Any]:
    payload = json.loads(identity_path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != SCHEMA_VERSION or not payload.get("build_id"):
        raise RuntimeError("构建身份文件无效。")
    artifacts: list[dict[str, object]] = []
    required = _artifact_paths(release_root)
    if len(required) < 7:
        raise RuntimeError("发布文件不完整，缺少前端 bundle 或 Mio 音色安装链。")
    for name, target in required:
        if not target.is_file():
            raise RuntimeError(f"发布文件缺失：{target}")
        artifacts.append(
            {
                "name": name,
                "path": target.relative_to(release_root).as_posix(),
                "size": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )
    payload["artifacts"] = artifacts
    manifest_path = release_root / MANIFEST_FILENAME
    _write_json(manifest_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Mio 可追溯构建身份和发布清单。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--desktop-root", type=Path, required=True)
    prepare.add_argument("--backend-project-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--identity", type=Path, required=True)
    finalize.add_argument("--release-root", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "prepare":
        payload = prepare_manifest(args.desktop_root.resolve(), args.backend_project_root.resolve(), args.output.resolve())
    else:
        payload = finalize_manifest(args.identity.resolve(), args.release_root.resolve())
    print(json.dumps({"build_id": payload["build_id"], "artifacts": len(payload.get("artifacts") or [])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
