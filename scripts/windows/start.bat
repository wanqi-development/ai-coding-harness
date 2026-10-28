@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
set "COMMON=%~dp0_common.bat"
call "%COMMON%"

call "%COMMON%" check-port "%BACKEND_PORT%" "后端"
if errorlevel 1 exit /b 1
call "%COMMON%" check-port "%FRONTEND_PORT%" "前端"
if errorlevel 1 exit /b 1
call "%COMMON%" preflight-backend
if errorlevel 1 exit /b 1
call "%COMMON%" preflight-frontend
if errorlevel 1 exit /b 1

set "HARNESS_SKIP_PORT_CHECK=1"
set "HARNESS_SKIP_PREFLIGHT=1"

echo [1/3] 启动后端 %BACKEND_HOST%:%BACKEND_PORT% ...
start "harness-backend" cmd /k ""%~dp0start-backend.bat""

echo       等待后端就绪（最多 %BACKEND_TRIES% 次探测）...
call "%COMMON%" wait-http %BACKEND_HOST% %BACKEND_PORT% /health %BACKEND_TRIES%
if errorlevel 1 (
  echo [警告] 后端未就绪，仍继续启动前端；请查看 harness-backend 窗口中的报错。
)

echo [2/3] 启动前端 %FRONTEND_URL% ...
start "harness-frontend" cmd /k ""%~dp0start-frontend.bat""

echo       等待前端就绪（最多 %FRONTEND_TRIES% 次探测）...
call "%COMMON%" wait-http localhost %FRONTEND_PORT% / %FRONTEND_TRIES%
if errorlevel 1 (
  echo [警告] 前端未就绪，仍继续；请查看 harness-frontend 窗口中的报错。
)

echo [3/3] 打开浏览器 ...
start "" %FRONTEND_URL%

echo.
echo 完成。关闭对应窗口即可停止服务。
exit /b 0
