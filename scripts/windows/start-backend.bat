@echo off
setlocal enabledelayedexpansion
set "COMMON=%~dp0_common.bat"
call "%COMMON%"

if not defined HARNESS_SKIP_PORT_CHECK (
  call "%COMMON%" check-port "%BACKEND_PORT%" "backend"
  if errorlevel 1 exit /b 1
)
if not defined HARNESS_SKIP_PREFLIGHT (
  call "%COMMON%" preflight-backend
  if errorlevel 1 exit /b 1
)

set "HOST=%BACKEND_HOST%"
set "PORT=%BACKEND_PORT%"

cd /d "%REPO_ROOT%"
echo [backend] %BACKEND_HOST%:%BACKEND_PORT%
echo           Python: %PYTHON%
"%PYTHON%" -m ai_coding_harness.main
exit /b %errorlevel%
