@echo off
:: Launches self-healing watchdog daemon completely hidden in background
cd /d "%~dp0"
start "" wscript.exe "%~dp0run_watchdog_hidden.vbs"
