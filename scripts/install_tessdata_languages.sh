#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${OCR_TESSDATA_DIR:-$ROOT_DIR/data/tessdata}"
BASE_URL="${TESSDATA_BASE_URL:-https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main}"
FORCE=0

if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
  shift
fi

if [[ $# -gt 0 ]]; then
  LANGS=("$@")
else
  LANGS=(
    eng osd
    hin ben mar guj tam tel kan mal pan urd
    fra deu spa ita por nld rus ukr
    ara heb ell tur
    jpn kor chi_sim chi_tra
  )
fi

mkdir -p "$OUT_DIR"

echo "Installing Tesseract language data to: $OUT_DIR"
echo "Source: $BASE_URL"

for lang in "${LANGS[@]}"; do
  target="$OUT_DIR/${lang}.traineddata"
  if [[ -f "$target" && "$FORCE" -ne 1 ]]; then
    echo "- $lang: already present (skip)"
    continue
  fi

  url="$BASE_URL/${lang}.traineddata"
  echo "- $lang: downloading"
  curl -fL "$url" -o "$target"
done

echo
echo "Done."
echo "To run portal with these languages:"
echo "  OCR_TESSDATA_DIR=$OUT_DIR ./run_portal.sh"
