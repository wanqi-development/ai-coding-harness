@echo off
if not defined SystemRoot set "SystemRoot=C:\Windows"
set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"
rem Shared configuration and checks for the Windows launchers.
set "COMMON_DIR=%~dp0"
for %%I in ("%~dp0..\..") do set "REPO_ROOT=%%~fI"

rem ---- 可按需修改 ----
rem 项目运行环境 = conda 环境 aicoding；换环境时改这一行
set "PYTHON=D:\developmentBinary\miniconda3\envs\aicoding\python.exe"
if not defined BACKEND_HOST set "BACKEND_HOST=127.0.0.1"
if not defined BACKEND_PORT set "BACKEND_PORT=8000"
if not defined FRONTEND_PORT set "FRONTEND_PORT=5173"
if not defined FRONTEND_URL set "FRONTEND_URL=http://localhost:%FRONTEND_PORT%"
set "CURL=%SystemRoot%\System32\curl.exe"
set "PING=%SystemRoot%\System32\ping.exe"
if not defined BACKEND_TRIES set "BACKEND_TRIES=30"
if not defined FRONTEND_TRIES set "FRONTEND_TRIES=15"
set "PYTHONPATH=src"
if not defined NPM if exist "%ProgramFiles%\nodejs\npm.cmd" set "NPM=%ProgramFiles%\nodejs\npm.cmd"
if not defined NPM if exist "%ProgramFiles(x86)%\nodejs\npm.cmd" set "NPM=%ProgramFiles(x86)%\nodejs\npm.cmd"
if not defined NPM for /d %%D in ("D:\developmentBinary\node-*") do if not defined NPM if exist "%%~fD\npm.cmd" set "NPM=%%~fD\npm.cmd"
if not defined NPM for /f "delims=" %%I in ('where.exe npm.cmd 2^>nul') do if not defined NPM set "NPM=%%I"
if defined NPM for %%I in ("%NPM%") do set "PATH=%%~dpI;%PATH%"
rem --------------------

if "%~1"=="" exit /b 0
if /i "%~1"=="check-port" goto :check_port
if /i "%~1"=="wait-http" goto :wait_http
if /i "%~1"=="preflight-backend" goto :preflight_backend
if /i "%~1"=="preflight-frontend" goto :preflight_frontend

echo [错误] _common.bat 收到未知动作：%~1
exit /b 1

:preflight_backend
if not exist "%REPO_ROOT%\.env" (
  echo [警告] 未找到 .env，后端可能无法启动。请先由 .env.example 复制并填写配置。
)
"%PYTHON%" -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo [错误] Python 无法启动：%PYTHON%
  echo        项目运行环境是 conda 环境 aicoding（conda env list 查看）；换环境时改 _common.bat。
  exit /b 1
)
where.exe git >nul 2>&1
if errorlevel 1 (
  echo [警告] 未找到 git：右侧栏「代码」面板用不了（装 git，或设 REPO_VIEW_GIT 指定可执行文件）。
)
exit /b 0

:preflight_frontend
if not defined NPM (
  echo [错误] 未找到 npm，无法启动前端。
  exit /b 1
)
if not exist "%NPM%" (
  echo [错误] npm 路径无效：%NPM%
  exit /b 1
)
exit /b 0

:check_port
rem %2 = 端口, %3 = 名称；被占用则询问，返回 0=继续，1=取消
set "port=%~2"
set "name=%~3"
set "found="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":!port! " ^| findstr LISTENING') do (
  if not defined found set "found=%%p"
)
if not defined found exit /b 0
echo [警告] 端口 !port!（!name!）已被 PID !found! 占用。
set /p "ans=      是否结束该进程并继续？(y/N) "
if /i "!ans!"=="y" (
  taskkill /F /PID !found! >nul 2>&1
  echo       已结束占用进程：!found!
  "%PING%" -n 2 127.0.0.1 >nul
  exit /b 0
)
echo 取消启动。
pause
exit /b 1

:wait_http
rem %2=主机 %3=端口 %4=路径 %5=探测次数上限；返回 0=就绪，1=超时
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
