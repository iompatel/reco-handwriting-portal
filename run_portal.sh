#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -n "${PYTHON:-}" ]]; then
  PY_BIN="$PYTHON"
elif [[ -x ".venv313/bin/python" ]]; then
  PY_BIN=".venv313/bin/python"
elif [[ -x ".venv/bin/python" ]]; then
  PY_BIN=".venv/bin/python"
else
  PY_BIN="python3"
fi

HOST="${HOST:-localhost}"
PORT="${PORT:-5000}"
DB_PATH="${OCR_DB_PATH:-data/app.db}"
CHECKPOINT="${OCR_CHECKPOINT:-checkpoints/fix2/best.pt}"

echo "Starting portal with: $PY_BIN"
echo "Host: $HOST  Port: $PORT"
echo "DB: $DB_PATH"
echo "Checkpoint: $CHECKPOINT"

exec "$PY_BIN" app.py \
  --host "$HOST" \
  --port "$PORT" \
  --db-path "$DB_PATH" \
  --checkpoint "$CHECKPOINT"
