from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import torch
import torch.nn.functional as F
from torchvision import transforms

from src.checkpointing import build_model_from_checkpoint
from src.decoder import ctc_greedy_decode, ctc_prefix_beam_search
from src.image_utils import open_grayscale, resize_with_padding


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run inference with CRNN handwriting model")
    parser.add_argument("--checkpoint", type=str, required=True)
    parser.add_argument("--image", type=str, required=True, help="Image file or directory")
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", type=str, default=None, help="Optional CSV output path")
    return parser.parse_args()


def resolve_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def list_images(path: str | Path) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"Image path not found: {path}")

    valid_ext = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    images = [p for p in sorted(path.iterdir()) if p.suffix.lower() in valid_ext]
    if not images:
        raise FileNotFoundError(f"No image files found in directory: {path}")
    return images


def prepare_batch(image_paths: list[Path], width: int, height: int) -> torch.Tensor:
    to_tensor = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.5,), (0.5,)),
    ])

    tensors = []
    for p in image_paths:
        img = open_grayscale(p)
        img = resize_with_padding(img, target_width=width, target_height=height)
        tensors.append(to_tensor(img))
    return torch.stack(tensors)


def main() -> None:
    args = parse_args()
    device = resolve_device(args.device)

    checkpoint = torch.load(args.checkpoint, map_location=device)
    model, tokenizer = build_model_from_checkpoint(checkpoint, device)

    img_width = int(checkpoint.get("img_width", 128))
    img_height = int(checkpoint.get("img_height", 32))

    image_paths = list_images(args.image)
    batch = prepare_batch(image_paths, width=img_width, height=img_height).to(device)

    with torch.no_grad():
        logits = model(batch)
        log_probs = F.log_softmax(logits, dim=2)

    output_rows = []
    if args.beam_width > 1:
        for idx, image_path in enumerate(image_paths):
            beams = ctc_prefix_beam_search(
                log_probs[:, idx, :].cpu(),
                tokenizer=tokenizer,
                beam_width=args.beam_width,
                top_k=args.top_k,
            )
            best_text, best_conf = beams[0] if beams else ("", 0.0)
            output_rows.append(
                {
                    "image": str(image_path),
                    "prediction": best_text,
                    "confidence": round(float(best_conf), 4),
                    "alternatives": json.dumps(beams),
                }
            )
    else:
        greedy = ctc_greedy_decode(log_probs, tokenizer)
        for image_path, (text, conf) in zip(image_paths, greedy):
            output_rows.append(
                {
                    "image": str(image_path),
                    "prediction": text,
                    "confidence": round(float(conf), 4),
                    "alternatives": json.dumps([(text, conf)]),
                }
            )

    for row in output_rows:
        print(f"{row['image']} -> {row['prediction']} (confidence={row['confidence']})")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["image", "prediction", "confidence", "alternatives"],
            )
            writer.writeheader()
            writer.writerows(output_rows)
        print(f"Saved predictions to {out}")


if __name__ == "__main__":
    main()
