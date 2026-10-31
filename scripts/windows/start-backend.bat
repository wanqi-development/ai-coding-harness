@echo off
"%SystemRoot%\System32\chcp.com" 936 >nul
setlocal enabledelayedexpansion
set "COMMON=%~dp0_common.bat"
call "%COMMON%"

if not defined HARNESS_SKIP_PORT_CHECK (
  call "%COMMON%" check-port "%BACKEND_PORT%" "后端"
  if errorlevel 1 exit /b 1
)
if not defined HARNESS_SKIP_PREFLIGHT (
  call "%COMMON%" preflight-backend
  if errorlevel 1 exit /b 1
)

set "HOST=%BACKEND_HOST%"
set "PORT=%BACKEND_PORT%"

cd /d "%REPO_ROOT%"
echo [后端] %BACKEND_HOST%:%BACKEND_PORT%
echo        使用 %PYTHON%
"%PYTHON%" -m ai_coding_harness.main
exit /b %errorlevel%
