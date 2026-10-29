#!/usr/bin/env bash
# VS Production Manager - one-time setup on macOS or Linux.
#   bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[ai,google,dev]"
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example - edit it before going live."; }
mkdir -p data credentials reference_files
.venv/bin/vspm init
if ls reference_files/*.xlsx >/dev/null 2>&1; then
  .venv/bin/vspm make-test-workbook
  .venv/bin/vspm report
fi
.venv/bin/python -m pytest -q
echo "Done. Start the app with: bash scripts/start.sh"
