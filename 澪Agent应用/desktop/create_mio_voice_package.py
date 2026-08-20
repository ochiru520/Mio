"""Create the optional, independently distributable Mio native voice data pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


COMPONENTS = {
    "genie": ("GenieData",),
    "voice": (
        "models/genie/mio-v1",
        "materials/prepared/wav32k_v2",
        "materials/emotion-references-zh",
        "emotion-references.json",
        "emotion-references-zh.json",
    ),
}
# Backward-compatible alias for the existing voice-package tests and callers.
INCLUDE = COMPONENTS["voice"]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest().lower()


def source_files(root: Path, component: str) -> list[tuple[str, Path]]:
    include = COMPONENTS[component]
    result: list[tuple[str, Path]] = []
    for item in include:
        source = root / Path(item)
        if source.is_file():
            result.append((f"payload/{Path(item).as_posix()}", source))
        elif source.is_dir():
            for file in sorted(source.rglob("*")):
                if file.is_file():
                    result.append((f"payload/{file.relative_to(root).as_posix()}", file))
        else:
            raise FileNotFoundError(f"Mio 音色包缺少源文件：{source}")
    return result


def create(root: Path, output: Path, component: str = "voice") -> dict[str, object]:
    files = source_files(root, component)
    manifest = {
        "format": "mio-genie-runtime-package" if component == "genie" else "mio-native-voice-package",
        "component": component,
        "version": "genie-2.0.2" if component == "genie" else "mio-v1-genie-2.0.2",
        "files": [
            {"path": name, "size": path.stat().st_size, "sha256": digest(path)}
            for name, path in files
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for name, path in files:
            archive.write(path, name)
    temporary.replace(output)
    return {
        "file_name": output.name,
        "size_bytes": output.stat().st_size,
        "sha256": digest(output),
        "version": manifest["version"],
        "file_count": len(files),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--component", choices=tuple(COMPONENTS), default="voice")
    args = parser.parse_args()
    result = create(args.voice_root.resolve(), args.output.resolve(), args.component)
    if args.source_manifest:
        payload = {
            "format": "mio-genie-runtime-source" if args.component == "genie" else "mio-native-voice-source",
            "component": args.component,
            **result,
            "urls": [],
        }
        args.source_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.source_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
