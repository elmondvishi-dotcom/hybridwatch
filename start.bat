@echo off
cd /d "%~dp0"
echo Starting HybridWatch... (close this window to stop it)
echo Then open http://localhost:8000  (Model lab: http://localhost:8000/lab)
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py run.py %*
) else (
  python run.py %*
)
echo.
echo HybridWatch stopped. If there is an error above, copy it and send it.
pause
