#!/usr/bin/env bash
set -euo pipefail

DB_PATH="data/app.db"
INTERVAL=2
MODE="snapshot"
LIMIT=10
TABLE="detection_history"
ONCE=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/watch_sqlite.sh [options]

Options:
  --db <path>         SQLite db path (default: data/app.db)
  --interval <sec>    Refresh interval seconds (default: 2)
  --limit <n>         Number of latest rows (default: 10)
  --table <name>      Table to watch in snapshot mode (default: detection_history)
  --stream            Stream only new rows from detection_history
  --once              Print one snapshot and exit
  -h, --help          Show help

Examples:
  scripts/watch_sqlite.sh
  scripts/watch_sqlite.sh --table users --limit 20
  scripts/watch_sqlite.sh --stream
  scripts/watch_sqlite.sh --stream --db data/app.db
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      DB_PATH="${2:-}"
      shift 2
      ;;
    --interval)
      INTERVAL="${2:-2}"
      shift 2
      ;;
    --limit)
      LIMIT="${2:-10}"
      shift 2
      ;;
    --table)
      TABLE="${2:-detection_history}"
      shift 2
      ;;
    --stream)
      MODE="stream"
      shift
      ;;
    --once)
      ONCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f "$DB_PATH" ]]; then
  echo "Database not found: $DB_PATH" >&2
  exit 1
fi

if [[ "$MODE" == "stream" ]]; then
  echo "Watching new rows from detection_history in $DB_PATH (Ctrl+C to stop)"
  LAST_ID="$(sqlite3 "$DB_PATH" "SELECT COALESCE(MAX(id),0) FROM detection_history;")"
  LAST_ID="${LAST_ID:-0}"

  while true; do
    NEW_ROWS="$(sqlite3 -header -column "$DB_PATH" \
      "SELECT id, user_id, file_name, ROUND(confidence*100,1) || '%', created_at
       FROM detection_history
       WHERE id > $LAST_ID
       ORDER BY id ASC;")"

    if [[ -n "${NEW_ROWS//[[:space:]]/}" ]]; then
      echo
      echo "Time: $(date '+%F %T')"
      echo "$NEW_ROWS"
      MAX_ID="$(sqlite3 "$DB_PATH" "SELECT COALESCE(MAX(id),$LAST_ID) FROM detection_history;")"
      LAST_ID="${MAX_ID:-$LAST_ID}"
    fi
    sleep "$INTERVAL"
  done
else
  while true; do
    if [[ "$ONCE" -eq 0 ]] && [[ -t 1 ]] && command -v clear >/dev/null 2>&1; then
      clear
    fi
    echo "Time: $(date '+%F %T')"
    echo "DB: $DB_PATH"
    echo "Table: $TABLE"
    echo
    sqlite3 -header -column "$DB_PATH" "SELECT * FROM $TABLE ORDER BY id DESC LIMIT $LIMIT;"
    if [[ "$ONCE" -eq 1 ]]; then
      exit 0
    fi
    sleep "$INTERVAL"
  done
fi
