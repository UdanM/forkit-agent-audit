@echo off
cd /d "%~dp0"
python open_vm_dashboard.py %*
if errorlevel 1 pause
