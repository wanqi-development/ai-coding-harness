@echo off
setlocal
if not defined SystemRoot set "SystemRoot=C:\Windows"
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "SUPERVISOR=%~dp0scripts\windows\start-dev.ps1"

if not exist "%POWERSHELL%" (
  echo [ERROR] Windows PowerShell was not found:
  echo         %POWERSHELL%
  if not defined HARNESS_NO_PAUSE pause
  exit /b 1
)

if /i "%~1"=="--check-env" (
  "%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%SUPERVISOR%" -CheckEnv
) else (
  "%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%SUPERVISOR%"
)
set "RC=%ERRORLEVEL%"

if not defined HARNESS_NO_PAUSE (
  echo.
  if not "%RC%"=="0" (
    echo Startup failed. Review the launcher log above before closing this window.
  ) else (
    echo Services are running. Use stop.bat when you want to stop them.
  )
  pause
)
exit /b %RC%
