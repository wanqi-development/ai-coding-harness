@echo off
rem Shared configuration and dependency discovery for the Windows launchers.
rem Do not hard-code developer-specific Python, Node, or npm paths here.

for %%I in ("%~dp0..\..") do set "REPO_ROOT=%%~fI"
set "COMMON_DIR=%~dp0"

rem Restore the standard Windows command search path if a GUI runner started us
rem with a stripped environment. Existing entries are preserved.
if not defined SystemRoot set "SystemRoot=C:\Windows"
if not defined HARNESS_RUNTIME_PATH_READY (
  set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0\;%PATH%"
  set "PATHEXT=.COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"
  set "HARNESS_RUNTIME_PATH_READY=1"
)

rem Python has one project-local owner: the repository .venv. Never fall back to
rem conda or a global interpreter, because they may contain a different dependency set.
set "PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"

if not defined BACKEND_HOST set "BACKEND_HOST=127.0.0.1"
if not defined BACKEND_PORT set "BACKEND_PORT=8000"
if not defined FRONTEND_PORT set "FRONTEND_PORT=5173"
set "FRONTEND_URL=http://localhost:%FRONTEND_PORT%"
if not defined CURL set "CURL=%SystemRoot%\System32\curl.exe"
if not defined PING set "PING=%SystemRoot%\System32\ping.exe"
if not defined BACKEND_TRIES set "BACKEND_TRIES=30"
if not defined FRONTEND_TRIES set "FRONTEND_TRIES=15"

if not defined HARNESS_SRC_ON_PATH (
  if defined PYTHONPATH (set "PYTHONPATH=%REPO_ROOT%\src;%PYTHONPATH%") else set "PYTHONPATH=%REPO_ROOT%\src"
  set "HARNESS_SRC_ON_PATH=1"
)

if "%~1"=="" exit /b 0
if /i "%~1"=="check-port" goto :check_port
if /i "%~1"=="wait-http" goto :wait_http
if /i "%~1"=="preflight-backend" goto :preflight_backend
if /i "%~1"=="preflight-frontend" goto :preflight_frontend
if /i "%~1"=="preflight-runtime" goto :preflight_runtime

echo [ERROR] Unknown _common.bat action: %~1
exit /b 1

:preflight_backend
if not exist "%REPO_ROOT%\.env" (
  echo [WARN] .env was not found. Copy .env.example and fill in the runtime settings.
)
call :check_python
if errorlevel 1 exit /b 1
where git >nul 2>&1
if errorlevel 1 (
  echo [WARN] git was not found. The Code panel will be unavailable.
)
exit /b 0

:preflight_frontend
call :check_node
if errorlevel 1 exit /b 1
if not exist "%REPO_ROOT%\frontend\node_modules" (
  echo [ERROR] frontend dependencies are missing.
  echo         Run: npm --prefix "%REPO_ROOT%\frontend" install
  exit /b 1
)
exit /b 0

:preflight_runtime
call :check_python
if errorlevel 1 exit /b 1
call :check_node
if errorlevel 1 exit /b 1
echo [OK] Python: "%PYTHON%"
exit /b 0

:check_python
if not exist "%PYTHON%" (
  echo [ERROR] The project virtual environment was not found.
  echo         Expected: "%PYTHON%"
  echo         Create it with: uv sync --extra dev
  exit /b 1
)
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.14 cannot run: "%PYTHON%"
  echo         Recreate .venv with Python 3.14.
  exit /b 1
)
"%PYTHON%" -c "import ssl" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] The project Python cannot load its SSL module: "%PYTHON%"
  echo         The interpreter build is broken. Recreate .venv with uv sync --extra dev.
  exit /b 1
)
cd /d "%REPO_ROOT%"
"%PYTHON%" -c "import ai_coding_harness.main" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Project dependencies are not installed for: "%PYTHON%"
  echo         Run: uv sync --extra dev
  exit /b 1
)
exit /b 0

:check_node
where node.exe >nul 2>&1
if errorlevel 1 (
  echo [ERROR] node was not found on PATH.
  echo         Install Node.js 20+ and make node available on PATH.
  exit /b 1
)
node --version
if errorlevel 1 (
  echo [ERROR] node cannot run.
  exit /b 1
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm.cmd was not found on PATH.
  echo         Install Node.js 20+ with npm.
  exit /b 1
)
call npm.cmd --version
if errorlevel 1 (
  echo [ERROR] npm cannot run.
  exit /b 1
)
exit /b 0

:check_port
rem %2 = port, %3 = display name. Return 0 to continue, 1 to cancel.
set "port=%~2"
set "name=%~3"
set "found="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":!port! " ^| findstr LISTENING') do (
  if not defined found set "found=%%p"
)
if not defined found exit /b 0
echo [WARN] Port !port! (!name!) is already used by PID !found!.
set /p "ans=       Stop that process and continue? (y/N) "
if /i "!ans!"=="y" (
  taskkill /F /PID !found! >nul 2>&1
  echo       Stopped process !found!.
  "%PING%" -n 2 127.0.0.1 >nul
  exit /b 0
)
echo Startup cancelled.
pause
exit /b 1

:wait_http
rem %2=host %3=port %4=path %5=maximum probes. Return 0 when ready.
set "host=%~2"
set "port=%~3"
set "path=%~4"
set "tries=%~5"
set /a "waited=0"
:wait_http_loop
"%CURL%" -s -f --connect-timeout 1 --max-time 2 -o nul "http://!host!:!port!!path!" >nul 2>&1
if not errorlevel 1 goto :wait_http_ok
if !waited! geq !tries! goto :wait_http_timeout
<nul set /p "=."
"%PING%" -n 2 127.0.0.1 >nul
set /a "waited+=1"
goto :wait_http_loop

:wait_http_ok
if !waited! gtr 0 echo.
exit /b 0

:wait_http_timeout
echo.
exit /b 1
