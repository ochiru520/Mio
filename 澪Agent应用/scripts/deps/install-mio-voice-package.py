"""Validate and atomically install the optional Mio native voice data pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


FORMATS = {"mio-native-voice-package", "mio-genie-runtime-package"}
MAX_ENTRIES = 256
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest().lower()


def safe_relative(name: str) -> Path:
    value = PurePosixPath(name)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        raise ValueError(f"模型包包含不安全路径：{name}")
    if value.parts[0] != "payload":
        raise ValueError(f"模型包文件不在 payload 下：{name}")
    return Path(*value.parts[1:])


def load_manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    try:
        raw = archive.read("manifest.json")
    except KeyError as exc:
        raise ValueError("模型包缺少 manifest.json。") from exc
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("模型包清单异常过大。")
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("format") not in FORMATS:
        raise ValueError("这不是 Mio 支持的本地语音模型包。")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("模型包清单没有文件。")
    return payload


def install(package: Path, target: Path) -> dict[str, object]:
    if not package.is_file():
        raise FileNotFoundError(f"找不到模型包：{package}")
    target.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".mio-voice-install-", dir=target.parent))
    installed = 0
    try:
        with zipfile.ZipFile(package) as archive:
            manifest = load_manifest(archive)
            entries = manifest["files"]
            if len(entries) > MAX_ENTRIES:
                raise ValueError("模型包文件数量异常。")
            total = 0
            expected_names: set[str] = set()
            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError("模型包文件清单格式不正确。")
                name = str(item.get("path") or "")
                relative = safe_relative(name)
                size = int(item.get("size") or 0)
                sha256 = str(item.get("sha256") or "").strip().lower()
                if size <= 0 or len(sha256) != 64:
                    raise ValueError(f"模型包文件校验信息不完整：{name}")
                total += size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("模型包解压后体积异常。")
                if name in expected_names:
                    raise ValueError(f"模型包包含重复文件：{name}")
                expected_names.add(name)
                try:
                    info = archive.getinfo(name)
                except KeyError as exc:
                    raise ValueError(f"模型包缺少文件：{name}") from exc
                if info.is_dir() or info.file_size != size:
                    raise ValueError(f"模型包文件大小不一致：{name}")
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                if digest(destination) != sha256:
                    raise ValueError(f"模型包文件 SHA-256 校验失败：{name}")

            for item in entries:
                relative = safe_relative(str(item["path"]))
                source = staging / relative
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".mio-installing")
                shutil.copy2(source, temporary)
                os.replace(temporary, destination)
                installed += 1

        package_format = str(manifest.get("format") or "")
        marker_name = ".mio-genie-runtime-package.json" if package_format == "mio-genie-runtime-package" else ".mio-native-voice-package.json"
        marker = target / marker_name
        marker_payload = {
            "format": package_format,
            "version": manifest.get("version"),
            "package_sha256": digest(package),
            "installed_files": installed,
        }
        marker.write_text(json.dumps(marker_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return marker_payload
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    result = install(args.package.resolve(), args.target.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
