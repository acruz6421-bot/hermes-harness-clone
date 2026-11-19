@echo off
cd /d "%~dp0"
start "" /b ".venv-hivemind\Scripts\pythonw.exe" "clone\bridge.py"
echo Bridge started in background. Logs: clone\logs
