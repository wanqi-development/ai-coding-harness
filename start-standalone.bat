@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass ^
  -File "%ROOT%scripts\windows\start-standalone.ps1" %*
set "CODE=%ERRORLEVEL%"

echo.
echo Standalone harness has stopped. Exit code: %CODE%
pause
exit /b %CODE%
