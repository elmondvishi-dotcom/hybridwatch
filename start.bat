@echo off
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting HybridWatch... (close this window to stop it)
"%PY%" run.py %*
pause
