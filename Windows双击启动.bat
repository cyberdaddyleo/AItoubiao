@echo off
chcp 65001 >nul
rem AI投标系统 · Windows 本机版
rem 需要先安装 Python 3.10 及以上（安装时勾选 Add python.exe to PATH）。
rem 双击后自动安装依赖（只需一次）、启动后台、自动登录并打开浏览器。关闭这个窗口即停止运行。
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo 没有找到 Python。请先从 https://www.python.org/downloads/ 安装 Python 3.10 及以上版本，
  echo 安装时勾选「Add python.exe to PATH」，然后重新双击本文件。
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 第一次运行：正在安装运行环境，约 1~3 分钟……
  python -m venv .venv
  .venv\Scripts\python -m pip install -q --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
  .venv\Scripts\pip install -q -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
  if errorlevel 1 (
    echo 国内镜像安装失败，改用官方源重试……
    .venv\Scripts\pip install -q -r requirements.txt
  )
)

set BID_DESKTOP=1
set BID_PORT=8600
set BID_DATA_DIR=%LOCALAPPDATA%\AIBidding\data
if not exist "%BID_DATA_DIR%" mkdir "%BID_DATA_DIR%"

if not exist "%LOCALAPPDATA%\AIBidding\.initialized" (
  choice /c YN /m "第一次使用，要先载入演示数据看看完整效果吗？（Y=载入 N=空白开始）"
  if not errorlevel 2 .venv\Scripts\python -m app.seed_demo
  echo done> "%LOCALAPPDATA%\AIBidding\.initialized"
)

rem 后台等服务就绪后，用本机令牌自动登录并打开浏览器
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "for($i=0;$i -lt 120;$i++){try{Invoke-WebRequest 'http://127.0.0.1:8600/api/health' -UseBasicParsing -TimeoutSec 1 | Out-Null; break}catch{Start-Sleep -Milliseconds 500}}; $t=(Get-Content -Raw ($env:BID_DATA_DIR + '\desktop.token')).Trim(); Start-Process ('http://127.0.0.1:8600/api/auth/desktop?token=' + $t)"

echo.
echo AI投标系统已启动：浏览器会自动打开 http://127.0.0.1:8600
echo 数据保存在：%BID_DATA_DIR%
echo 关闭这个窗口即停止运行（定时采集也会停止）。
echo.
.venv\Scripts\python -m app.main
pause
