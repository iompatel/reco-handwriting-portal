from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader, random_split

from src.checkpointing import save_checkpoint
from src.dataset import HandwritingDataset, ctc_collate_fn, load_metadata
from src.decoder import ctc_greedy_decode
from src.model import CRNN
from src.text_utils import CharTokenizer, char_error_rate, word_error_rate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train CRNN handwriting recognizer")
    parser.add_argument("--metadata", type=str, required=True, help="Path to labels CSV")
    parser.add_argument("--data-root", type=str, default=None, help="Optional root for relative image paths")
    parser.add_argument("--alphabet", type=str, default="abcdefghijklmnopqrstuvwxyz0123456789 ")
    parser.add_argument("--img-width", type=int, default=128)
    parser.add_argument("--img-height", type=int, default=32)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-5)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--hidden-size", type=int, default=256)
    parser.add_argument("--rnn-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--checkpoint-dir", type=str, default="checkpoints")
    parser.add_argument("--resume", type=str, default=None, help="Path to checkpoint to resume from")
    parser.add_argument(
        "--init-checkpoint",
        type=str,
        default=None,
        help="Initialize model weights from checkpoint (no optimizer state)",
    )
    parser.add_argument("--patience", type=int, default=7, help="Early stopping patience on val CER")
    parser.add_argument("--device", type=str, default="auto", help="auto|cpu|cuda|mps")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-augment", action="store_true", help="Disable image augmentation")
    parser.add_argument("--amp", action="store_true", help="Enable mixed precision on CUDA")
    parser.add_argument("--lr-patience", type=int, default=2, help="LR scheduler patience on val CER")
    parser.add_argument("--lr-factor", type=float, default=0.5, help="LR reduce factor")
    parser.add_argument("--min-lr", type=float, default=1e-6, help="Minimum LR for scheduler")
    parser.add_argument("--log-interval", type=int, default=80, help="Batch interval for progress logs (0 disables)")
    parser.add_argument(
        "--blank-penalty",
        type=float,
        default=0.05,
        help="Penalty weight on blank-token probability to avoid CTC blank collapse",
    )
    return parser.parse_args()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def resolve_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def split_rows(rows: list[dict[str, str]], seed: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    train_rows = [r for r in rows if r["split"] == "train"]
    val_rows = [r for r in rows if r["split"] == "val"]

    if val_rows:
        if not train_rows:
            raise ValueError("Metadata contains val rows but no train rows.")
        return train_rows, val_rows

    if not train_rows:
        train_rows = rows

    if len(train_rows) < 2:
        raise ValueError(
            "Need at least 2 samples when CSV has no 'val' split. "
            "Provide explicit train/val rows in metadata."
        )

    n_train = max(1, int(0.9 * len(train_rows)))
    n_val = max(1, len(train_rows) - n_train)
    if n_train + n_val > len(train_rows):
        n_train = len(train_rows) - n_val

    generator = torch.Generator().manual_seed(seed)
    ds = list(train_rows)
    subset_train, subset_val = random_split(ds, [n_train, n_val], generator=generator)
    return [ds[i] for i in subset_train.indices], [ds[i] for i in subset_val.indices]


def max_target_length_for_width(img_width: int) -> int:
    # CNN width path:
    # pool(2,2) -> floor(w/2), pool(2,2) -> floor(w/4),
    # pool(2,1) -> same, pool(2,1) -> same, conv(k=2,s=1,p=0) -> -1
    return max(1, (img_width // 4) - 1)


def evaluate(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.CTCLoss,
    tokenizer: CharTokenizer,
    device: torch.device,
) -> dict[str, float]:
    model.eval()
    total_loss = 0.0
    total_items = 0
    total_cer = 0.0
    total_wer = 0.0
    exact = 0

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
            total_items += images.size(0)

            predictions = ctc_greedy_decode(log_probs, tokenizer)
            for (pred_text, _), target_text in zip(predictions, batch["texts"]):
                total_cer += char_error_rate(target_text, pred_text)
                total_wer += word_error_rate(target_text, pred_text)
                exact += int(pred_text == target_text)

    if total_items == 0:
        return {"loss": 0.0, "cer": 1.0, "wer": 1.0, "acc": 0.0}

    return {
        "loss": total_loss / total_items,
        "cer": total_cer / total_items,
        "wer": total_wer / total_items,
        "acc": exact / total_items,
    }


def append_history(history_path: Path, row: dict[str, float | int]) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = history_path.exists()
    with history_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    args = parse_args()
    set_seed(args.seed)

    device = resolve_device(args.device)
    tokenizer = CharTokenizer(alphabet=args.alphabet)

    rows = load_metadata(args.metadata, args.data_root)
    train_rows, val_rows = split_rows(rows, args.seed)
    max_target_len = max_target_length_for_width(args.img_width)
    print(f"Max target length for img_width={args.img_width}: {max_target_len}")

    train_ds = HandwritingDataset(
        rows=train_rows,
        tokenizer=tokenizer,
        img_width=args.img_width,
        img_height=args.img_height,
        augment=not args.no_augment,
        max_label_len=max_target_len,
    )
    val_ds = HandwritingDataset(
        rows=val_rows,
        tokenizer=tokenizer,
        img_width=args.img_width,
        img_height=args.img_height,
        augment=False,
        max_label_len=max_target_len,
    )
    print(
        "Train dataset: "
        f"kept={len(train_ds)} "
        f"skipped_invalid={train_ds.skipped_invalid} "
        f"skipped_too_long={train_ds.skipped_too_long}"
    )
    print(
        "Val dataset: "
        f"kept={len(val_ds)} "
        f"skipped_invalid={val_ds.skipped_invalid} "
        f"skipped_too_long={val_ds.skipped_too_long}"
    )

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
        collate_fn=ctc_collate_fn,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
        collate_fn=ctc_collate_fn,
    )

    model = CRNN(
        num_classes=tokenizer.num_classes,
        hidden_size=args.hidden_size,
        rnn_layers=args.rnn_layers,
        dropout=args.dropout,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    lr_scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer,
        mode="min",
        factor=float(args.lr_factor),
        patience=int(args.lr_patience),
        min_lr=float(args.min_lr),
    )
    criterion = nn.CTCLoss(blank=tokenizer.blank_idx, zero_infinity=True)

    start_epoch = 1
    best_cer = float("inf")
    stale_epochs = 0

    if args.resume and args.init_checkpoint:
        raise ValueError("Use either --resume or --init-checkpoint, not both.")

    if args.init_checkpoint:
        init_ckpt = torch.load(args.init_checkpoint, map_location=device)
        model.load_state_dict(init_ckpt["model_state"])
        print(f"Initialized weights from {args.init_checkpoint}")

    if args.resume:
        ckpt = torch.load(args.resume, map_location=device)
        model.load_state_dict(ckpt["model_state"])
        if "optimizer_state" in ckpt:
            optimizer.load_state_dict(ckpt["optimizer_state"])
        if "scheduler_state" in ckpt:
            lr_scheduler.load_state_dict(ckpt["scheduler_state"])
        start_epoch = int(ckpt.get("epoch", 0)) + 1
        best_cer = float(ckpt.get("best_cer", best_cer))
        print(f"Resumed from epoch {start_epoch - 1} | best CER: {best_cer:.4f}")

    use_amp = bool(args.amp and device.type == "cuda")
    if hasattr(torch, "amp") and hasattr(torch.amp, "GradScaler"):
        scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
    else:
        scaler = torch.cuda.amp.GradScaler(enabled=use_amp)

    checkpoint_dir = Path(args.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    history_path = checkpoint_dir / "history.csv"

    for epoch in range(start_epoch, args.epochs + 1):
        model.train()
        running_loss = 0.0
        seen = 0

        total_steps = max(1, len(train_loader))
        for step, batch in enumerate(train_loader, start=1):
            images = batch["images"].to(device)
            labels = batch["labels"].to(device)
            label_lengths = batch["label_lengths"].to(device)
            optimizer.zero_grad(set_to_none=True)

            autocast_device = "cuda" if device.type == "cuda" else "cpu"
            with torch.autocast(device_type=autocast_device, enabled=use_amp):
                logits = model(images)
                log_probs = F.log_softmax(logits, dim=2)
                input_lengths = torch.full(
                    size=(images.size(0),),
                    fill_value=log_probs.size(0),
                    dtype=torch.long,
                    device=device,
                )
                ctc_loss = criterion(log_probs, labels, input_lengths, label_lengths)
                if args.blank_penalty > 0:
                    probs = log_probs.exp()
                    blank_prob = probs[:, :, tokenizer.blank_idx].mean()
                    loss = ctc_loss + args.blank_penalty * blank_prob
                else:
                    loss = ctc_loss

            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            scaler.step(optimizer)
            scaler.update()

            running_loss += float(loss.item()) * images.size(0)
            seen += images.size(0)
            if args.log_interval > 0 and (step % args.log_interval == 0 or step == total_steps):
                avg_loss = running_loss / max(1, seen)
                print(f"Epoch {epoch:03d} | step {step:04d}/{total_steps:04d} | train_loss={avg_loss:.4f}")

        train_loss = running_loss / max(1, seen)
        val_metrics = evaluate(model, val_loader, criterion, tokenizer, device)
        lr_scheduler.step(val_metrics["cer"])
        current_lr = float(optimizer.param_groups[0]["lr"])

        print(
            f"Epoch {epoch:03d} | train_loss={train_loss:.4f} | "
            f"val_loss={val_metrics['loss']:.4f} | val_cer={val_metrics['cer']:.4f} | "
            f"val_wer={val_metrics['wer']:.4f} | val_acc={val_metrics['acc']:.4f} | "
            f"lr={current_lr:.7f}"
        )

        row = {
            "epoch": epoch,
            "train_loss": round(train_loss, 6),
            "val_loss": round(val_metrics["loss"], 6),
            "val_cer": round(val_metrics["cer"], 6),
            "val_wer": round(val_metrics["wer"], 6),
            "val_acc": round(val_metrics["acc"], 6),
            "lr": round(current_lr, 8),
        }
        append_history(history_path, row)

        checkpoint_state = {
            "epoch": epoch,
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "scheduler_state": lr_scheduler.state_dict(),
            "best_cer": best_cer,
            "alphabet": tokenizer.alphabet,
            "hidden_size": args.hidden_size,
            "rnn_layers": args.rnn_layers,
            "dropout": args.dropout,
            "blank_penalty": args.blank_penalty,
            "img_width": args.img_width,
            "img_height": args.img_height,
        }
        save_checkpoint(checkpoint_dir / "last.pt", checkpoint_state)

        if val_metrics["cer"] < best_cer:
            best_cer = val_metrics["cer"]
            stale_epochs = 0
            checkpoint_state["best_cer"] = best_cer
            save_checkpoint(checkpoint_dir / "best.pt", checkpoint_state)
            print(f"Saved new best model with CER={best_cer:.4f}")
        else:
            stale_epochs += 1
            if stale_epochs >= args.patience:
                print(f"Early stopping at epoch {epoch} (no CER improvement for {args.patience} epochs)")
                break

    print("Training complete.")
    print(f"Best checkpoint: {checkpoint_dir / 'best.pt'}")


if __name__ == "__main__":
    main()
