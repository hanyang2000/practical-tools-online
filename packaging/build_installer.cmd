@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

set "PYTHON_BIN=%PYTHON_BIN%"
if "%PYTHON_BIN%"=="" set "PYTHON_BIN=py -3.11"

echo [1/4] Refreshing the complete Windows staging tree from current source...
%PYTHON_BIN% packaging\build_release.py || exit /b %ERRORLEVEL%

echo [2/4] Building the standalone center console...
%PYTHON_BIN% -m PyInstaller --noconfirm --clean packaging\center_console.spec || exit /b %ERRORLEVEL%
copy /Y dist\PracticalToolsCenterConsole\PracticalToolsCenterConsole.exe dist\PracticalToolsOnlinePortable\PracticalToolsCenterConsole.exe >nul || exit /b %ERRORLEVEL%

echo [3/4] Checking Inno Setup compiler...
where ISCC.exe >nul 2>&1
if errorlevel 1 (
  echo ISCC.exe not found. Install Inno Setup on the Windows build machine.
  exit /b 2
)

echo [4/4] Building the Windows installer...
for /f "delims=" %%V in ('%PYTHON_BIN% -c "from app import __version__; print(__version__)"') do set "APP_VERSION=%%V"
ISCC.exe /DAppVersion=%APP_VERSION% packaging\windows\installer\PracticalToolsOnline.iss || exit /b %ERRORLEVEL%
echo Installer created under dist\installer\
endlocal
