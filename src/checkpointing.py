from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from src.model import CRNN
from src.text_utils import CharTokenizer


def save_checkpoint(path: str | Path, state: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(state, path)


def build_model_from_checkpoint(checkpoint: dict[str, Any], device: torch.device) -> tuple[CRNN, CharTokenizer]:
    alphabet = checkpoint.get("alphabet")
    if not alphabet:
        raise ValueError("Checkpoint is missing alphabet metadata")

    tokenizer = CharTokenizer(alphabet=alphabet)
    model = CRNN(
        num_classes=tokenizer.num_classes,
        hidden_size=int(checkpoint.get("hidden_size", 256)),
        rnn_layers=int(checkpoint.get("rnn_layers", 2)),
        dropout=float(checkpoint.get("dropout", 0.2)),
    )
    model.load_state_dict(checkpoint["model_state"])
    model.to(device)
    model.eval()
    return model, tokenizer
