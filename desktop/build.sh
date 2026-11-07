#!/usr/bin/env bash
# Build the desktop app with PyInstaller (used by .github/workflows/desktop.yml; works locally too).
set -e
cd "$(dirname "$0")/.."
SEP=":"; [ "${OS:-}" = "Windows_NT" ] && SEP=";"
ICON=""
[ -f desktop/icon.ico ] && [ "${OS:-}" = "Windows_NT" ] && ICON="--icon desktop/icon.ico"
[ -f desktop/icon.icns ] && [ "$(uname)" = "Darwin" ] && ICON="--icon desktop/icon.icns"
pyinstaller --noconfirm --clean --windowed --name AIBrain $ICON --paths . \
  --add-data "web${SEP}web" --add-data "extension${SEP}extension" --add-data "companion${SEP}companion" \
  --add-data "brain/safety_core.md${SEP}brain" \
  --collect-submodules brain --collect-submodules uvicorn --collect-submodules websockets \
  --collect-submodules webview --collect-data webview \
  --hidden-import brain.server \
  --hidden-import pyautogui --hidden-import pyperclip \
  --osx-bundle-identifier com.aibrain.desktop \
  desktop/brain_desktop.py
