from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

DEFAULT_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 "


@dataclass
class CharTokenizer:
    alphabet: str = DEFAULT_ALPHABET

    def __post_init__(self) -> None:
        seen = set()
        cleaned = []
        for ch in self.alphabet:
            if ch not in seen:
                cleaned.append(ch)
                seen.add(ch)
        self.alphabet = "".join(cleaned)
        self.blank_idx = 0
        self.idx_to_char = ["<BLANK>", *list(self.alphabet)]
        self.char_to_idx = {ch: i + 1 for i, ch in enumerate(self.alphabet)}

    @property
    def num_classes(self) -> int:
        return len(self.idx_to_char)

    def sanitize(self, text: str) -> str:
        return "".join(ch for ch in text.lower() if ch in self.char_to_idx)

    def encode(self, text: str) -> list[int]:
        clean = self.sanitize(text)
        return [self.char_to_idx[ch] for ch in clean]

    def decode(self, token_ids: Iterable[int]) -> str:
        chars = []
        for idx in token_ids:
            if idx == self.blank_idx:
                continue
            if 0 <= idx < len(self.idx_to_char):
                chars.append(self.idx_to_char[idx])
        return "".join(chars)


def levenshtein_distance(a: Sequence, b: Sequence) -> int:
    if a == b:
        return 0
    if len(a) == 0:
        return len(b)
    if len(b) == 0:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        cur = [i]
        for j, item_b in enumerate(b, start=1):
            insert_cost = cur[j - 1] + 1
            delete_cost = prev[j] + 1
            sub_cost = prev[j - 1] + (0 if item_a == item_b else 1)
            cur.append(min(insert_cost, delete_cost, sub_cost))
        prev = cur
    return prev[-1]


def char_error_rate(reference: str, hypothesis: str) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    edits = levenshtein_distance(list(reference), list(hypothesis))
    return edits / max(1, len(reference))


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref_words = reference.split()
    hyp_words = hypothesis.split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    edits = levenshtein_distance(ref_words, hyp_words)
    return edits / max(1, len(ref_words))
