@echo off
setlocal
cd /d "%~dp0"
call "%~dp0portable\START.cmd"
set "EXIT_CODE=%ERRORLEVEL%"
exit /b %EXIT_CODE%
