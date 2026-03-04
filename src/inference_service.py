from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:
    import torch
    import torch.nn.functional as F
except Exception as exc:  # pragma: no cover
    torch = None
    F = None
    TORCH_IMPORT_ERROR = str(exc)
else:
    TORCH_IMPORT_ERROR = None

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None

from src.image_utils import resize_with_padding

_VALID_ENGINES = {"auto", "hybrid", "rnn", "tesseract"}
_ASCII_FRIENDLY_LANGS = {"eng", "osd", "snum"}


@dataclass
class PreprocessConfig:
    autocontrast: bool = True
    threshold: int = 0
    sharpen: float = 0.0
    grayscale: bool = True
    denoise: bool = False
    adaptive_threshold: bool = False
    invert_colors: bool = False
    contrast_boost: float = 1.0
    handwriting_boost: bool = False
    student_notebook_mode: bool = False
    remove_notebook_lines: bool = False
    smart_text_cleanup: bool = True


@dataclass
class InferenceService:
    checkpoint_path: Path
    device_name: str = "auto"

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _device: Any = field(default=None, init=False)
    _model: Any = field(default=None, init=False)
    _tokenizer: Any = field(default=None, init=False)
    _img_width: int = field(default=128, init=False)
    _img_height: int = field(default=32, init=False)
    _error: str | None = field(default=None, init=False)
    _tesseract_languages: list[str] = field(default_factory=list, init=False)
    _tessdata_dir: Path | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._tessdata_dir = self._detect_tessdata_dir()
        self._load_tesseract_languages()

    @staticmethod
    def _normalize_dir(value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value).expanduser()
        if path.exists() and path.is_dir():
            return path.resolve()
        return None

    def _detect_tessdata_dir(self) -> Path | None:
        from_env = self._normalize_dir(os.getenv("OCR_TESSDATA_DIR"))
        if from_env is not None:
            return from_env

        project_dir = self._normalize_dir("data/tessdata")
        if project_dir is not None:
            return project_dir

        return None

    def _tesseract_base_config(self) -> str:
        if self._tessdata_dir is None:
            return ""
        return f'--tessdata-dir "{self._tessdata_dir}"'

    def _load_tesseract_languages(self, force_refresh: bool = False) -> list[str]:
        if force_refresh and self._tessdata_dir is None:
            self._tessdata_dir = self._detect_tessdata_dir()
        if self._tesseract_languages and not force_refresh:
            return self._tesseract_languages
        if pytesseract is None:
            self._tesseract_languages = []
            return self._tesseract_languages

        config = self._tesseract_base_config()
        try:
            langs = pytesseract.get_languages(config=config)
        except Exception:
            self._tesseract_languages = []
            return self._tesseract_languages

        self._tesseract_languages = sorted({(lang or "").strip().lower() for lang in langs if (lang or "").strip()})
        return self._tesseract_languages

    def get_ocr_capabilities(self) -> dict[str, Any]:
        langs = self._load_tesseract_languages(force_refresh=True)
        return {
            "engines": ["auto", "hybrid", "rnn", "tesseract"],
            "available_languages": langs,
            "default_language": "eng" if "eng" in langs else (langs[0] if langs else ""),
            "torch_available": TORCH_IMPORT_ERROR is None,
            "rnn_checkpoint_exists": self.checkpoint_path.exists(),
            "tesseract_available": bool(langs),
            "tessdata_dir": str(self._tessdata_dir) if self._tessdata_dir is not None else "",
        }

    def _resolve_device(self):
        if self.device_name != "auto":
            return torch.device(self.device_name)
        if torch.cuda.is_available():
            return torch.device("cuda")
        if torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    def ensure_loaded(self) -> None:
        if self._model is not None:
            return

        with self._lock:
            if self._model is not None:
                return

            if TORCH_IMPORT_ERROR:
                self._error = f"PyTorch import failed: {TORCH_IMPORT_ERROR}"
                raise RuntimeError(self._error)

            if not self.checkpoint_path.exists():
                self._error = (
                    f"Checkpoint not found: {self.checkpoint_path}. "
                    "Train first with train.py to generate checkpoints/best.pt"
                )
                raise FileNotFoundError(self._error)

            self._device = self._resolve_device()
            checkpoint = torch.load(self.checkpoint_path, map_location=self._device)
            from src.checkpointing import build_model_from_checkpoint

            model, tokenizer = build_model_from_checkpoint(checkpoint, self._device)
            self._img_width = int(checkpoint.get("img_width", 128))
            self._img_height = int(checkpoint.get("img_height", 32))

            self._model = model
            self._tokenizer = tokenizer
            self._error = None

    def get_status(self) -> dict[str, Any]:
        loaded = self._model is not None
        device = str(self._device) if self._device is not None else self.device_name
        return {
            "loaded": loaded,
            "checkpoint": str(self.checkpoint_path),
            "checkpoint_exists": self.checkpoint_path.exists(),
            "device": device,
            "error": self._error,
            "input_size": [self._img_width, self._img_height],
            "ocr_languages": self._load_tesseract_languages(),
        }

    @staticmethod
    def _apply_adaptive_threshold(image: Image.Image, offset: float = 10.0) -> Image.Image:
        src = np.asarray(image, dtype=np.float32)
        local_mean = np.asarray(image.filter(ImageFilter.BoxBlur(radius=4)), dtype=np.float32)
        binary = np.where(src > (local_mean - offset), 255, 0).astype(np.uint8)
        return Image.fromarray(binary, mode="L")

    @staticmethod
    def _remove_notebook_lines(image: Image.Image) -> Image.Image:
        arr = np.asarray(image, dtype=np.uint8).copy()
        low_mask = arr < 200
        very_dark_mask = arr < 95
        row_ratio = low_mask.mean(axis=1)
        row_very_dark_ratio = very_dark_mask.mean(axis=1)
        row_mean = arr.mean(axis=1)
        row_std = arr.std(axis=1)

        # Notebook lines are typically long, thin, and medium-dark but do not
        # have many very-dark pixels like handwriting strokes.
        line_rows = (
            (row_ratio > 0.55)
            & (row_very_dark_ratio < 0.04)
            & (row_mean > 95)
            & (row_mean < 220)
            & (row_std < 18)
        )

        if not np.any(line_rows):
            return image

        for row_idx in np.where(line_rows)[0]:
            if row_idx == 0:
                arr[row_idx, :] = arr[min(row_idx + 1, arr.shape[0] - 1), :]
            elif row_idx == arr.shape[0] - 1:
                arr[row_idx, :] = arr[max(row_idx - 1, 0), :]
            else:
                arr[row_idx, :] = (
                    (arr[row_idx - 1, :].astype(np.uint16) + arr[row_idx + 1, :].astype(np.uint16)) // 2
                ).astype(np.uint8)

        return Image.fromarray(arr, mode="L")

    @staticmethod
    def _resample_bicubic() -> int:
        if hasattr(Image, "Resampling"):
            return Image.Resampling.BICUBIC
        return Image.BICUBIC

    @staticmethod
    def _enhance_handwriting_strokes(image: Image.Image) -> Image.Image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
        red = rgb[:, :, 0]
        green = rgb[:, :, 1]
        blue = rgb[:, :, 2]

        luminance = 0.299 * red + 0.587 * green + 0.114 * blue
        darkness = 255.0 - luminance
        blue_ink = np.clip(blue - 0.55 * red - 0.45 * green, 0.0, 255.0)
        red_ink = np.clip(red - 0.62 * green - 0.62 * blue, 0.0, 255.0)

        enhanced = np.clip((darkness * 0.82) + (np.maximum(blue_ink, red_ink) * 1.35), 0.0, 255.0)
        out = Image.fromarray(enhanced.astype(np.uint8), mode="L")
        return ImageOps.autocontrast(out, cutoff=1)

    def _preprocess_common(self, image: Image.Image, config: PreprocessConfig) -> Image.Image:
        if config.grayscale:
            image = image.convert("L")
        else:
            image = image.convert("RGB").convert("L")

        if config.student_notebook_mode:
            config = replace(
                config,
                denoise=True,
                adaptive_threshold=True,
                contrast_boost=max(1.0, config.contrast_boost, 1.7),
                sharpen=max(0.35, config.sharpen),
            )

        if config.remove_notebook_lines:
            image = self._remove_notebook_lines(image)

        if config.denoise:
            image = image.filter(ImageFilter.MedianFilter(size=3))

        if config.autocontrast:
            image = ImageOps.autocontrast(image)

        if config.contrast_boost > 1.0:
            image = ImageEnhance.Contrast(image).enhance(config.contrast_boost)

        if config.sharpen > 0:
            image = ImageEnhance.Sharpness(image).enhance(1.0 + config.sharpen)

        if config.adaptive_threshold:
            image = self._apply_adaptive_threshold(image)
        elif config.threshold > 0:
            image = image.point(lambda px: 255 if px > config.threshold else 0)

        if config.invert_colors:
            image = ImageOps.invert(image)

        return image

    def _preprocess_for_rnn(self, image: Image.Image, config: PreprocessConfig) -> Image.Image:
        common = self._preprocess_common(image, config)
        return resize_with_padding(
            common,
            target_width=self._img_width,
            target_height=self._img_height,
        )

    def _preprocess_for_tesseract(self, image: Image.Image, config: PreprocessConfig) -> Image.Image:
        common = self._preprocess_common(image, config)
        width, height = common.size
        min_side = max(1, min(width, height))
        if min_side < 80:
            scale = 3
        elif min_side < 140:
            scale = 2
        else:
            scale = 1

        if scale > 1:
            common = common.resize((width * scale, height * scale), resample=self._resample_bicubic())

        return common

    def _tesseract_notebook_lines_candidate(
        self,
        image: Image.Image,
        language_expr: str,
        language_codes: list[str],
        smart_cleanup: bool,
    ) -> tuple[str, float]:
        if pytesseract is None:
            return "", 0.0

        rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
        red = rgb[:, :, 0]
        green = rgb[:, :, 1]
        blue = rgb[:, :, 2]
        luminance = (0.299 * red) + (0.587 * green) + (0.114 * blue)

        # Favor blue/purple pen strokes and dark strokes.
        ink_mask = (((blue - red) > 12) & ((blue - green) > 5) & (blue < 235)) | (luminance < 90)
        mask = ink_mask.astype(np.uint8)

        # Neighborhood consensus to suppress thin notebook lines/noise.
        acc = np.zeros_like(mask, dtype=np.uint8)
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                acc += np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
        dense_mask = acc >= 6

        row_ratio = dense_mask.mean(axis=1)
        active_rows = row_ratio > 0.02
        for _ in range(2):
            active_rows = np.logical_or(active_rows, np.r_[False, active_rows[:-1]])
            active_rows = np.logical_or(active_rows, np.r_[active_rows[1:], False])

        spans: list[tuple[int, int]] = []
        in_span = False
        start = 0
        for idx, is_active in enumerate(active_rows):
            if is_active and not in_span:
                start = idx
                in_span = True
            elif not is_active and in_span:
                end = idx - 1
                in_span = False
                if end - start + 1 >= 10:
                    spans.append((start, end))
        if in_span:
            end = len(active_rows) - 1
            if end - start + 1 >= 10:
                spans.append((start, end))

        if not spans:
            return "", 0.0

        gray = np.asarray(image.convert("L"), dtype=np.uint8)
        line_results: list[tuple[str, float, float]] = []
        image_width = gray.shape[1]

        # Keep only most likely text bands for speed/stability.
        if len(spans) > 8:
            spans = sorted(spans, key=lambda row: row[1] - row[0], reverse=True)[:8]
            spans = sorted(spans, key=lambda row: row[0])

        for y0, y1 in spans:
            band_mask = dense_mask[y0 : y1 + 1, :]
            col_ratio = band_mask.mean(axis=0)
            cols = np.where(col_ratio > 0.01)[0]
            if len(cols) < max(24, int(image_width * 0.08)):
                continue

            x0, x1 = int(cols.min()), int(cols.max())
            x0 = max(0, x0 - 18)
            x1 = min(gray.shape[1] - 1, x1 + 18)
            y0 = max(0, y0 - 10)
            y1 = min(gray.shape[0] - 1, y1 + 10)

            line_img = Image.fromarray(gray[y0 : y1 + 1, x0 : x1 + 1], mode="L")
            line_img = ImageOps.autocontrast(line_img)
            line_img = ImageEnhance.Contrast(line_img).enhance(1.8)
            if line_img.size[1] < 80:
                line_img = line_img.resize(
                    (line_img.size[0] * 2, line_img.size[1] * 2),
                    resample=self._resample_bicubic(),
                )

            best_line_text = ""
            best_line_conf = 0.0
            best_line_score = -1e9
            for psm in (7, 6):
                line_text, line_conf = self._tesseract_candidate(
                    image=line_img,
                    language_expr=language_expr,
                    psm=psm,
                    smart_cleanup=smart_cleanup,
                )
                if not line_text:
                    continue
                line_score = self._score_candidate(
                    prediction=line_text,
                    confidence=line_conf,
                    language_codes=language_codes,
                    source=f"tesseract_lines_psm{psm}",
                )
                if line_score > best_line_score:
                    best_line_score = line_score
                    best_line_text = line_text
                    best_line_conf = line_conf

            if not best_line_text:
                continue

            quality = self._text_quality_score(best_line_text, language_codes)
            if quality < 0.22:
                continue
            line_results.append((best_line_text, best_line_conf, best_line_score))

        if not line_results:
            return "", 0.0

        combined = " ".join(row[0] for row in line_results).strip()
        if not combined:
            return "", 0.0

        expects_ascii = all(code in _ASCII_FRIENDLY_LANGS for code in language_codes) if language_codes else False
        if expects_ascii:
            combined = re.sub(r"\bTS\b", "IS", combined, flags=re.IGNORECASE)
            combined = re.sub(r"\bT[MN]\b", "IM", combined, flags=re.IGNORECASE)
            combined = re.sub(r"\bM[NH]\b", "MY", combined, flags=re.IGNORECASE)
            combined = re.sub(r"\bSTVOENT\b", "STUDENT", combined, flags=re.IGNORECASE)

        combined = self._cleanup_text(combined) if smart_cleanup else combined.strip()
        if not combined:
            return "", 0.0

        if expects_ascii:
            letters = [char for char in combined if char.isalpha() and char.isascii()]
            if letters:
                uppercase_ratio = sum(1 for char in letters if char.isupper()) / len(letters)
                if uppercase_ratio >= 0.64:
                    combined = combined.upper()

        confidence = float(sum(row[1] for row in line_results) / max(1, len(line_results)))
        confidence = max(0.0, min(0.99, confidence))
        return combined, confidence

    @staticmethod
    def _to_tensor(image: Image.Image):
        arr = np.asarray(image, dtype=np.float32) / 255.0
        arr = (arr - 0.5) / 0.5
        return torch.from_numpy(arr).unsqueeze(0)

    @staticmethod
    def _is_ascii_dominant(text: str) -> bool:
        payload = [char for char in text if not char.isspace()]
        if not payload:
            return True
        ascii_count = sum(1 for char in payload if ord(char) < 128)
        return (ascii_count / len(payload)) >= 0.85

    @staticmethod
    def _cleanup_text(text: str) -> str:
        cleaned = (text or "").replace("\u200b", "").replace("\ufeff", "")
        cleaned = cleaned.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return ""

        cleaned = cleaned.strip("\"'")
        cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
        cleaned = re.sub(r"([,.;:!?]){2,}", r"\1", cleaned)
        if not cleaned:
            return ""

        latin = sum(1 for char in cleaned if "a" <= char.lower() <= "z")
        non_ascii_alpha = sum(1 for char in cleaned if char.isalpha() and not char.isascii())
        if latin >= non_ascii_alpha * 2:
            cleaned = re.sub(r"(.)\1{3,}", r"\1\1", cleaned)
            cleaned = re.sub(r"(?<=[A-Za-z])0(?=[A-Za-z])", "o", cleaned)
            cleaned = re.sub(r"(?<=[A-Za-z])1(?=[A-Za-z])", "l", cleaned)

        return cleaned

    def _decode_single(
        self,
        log_probs_single: torch.Tensor,
        mode: str,
        beam_width: int,
        top_k: int,
        smart_cleanup: bool,
    ) -> tuple[str, float, list[dict[str, Any]]]:
        mode = mode.lower()
        if mode not in {"greedy", "beam"}:
            mode = "beam"

        if mode == "beam":
            from src.decoder import ctc_prefix_beam_search

            beams = ctc_prefix_beam_search(
                log_probs_single.cpu(),
                tokenizer=self._tokenizer,
                beam_width=beam_width,
                top_k=top_k,
            )
            alternatives = [
                {
                    "text": self._cleanup_text(text) if smart_cleanup else text,
                    "confidence": float(round(conf, 4)),
                }
                for text, conf in beams
            ]
            best_text = alternatives[0]["text"] if alternatives else ""
            best_conf = alternatives[0]["confidence"] if alternatives else 0.0

            if not best_text:
                best_non_empty = next((alt for alt in alternatives if alt["text"]), None)
                if best_non_empty and best_non_empty["confidence"] >= max(0.001, best_conf * 0.6):
                    best_text = best_non_empty["text"]
                    best_conf = best_non_empty["confidence"]

            return best_text, float(best_conf), alternatives

        from src.decoder import ctc_greedy_decode

        greedy = ctc_greedy_decode(log_probs_single.unsqueeze(1), self._tokenizer)
        text, conf = greedy[0] if greedy else ("", 0.0)
        text = self._cleanup_text(text) if smart_cleanup else text
        rounded = float(round(conf, 4))
        return text, rounded, [{"text": text, "confidence": rounded}]

    def _resolve_ocr_languages(self, requested: str | None) -> tuple[list[str], list[str]]:
        available = self._load_tesseract_languages()
        if not available:
            return [], []

        normalized = (requested or "auto").strip().lower()
        if normalized in {"", "auto"}:
            if "eng" in available:
                return ["eng"], []
            return [available[0]], []

        raw_codes = [part for part in re.split(r"[\s,+;]+", normalized) if part]
        if not raw_codes:
            if "eng" in available:
                return ["eng"], []
            return [available[0]], []

        if "all" in raw_codes:
            preferred = [code for code in available if code not in {"osd", "snum"}]
            return (preferred or available), []

        selected: list[str] = []
        missing: list[str] = []
        for code in raw_codes:
            if code in available:
                if code not in selected:
                    selected.append(code)
            elif code not in missing:
                missing.append(code)

        if not selected:
            selected = ["eng"] if "eng" in available else [available[0]]

        return selected, missing

    @staticmethod
    def _language_bonus(prediction: str, language_codes: list[str]) -> float:
        if not prediction:
            return -0.15
        if not language_codes:
            return 0.0

        expects_ascii = all(code in _ASCII_FRIENDLY_LANGS for code in language_codes)
        ascii_dominant = InferenceService._is_ascii_dominant(prediction)

        if expects_ascii:
            return 0.08 if ascii_dominant else -0.08
        return 0.08 if not ascii_dominant else -0.03

    @staticmethod
    def _text_quality_score(prediction: str, language_codes: list[str]) -> float:
        text = (prediction or "").strip()
        if not text:
            return 0.0

        payload = [char for char in text if not char.isspace()]
        if not payload:
            return 0.0

        alnum = [char for char in payload if char.isalnum()]
        alpha = [char for char in alnum if char.isalpha()]
        digits = [char for char in alnum if char.isdigit()]
        punctuation_count = max(0, len(payload) - len(alnum))
        token_count = len([token for token in re.split(r"\s+", text) if token])

        unique_ratio = 0.0
        if alnum:
            unique_ratio = len({char.lower() for char in alnum}) / len(alnum)

        longest_repeat = 1
        current_repeat = 1
        lowered = [char.lower() for char in alnum]
        for idx in range(1, len(lowered)):
            if lowered[idx] == lowered[idx - 1]:
                current_repeat += 1
                longest_repeat = max(longest_repeat, current_repeat)
            else:
                current_repeat = 1

        score = 0.34
        score += min(0.22, len(alnum) * 0.022)
        score += min(0.16, max(0, token_count - 1) * 0.05)
        score += min(0.14, unique_ratio * 0.14)
        score -= min(0.42, (punctuation_count / max(1, len(payload))) * 0.9)

        if len(payload) <= 1:
            score -= 0.62
        if len(alnum) <= 1:
            score -= 0.48
        if longest_repeat >= 4:
            score -= min(0.35, (longest_repeat - 3) * 0.08)

        confusion_set = {"o", "0", "q", "g", "9", "6"}
        if len(alnum) >= 5:
            confusion_ratio = sum(1 for char in lowered if char in confusion_set) / len(alnum)
            if confusion_ratio > 0.7:
                score -= min(0.35, 0.16 + (confusion_ratio - 0.7) * 1.2)

        expects_ascii = all(code in _ASCII_FRIENDLY_LANGS for code in language_codes) if language_codes else False
        if expects_ascii:
            ascii_letters = [char.lower() for char in alpha if char.isascii()]
            if len(ascii_letters) >= 4:
                vowels = sum(1 for char in ascii_letters if char in "aeiou")
                vowel_ratio = vowels / len(ascii_letters)
                if vowel_ratio < 0.14:
                    score -= 0.18

            if alpha and digits:
                digit_ratio = len(digits) / max(1, len(alnum))
                if digit_ratio > 0.45:
                    score -= 0.14

            if re.fullmatch(r"[o0qg96]+", "".join(lowered)):
                score -= 0.45

        return float(max(0.0, min(1.0, score)))

    @staticmethod
    def _score_candidate(prediction: str, confidence: float, language_codes: list[str], source: str) -> float:
        conf = max(0.0, min(1.0, float(confidence)))
        length_bonus = min(0.16, len(prediction) * 0.01)
        word_bonus = min(0.14, max(0, len(prediction.split()) - 1) * 0.03)
        symbol_count = sum(
            1
            for char in prediction
            if not char.isalnum() and not char.isspace() and char not in ".,;:!?-'\"/()[]{}"
        )
        symbol_penalty = min(0.18, (symbol_count / max(1, len(prediction))) * 0.4)
        repeat_penalty = 0.08 if re.search(r"(.)\1{4,}", prediction) else 0.0
        quality_score = InferenceService._text_quality_score(prediction, language_codes)
        quality_bonus = (quality_score - 0.5) * 0.52
        source_bonus = 0.02 if (source.startswith("tesseract") and quality_score >= 0.55) else 0.0

        return (
            conf
            + length_bonus
            + word_bonus
            + source_bonus
            + quality_bonus
            + InferenceService._language_bonus(prediction, language_codes)
            - symbol_penalty
            - repeat_penalty
        )

    def _tesseract_candidate(
        self,
        image: Image.Image,
        language_expr: str,
        psm: int,
        smart_cleanup: bool,
    ) -> tuple[str, float]:
        if pytesseract is None:
            return "", 0.0

        base_config = self._tesseract_base_config()
        config = f"{base_config} --oem 1 --psm {psm}".strip()
        text = ""
        confidence = 0.0

        try:
            data = pytesseract.image_to_data(image, lang=language_expr, config=config, output_type=pytesseract.Output.DICT)
            tokens = []
            conf_values = []
            for token, conf_raw in zip(data.get("text", []), data.get("conf", [])):
                clean_token = (token or "").strip()
                if clean_token:
                    tokens.append(clean_token)
                try:
                    conf_val = float(conf_raw)
                except (TypeError, ValueError):
                    conf_val = -1.0
                if clean_token and conf_val >= 0:
                    conf_values.append(conf_val)

            text = " ".join(tokens).strip()
            if conf_values:
                confidence = float(sum(conf_values) / (len(conf_values) * 100.0))

            if not text:
                text = (pytesseract.image_to_string(image, lang=language_expr, config=config) or "").strip()

            if smart_cleanup:
                text = self._cleanup_text(text)

            if text and confidence <= 0.0:
                confidence = min(0.94, 0.42 + len(text) * 0.018)

            return text, max(0.0, min(0.99, confidence))
        except Exception:
            return "", 0.0

    def _build_variants(self, config: PreprocessConfig) -> list[PreprocessConfig]:
        variants = [config]
        if not config.handwriting_boost:
            return variants

        variants.append(
            replace(
                config,
                denoise=True,
                contrast_boost=max(1.7, config.contrast_boost),
                sharpen=max(0.5, config.sharpen),
            )
        )
        variants.append(
            replace(
                config,
                adaptive_threshold=True,
                threshold=0,
                contrast_boost=max(1.5, config.contrast_boost),
            )
        )
        variants.append(
            replace(
                config,
                invert_colors=not config.invert_colors,
                denoise=True,
            )
        )
        return variants

    @staticmethod
    def _is_likely_notebook_page(image: Image.Image) -> bool:
        width, height = image.size
        if width <= 0 or height <= 0:
            return False

        # Typical phone photo of notebook page or broad handwritten notes.
        return bool(
            (width >= 700 and height >= 500)
            or (height >= 900 and width >= 500)
            or (width >= 1200 and height >= 380)
        )

    def predict_images(
        self,
        named_images: list[tuple[str, Image.Image]],
        decode_mode: str,
        beam_width: int,
        top_k: int,
        preprocess: PreprocessConfig,
        ocr_engine: str = "auto",
        ocr_languages: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        engine = (ocr_engine or "auto").strip().lower()
        if engine not in _VALID_ENGINES:
            engine = "auto"

        available_languages = self._load_tesseract_languages()
        selected_languages, missing_languages = self._resolve_ocr_languages(ocr_languages)
        language_expr = "+".join(selected_languages) if selected_languages else "eng"
        requested_non_ascii_lang = any(code not in _ASCII_FRIENDLY_LANGS for code in selected_languages)

        wants_rnn = engine in {"auto", "hybrid", "rnn"}
        wants_tesseract = engine in {"auto", "hybrid", "tesseract"}

        rnn_ready = False
        rnn_error = ""
        if wants_rnn:
            try:
                self.ensure_loaded()
                rnn_ready = True
            except Exception as exc:
                rnn_error = str(exc)
                if engine == "rnn":
                    raise

        tesseract_ready = wants_tesseract and pytesseract is not None and bool(available_languages)
        if engine == "tesseract" and not tesseract_ready:
            raise RuntimeError("Tesseract OCR is unavailable or language packs are not installed.")

        if engine == "auto" and requested_non_ascii_lang and tesseract_ready:
            rnn_ready = False

        if not rnn_ready and not tesseract_ready:
            if rnn_error:
                raise RuntimeError(rnn_error)
            raise RuntimeError("No OCR engine is available for prediction.")

        warnings: list[str] = []
        if missing_languages:
            warnings.append(f"Missing OCR language packs: {', '.join(missing_languages)}")
        if wants_tesseract and not tesseract_ready:
            warnings.append("Tesseract OCR unavailable; using RNN only.")
        if wants_rnn and not rnn_ready:
            warnings.append("RNN unavailable for this request; using Tesseract.")

        variants = self._build_variants(preprocess)
        outputs: list[dict[str, Any]] = []

        for name, image in named_images:
            notebook_like = self._is_likely_notebook_page(image)
            best_result: dict[str, Any] | None = None
            best_score = -1e9
            pass_index = 0
            notebook_line_candidate: dict[str, Any] | None = None
            notebook_line_checked = False
            image_variants = list(variants)

            # For large notebook pages, auto-try a notebook-optimized variant
            # even if the user didn't explicitly enable notebook mode.
            if notebook_like and not preprocess.student_notebook_mode:
                image_variants.append(
                    replace(
                        preprocess,
                        student_notebook_mode=True,
                        remove_notebook_lines=True,
                        denoise=True,
                        adaptive_threshold=True,
                        contrast_boost=max(1.8, preprocess.contrast_boost),
                        handwriting_boost=False,
                    )
                )

            for cfg in image_variants:
                pass_index += 1
                candidates: list[dict[str, Any]] = []

                if rnn_ready:
                    rnn_image = self._preprocess_for_rnn(image, cfg)
                    batch = self._to_tensor(rnn_image).unsqueeze(0).to(self._device)

                    with torch.no_grad():
                        logits = self._model(batch)
                        log_probs = F.log_softmax(logits, dim=2)

                    pred, conf, alts = self._decode_single(
                        log_probs_single=log_probs[:, 0, :],
                        mode=decode_mode,
                        beam_width=beam_width,
                        top_k=top_k,
                        smart_cleanup=preprocess.smart_text_cleanup,
                    )

                    if pred:
                        candidates.append({"text": pred, "confidence": conf, "source": "rnn"})

                    for alt in alts:
                        alt_text = (alt.get("text") or "").strip()
                        if not alt_text:
                            continue
                        candidates.append(
                            {
                                "text": alt_text,
                                "confidence": float(alt.get("confidence") or 0.0),
                                "source": "rnn_alt",
                            }
                        )

                if tesseract_ready:
                    use_notebook_lines = cfg.student_notebook_mode or notebook_like
                    if use_notebook_lines:
                        if not notebook_line_checked:
                            notebook_line_checked = True
                            line_text, line_conf = self._tesseract_notebook_lines_candidate(
                                image=image,
                                language_expr=language_expr,
                                language_codes=selected_languages,
                                smart_cleanup=preprocess.smart_text_cleanup,
                            )
                            if line_text:
                                notebook_line_candidate = {
                                    "text": line_text,
                                    "confidence": line_conf,
                                    "source": "tesseract_lines",
                                }

                        if notebook_line_candidate:
                            candidates.append(dict(notebook_line_candidate))

                    tess_image = self._preprocess_for_tesseract(image, cfg)
                    img_w, img_h = tess_image.size
                    single_line_hint = img_w >= max(1, img_h * 5) and img_h < 280
                    if preprocess.student_notebook_mode:
                        psm_modes = [11, 6, 4]
                        if single_line_hint:
                            psm_modes.insert(0, 7)
                    elif single_line_hint:
                        psm_modes = [7, 6, 8]
                    else:
                        psm_modes = [6, 11, 4]

                    for psm in psm_modes:
                        tess_text, tess_conf = self._tesseract_candidate(
                            image=tess_image,
                            language_expr=language_expr,
                            psm=psm,
                            smart_cleanup=preprocess.smart_text_cleanup,
                        )
                        if tess_text:
                            candidates.append(
                                {
                                    "text": tess_text,
                                    "confidence": tess_conf,
                                    "source": f"tesseract_psm{psm}",
                                }
                            )

                if not candidates:
                    continue

                for candidate in candidates:
                    engine_bias = 0.0
                    if engine == "rnn" and candidate["source"].startswith("rnn"):
                        engine_bias = 0.08
                    elif engine == "tesseract" and candidate["source"].startswith("tesseract"):
                        engine_bias = 0.08
                    elif engine == "auto" and requested_non_ascii_lang and candidate["source"].startswith("tesseract"):
                        engine_bias = 0.06

                    if notebook_like:
                        if candidate["source"] == "tesseract_lines":
                            engine_bias += 0.14
                        elif candidate["source"].startswith("tesseract"):
                            engine_bias += 0.06
                        elif candidate["source"].startswith("rnn"):
                            engine_bias -= 0.12

                    candidate["score"] = self._score_candidate(
                        prediction=candidate["text"],
                        confidence=float(candidate["confidence"] or 0.0),
                        language_codes=selected_languages,
                        source=str(candidate["source"]),
                    ) + engine_bias
                    candidate["quality"] = self._text_quality_score(
                        prediction=candidate["text"],
                        language_codes=selected_languages,
                    )

                candidates.sort(
                    key=lambda row: (
                        float(row.get("score") or 0.0),
                        float(row.get("quality") or 0.0),
                        float(row.get("confidence") or 0.0),
                    ),
                    reverse=True,
                )
                local_best = candidates[0]
                local_best_score = float(local_best.get("score") or 0.0)
                local_best_quality = float(local_best.get("quality") or 0.0)
                if local_best_quality < 0.35:
                    for fallback in candidates[1:]:
                        fallback_score = float(fallback.get("score") or 0.0)
                        fallback_quality = float(fallback.get("quality") or 0.0)
                        if fallback_quality >= 0.48 and fallback_score >= local_best_score - 0.22:
                            local_best = fallback
                            break

                alternatives: list[dict[str, Any]] = []
                seen_texts: set[str] = set()
                for candidate in candidates:
                    text_value = (candidate.get("text") or "").strip()
                    if not text_value or text_value in seen_texts:
                        continue
                    seen_texts.add(text_value)
                    alternatives.append(
                        {
                            "text": text_value,
                            "confidence": float(round(float(candidate.get("confidence") or 0.0), 4)),
                            "source": candidate.get("source") or "unknown",
                        }
                    )
                    if len(alternatives) >= max(1, top_k):
                        break

                result = {
                    "file": name,
                    "prediction": local_best["text"],
                    "confidence": float(round(float(local_best.get("confidence") or 0.0), 4)),
                    "alternatives": alternatives,
                    "pass_used": pass_index,
                    "source": local_best.get("source") or "unknown",
                }

                local_score = float(local_best.get("score") or 0.0)
                if local_score > best_score:
                    best_result = result
                    best_score = local_score

                best_source = str(local_best.get("source") or "")
                best_quality = float(local_best.get("quality") or 0.0)
                best_conf = float(local_best.get("confidence") or 0.0)
                if best_source == "tesseract_lines" and best_quality >= 0.6 and best_conf >= 0.55:
                    # High-quality notebook line aggregation found; no need for more passes.
                    break
                if notebook_like and best_source.startswith("tesseract") and best_quality >= 0.72 and best_conf >= 0.62:
                    # For large notebook pages, stop once we have a high-quality OCR hit.
                    break

            if best_result is None:
                best_result = {
                    "file": name,
                    "prediction": "",
                    "confidence": 0.0,
                    "alternatives": [],
                    "pass_used": 0,
                    "source": "none",
                }

            if warnings:
                best_result["warnings"] = list(warnings)

            outputs.append(best_result)

        meta = {
            "ocr_engine": engine,
            "ocr_languages_requested": (ocr_languages or "auto").strip() or "auto",
            "ocr_languages_used": selected_languages,
            "unsupported_ocr_languages": missing_languages,
            "available_ocr_languages": available_languages,
            "warnings": warnings,
        }
        return outputs, meta
