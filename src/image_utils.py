from __future__ import annotations

from pathlib import Path

from PIL import Image


def resize_with_padding(
    image: Image.Image,
    target_width: int,
    target_height: int,
    fill_value: int = 255,
) -> Image.Image:
    if image.mode != "L":
        image = image.convert("L")

    orig_width, orig_height = image.size
    if orig_width <= 0 or orig_height <= 0:
        raise ValueError("Invalid source image size")

    scale = min(target_width / orig_width, target_height / orig_height)
    new_width = max(1, int(orig_width * scale))
    new_height = max(1, int(orig_height * scale))

    resized = image.resize((new_width, new_height), Image.BILINEAR)
    canvas = Image.new("L", (target_width, target_height), color=fill_value)

    x_offset = 0
    y_offset = (target_height - new_height) // 2
    canvas.paste(resized, (x_offset, y_offset))
    return canvas


def open_grayscale(path: str | Path) -> Image.Image:
    return Image.open(path).convert("L")
