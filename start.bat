@echo off
REM Start the AI Brain on Windows.
cd /d "%~dp0"

if not exist .venv\setup-complete.txt goto setup
REM Already set up: install anything new an update added to requirements.txt
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 goto update
goto run

:setup
echo.
echo  Setting up the AI Brain (first start, 2-3 minutes)...
echo.
if exist .venv rmdir /s /q .venv
python -m venv .venv
if errorlevel 1 (
  echo.
  echo  Python 3.10 or newer is required: https://www.python.org/downloads/
  echo  During install, tick "Add Python to PATH".
  pause
  exit /b 1
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip cache purge >nul 2>&1

:update
echo.
echo  Installing updates...
echo.
.venv\Scripts\python -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 (
  echo.
  echo  Setup did not finish. Check your internet connection, then double-click start again.
  echo  If it fails again, send a screenshot of this window.
  pause
  exit /b 1
)
copy /y requirements.txt .venv\requirements.installed >nul
echo done> .venv\setup-complete.txt

:run
if not exist .env copy .env.example .env >nul
echo.
echo  Starting... add a free AI key in the Settings tab if you have not yet.
echo  Keep this window open while you use the Brain.
echo.
.venv\Scripts\python run.py
pause
