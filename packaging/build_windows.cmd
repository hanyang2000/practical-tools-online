@echo off
setlocal
cd /d "%~dp0.."
set PYTHON_BIN=%PYTHON_BIN%
if "%PYTHON_BIN%"=="" set PYTHON_BIN=py -3

%PYTHON_BIN% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo 未找到 PyInstaller，请先执行：%PYTHON_BIN% -m pip install -r agent\requirements.txt
  exit /b 2
)
%PYTHON_BIN% -m PyInstaller --noconfirm --clean packaging\agent_windows.spec
if errorlevel 1 exit /b %errorlevel%
if not exist dist\PracticalToolsAgent-package mkdir dist\PracticalToolsAgent-package
move /Y dist\PracticalToolsAgent.exe dist\PracticalToolsAgent-package\PracticalToolsAgent.exe >nul
copy /Y agent\screenshot\config.yaml dist\PracticalToolsAgent-package\config.yaml >nul
echo Windows Agent 已生成：dist\PracticalToolsAgent-package\PracticalToolsAgent.exe
endlocal
