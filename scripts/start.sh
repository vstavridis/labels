#!/usr/bin/env bash
# VS Production Manager - start the Streamlit app (macOS / Linux).
#   bash scripts/start.sh            -> http://localhost:8501 (this computer only)
#   VSPM_HOST=0.0.0.0 bash scripts/start.sh   -> reachable from other computers on the network
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "Not installed yet: run bash scripts/setup.sh"; exit 1; }
PORT="${VSPM_PORT:-8501}"
HOST="${VSPM_HOST:-localhost}"
( sleep 3; command -v open >/dev/null && open "http://localhost:$PORT" || xdg-open "http://localhost:$PORT" ) >/dev/null 2>&1 &
exec .venv/bin/python -m streamlit run src/vspm/ui/app.py --server.port "$PORT" --server.address "$HOST" \
  --server.headless true --browser.gatherUsageStats false
