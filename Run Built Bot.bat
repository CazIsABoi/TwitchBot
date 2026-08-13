@echo off
cd /d "%~dp0dist\TwitchBot"
if not exist TwitchBot.exe (
  echo Build the bot first with build_exe.bat
  pause
  exit /b 1
)
echo Starting TwitchBot from dist...
TwitchBot.exe
echo.
echo Exit code: %ERRORLEVEL%
if exist crash.log (
  echo ----- crash.log -----
  type crash.log
)
pause
