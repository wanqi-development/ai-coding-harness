@echo off
setlocal
if not defined SystemRoot set "SystemRoot=C:\Windows"
if /i "%~1"=="--check-env" goto :check_env

call "%~dp0scripts\windows\start.bat" %*
exit /b %errorlevel%

:check_env
call "%~dp0scripts\windows\_common.bat" preflight-runtime
exit /b %errorlevel%
