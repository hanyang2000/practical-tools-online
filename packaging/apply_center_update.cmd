@echo off
setlocal
set "ROOT=%~dp0.."
set "DATA_ROOT=%LOCALAPPDATA%\PracticalToolsOnline\data"
if exist "%ROOT%\.env" for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b "PRACTICAL_DATA_ROOT=" "%ROOT%\.env"`) do set "DATA_ROOT=%%B"
set "STAGING=%DATA_ROOT%\update-staging"
if not exist "%STAGING%\pending.json" (
  echo No pending update was found.
  exit /b 1
)
echo This green-package updater replaces only verified payload files.
echo It never changes .env, data, registry, or Windows services.
if exist "%ROOT%\CenterUpdater.exe" (
  "%ROOT%\CenterUpdater.exe" "%STAGING%\pending.json" --app-root "%ROOT%" --launcher "%ROOT%\portable\portable_center.py" --runtime "%ROOT%\runtime\python.exe" --health-url "http://127.0.0.1:18180/api/health" --result-file "%STAGING%\result.json"
  exit /b %errorlevel%
)
if exist "%ROOT%\runtime\python.exe" if exist "%ROOT%\packaging\update_worker.py" (
  "%ROOT%\runtime\python.exe" "%ROOT%\packaging\update_worker.py" "%STAGING%\pending.json" --app-root "%ROOT%" --launcher "%ROOT%\portable\portable_center.py" --runtime "%ROOT%\runtime\python.exe" --health-url "http://127.0.0.1:18180/api/health" --result-file "%STAGING%\result.json"
  exit /b %errorlevel%
)
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\packaging\update_worker.py" "%STAGING%\pending.json" --app-root "%ROOT%" --launcher "%ROOT%\portable\portable_center.py" --runtime "%ROOT%\.venv\Scripts\python.exe" --health-url "http://127.0.0.1:18180/api/health" --result-file "%STAGING%\result.json"
  exit /b %errorlevel%
)
echo Python runtime not found in this source package.
echo Use the bundled center updater executable supplied with the green package.
exit /b 2
