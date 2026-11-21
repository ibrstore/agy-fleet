@echo off
:: Starts agy-fleet daemon completely silent in background without any open window
cd /d "%~dp0\.."
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m daemon.agent
) else (
    start "" pythonw -m daemon.agent
)
