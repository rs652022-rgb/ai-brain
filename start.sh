#!/usr/bin/env bash
# Start the AI Brain on Mac or Linux.
cd "$(dirname "$0")"
if [ ! -f .venv/setup-complete.txt ]; then
  echo "Setting up the AI Brain (first start, 2-3 minutes)..."
  rm -rf .venv
  python3 -m venv .venv || { echo "Python 3.10+ is required: https://www.python.org/downloads/"; exit 1; }
  .venv/bin/python -m pip install --upgrade pip
fi
if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installing updates..."
  .venv/bin/python -m pip install --no-cache-dir -r requirements.txt || { echo "Setup did not finish. Check your internet and run ./start.sh again."; exit 1; }
  cp requirements.txt .venv/requirements.installed
  echo done > .venv/setup-complete.txt
fi
[ -f .env ] || cp .env.example .env
echo "Starting... add a free AI key in the Settings tab if you haven't yet."
.venv/bin/python run.py
