#!/usr/bin/env bash
# Milestone A exit gate: start a fresh API and run scripts/demo.py against it.
#   ./scripts/demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
PORT="${DEMO_PORT:-8723}"
WORK="$(mktemp -d)"
trap 'kill $API_PID 2>/dev/null || true; rm -rf "$WORK"' EXIT

MARKWRIGHT_DB="$WORK/registry.sqlite" \
MARKWRIGHT_VAULT="$WORK/vault.json" \
MARKWRIGHT_VAULT_PASSPHRASE="demo-only" \
MARKWRIGHT_PROFILE=dev \
$PY -m uvicorn markwright.api.app:app --port "$PORT" --log-level warning &
API_PID=$!

for _ in $(seq 1 40); do
  curl -s "http://localhost:$PORT/healthz" >/dev/null && break
  sleep 0.25
done

MARKWRIGHT_URL="http://localhost:$PORT" $PY scripts/demo.py
