@echo off
:: Antigravity Fleet Windows Self-Healing Watchdog
:: Automatically recovers daemon on crash, exit, or update, and keeps code up to date.
cd /d "%~dp0\.."

:watchdog_loop
:: 1. Silent git pull to auto-apply remote updates
git pull origin main >nul 2>&1

:: 2. Launch daemon
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m daemon.agent
) else (
    python -m daemon.agent
)

:: 3. Auto-recover loop: if daemon exits (crash or update restart), restart within 3s
timeout /t 3 /nobreak >nul
goto watchdog_loop
