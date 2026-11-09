@echo off
chcp 65001 >nul
cd /d "%~dp0"
netstat -ano | findstr ":8080" | findstr LISTENING >nul
if not errorlevel 1 exit /b 0
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
start "" /min ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8080
