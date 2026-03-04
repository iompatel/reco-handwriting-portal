from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import torch
from PIL import ImageFilter
from torch.utils.data import Dataset
from torchvision import transforms

from src.image_utils import open_grayscale, resize_with_padding
from src.text_utils import CharTokenizer


def load_metadata(
    metadata_path: str | Path,
    data_root: str | Path | None = None,
    split: str | None = None,
) -> list[dict[str, str]]:
    metadata_path = Path(metadata_path)
    root = Path(data_root) if data_root else metadata_path.parent

    rows: list[dict[str, str]] = []
    with metadata_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"image", "text"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Metadata CSV must include columns: image,text")

        for row in reader:
            row_split = (row.get("split") or "").strip().lower()
            if split:
                wanted = split.lower()
                if row_split:
                    if row_split != wanted:
                        continue
                elif wanted != "train":
                    continue

            image_path = Path(row["image"])
            if not image_path.is_absolute():
                image_path = root / image_path

            rows.append(
                {
                    "image": str(image_path),
                    "text": row["text"],
                    "split": row_split or "train",
                }
            )

    if split and not rows:
        raise ValueError(f"No rows found for split='{split}' in {metadata_path}")
    return rows


class HandwritingDataset(Dataset):
    def __init__(
        self,
        rows: list[dict[str, str]],
        tokenizer: CharTokenizer,
        img_width: int = 128,
        img_height: int = 32,
        augment: bool = False,
        max_label_len: int | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.img_width = img_width
        self.img_height = img_height
        self.rows = []
        self.max_label_len = int(max_label_len) if max_label_len is not None else None
        self.skipped_invalid = 0
        self.skipped_too_long = 0

        for row in rows:
            clean = self.tokenizer.sanitize(row["text"])
            if not clean:
                self.skipped_invalid += 1
                continue
            if self.max_label_len is not None and len(clean) > self.max_label_len:
                self.skipped_too_long += 1
                continue
            self.rows.append({"image": row["image"], "text": clean, "split": row["split"]})

        if not self.rows:
            raise ValueError("Dataset has no valid samples after sanitization")

        self.augment = augment
        self.affine = transforms.RandomAffine(
            degrees=3,
            translate=(0.04, 0.08),
            scale=(0.92, 1.08),
            shear=3,
            fill=255,
        )
        self.to_tensor = transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.Normalize((0.5,), (0.5,)),
            ]
        )

    def __len__(self) -> int:
        return len(self.rows)

    def _apply_augmentation(self, image):
        image = self.affine(image)
        if torch.rand(1).item() < 0.3:
            image = image.filter(ImageFilter.GaussianBlur(radius=float(torch.rand(1).item() * 0.9)))
        return image

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor, str]:
        row = self.rows[index]
        image = open_grayscale(row["image"])
        if self.augment:
            image = self._apply_augmentation(image)

        image = resize_with_padding(image, self.img_width, self.img_height)
        image_tensor = self.to_tensor(image)
        label = torch.tensor(self.tokenizer.encode(row["text"]), dtype=torch.long)
        return image_tensor, label, row["text"]


def ctc_collate_fn(batch: list[tuple[torch.Tensor, torch.Tensor, str]]) -> dict[str, Any]:
    images, labels, texts = zip(*batch)
    image_batch = torch.stack(images)
    label_lengths = torch.tensor([len(lbl) for lbl in labels], dtype=torch.long)
    labels_flat = torch.cat(labels)

    return {
        "images": image_batch,
        "labels": labels_flat,
        "label_lengths": label_lengths,
        "texts": list(texts),
    }
