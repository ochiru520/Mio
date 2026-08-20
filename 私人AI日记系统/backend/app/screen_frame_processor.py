from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO

from PIL import Image, ImageChops, ImageStat


@dataclass(frozen=True)
class ProcessedFrame:
    content: bytes
    original_width: int
    original_height: int
    width: int
    height: int
    mean_brightness: float
    black_ratio: float
    nearly_blank: bool

    def metadata(self) -> dict[str, int | float | bool]:
        data = asdict(self)
        data.pop("content")
        return data


@dataclass(frozen=True)
class FrameChange:
    global_percent: float
    local_percent: float

    @property
    def effective_percent(self) -> float:
        return round(max(self.global_percent, self.local_percent), 2)


def process_frame(
    content: bytes,
    *,
    max_width: int = 960,
    max_height: int = 540,
    jpeg_quality: int = 76,
) -> ProcessedFrame:
    if not content:
        raise ValueError("屏幕帧为空")
    with Image.open(BytesIO(content)) as source:
        image = source.convert("RGB")
    original_width, original_height = image.size
    image.thumbnail((max(320, max_width), max(180, max_height)), Image.Resampling.LANCZOS)

    sample = image.convert("L")
    sample.thumbnail((96, 54), Image.Resampling.BILINEAR)
    pixels = list(sample.getdata())
    black_ratio = sum(1 for value in pixels if value <= 12) / max(1, len(pixels))
    brightness = float(ImageStat.Stat(sample).mean[0])
    deviation = float(ImageStat.Stat(sample).stddev[0])

    output = BytesIO()
    image.save(
        output,
        format="JPEG",
        quality=max(45, min(90, int(jpeg_quality))),
        optimize=True,
    )
    return ProcessedFrame(
        content=output.getvalue(),
        original_width=original_width,
        original_height=original_height,
        width=image.width,
        height=image.height,
        mean_brightness=round(brightness, 2),
        black_ratio=round(black_ratio, 4),
        nearly_blank=bool(black_ratio >= 0.985 or (brightness >= 254.0 and deviation <= 0.8)),
    )


def calculate_change_percent(
    previous_thumbnail: Image.Image | None,
    current_image: Image.Image,
    *,
    max_size: tuple[int, int] = (96, 54),
) -> float:
    """Return the stronger of whole-frame and multi-tile local changes."""
    return calculate_change_metrics(
        previous_thumbnail,
        current_image,
        max_size=max_size,
    ).effective_percent


def calculate_change_metrics(
    previous_thumbnail: Image.Image | None,
    current_image: Image.Image,
    *,
    max_size: tuple[int, int] = (192, 108),
    tile_grid: tuple[int, int] = (12, 6),
) -> FrameChange:
    """Measure global change and sustained local change such as a dialogue box."""
    current = current_image.convert("RGB").copy()
    current.thumbnail(max_size, Image.Resampling.BILINEAR)
    if previous_thumbnail is None:
        return FrameChange(0.0, 0.0)
    previous = previous_thumbnail.convert("RGB").copy()
    previous.thumbnail(max_size, Image.Resampling.BILINEAR)
    if previous.size != current.size:
        return FrameChange(0.0, 0.0)
    difference = ImageChops.difference(previous, current)
    global_percent = sum(ImageStat.Stat(difference).mean) / (3 * 255) * 100

    columns = max(1, min(int(tile_grid[0]), current.width))
    rows = max(1, min(int(tile_grid[1]), current.height))
    tile_scores: list[float] = []
    for row in range(rows):
        top = row * current.height // rows
        bottom = (row + 1) * current.height // rows
        for column in range(columns):
            left = column * current.width // columns
            right = (column + 1) * current.width // columns
            tile = difference.crop((left, top, right, bottom))
            tile_scores.append(sum(ImageStat.Stat(tile).mean) / (3 * 255) * 100)

    # Average the strongest four tiles. A dialogue line affects several
    # neighboring tiles, while a blinking cursor or tiny animation usually
    # affects only one and is therefore diluted.
    strongest = sorted(tile_scores, reverse=True)[: min(4, len(tile_scores))]
    local_percent = sum(strongest) / max(1, len(strongest))
    return FrameChange(round(global_percent, 2), round(local_percent, 2))


__all__ = [
    "FrameChange",
    "ProcessedFrame",
    "calculate_change_metrics",
    "calculate_change_percent",
    "process_frame",
]
