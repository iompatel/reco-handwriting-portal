"""Regression tests for dataset preparation that do not require PyTorch."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from scripts import generate_synthetic_dataset as synthetic
from scripts.prepare_training_dataset import (
    HFSource,
    copy_rows_to_output,
    load_hf_rows,
)
from src.text_utils import ctc_required_timesteps


class FakeDataset(list):
    column_names = ["image", "text"]


class TrainingDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="reco-ml-tests-"))
        self.images = self.root / "images"
        self.images.mkdir()

    def test_repeated_hf_source_does_not_overwrite_an_earlier_image(self):
        datasets_module = types.ModuleType("datasets")
        calls = iter(
            [
                FakeDataset([{"image": Image.new("L", (2, 2), 0), "text": "dark"}]),
                FakeDataset([{"image": Image.new("L", (2, 2), 255), "text": "light"}]),
            ]
        )
        datasets_module.load_dataset = lambda *args, **kwargs: next(calls)
        source = HFSource("same/dataset", None, "train", "text", "image")

        with mock.patch.dict(sys.modules, {"datasets": datasets_module}):
            first = load_hf_rows(source, 1, self.images, 1, 20)
            second = load_hf_rows(source, 1, self.images, 1, 20)

        self.assertNotEqual(first[0]["image"], second[0]["image"])
        with Image.open(self.root / first[0]["image"]) as image:
            self.assertEqual(image.getpixel((0, 0)), 0)

    def test_local_rows_dedupe_by_pixels(self):
        first = self.root / "first.png"
        second = self.root / "second.png"
        Image.new("L", (4, 4), 128).save(first)
        Image.new("L", (4, 4), 128).save(second)
        rows = [
            {"image_abs": str(first), "text": "same", "split": "train", "source": "local:first"},
            {"image_abs": str(second), "text": "same", "split": "val", "source": "local:second"},
        ]

        output = copy_rows_to_output(rows, self.images, 0.8, 0.1, 42)

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["text"], "same")
        self.assertEqual(output[0]["split"], "train")

    def test_conflicting_labels_for_same_pixels_are_rejected(self):
        first = self.root / "conflict-one.png"
        second = self.root / "conflict-two.png"
        Image.new("L", (4, 4), 128).save(first)
        Image.new("L", (4, 4), 128).save(second)
        rows = [
            {"image_abs": str(first), "text": "first", "split": "train", "source": "local:first"},
            {"image_abs": str(second), "text": "second", "split": "val", "source": "local:second"},
        ]
        self.assertEqual(copy_rows_to_output(rows, self.images, 0.8, 0.1, 42), [])

    def test_ctc_required_timesteps_counts_adjacent_repeats(self):
        self.assertEqual(ctc_required_timesteps("aba"), 3)
        self.assertEqual(ctc_required_timesteps("aab"), 4)
        self.assertEqual(ctc_required_timesteps("bookkeeper"), 13)

    def test_oversized_fallback_font_is_rejected(self):
        self.assertIsNone(synthetic.fit_font("W" * 1000, [], 32, 16))

    def test_synthetic_generation_retries_until_text_fits(self):
        with mock.patch.object(synthetic, "random_phrase", side_effect=["W" * 1000, "ok"]):
            rows = synthetic.generate_split(
                split="train",
                count=1,
                out_images=self.images,
                word_pool=["ok"],
                fonts=[],
                width=64,
                height=24,
                min_len=2,
                max_len=10,
                min_words=1,
                max_words=1,
                phrase_prob=0.0,
                notebook_prob=0.0,
                alphabet="ok",
            )
        self.assertEqual(rows[0]["text"], "ok")
        self.assertTrue((self.images / "train_000000.png").is_file())


if __name__ == "__main__":
    unittest.main()
