@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m terminal.simulator %*
if errorlevel 1 pause
