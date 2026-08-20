from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path


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
        print(f"patched: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
