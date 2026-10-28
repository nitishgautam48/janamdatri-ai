#!/usr/bin/env bash
# Run Janamdatri-AI on this computer.
#   App + API:        http://localhost:8000
#   SMS inbox (OTPs): http://localhost:8025   (Mailpit, started automatically)
#
#   ./run_local.sh            core app (SQLite, no Redis needed); AI comes from the cloud keys in .env
#   WITH_AI=1 ./run_local.sh  also installs the optional local models (Whisper, PaddleOCR, Coqui TTS)
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python 3.11 or newer is required (found: $("$PY" --version 2>&1))."; exit 1; }
[ -f .env ] || cp .env.example .env
cd backend
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
if [ "${WITH_AI:-0}" = "1" ]; then
  # one package at a time, so a package that doesn't support this Python version doesn't block the others
  sed 's/#.*//' requirements-ai.txt | while read -r pkg; do
    [ -n "$pkg" ] && { python -m pip install -q "$pkg" || echo "  (skipped $pkg: not available for this Python/OS)"; }
  done
  python -m scripts.download_models || true
fi
alembic upgrade head
[ -f models/risk_gbm.joblib ] || python -m scripts.train_risk_model

# Mailpit: local inbox for every SMS (OTPs, ASHA and family alerts)
python -m scripts.mailpit path >/dev/null
python -m scripts.mailpit &
MAILPIT_PID=$!
trap 'kill "$MAILPIT_PID" 2>/dev/null || true' EXIT INT TERM

echo
echo "  Janamdatri-AI:        http://localhost:${PORT:-8000}   (app: /app.dc.html, API docs: /docs)"
echo "  SMS inbox (Mailpit):  http://localhost:8025   ← OTPs arrive here"
echo "  Ctrl+C to stop"
echo
uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
