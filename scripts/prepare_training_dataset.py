from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

TEXT_KEY_CANDIDATES = (
    "text",
    "transcription",
    "label",
    "sentence",
    "gt",
    "ground_truth",
    "target",
)
IMAGE_KEY_CANDIDATES = (
    "image",
    "img",
    "pixel_values",
    "scan",
)


@dataclass
class HFSource:
    dataset: str
    config: str | None
    split: str | None
    text_key: str | None
    image_key: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build merged handwriting dataset from public + local sources"
    )
    parser.add_argument("--out-dir", type=str, default="data/training_mix")
    parser.add_argument(
        "--hf-source",
        action="append",
        default=[],
        help=(
            "Public source spec: dataset|config|split|text_key|image_key. "
            "Use empty values to skip, example: Teklia/IAM-line||train"
        ),
    )
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=12000,
        help="Max samples to import per --hf-source",
    )
    parser.add_argument(
        "--local-metadata",
        action="append",
        default=[],
        help=(
            "Local metadata CSV spec: /path/to/labels.csv|/optional/data_root. "
            "If data_root is omitted, CSV parent directory is used."
        ),
    )
    parser.add_argument("--include-history", action="store_true")
    parser.add_argument("--db-path", type=str, default="data/app.db")
    parser.add_argument("--upload-root", type=str, default="data/uploads")
    parser.add_argument("--history-min-confidence", type=float, default=0.62)
    parser.add_argument("--min-text-len", type=int, default=2)
    parser.add_argument("--max-text-len", type=int, default=180)
    parser.add_argument("--train-ratio", type=float, default=0.9)
    parser.add_argument("--val-ratio", type=float, default=0.08)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def normalize_text(text: str, min_len: int, max_len: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) < min_len or len(cleaned) > max_len:
        return ""
    return cleaned


def sanitize_slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip())
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "source"


def parse_hf_source(raw: str) -> HFSource:
    parts = [part.strip() for part in raw.split("|")]
    while len(parts) < 5:
        parts.append("")
    dataset, config, split, text_key, image_key = parts[:5]
    if not dataset:
        raise ValueError(f"Invalid --hf-source value: '{raw}'")
    return HFSource(
        dataset=dataset,
        config=config or None,
        split=split or None,
        text_key=text_key or None,
        image_key=image_key or None,
    )


def resolve_local_metadata_spec(raw: str) -> tuple[Path, Path]:
    parts = [part.strip() for part in raw.split("|")]
    csv_path = Path(parts[0]).expanduser().resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"Local metadata file not found: {csv_path}")

    if len(parts) > 1 and parts[1]:
        data_root = Path(parts[1]).expanduser().resolve()
    else:
        data_root = csv_path.parent.resolve()
    return csv_path, data_root


def assign_split(key: str, train_ratio: float, val_ratio: float, seed: int) -> str:
    digest = hashlib.md5(f"{seed}|{key}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    if bucket < train_ratio:
        return "train"
    if bucket < (train_ratio + val_ratio):
        return "val"
    return "test"


def read_image_from_record(value: Any) -> Image.Image | None:
    if value is None:
        return None
    if isinstance(value, Image.Image):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("bytes"), (bytes, bytearray)):
            return Image.open(io.BytesIO(value["bytes"]))
        if value.get("path"):
            path = Path(str(value["path"])).expanduser()
            if path.exists():
                return Image.open(path)
    if isinstance(value, str):
        path = Path(value).expanduser()
        if path.exists():
            return Image.open(path)
    return None


def load_hf_rows(
    source: HFSource,
    max_rows: int,
    out_images: Path,
    min_text_len: int,
    max_text_len: int,
) -> list[dict[str, str]]:
    try:
        from datasets import load_dataset
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "HuggingFace datasets package is required. Install with: pip install datasets"
        ) from exc

    source_slug = sanitize_slug(source.dataset.replace("/", "_"))
    dataset = load_dataset(
        source.dataset,
        name=source.config,
        split=source.split or "train",
    )

    rows: list[dict[str, str]] = []
    image_key = source.image_key
    text_key = source.text_key
    if image_key is None:
        for key in IMAGE_KEY_CANDIDATES:
            if key in dataset.column_names:
                image_key = key
                break
    if text_key is None:
        for key in TEXT_KEY_CANDIDATES:
            if key in dataset.column_names:
                text_key = key
                break

    if not image_key or not text_key:
        raise ValueError(
            f"Could not infer image/text keys for {source.dataset}. "
            f"Columns: {dataset.column_names}"
        )

    default_split = "train"
    if source.split:
        split_name = source.split.lower()
        if split_name in {"validation", "val", "dev"}:
            default_split = "val"
        elif split_name in {"test", "eval"}:
            default_split = "test"

    for idx, item in enumerate(dataset):
        if len(rows) >= max_rows:
            break

        raw_text = item.get(text_key)
        text = normalize_text(str(raw_text or ""), min_text_len, max_text_len)
        if not text:
            continue

        image_obj = read_image_from_record(item.get(image_key))
        if image_obj is None:
            continue

        image_obj = image_obj.convert("L")
        rel_path = Path("images") / f"{source_slug}_{len(rows):07d}.png"
        image_obj.save(out_images / rel_path.name)

        rows.append(
            {
                "image": str(rel_path),
                "text": text,
                "split": default_split,
                "source": f"hf:{source.dataset}",
            }
        )

    return rows


def load_local_rows(
    metadata_csv: Path,
    data_root: Path,
    min_text_len: int,
    max_text_len: int,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with metadata_csv.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "image" not in (reader.fieldnames or []) or "text" not in (reader.fieldnames or []):
            raise ValueError(f"CSV must contain image,text columns: {metadata_csv}")

        for item in reader:
            image_ref = (item.get("image") or "").strip()
            text = normalize_text(item.get("text") or "", min_text_len, max_text_len)
            if not image_ref or not text:
                continue

            image_path = Path(image_ref)
            if not image_path.is_absolute():
                image_path = data_root / image_ref
            if not image_path.exists():
                continue

            split_raw = (item.get("split") or "").strip().lower()
            if split_raw in {"validation", "val", "dev"}:
                split = "val"
            elif split_raw in {"test", "eval"}:
                split = "test"
            elif split_raw == "train":
                split = "train"
            else:
                split = ""

            rows.append(
                {
                    "image_abs": str(image_path.resolve()),
                    "text": text,
                    "split": split,
                    "source": f"local:{metadata_csv.name}",
                }
            )
    return rows


def load_history_rows(
    db_path: Path,
    upload_root: Path,
    min_confidence: float,
    min_text_len: int,
    max_text_len: int,
) -> list[dict[str, str]]:
    if not db_path.exists():
        return []

    rows: list[dict[str, str]] = []
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        query = """
            SELECT image_path, prediction, confidence
            FROM detection_history
            WHERE COALESCE(prediction, '') != ''
              AND COALESCE(image_path, '') != ''
              AND confidence >= ?
            ORDER BY created_at DESC
        """
        for item in conn.execute(query, (float(min_confidence),)):
            rel = (item["image_path"] or "").strip()
            if not rel:
                continue
            image_path = (upload_root / rel).resolve()
            if not image_path.exists():
                continue
            text = normalize_text(item["prediction"] or "", min_text_len, max_text_len)
            if not text:
                continue
            rows.append(
                {
                    "image_abs": str(image_path),
                    "text": text,
                    "split": "",
                    "source": "history:high_conf",
                }
            )
    finally:
        conn.close()
    return rows


def copy_rows_to_output(
    rows: list[dict[str, str]],
    out_images: Path,
    train_ratio: float,
    val_ratio: float,
    seed: int,
) -> list[dict[str, str]]:
    out_rows: list[dict[str, str]] = []
    seen = set()

    for idx, row in enumerate(rows):
        image_abs = row.get("image_abs")
        if not image_abs:
            # Rows that are already copied (HF sources).
            image_rel = row["image"]
            split = row["split"] or assign_split(
                key=f"{row['source']}::{image_rel}::{row['text']}",
                train_ratio=train_ratio,
                val_ratio=val_ratio,
                seed=seed,
            )
            dedupe_key = f"{image_rel}::{row['text']}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out_rows.append(
                {
                    "image": image_rel,
                    "text": row["text"],
                    "split": split,
                    "source": row["source"],
                }
            )
            continue

        source_slug = sanitize_slug(row["source"])
        src_path = Path(image_abs)
        rel_path = Path("images") / f"{source_slug}_{idx:07d}{src_path.suffix.lower() or '.png'}"
        target = out_images / rel_path.name

        try:
            image_obj = Image.open(src_path).convert("L")
            image_obj.save(target)
        except Exception:
            continue

        split = row["split"] or assign_split(
            key=f"{row['source']}::{src_path.as_posix()}::{row['text']}",
            train_ratio=train_ratio,
            val_ratio=val_ratio,
            seed=seed,
        )
        dedupe_key = f"{rel_path.as_posix()}::{row['text']}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out_rows.append(
            {
                "image": rel_path.as_posix(),
                "text": row["text"],
                "split": split,
                "source": row["source"],
            }
        )
    return out_rows


def main() -> None:
    args = parse_args()
    if args.train_ratio <= 0 or args.train_ratio >= 1:
        raise ValueError("--train-ratio must be between 0 and 1")
    if args.val_ratio < 0 or args.val_ratio >= 1:
        raise ValueError("--val-ratio must be between 0 and 1")
    if args.train_ratio + args.val_ratio >= 1:
        raise ValueError("train_ratio + val_ratio must be < 1")

    out_dir = Path(args.out_dir).resolve()
    out_images = out_dir / "images"
    out_images.mkdir(parents=True, exist_ok=True)

    merged_rows: list[dict[str, str]] = []

    for source_raw in args.hf_source:
        source = parse_hf_source(source_raw)
        print(f"[HF] Loading {source.dataset} ({source.split or 'train'}) ...")
        hf_rows = load_hf_rows(
            source=source,
            max_rows=args.max_per_source,
            out_images=out_images,
            min_text_len=args.min_text_len,
            max_text_len=args.max_text_len,
        )
        print(f"[HF] Added {len(hf_rows)} rows from {source.dataset}")
        merged_rows.extend(hf_rows)

    for local_spec in args.local_metadata:
        csv_path, data_root = resolve_local_metadata_spec(local_spec)
        local_rows = load_local_rows(
            metadata_csv=csv_path,
            data_root=data_root,
            min_text_len=args.min_text_len,
            max_text_len=args.max_text_len,
        )
        print(f"[LOCAL] Added {len(local_rows)} rows from {csv_path}")
        merged_rows.extend(local_rows)

    if args.include_history:
        history_rows = load_history_rows(
            db_path=Path(args.db_path).resolve(),
            upload_root=Path(args.upload_root).resolve(),
            min_confidence=args.history_min_confidence,
            min_text_len=args.min_text_len,
            max_text_len=args.max_text_len,
        )
        print(f"[HISTORY] Added {len(history_rows)} rows from detection_history")
        merged_rows.extend(history_rows)

    if not merged_rows:
        raise RuntimeError("No rows collected. Add --hf-source and/or --local-metadata.")

    final_rows = copy_rows_to_output(
        rows=merged_rows,
        out_images=out_images,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        seed=args.seed,
    )
    if not final_rows:
        raise RuntimeError("No valid rows after image/text filtering.")

    csv_path = out_dir / "labels.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["image", "text", "split", "source"])
        writer.writeheader()
        writer.writerows(final_rows)

    counts = {"train": 0, "val": 0, "test": 0}
    for row in final_rows:
        counts[row["split"]] = counts.get(row["split"], 0) + 1

    print(f"\nDataset ready: {out_dir}")
    print(f"CSV: {csv_path}")
    print(f"Total rows: {len(final_rows)}")
    print(f"Split counts: train={counts.get('train', 0)} val={counts.get('val', 0)} test={counts.get('test', 0)}")


if __name__ == "__main__":
    main()
