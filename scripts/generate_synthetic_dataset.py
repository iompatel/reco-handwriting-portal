from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


COMMON_WORDS = [
    "college",
    "project",
    "recognition",
    "handwriting",
    "network",
    "python",
    "dataset",
    "training",
    "accuracy",
    "sequence",
    "model",
    "testing",
    "science",
    "engineering",
    "computer",
    "vision",
    "neural",
    "learning",
    "analysis",
    "prediction",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic handwriting-style dataset")
    parser.add_argument("--out-dir", type=str, default="data/synthetic")
    parser.add_argument("--num-train", type=int, default=2500)
    parser.add_argument("--num-val", type=int, default=300)
    parser.add_argument("--num-test", type=int, default=300)
    parser.add_argument("--img-width", type=int, default=256)
    parser.add_argument("--img-height", type=int, default=64)
    parser.add_argument("--min-len", type=int, default=3)
    parser.add_argument("--max-len", type=int, default=28)
    parser.add_argument("--min-words", type=int, default=1)
    parser.add_argument("--max-words", type=int, default=4)
    parser.add_argument("--phrase-prob", type=float, default=0.72)
    parser.add_argument("--notebook-prob", type=float, default=0.6)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--word-list", type=str, default=None, help="Optional path to text file of words")
    parser.add_argument("--alphabet", type=str, default="abcdefghijklmnopqrstuvwxyz0123456789")
    return parser.parse_args()


def discover_fonts() -> list[Path]:
    font_dirs = [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path("/usr/share/fonts"),
    ]
    fonts: list[Path] = []
    for directory in font_dirs:
        if directory.exists():
            fonts.extend([p for p in directory.rglob("*") if p.suffix.lower() in {".ttf", ".otf"}])
    return fonts


def load_words(path: str | None) -> list[str]:
    if not path:
        return COMMON_WORDS
    word_path = Path(path)
    if not word_path.exists():
        raise FileNotFoundError(f"Word list not found: {path}")

    words = []
    with word_path.open("r", encoding="utf-8") as f:
        for line in f:
            word = line.strip().lower()
            if word:
                words.append(word)
    if not words:
        raise ValueError("Word list file is empty")
    return words


def random_token(word_pool: list[str], alphabet: str, min_len: int, max_len: int) -> str:
    size = random.randint(min_len, max_len)
    token = "".join(random.choice(alphabet) for _ in range(size))
    return token.lower()


def random_phrase(
    word_pool: list[str],
    alphabet: str,
    min_len: int,
    max_len: int,
    min_words: int,
    max_words: int,
    phrase_prob: float,
) -> str:
    if random.random() >= phrase_prob:
        return random_token(word_pool, alphabet, min_len, max_len)

    words_count = random.randint(max(1, min_words), max(min_words, max_words))
    chunks: list[str] = []
    for _ in range(words_count):
        if random.random() < 0.82:
            chunks.append(random.choice(word_pool))
        else:
            chunks.append(random_token(word_pool, alphabet, 2, 8))

    text = " ".join(chunks).strip()
    if random.random() < 0.36:
        text = text.upper()
    elif random.random() < 0.5:
        text = text.title()
    return text


def fit_font(text: str, fonts: list[Path], width: int, height: int) -> ImageFont.ImageFont:
    probe = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(probe)

    if fonts:
        for _ in range(20):
            font_path = random.choice(fonts)
            size = random.randint(max(14, int(height * 0.35)), max(22, int(height * 0.85)))
            try:
                font = ImageFont.truetype(str(font_path), size=size)
            except OSError:
                continue
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            if text_w <= width - 4 and text_h <= height - 2:
                return font

    return ImageFont.load_default()


def render_sample(text: str, fonts: list[Path], width: int, height: int, notebook_prob: float) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    if random.random() < notebook_prob:
        line_color = random.randint(185, 220)
        spacing = random.randint(max(10, height // 5), max(14, height // 3))
        offset = random.randint(0, max(1, spacing - 1))
        for y in range(offset, height, spacing):
            draw.line([(0, y), (width, y)], fill=(line_color, line_color, line_color), width=1)

    font = fit_font(text, fonts, width, height)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x_max = max(1, width - text_w - 2)
    y_max = max(1, height - text_h - 2)
    x = random.randint(1, x_max)
    y = random.randint(0, y_max)

    ink_palette = [
        (22, 35, 110),  # blue pen
        (15, 15, 15),  # black pen
        (85, 20, 90),  # violet pen
        (25, 70, 28),  # green pen
    ]
    draw.text((x, y), text, fill=random.choice(ink_palette), font=font)

    for _ in range(random.randint(24, 120)):
        px = random.randint(0, width - 1)
        py = random.randint(0, height - 1)
        noise = random.randint(180, 255)
        image.putpixel((px, py), (noise, noise, noise))

    if random.random() < 0.35:
        image = image.rotate(random.uniform(-5, 5), resample=Image.BILINEAR, fillcolor=(255, 255, 255))
    if random.random() < 0.4:
        image = image.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.1, 0.9)))
    if random.random() < 0.5:
        image = ImageEnhance.Contrast(image).enhance(random.uniform(0.9, 1.35))
    if random.random() < 0.4:
        image = ImageEnhance.Brightness(image).enhance(random.uniform(0.92, 1.08))

    gray = ImageOps.autocontrast(image.convert("L"), cutoff=1)
    return gray


def generate_split(
    split: str,
    count: int,
    out_images: Path,
    word_pool: list[str],
    fonts: list[Path],
    width: int,
    height: int,
    min_len: int,
    max_len: int,
    min_words: int,
    max_words: int,
    phrase_prob: float,
    notebook_prob: float,
    alphabet: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i in range(count):
        text = random_phrase(
            word_pool=word_pool,
            alphabet=alphabet,
            min_len=min_len,
            max_len=max_len,
            min_words=min_words,
            max_words=max_words,
            phrase_prob=phrase_prob,
        )
        image = render_sample(text, fonts, width, height, notebook_prob=notebook_prob)

        name = f"{split}_{i:06d}.png"
        rel_path = Path("images") / name
        image.save(out_images / name)

        rows.append({"image": str(rel_path), "text": text, "split": split})
    return rows


def main() -> None:
    args = parse_args()
    random.seed(args.seed)

    out_dir = Path(args.out_dir)
    out_images = out_dir / "images"
    out_images.mkdir(parents=True, exist_ok=True)

    words = load_words(args.word_list)
    fonts = discover_fonts()

    rows = []
    rows.extend(
        generate_split(
            split="train",
            count=args.num_train,
            out_images=out_images,
            word_pool=words,
            fonts=fonts,
            width=args.img_width,
            height=args.img_height,
            min_len=args.min_len,
            max_len=args.max_len,
            min_words=args.min_words,
            max_words=args.max_words,
            phrase_prob=args.phrase_prob,
            notebook_prob=args.notebook_prob,
            alphabet=args.alphabet,
        )
    )
    rows.extend(
        generate_split(
            split="val",
            count=args.num_val,
            out_images=out_images,
            word_pool=words,
            fonts=fonts,
            width=args.img_width,
            height=args.img_height,
            min_len=args.min_len,
            max_len=args.max_len,
            min_words=args.min_words,
            max_words=args.max_words,
            phrase_prob=args.phrase_prob,
            notebook_prob=args.notebook_prob,
            alphabet=args.alphabet,
        )
    )
    rows.extend(
        generate_split(
            split="test",
            count=args.num_test,
            out_images=out_images,
            word_pool=words,
            fonts=fonts,
            width=args.img_width,
            height=args.img_height,
            min_len=args.min_len,
            max_len=args.max_len,
            min_words=args.min_words,
            max_words=args.max_words,
            phrase_prob=args.phrase_prob,
            notebook_prob=args.notebook_prob,
            alphabet=args.alphabet,
        )
    )

    csv_path = out_dir / "labels.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["image", "text", "split"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Generated dataset at: {out_dir}")
    print(f"Metadata CSV: {csv_path}")
    print(f"Total samples: {len(rows)}")


if __name__ == "__main__":
    main()
