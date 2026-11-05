@echo off
REM Brain Companion for Windows: lets your Brain on the web use this computer.
cd /d "%~dp0"
if exist .venv\setup-complete.txt (
  fc /b requirements.txt .venv\requirements.installed >nul 2>&1
  if not errorlevel 1 goto run
)
echo.
echo  Setting up Brain Companion (first start, about 1 minute)...
python -m venv .venv
if errorlevel 1 (
  echo  Python 3.10 or newer is required: https://www.python.org/downloads/  (tick "Add Python to PATH")
  pause
  exit /b 1
)
.venv\Scripts\python -m pip install --upgrade pip >nul
.venv\Scripts\python -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 (
  echo  Setup did not finish. Check your internet connection and run this again.
  pause
  exit /b 1
)
copy /y requirements.txt .venv\requirements.installed >nul
echo done> .venv\setup-complete.txt
:run
title Brain Companion - close this window to stop
.venv\Scripts\python brain_companion.py %*
pause
