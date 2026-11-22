@echo off
:: Runs agy-fleet daemon in foreground with live console logs
cd /d "%~dp0\.."
echo ========================================================
echo   ANTIGRAVITY FLEET DAEMON (DEBUG CONSOLE)
echo ========================================================
echo [1/2] Menarik pembaruan terbaru dari GitHub...
git pull origin main
echo.
echo [2/2] Memulai daemon agy-fleet...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m daemon.agent
) else (
    python -m daemon.agent
)
echo.
echo Daemon berhenti.
pause
