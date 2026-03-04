from __future__ import annotations

import argparse
import csv
from pathlib import Path

import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader

from src.checkpointing import build_model_from_checkpoint
from src.dataset import HandwritingDataset, ctc_collate_fn, load_metadata
from src.decoder import ctc_greedy_decode, ctc_prefix_beam_search
from src.text_utils import char_error_rate, word_error_rate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate CRNN handwriting recognizer")
    parser.add_argument("--checkpoint", type=str, required=True)
    parser.add_argument("--metadata", type=str, required=True)
    parser.add_argument("--data-root", type=str, default=None)
    parser.add_argument("--split", type=str, default="test", help="Dataset split: test|val|train")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--beam-width", type=int, default=1, help=">1 enables beam search")
    parser.add_argument("--save-preds", type=str, default=None, help="Optional CSV output")
    return parser.parse_args()


def resolve_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def max_target_length_for_width(img_width: int) -> int:
    return max(1, (img_width // 4) - 1)


def main() -> None:
    args = parse_args()
    device = resolve_device(args.device)

    checkpoint = torch.load(args.checkpoint, map_location=device)
    model, tokenizer = build_model_from_checkpoint(checkpoint, device)

    img_width = int(checkpoint.get("img_width", 128))
    img_height = int(checkpoint.get("img_height", 32))

    rows = load_metadata(args.metadata, args.data_root, split=args.split)
    max_target_len = max_target_length_for_width(img_width)
    dataset = HandwritingDataset(
        rows=rows,
        tokenizer=tokenizer,
        img_width=img_width,
        img_height=img_height,
        augment=False,
        max_label_len=max_target_len,
    )
    print(
        f"Eval dataset rows={len(dataset)} "
        f"(skipped_invalid={dataset.skipped_invalid}, skipped_too_long={dataset.skipped_too_long}, "
        f"max_target_len={max_target_len})"
    )
    loader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
        collate_fn=ctc_collate_fn,
    )

    criterion = nn.CTCLoss(blank=tokenizer.blank_idx, zero_infinity=True)

    total_loss = 0.0
    total = 0
    exact = 0
    sum_cer = 0.0
    sum_wer = 0.0
    pred_rows: list[dict[str, str | float]] = []

    model.eval()
    with torch.no_grad():
        for batch in loader:
            images = batch["images"].to(device)
            labels = batch["labels"].to(device)
            label_lengths = batch["label_lengths"].to(device)
            logits = model(images)
            log_probs = F.log_softmax(logits, dim=2)
            input_lengths = torch.full(
                size=(images.size(0),),
                fill_value=log_probs.size(0),
                dtype=torch.long,
                device=device,
            )
            loss = criterion(log_probs, labels, input_lengths, label_lengths)

            total_loss += float(loss.item()) * images.size(0)
            total += images.size(0)

            if args.beam_width > 1:
                predictions = []
                for i in range(images.size(0)):
                    beams = ctc_prefix_beam_search(
                        log_probs[:, i, :].cpu(),
                        tokenizer=tokenizer,
                        beam_width=args.beam_width,
                        top_k=1,
                    )
                    if beams:
                        predictions.append(beams[0])
                    else:
                        predictions.append(("", 0.0))
            else:
                predictions = ctc_greedy_decode(log_probs, tokenizer)

            for (pred_text, conf), target_text in zip(predictions, batch["texts"]):
                exact += int(pred_text == target_text)
                sum_cer += char_error_rate(target_text, pred_text)
                sum_wer += word_error_rate(target_text, pred_text)
                pred_rows.append(
                    {
                        "ground_truth": target_text,
                        "prediction": pred_text,
                        "confidence": round(float(conf), 4),
                    }
                )

    if total == 0:
        raise RuntimeError("No samples available for evaluation")

    metrics = {
        "loss": total_loss / total,
        "cer": sum_cer / total,
        "wer": sum_wer / total,
        "exact_match": exact / total,
    }

    print(
        "Evaluation "
        f"(split={args.split}, beam_width={args.beam_width}) | "
        f"loss={metrics['loss']:.4f} | cer={metrics['cer']:.4f} | "
        f"wer={metrics['wer']:.4f} | exact={metrics['exact_match']:.4f}"
    )

    if args.save_preds:
        out_path = Path(args.save_preds)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["ground_truth", "prediction", "confidence"])
            writer.writeheader()
            writer.writerows(pred_rows)
        print(f"Saved predictions to {out_path}")


if __name__ == "__main__":
    main()
