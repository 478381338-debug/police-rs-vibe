@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 青山卫士 · 生态警务遥感态势平台

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 创建 Python 虚拟环境...
  python -m venv .venv
  if errorlevel 1 (
    echo 创建虚拟环境失败，请确认已安装 Python 3.12。
    pause
    exit /b 1
  )
  echo [2/3] 安装依赖（首次可能需要几分钟）...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo.
echo ============================================================
echo   青山卫士 · 生态警务遥感态势平台
echo   本机访问: http://127.0.0.1:8080
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-NetIPConfiguration ^| Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway -ne $null } ^| Select-Object -First 1).IPv4Address.IPAddress"') do set LANIP=%%i
if defined LANIP echo   局域网访问: http://%LANIP%:8080
echo   公网访问: 运行 start_public.bat（需要保持本窗口运行）
echo ============================================================
echo.

start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8080'"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8080
pause
