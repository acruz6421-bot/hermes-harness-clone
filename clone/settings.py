# -*- coding: utf-8 -*-
"""Reads clone/.env (KEY=value lines). Shared by the bridge, build_soul and setup."""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"


def load():
    cfg = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():  # real environment wins
        if k.startswith(("CLONE_", "TELEGRAM_")) or k in ("HARNESS", "HERMES_HOME", "HERMES_BIN"):
            cfg[k.removeprefix("CLONE_")] = v
    return cfg


CFG = load()
