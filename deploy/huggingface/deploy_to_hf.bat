@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."

echo ================================================================
echo  警务遥感 - 免费部署到 Hugging Face Spaces
echo  需要：一个免费 Hugging Face 账号（邮箱注册，不要信用卡）
echo  注册：https://huggingface.co/join
echo  Token：https://huggingface.co/settings/tokens
echo ================================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo 错误：找不到 .venv\Scripts\python.exe
  pause
  exit /b 1
)

set /p HF_USER=请输入 Hugging Face 用户名: 
if "%HF_USER%"=="" (
  echo 错误：用户名不能为空。
  pause
  exit /b 1
)

set /p HF_SPACE=请输入 Space 名称（直接回车用 EcoPolice）: 
if "%HF_SPACE%"=="" set HF_SPACE=EcoPolice

if "%HF_TOKEN%"=="" (
  set /p HF_TOKEN=请粘贴 Access Token（hf_...，输入时不显示）: 
)
if "%HF_TOKEN%"=="" (
  echo 错误：Token 不能为空。请到 https://huggingface.co/settings/tokens 创建 Write 权限 Token。
  pause
  exit /b 1
)

echo.
echo 检查上传工具 huggingface_hub ...
".venv\Scripts\python.exe" -c "import huggingface_hub" >nul 2>&1
if errorlevel 1 (
  echo 正在安装 huggingface_hub（仅本机 .venv，约几 MB）...
  ".venv\Scripts\python.exe" -m pip install -q huggingface_hub
  if errorlevel 1 (
    echo 错误：huggingface_hub 安装失败。请检查网络或 pip 源。
    pause
    exit /b 1
  )
)

echo.
".venv\Scripts\python.exe" "deploy\huggingface\upload.py" "%HF_USER%/%HF_SPACE%"
set RESULT=%ERRORLEVEL%
set HF_TOKEN=
echo.
if %RESULT%==0 (
  echo 部署命令已完成。浏览器打开上面的公网网址；首次构建需要几分钟。
) else (
  echo 部署失败，请按上面的错误提示处理。
)
pause
endlocal
