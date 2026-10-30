@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
set "COMMON=%~dp0_common.bat"
call "%COMMON%"

if not defined HARNESS_SKIP_PORT_CHECK (
  call "%COMMON%" check-port "%FRONTEND_PORT%" "Ç°¶Ë"
  if errorlevel 1 exit /b 1
)
if not defined HARNESS_SKIP_PREFLIGHT (
  call "%COMMON%" preflight-frontend
  if errorlevel 1 exit /b 1
)

set "HARNESS_FRONTEND_PORT=%FRONTEND_PORT%"
set "HARNESS_BACKEND_URL=http://%BACKEND_HOST%:%BACKEND_PORT%"

cd /d "%REPO_ROOT%"
echo [Ç°¶Ë] %FRONTEND_URL%
npm --prefix frontend run dev
exit /b %errorlevel%
