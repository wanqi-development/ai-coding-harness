@echo off
setlocal enabledelayedexpansion
set "COMMON=%~dp0_common.bat"
call "%COMMON%"

if not defined HARNESS_SKIP_PORT_CHECK (
  call "%COMMON%" check-port "%FRONTEND_PORT%" "frontend"
  if errorlevel 1 exit /b 1
)
if not defined HARNESS_SKIP_PREFLIGHT (
  call "%COMMON%" preflight-frontend
  if errorlevel 1 exit /b 1
)

set "HARNESS_FRONTEND_PORT=%FRONTEND_PORT%"
set "HARNESS_BACKEND_URL=http://%BACKEND_HOST%:%BACKEND_PORT%"

cd /d "%REPO_ROOT%"
echo [frontend] %FRONTEND_URL%
call npm.cmd --prefix frontend run dev
exit /b %errorlevel%
