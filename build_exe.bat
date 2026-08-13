@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title Build TwitchBot.exe (Python 3.12)
echo ============================================
echo   Build TwitchBot.exe
echo   Requires: Python 3.12 venv
echo ============================================
echo.

REM Prefer explicit 3.12 launcher
set "PYEXE="
py -3.12 -c "import sys; print(sys.version)" 2>nul
if not errorlevel 1 set "PYEXE=py -3.12"

if not defined PYEXE (
  python -c "import sys; assert sys.version_info[:2]==(3,12)" 2>nul
  if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
  echo [ERROR] Python 3.12 not found.
  echo Install Python 3.12 and run:
  echo   py -3.12 -m venv venv
  pause
  exit /b 1
)

echo Using: %PYEXE%
%PYEXE% -c "import sys; print('Python', sys.version)"

if not exist "venv\Scripts\python.exe" (
  echo Creating venv with Python 3.12...
  %PYEXE% -m venv venv
  if errorlevel 1 (
    echo [ERROR] venv create failed
    pause
    exit /b 1
  )
)

call "venv\Scripts\activate.bat"

python -c "import sys; print('venv Python', sys.version); raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)"
if errorlevel 1 (
  echo [ERROR] venv is not Python 3.12.
  echo Delete the venv folder and recreate:
  echo   rmdir /s /q venv
  echo   py -3.12 -m venv venv
  pause
  exit /b 1
)

python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo [ERROR] pip install failed
  pause
  exit /b 1
)

if not exist ".env" if exist "env" copy /Y "env" ".env" >nul
if not exist ".env" (
  echo [ERROR] .env missing
  pause
  exit /b 1
)
if not exist "launcher.py" (
  echo [ERROR] launcher.py missing - copy it into this folder
  pause
  exit /b 1
)

REM Prove audio_handler no longer imports just_playback at module top
python -c "import pathlib; t=pathlib.Path('audio_handler.py').read_text(encoding='utf-8').splitlines(); print('audio_handler line5:', t[4] if len(t)>4 else '?'); assert 'just_playback' not in t[4], 'OLD audio_handler.py — still imports just_playback on line 5. Copy the updated file.'"

echo.
echo Finding _cffi_backend...
for /f "delims=" %%i in ('python -c "import _cffi_backend, pathlib; print(pathlib.Path(_cffi_backend.__file__).resolve())"') do set "CFFI_PYD=%%i"
echo Found: %CFFI_PYD%
for /f "delims=" %%i in ('python -c "import pathlib; print(pathlib.Path(r'''%CFFI_PYD%''').name)"') do set "CFFI_NAME=%%i"

echo Cleaning old build + pycache...
if exist "build" rmdir /s /q build
if exist "dist" rmdir /s /q dist
if exist "TwitchBot.spec" del /q "TwitchBot.spec"
for /d /r %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"

set "ADD_DATA=--add-data rewards_config.json;."
if exist "ignored_chatters.json" set "ADD_DATA=%ADD_DATA% --add-data ignored_chatters.json;."
if exist "blocked_terms_dont_open_on_stream.json" set "ADD_DATA=%ADD_DATA% --add-data blocked_terms_dont_open_on_stream.json;."

echo.
echo Building entry point: launcher.py
pyinstaller --noconfirm --clean ^
  --name TwitchBot ^
  --onedir ^
  --console ^
  %ADD_DATA% ^
  --add-binary "%CFFI_PYD%;." ^
  --hidden-import _cffi_backend ^
  --hidden-import cffi ^
  --hidden-import edge_tts ^
  --hidden-import just_playback ^
  --hidden-import just_playback.playback ^
  --hidden-import pynput ^
  --hidden-import pynput.keyboard._win32 ^
  --hidden-import obswebsocket ^
  --hidden-import dotenv ^
  --collect-all cffi ^
  --collect-all just_playback ^
  --hidden-import setup_wizard ^
  --hidden-import rewards ^
  --hidden-import reward_queue ^
  --hidden-import audio_handler ^
  --hidden-import image_handler ^
  --hidden-import tts_handler ^
  --hidden-import obs_handler ^
  --hidden-import config ^
  --hidden-import twitch ^
  launcher.py

if errorlevel 1 (
  echo [ERROR] PyInstaller failed
  pause
  exit /b 1
)

copy /Y ".env" "dist\TwitchBot\.env" >nul
if exist "sounds" xcopy /E /I /Y "sounds" "dist\TwitchBot\sounds" >nul
if not exist "dist\TwitchBot\sounds" mkdir "dist\TwitchBot\sounds"
if exist "images" xcopy /E /I /Y "images" "dist\TwitchBot\images" >nul
copy /Y "rewards_config.json" "dist\TwitchBot\rewards_config.json" >nul
if exist "ignored_chatters.json" copy /Y "ignored_chatters.json" "dist\TwitchBot\ignored_chatters.json" >nul
if exist "blocked_terms_dont_open_on_stream.json" copy /Y "blocked_terms_dont_open_on_stream.json" "dist\TwitchBot\blocked_terms_dont_open_on_stream.json" >nul

copy /Y "%CFFI_PYD%" "dist\TwitchBot\%CFFI_NAME%" >nul
if exist "dist\TwitchBot\_internal" copy /Y "%CFFI_PYD%" "dist\TwitchBot\_internal\%CFFI_NAME%" >nul

(
echo @echo off
echo cd /d "%%~dp0"
echo TwitchBot.exe
echo echo Exit code: %%ERRORLEVEL%%
echo if exist crash.log type crash.log
echo pause
) > "dist\TwitchBot\Run TwitchBot.bat"

echo.
echo ============================================
echo Verify:
findstr /C:"just_playback" "audio_handler.py" | findstr /V "Lazy\|just_playback\|from just"
echo Entry should be launcher - check dist folder.
dir /b "dist\TwitchBot\_internal\_cffi*" 2>nul
echo.
echo Run: dist\TwitchBot\Run TwitchBot.bat
pause
