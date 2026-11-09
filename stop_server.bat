@echo off
chcp 65001 >nul
echo 停止 8080 端口服务...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080" ^| findstr LISTENING') do (
  taskkill /F /PID %%a >nul 2>&1
)
echo 停止 cloudflared 隧道...
taskkill /F /IM cloudflared.exe >nul 2>&1
echo 已停止。
pause
