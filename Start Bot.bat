@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title TwitchBot
echo ============================================
echo   TwitchBot launcher
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found on PATH.
  echo Install Python 3.11+ from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
  )
)

call "venv\Scripts\activate.bat"

echo Installing / updating dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed.
  pause
  exit /b 1
)

echo.
echo Checking first-run setup...
python setup_wizard.py --if-needed
if errorlevel 1 (
  echo Setup did not finish. Run: python setup_wizard.py
  pause
  exit /b 1
)

echo.
echo Starting bot...
echo.
python twitch.py
echo.
echo Bot stopped.
pause
