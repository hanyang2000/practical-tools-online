@echo off
setlocal
cd /d "%~dp0"
if not exist .env (
  echo 未找到 .env，请先复制 .env.example 并填写 PRACTICAL_DATABASE_URL 等配置。
  exit /b 2
)
set PRACTICAL_ENV_FILE=%~dp0.env
if not exist PracticalToolsOnline.exe (
  echo 未找到 PracticalToolsOnline.exe，请先运行 build_center_windows.cmd。
  exit /b 2
)
PracticalToolsOnline.exe
endlocal
