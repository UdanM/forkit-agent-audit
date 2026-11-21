#!/usr/bin/env sh
# AutoDiscover: start the demo on macOS or Linux (./start-autodiscover.sh).
# Launcher idea from UdanM/forkit-agent-audit (Start Forkit.cmd).
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required."; exit 1; }
"$PY" -c 'import streamlit' 2>/dev/null || {
  echo "Installing the demo requirements once (streamlit)..."; "$PY" -m pip install --user -r requirements.txt; }
echo "Starting AutoDiscover. Keep this terminal open while you use the demo."
exec "$PY" -m streamlit run app.py --browser.gatherUsageStats false
