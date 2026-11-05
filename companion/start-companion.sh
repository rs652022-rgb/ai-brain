#!/usr/bin/env bash
# Brain Companion for macOS and Linux: lets your Brain on the web use this computer.
cd "$(dirname "$0")"
if [ ! -f .venv/setup-complete.txt ] || ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Setting up Brain Companion (first start, about 1 minute)..."
  python3 -m venv .venv || { echo "Python 3.10 or newer is required."; exit 1; }
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt || { echo "Setup did not finish."; exit 1; }
  cp requirements.txt .venv/requirements.installed
  echo done > .venv/setup-complete.txt
fi
exec .venv/bin/python brain_companion.py "$@"
