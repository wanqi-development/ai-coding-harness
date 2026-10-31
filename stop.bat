@echo off
setlocal
if not defined SystemRoot set "SystemRoot=C:\Windows"
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "STOPPER=%~dp0scripts\windows\stop-dev.ps1"

"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%STOPPER%"
set "RC=%ERRORLEVEL%"
if not defined HARNESS_NO_PAUSE pause
exit /b %RC%
