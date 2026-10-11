from __future__ import annotations

import argparse
import base64
import csv
import hashlib
from importlib import metadata
from pathlib import Path
import re


REPLACEMENTS = {
    Path("G2P/Chinese/ToneSandhi.py"): {
        "import jieba_fast as jieba": "import jieba",
        '                    and sub_finals_list[i - 1][-1][-1] == "3"\n'
        '                    and sub_finals_list[i][0][-1] == "3"': (
            '                    and sub_finals_list[i - 1]\n'
            '                    and sub_finals_list[i]\n'
            '                    and sub_finals_list[i - 1][-1]\n'
            '                    and sub_finals_list[i][0]\n'
            '                    and sub_finals_list[i - 1][-1][-1] == "3"\n'
            '                    and sub_finals_list[i][0][-1] == "3"'
        ),
    },
    Path("G2P/Chinese/ChineseG2P.py"): {
        "import jieba_fast\n": "import jieba\n",
        "import jieba_fast.posseg as psg": "import jieba.posseg as psg",
        "jieba_fast.setLogLevel(logging.ERROR)": "jieba.setLogLevel(logging.ERROR)",
    },
}


def patch_distribution_metadata(distribution, site_packages: Path, changed_files: list[Path]) -> None:
    """Keep the installed dependency declaration and RECORD honest after patching."""
    files = list(distribution.files or ())
    entry = next((item for item in files if item.name == "METADATA" and ".dist-info" in str(item)), None)
    if entry is None:
        raise FileNotFoundError("Genie 安装缺少 METADATA，请重新安装 genie-tts 2.0.2。")
    metadata_path = Path(distribution.locate_file(entry)).resolve()
    site_packages = site_packages.resolve()
    if not metadata_path.is_relative_to(site_packages):
        raise ValueError("Genie 元数据不在指定 Python 环境内，拒绝修改。")
    original = metadata_path.read_text(encoding="utf-8")
    updated = re.sub(r"(?m)^Requires-Dist:\s*jieba[-_]fast\b[^\r\n]*", "Requires-Dist: jieba>=0.42.1", original)
    if updated != original:
        metadata_path.write_text(updated, encoding="utf-8")
        changed_files.append(metadata_path)
    record_path = metadata_path.with_name("RECORD")
    if not record_path.is_file():
        raise FileNotFoundError("Genie 安装缺少 RECORD，请重新安装 genie-tts 2.0.2。")
    with record_path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.reader(source))
    # Include the already-patched G2P files, so reruns can repair earlier RECORDs.
    targets = {file.resolve().relative_to(site_packages).as_posix(): file for file in changed_files}
    present = {row[0] for row in rows if row}
    if not targets.keys() <= present:
        raise ValueError("Genie RECORD 缺少被修补文件的记录，拒绝生成不完整清单。")
    for row in rows:
        if not row or row[0] not in targets:
            continue
        payload = targets[row[0]].read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode("ascii").rstrip("=")
        row[1:] = ["sha256=" + digest, str(len(payload))]
    with record_path.open("w", encoding="utf-8", newline="") as destination:
        csv.writer(destination).writerows(rows)
    print(f"patched dependency metadata and RECORD: {metadata_path.parent}")


def main() -> int:
    parser = argparse.ArgumentParser(description="让 Genie 2.0.2 使用纯 Python jieba。")
    parser.add_argument("--site-packages", required=True)
    args = parser.parse_args()

    version = metadata.version("genie-tts")
    if version != "2.0.2":
        raise RuntimeError(f"只支持修补 Genie 2.0.2，当前版本为 {version}")

    distribution = metadata.distribution("genie-tts")
    top_level_text = distribution.read_text("top_level.txt") or "genie_tts"
    candidates = [name.strip() for name in top_level_text.splitlines() if name.strip()]
    roots = [Path(args.site_packages).resolve() / name for name in candidates]
    roots.append(Path(args.site_packages).resolve() / "genie_tts")
    root = next((candidate for candidate in roots if candidate.is_dir()), None)
    if root is None:
        expected = ", ".join(str(candidate) for candidate in roots)
        raise FileNotFoundError(f"没有找到 Genie 包目录（已检查：{expected}）。请重新安装 genie-tts 2.0.2。")

    patched_files = []
    for relative, replacements in REPLACEMENTS.items():
        target = root / relative
        original = target.read_text(encoding="utf-8")
        updated = original
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if "jieba_fast" in updated:
            raise RuntimeError(f"{target.name} 仍包含 jieba_fast 导入")
        if updated != original:
            target.write_text(updated, encoding="utf-8")
        patched_files.append(target)
        print(f"patched: {target}")
    patch_distribution_metadata(distribution, Path(args.site_packages), patched_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
