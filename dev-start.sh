#!/usr/bin/env bash
# Start Sourcecado (backend + GUI) against your dev state dir, so your
# contacts and tasks are always there. Run from the repo root:
#     ./dev-start.sh        (press Ctrl-C to stop both)
#
# Override the data location by setting CLUB_STATE_DIR before running.
set -euo pipefail

export CLUB_STATE_DIR="${CLUB_STATE_DIR:-$HOME/.local/state/sourcecado-dev}"
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "Sourcecado state dir: $CLUB_STATE_DIR"
mkdir -p "$CLUB_STATE_DIR"; chmod 700 "$CLUB_STATE_DIR"
[ -e "$CLUB_STATE_DIR/.env" ] || { cp "$ROOT/.env.example" "$CLUB_STATE_DIR/.env"; chmod 600 "$CLUB_STATE_DIR/.env"; }

echo "Starting backend on :8765 ..."
( cd "$ROOT/backend" && exec .venv/bin/python -m coworker.run ) &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 30); do
  [ -f "$CLUB_STATE_DIR/backend-8765.token" ] && break
  sleep 1
done

echo "Starting GUI on :5180 ... open http://127.0.0.1:5180"
cd "$ROOT/frontend" && npm run dev
