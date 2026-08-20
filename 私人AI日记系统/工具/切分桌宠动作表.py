from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


动作名称 = ("待机", "眨眼", "说话", "开心", "担心", "害羞")


def 切分动作表(图片路径: Path, 输出目录: Path) -> list[Path]:
    with Image.open(图片路径) as source:
        image = source.convert("RGBA")
    columns, rows = (3, 2) if image.width >= image.height else (2, 3)
    cell_width = image.width // columns
    cell_height = image.height // rows
    if cell_width < 32 or cell_height < 32:
        raise ValueError("动作表尺寸太小，无法按 3x2 或 2x3 网格切分。")

    输出目录.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for index, name in enumerate(动作名称):
        column = index % columns
        row = index // columns
        left = column * cell_width
        top = row * cell_height
        right = image.width if column == columns - 1 else (column + 1) * cell_width
        bottom = image.height if row == rows - 1 else (row + 1) * cell_height
        output = 输出目录 / f"{name}.png"
        image.crop((left, top, right, bottom)).save(output, format="PNG", optimize=True)
        outputs.append(output)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="把澪的 3x2 或 2x3 桌宠动作表切成六张透明 PNG。")
    parser.add_argument("图片", type=Path)
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "数据" / "桌宠" / "动作",
    )
    args = parser.parse_args()
    outputs = 切分动作表(args.图片, args.输出目录)
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
