@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass ^
  -File "%ROOT%scripts\windows\stop-standalone.ps1" %*
set "CODE=%ERRORLEVEL%"

echo.
echo Standalone stop finished. Exit code: %CODE%
pause
exit /b %CODE%
