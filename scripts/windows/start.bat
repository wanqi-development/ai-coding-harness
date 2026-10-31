@echo off
setlocal enabledelayedexpansion
set "COMMON=%~dp0_common.bat"
call "%COMMON%"

call "%COMMON%" check-port "%BACKEND_PORT%" "backend"
if errorlevel 1 exit /b 1
call "%COMMON%" check-port "%FRONTEND_PORT%" "frontend"
if errorlevel 1 exit /b 1
call "%COMMON%" preflight-backend
if errorlevel 1 exit /b 1
call "%COMMON%" preflight-frontend
if errorlevel 1 exit /b 1

set "HARNESS_SKIP_PORT_CHECK=1"
set "HARNESS_SKIP_PREFLIGHT=1"

echo [1/3] Starting backend on %BACKEND_HOST%:%BACKEND_PORT% ...
start "harness-backend" cmd /k ""%~dp0start-backend.bat""

echo       Waiting for backend (%BACKEND_TRIES% probes) ...
call "%COMMON%" wait-http %BACKEND_HOST% %BACKEND_PORT% /health %BACKEND_TRIES%
if errorlevel 1 (
  echo [WARN] Backend did not become healthy. Check the harness-backend window.
)

echo [2/3] Starting frontend at %FRONTEND_URL% ...
start "harness-frontend" cmd /k ""%~dp0start-frontend.bat""

echo       Waiting for frontend (%FRONTEND_TRIES% probes) ...
call "%COMMON%" wait-http localhost %FRONTEND_PORT% / %FRONTEND_TRIES%
if errorlevel 1 (
  echo [WARN] Frontend did not become ready. Check the harness-frontend window.
)

echo [3/3] Opening browser ...
start "" %FRONTEND_URL%

echo.
echo Started. Close the service windows to stop.
exit /b 0
