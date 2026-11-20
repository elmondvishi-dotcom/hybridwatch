py py@echo off
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
echo Duke nisur HybridWatch... (mbylle kete dritare per ta ndalur)
"%PY%" run.py %*
pause
