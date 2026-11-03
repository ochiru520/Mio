from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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
    "generated",
}

# This chain is shipped even when the optional GPU runtime is not installed.
# Fixed names make a missing bootstrap/helper fail packaging instead of only
# failing when somebody first tries to install Anif on another computer.
ANIF_REQUIRED_ARTIFACTS = {
    "anif_setup_worker": "aniflive_setup_worker.py",
    "anif_setup_probe": "aniflive_setup_probe.py",
    "anif_v2_converter": "aniflive_v2_convert.py",
    "anif_v2_runtime": "aniflive_v2_runtime.py",
    "anif_v2_server": "aniflive_v2_server.py",
    "anif_shared_server": "aniflive_experimental_server.py",
    "anif_native_v2_helper": "mio_v2_stream_server.py",
    "anif_runtime_installer": "deps/install-aniflive-runtime.ps1",
    "anif_installer_common": "deps/common.ps1",
    "anif_source_lock": "deps/aniflive-sources.json",
    "anif_runtime_requirements": "deps/aniflive-runtime-requirements.txt",
    "anif_jieba_wheel": "deps/wheels/jieba_fast-0.53-cp310-cp310-win_amd64.whl",
    "anif_jieba_provenance": "deps/wheels/jieba-fast-provenance.json",
    "anif_jieba_license": "deps/wheels/jieba-fast-LICENSE.txt",
}


def _packaged_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise RuntimeError("Anif 打包清单必须使用规范的相对文件路径。")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or ".." in parsed.parts or relative != parsed.as_posix():
        raise RuntimeError("Anif 打包清单包含越界或非规范文件路径。")
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Anif 打包文件链接超出程序目录。")
    return target


def _anif_artifacts(release_root: Path) -> list[tuple[str, Path]]:
    root = _packaged_path(release_root, "_internal/agent_scripts")
    artifacts = [(name, _packaged_path(root, relative))
                 for name, relative in ANIF_REQUIRED_ARTIFACTS.items()]
    for _, target in artifacts:
        if not target.is_file():
            raise RuntimeError(f"Anif 安装链文件缺失：{target}")
    try:
        lock = json.loads((root / ANIF_REQUIRED_ARTIFACTS["anif_source_lock"]).read_text("utf-8-sig"))
        provenance = json.loads((root / ANIF_REQUIRED_ARTIFACTS["anif_jieba_provenance"]).read_text("utf-8-sig"))
        records = lock.get("bundled_wheels")
        if lock.get("schema") != 1 or not isinstance(records, list) or len(records) != 1:
            raise ValueError("源锁缺少固定的本地 wheel 身份。")
        record = records[0]
        path = _packaged_path(root / "deps", record["path"])
        expected = root / ANIF_REQUIRED_ARTIFACTS["anif_jieba_wheel"]
        if path != expected:
            raise ValueError("本地 wheel 路径不是本版本要求的文件。")
        artifact = provenance["artifact"]
        actual_sha = _sha256(path).lower()
        if (not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", record["sha256"])
                or actual_sha != record["sha256"].lower()
                or path.stat().st_size != record["bytes"]
                or artifact["path"] != path.name
                or actual_sha != artifact["sha256"].lower()
                or artifact["bytes"] != record["bytes"]
                or provenance.get("component") != "jieba-fast" or provenance.get("version") != "0.53"):
            raise ValueError("本地 wheel 的来源、大小或 SHA256 不一致。")
        notice = provenance["license_notice"]
        license_path = _packaged_path(expected.parent, notice["path"])
        if (license_path != root / ANIF_REQUIRED_ARTIFACTS["anif_jieba_license"]
                or _sha256(license_path).lower() != notice["sha256"].lower()):
            raise ValueError("本地 wheel 的许可文件 SHA256 不一致。")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise RuntimeError("Anif 安装链身份校验失败：" + str(error)) from error
    return artifacts


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
        "local_upgrade_protocol": 1,
        "source_paths": {"backend": str(backend_project_root.resolve()), "desktop": str(desktop_root.resolve())},
    }
    # Conservative compatibility: an intentional schema-core change disables
    # proactive version rollback until compatibility is explicitly designed.
    schema_digest = hashlib.sha256()
    for name in ("db.py", "migration_service.py"):
        source = backend_project_root / "backend" / "app" / name
        if source.is_file():
            schema_digest.update(name.encode("utf-8")); schema_digest.update(source.read_bytes())
    payload["data_schema_compatibility"] = schema_digest.hexdigest()
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload["build_id"] = f"mio-{payload['app_version']}-{stamp}-{hashlib.sha256(canonical).hexdigest()[:10]}"
    _write_json(output, payload)
    return payload


def _artifact_paths(release_root: Path) -> list[tuple[str, Path]]:
    frontend_root = release_root / "_internal" / "agent_frontend"
    artifacts: list[tuple[str, Path]] = [
        ("windows_executable", release_root / "Mio.exe"),
        ("embedded_build_identity", release_root / "_internal" / "build_identity.json"),
        ("live2d_app_asar", release_root / "_internal" / "live2d_desktop" / "resources" / "app.asar"),
        ("frontend_index", frontend_root / "index.html"),
        ("mio_voice_installer", release_root / "_internal" / "agent_scripts" / "deps" / "install-gpt-sovits.ps1"),
        ("mio_voice_package_installer", release_root / "_internal" / "agent_scripts" / "deps" / "install-mio-voice-package.py"),
    ]
    artifacts.extend(_anif_artifacts(release_root))
    updater = release_root / "_internal" / "MioUpdater.exe"
    update_config = release_root / "_internal" / "desktop" / "update_channel.json"
    if updater.is_file() or update_config.is_file():
        artifacts.extend([("standalone_updater", updater), ("update_channel", update_config)])
    default_voice_reference = release_root / "_internal" / "default_voice" / "mio_v2_00.wav"
    if default_voice_reference.is_file():
        artifacts.append(("mio_default_voice_reference", default_voice_reference))
    for target in sorted((frontend_root / "assets").glob("*")):
        if target.suffix.lower() in {".js", ".css"}:
            name = (f"frontend_bundle_{target.suffix.lower()[1:]}" if target.name.startswith("index-")
                    else f"frontend_chunk_{target.name}")
            artifacts.append((name, target))
    browser_node = release_root / "_internal" / "playwright" / "driver" / "node.exe"
    browser_cli = release_root / "_internal" / "playwright" / "driver" / "package" / "cli.js"
    if browser_node.is_file() or browser_cli.is_file():
        artifacts.extend([("browser_driver_node", browser_node), ("browser_driver_cli", browser_cli)])
    backend_root = release_root / "_internal" / "app"
    for target in sorted(backend_root.rglob("*.pyc")):
        artifacts.append((f"backend_module_{target.relative_to(backend_root).as_posix()}", target))
    return artifacts


def finalize_manifest(identity_path: Path, release_root: Path) -> dict[str, Any]:
    payload = json.loads(identity_path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != SCHEMA_VERSION or not payload.get("build_id"):
        raise RuntimeError("构建身份文件无效。")
    embedded_path = release_root / "_internal" / "build_identity.json"
    if not embedded_path.is_file():
        raise RuntimeError("发布程序缺少内嵌构建身份；必须重新打包，不能只生成顶层清单。")
    embedded = json.loads(embedded_path.read_text(encoding="utf-8"))
    if embedded != payload:
        raise RuntimeError("请求的构建身份与程序内嵌身份不一致；请部署同一次完整构建。")
    frontend_assets = release_root / "_internal" / "agent_frontend" / "assets"
    if not any(frontend_assets.glob("index-*.js")) or not any(frontend_assets.glob("index-*.css")):
        raise RuntimeError("发布文件不完整，缺少前端入口 JS 或 CSS bundle。")
    uses_capabilities = any("/api/capabilities" in target.read_text(encoding="utf-8")
                            for target in frontend_assets.glob("*.js"))
    if uses_capabilities:
        expected = ("main.pyc", "capability_checks.pyc", "capability_probe_worker.pyc", "routes/capabilities.pyc")
        missing = [name for name in expected if not (release_root / "_internal" / "app" / name).is_file()]
        if missing:
            raise RuntimeError("前端已启用能力实测，但后端未完整打包：" + ", ".join(missing))
    uses_browser = any("/browser/login" in target.read_text(encoding="utf-8")
                       for target in frontend_assets.glob("*.js"))
    if uses_browser and not all(path.is_file() for path in (
            release_root / "_internal" / "playwright" / "driver" / "node.exe",
            release_root / "_internal" / "playwright" / "driver" / "package" / "cli.js")):
        raise RuntimeError("前端已启用项目浏览器，但浏览器驱动未完整打包。")
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
