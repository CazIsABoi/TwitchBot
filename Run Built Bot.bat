@echo off
cd /d "%~dp0dist\CazIsABot"
if not exist CazIsABot.exe (
  echo Build the bot first with build_exe.bat
  pause
  exit /b 1
)
echo Starting CazIsABot from dist...
CazIsABot.exe
echo.
echo Exit code: %ERRORLEVEL%
if exist crash.log (
  echo ----- crash.log -----
  type crash.log
)
pause
