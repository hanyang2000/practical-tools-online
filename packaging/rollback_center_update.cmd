@echo off
setlocal
set "ROOT=%~dp0.."
set "DATA_ROOT=%LOCALAPPDATA%\PracticalToolsOnline\data"
if exist "%ROOT%\.env" for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b "PRACTICAL_DATA_ROOT=" "%ROOT%\.env"`) do set "DATA_ROOT=%%B"
set "STAGING=%DATA_ROOT%\update-staging"
if not exist "%ROOT%\update-backups" (
  echo No applied-program backup was found.
  exit /b 1
)
for /f "delims=" %%D in ('dir /b /ad /o-n "%ROOT%\update-backups"') do (
  set "BACKUP=%ROOT%\update-backups\%%D"
  goto :restore
)
echo No update backup was found.
exit /b 1
:restore
if exist "%ROOT%\CenterUpdater.exe" (
  "%ROOT%\CenterUpdater.exe" --rollback "%BACKUP%" --app-root "%ROOT%" --launcher "%ROOT%\portable\portable_center.py" --runtime "%ROOT%\runtime\python.exe" --health-url "http://127.0.0.1:18180/api/health"
  exit /b %errorlevel%
)
if exist "%ROOT%\runtime\python.exe" if exist "%ROOT%\packaging\update_worker.py" (
  "%ROOT%\runtime\python.exe" "%ROOT%\packaging\update_worker.py" --rollback "%BACKUP%" --app-root "%ROOT%" --launcher "%ROOT%\portable\portable_center.py" --runtime "%ROOT%\runtime\python.exe" --health-url "http://127.0.0.1:18180/api/health"
  exit /b %errorlevel%
)
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\packaging\update_worker.py" --rollback "%BACKUP%" --app-root "%ROOT%" --launcher "%ROOT%\portable\portable_center.py" --runtime "%ROOT%\.venv\Scripts\python.exe" --health-url "http://127.0.0.1:18180/api/health"
  exit /b %errorlevel%
)
echo Python runtime not found in this source package.
exit /b 2
