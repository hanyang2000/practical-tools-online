@echo off
setlocal
set "CENTER_ROOT=%~dp0.."
cd /d "%CENTER_ROOT%"
title Practical Tools Online - Portable Center
"%CENTER_ROOT%\runtime\python.exe" "%~dp0portable_center.py"
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" echo Startup failed. Check "%CENTER_ROOT%\data\logs\"
pause
exit /b %EXIT_CODE%
