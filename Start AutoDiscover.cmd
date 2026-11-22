@echo off
rem AutoDiscover: double-click to start the demo on Windows.
rem Launcher idea from UdanM/forkit-agent-audit (Start Forkit.cmd).
setlocal
cd /d "%~dp0"

set "PY=py -3"
where py >nul 2>nul || set "PY=python"
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ and try again.
  pause
  exit /b 1
)

%PY% -c "import streamlit" >nul 2>nul
if errorlevel 1 (
  echo Installing the demo requirements once ^(streamlit^)...
  %PY% -m pip install --user -r requirements.txt
  if errorlevel 1 (
    echo Could not install the requirements. Run: %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

echo Starting AutoDiscover. Keep this window open while you use the demo.
%PY% -m streamlit run app.py --server.headless false --browser.gatherUsageStats false
if errorlevel 1 pause
endlocal
