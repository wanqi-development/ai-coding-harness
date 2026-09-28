@echo off
chcp 936 >nul
rem ai-coding-harness 一键启动：后端(FastAPI) + 前端(Vite)
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ---- 可按需修改 ----
rem 项目运行环境 = conda 环境 aicoding（conda env list 可见）；换环境时改这一行
set "PYTHON=D:\developmentBinary\miniconda3\envs\aicoding\python.exe"
set "BACKEND_HOST=127.0.0.1"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "FRONTEND_URL=http://localhost:%FRONTEND_PORT%"
rem 探针用 System32 的 curl：PATH 上若有别的 curl 会先被命中
set "CURL=%SystemRoot%\System32\curl.exe"
rem 就绪探测次数上限。每次 = 一次探测(约 1~2 秒) + 1 秒间隔，故 30 次约 1 分钟；
rem 服务已就绪时第一次探测即通过，不产生延迟。
set "BACKEND_TRIES=30"
set "FRONTEND_TRIES=15"
rem --------------------

rem 端口占用检查（占用了询问是否结束占用进程）
call :check_port %BACKEND_PORT% 后端
if errorlevel 1 exit /b 1
call :check_port %FRONTEND_PORT% 前端
if errorlevel 1 exit /b 1

if not exist ".env" (
  echo [警告] 未找到 .env，端口配置无法读取，请先复制 .env.example 为 .env 并填写配置。
)

set "PYTHONPATH=src"

"%PYTHON%" -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo [错误] Python 无法启动：%PYTHON%
  echo        项目运行环境是 conda 环境 aicoding（conda env list 查看）；换环境时改上面的 PYTHON。
  exit /b 1
)

rem git 自检（只警告）：右侧栏「代码」面板靠本机 git 下载仓库缓存（不经沙箱）；
rem 没有 git 只是那个面板用不了，对话与沙箱链路照常，所以不拦启动。
where git >nul 2>&1
if errorlevel 1 (
  echo [警告] 未找到 git：右侧栏「代码」面板用不了（装 git，或设 REPO_VIEW_GIT 指定 git 可执行文件）。
)

echo [1/3] 启动后端 %BACKEND_HOST%:%BACKEND_PORT% ...
start "harness-backend" cmd /k %PYTHON% -m ai_coding_harness.main

echo       等待后端就绪（最多 %BACKEND_TRIES% 次探测）...
call :wait_http %BACKEND_HOST% %BACKEND_PORT% /health %BACKEND_TRIES%
if errorlevel 1 (
  echo [警告] 后端未就绪，仍继续启动前端；请查看 harness-backend 窗口中的报错。
)

echo [2/3] 启动前端 %FRONTEND_URL% ...
start "harness-frontend" cmd /k npm --prefix frontend run dev

echo       等待前端就绪（最多 %FRONTEND_TRIES% 次探测）...
call :wait_http localhost %FRONTEND_PORT% / %FRONTEND_TRIES%
if errorlevel 1 (
  echo [警告] 前端未就绪，仍继续；请查看 harness-frontend 窗口中的报错。
)

echo [3/3] 打开浏览器 ...
start "" %FRONTEND_URL%

echo.
echo 完成。关闭对应窗口即可停止服务。
endlocal
exit /b 0

:wait_http
rem %1=主机 %2=端口 %3=路径 %4=探测次数上限；返回 0=就绪，1=超时
rem 用 curl 的真实返回码判断，非 2xx 视为未就绪（-f）。
rem --connect-timeout 1：端口没人监听时连接会一直挂着不返回，必须自己设上限；
rem 否则每次探测要拖到 --max-time 才结束，整轮等待会远超预期。
set /a "waited=0"
:wait_http_loop
"%CURL%" -s -f --connect-timeout 1 --max-time 2 -o nul "http://%~1:%~2%~3" >nul 2>&1
if not errorlevel 1 goto :wait_http_ok
if !waited! geq %4 goto :wait_http_timeout
<nul set /p "=."
rem 用 ping 而不是 timeout 做 1 秒节拍：timeout 会被 PATH 上的同名程序顶掉，
rem 也不接受重定向的 stdin，循环节拍不能建在这种依赖上。
ping -n 2 127.0.0.1 >nul
set /a "waited+=1"
goto :wait_http_loop

:wait_http_ok
if !waited! gtr 0 echo.
exit /b 0

:wait_http_timeout
echo.
exit /b 1

:check_port
rem %1 = 端口, %2 = 名称；被占用则询问，返回 0=继续，1=取消
set "found="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%1 " ^| findstr LISTENING') do (
  if not defined found set "found=%%p"
)
if not defined found exit /b 0
echo [警告] 端口 %1（%2）已被 PID !found! 占用。
set /p "ans=      是否结束该进程并继续？(y/N) "
if /i "!ans!"=="y" (
  taskkill /F /PID !found! >nul 2>&1
  echo       已结束占用进程：!found!
  ping -n 2 127.0.0.1 >nul
  exit /b 0
)
echo 取消启动。
pause
exit /b 1
