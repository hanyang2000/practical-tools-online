@echo off
setlocal
cd /d "%~dp0.."
set PYTHON_BIN=%PYTHON_BIN%
if "%PYTHON_BIN%"=="" set PYTHON_BIN=py -3.11

echo [1/5] 检查 Python 3.11 与项目依赖...
%PYTHON_BIN% --version || exit /b 2
%PYTHON_BIN% -m pip install -e . || exit /b %errorlevel%
echo [2/5] 构建中心服务 onedir 包...
%PYTHON_BIN% -m PyInstaller --noconfirm --clean packaging\center_windows.spec || exit /b %errorlevel%
echo [3/5] 构建独立中心控制台...
%PYTHON_BIN% -m PyInstaller --noconfirm --clean packaging\center_console.spec || exit /b %errorlevel%
echo [4/5] 构建绿色版专用更新器...
%PYTHON_BIN% -m PyInstaller --noconfirm --clean packaging\center_updater.spec || exit /b %errorlevel%
echo [5/5] 复制配置和启动文件...
copy /Y packaging\center_windows.env.example dist\PracticalToolsOnline\.env.example >nul
copy /Y packaging\start_center_windows.cmd dist\PracticalToolsOnline\start_center_windows.cmd >nul
copy /Y dist\PracticalToolsCenterConsole\PracticalToolsCenterConsole.exe dist\PracticalToolsOnline\PracticalToolsCenterConsole.exe >nul
copy /Y packaging\apply_center_update.cmd dist\PracticalToolsOnline\apply_center_update.cmd >nul
copy /Y packaging\rollback_center_update.cmd dist\PracticalToolsOnline\rollback_center_update.cmd >nul
if exist dist\CenterUpdater\CenterUpdater.exe copy /Y dist\CenterUpdater\CenterUpdater.exe dist\PracticalToolsOnline\CenterUpdater.exe >nul
echo 中心服务包已生成：dist\PracticalToolsOnline\
echo 请复制 .env.example 为 .env，配置数据库后运行 start_center_windows.cmd
endlocal
