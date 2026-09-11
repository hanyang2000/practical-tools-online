@echo off
setlocal
set "ROOT=%~dp0.."
set "RUNTIME=%ROOT%\runtime\python.exe"
if not exist "%RUNTIME%" set "RUNTIME=python"
if "%~1"=="" (
  echo 用法：import_center_migration.cmd 旧中心导出的.ptcenter.zip [--inspect-only]
  exit /b 2
)
"%RUNTIME%" "%ROOT%\scripts\import_center_migration.py" %*
if errorlevel 1 pause
exit /b %errorlevel%
