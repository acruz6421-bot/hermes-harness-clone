#!/usr/bin/env sh
cd "$(dirname "$0")"
nohup .venv-hivemind/bin/python clone/bridge.py >/dev/null 2>&1 &
echo "Bridge started in background. Logs: clone/logs"
