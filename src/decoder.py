from __future__ import annotations

import math
from typing import Iterable

import torch

from src.text_utils import CharTokenizer


def _logsumexp(values: Iterable[float]) -> float:
    vals = [v for v in values if v != -math.inf]
    if not vals:
        return -math.inf
    m = max(vals)
    if m == -math.inf:
        return -math.inf
    return m + math.log(sum(math.exp(v - m) for v in vals))


def ctc_greedy_decode(log_probs: torch.Tensor, tokenizer: CharTokenizer) -> list[tuple[str, float]]:
    probs = log_probs.detach().exp()
    max_probs, max_ids = probs.max(dim=2)  # [T, B]
    results: list[tuple[str, float]] = []

    batch_size = max_ids.shape[1]
    blank_idx = tokenizer.blank_idx
    for b in range(batch_size):
        ids = max_ids[:, b].tolist()
        confidence_steps = max_probs[:, b].tolist()

        merged_tokens: list[int] = []
        merged_conf: list[float] = []

        prev = blank_idx
        for idx, conf in zip(ids, confidence_steps):
            if idx == blank_idx:
                prev = blank_idx
                continue
            if idx != prev:
                merged_tokens.append(idx)
                merged_conf.append(conf)
            prev = idx

        text = tokenizer.decode(merged_tokens)
        confidence = float(sum(merged_conf) / max(1, len(merged_conf)))
        results.append((text, confidence))

    return results


def ctc_prefix_beam_search(
    log_probs: torch.Tensor,
    tokenizer: CharTokenizer,
    beam_width: int = 10,
    top_k: int = 3,
) -> list[tuple[str, float]]:
    if log_probs.dim() != 2:
        raise ValueError("log_probs must be [T, C] for single-sample beam search")

    blank = tokenizer.blank_idx
    time_steps, num_classes = log_probs.shape
    beams: dict[tuple[int, ...], tuple[float, float]] = {(): (0.0, -math.inf)}

    for t in range(time_steps):
        next_beams: dict[tuple[int, ...], tuple[float, float]] = {}
        for prefix, (p_blank, p_nonblank) in beams.items():
            lp_blank = float(log_probs[t, blank].item())
            cur_blank, cur_nonblank = next_beams.get(prefix, (-math.inf, -math.inf))
            cur_blank = _logsumexp([cur_blank, p_blank + lp_blank, p_nonblank + lp_blank])
            next_beams[prefix] = (cur_blank, cur_nonblank)

            for c in range(num_classes):
                if c == blank:
                    continue
                lp = float(log_probs[t, c].item())
                end_char = prefix[-1] if prefix else None

                if c == end_char:
                    same_blank, same_nonblank = next_beams.get(prefix, (-math.inf, -math.inf))
                    same_nonblank = _logsumexp([same_nonblank, p_nonblank + lp])
                    next_beams[prefix] = (same_blank, same_nonblank)

                    new_prefix = prefix + (c,)
                    new_blank, new_nonblank = next_beams.get(new_prefix, (-math.inf, -math.inf))
                    new_nonblank = _logsumexp([new_nonblank, p_blank + lp])
                    next_beams[new_prefix] = (new_blank, new_nonblank)
                else:
                    new_prefix = prefix + (c,)
                    new_blank, new_nonblank = next_beams.get(new_prefix, (-math.inf, -math.inf))
                    new_nonblank = _logsumexp([new_nonblank, p_blank + lp, p_nonblank + lp])
                    next_beams[new_prefix] = (new_blank, new_nonblank)

        beams = dict(
            sorted(
                next_beams.items(),
                key=lambda item: _logsumexp(item[1]),
                reverse=True,
            )[:beam_width]
        )

    ranked = sorted(beams.items(), key=lambda item: _logsumexp(item[1]), reverse=True)

    predictions: list[tuple[str, float]] = []
    for prefix, (p_blank, p_nonblank) in ranked[:top_k]:
        score = _logsumexp([p_blank, p_nonblank])
        text = tokenizer.decode(prefix)
        avg_log = score / max(1, len(prefix))
        confidence = float(max(0.0, min(1.0, math.exp(avg_log))))
        predictions.append((text, confidence))

    return predictions
