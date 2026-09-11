@echo off
cd /d "%~dp0"
taskkill /FI "WINDOWTITLE eq Practical Tools Online - Portable Center*" /T /F >nul 2>&1
echo Center stop requested.
pause
