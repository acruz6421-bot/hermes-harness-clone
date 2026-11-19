# -*- coding: utf-8 -*-
"""One-shot installer. Safe to re-run.

  python setup.py --harness antigravity            # interactive: asks what is missing
  python setup.py --harness antigravity --check    # only verifies, changes nothing
  python setup.py --harness claude --yes           # non-interactive (values from clone/.env)
  python setup.py --uninstall                       # removes the MCP registration and the soul block

What it does:
  1. creates .venv-hivemind and installs the MCP SDK
  2. finds Hermes (HERMES_HOME, hermes binary and its python)
  3. writes clone/.env (Telegram token, owner id, harness...)
  4. registers the `hivemind` MCP server in the chosen harness
  5. writes the personality block into the harness' instruction file
  6. smoke-tests the memory read path
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "clone"))
import harnesses  # noqa: E402

VENV = ROOT / ".venv-hivemind"
ENV_FILE = ROOT / "clone" / ".env"
MCP_NAME = "hivemind"
WIN = os.name == "nt"


def say(ok, msg):
    print(("  [ok] " if ok else "  [!!] ") + msg)
    return ok


def venv_python():
    return VENV / ("Scripts/python.exe" if WIN else "bin/python")


def read_env():
    cfg = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def write_env(cfg):
    example = (ROOT / "clone" / ".env.example").read_text(encoding="utf-8").splitlines()
    out, seen = [], set()
    for line in example:
        k = line.split("=", 1)[0].strip().lstrip("# ").strip() if "=" in line else ""
        if k and k in cfg and k not in seen:
            out.append(f"{k}={cfg[k]}")
            seen.add(k)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in cfg.items() if k not in seen]
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    if not WIN:
        os.chmod(ENV_FILE, 0o600)


def find_hermes(cfg):
    home = Path(cfg.get("HERMES_HOME") or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
    binary = cfg.get("HERMES_BIN") or shutil.which("hermes")
    py = None
    if binary:
        d = Path(binary).resolve().parent
        py = next((str(c) for c in (d / "python.exe", d / "python", d / "python3") if c.exists()), None)
    return home, binary, py


def ask(cfg, key, prompt, interactive, default=""):
    if cfg.get(key):
        return
    if not interactive:
        if default:
            cfg[key] = default
        return
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip() or default
    if v:
        cfg[key] = v


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", choices=list(harnesses.HARNESSES))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--yes", action="store_true", help="non-interactive")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    cfg = read_env()
    if a.harness:
        cfg["HARNESS"] = a.harness
    h = harnesses.get(cfg.get("HARNESS", "antigravity"))
    interactive = not (a.yes or a.check) and sys.stdin.isatty()
    print(f"\nhermes-harness-clone · harness = {h.name}{' (EXPERIMENTAL)' if h.experimental else ''}\n")

    binary = h.find_bin()
    if a.uninstall:
        if binary:
            run(h.mcp_remove_cmd(binary, MCP_NAME))
        f = h.soul_file
        if f.exists():
            t = f.read_text(encoding="utf-8")
            b, e = "<!-- hermes-harness-clone:begin -->", "<!-- hermes-harness-clone:end -->"
            if b in t and e in t:
                f.write_text((t[:t.index(b)] + t[t.index(e) + len(e):]).strip() + "\n", encoding="utf-8")
        say(True, "MCP registration and soul block removed (clone/.env and memory untouched)")
        return 0

    ok = True
    # 1. harness
    ok &= say(bool(binary), f"{h.name} binary: {binary or 'NOT FOUND (install it or set HARNESS_BIN in clone/.env)'}")
    # 2. hermes
    home, hbin, hpy = find_hermes(cfg)
    ok &= say(home.exists(), f"HERMES_HOME: {home}")
    ok &= say(bool(hbin), f"hermes binary: {hbin or 'NOT FOUND (set HERMES_BIN)'}")
    say((home / "memory_store.db").exists(),
        "fact store memory_store.db " + ("found" if (home / "memory_store.db").exists()
                                         else "missing: enable the holographic memory provider in Hermes for search()/remember()"))
    if hbin:
        cfg.setdefault("HERMES_BIN", hbin)
    cfg.setdefault("HERMES_HOME", str(home))

    # 3. venv + mcp sdk
    if not a.check:
        if not venv_python().exists():
            venv.create(VENV, with_pip=True)
        if run([str(venv_python()), "-m", "pip", "--version"]).returncode != 0:
            run([str(venv_python()), "-m", "ensurepip", "--upgrade"])
        r = run([str(venv_python()), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
        ok &= say(r.returncode == 0, "python deps installed in .venv-hivemind" + ("" if r.returncode == 0 else ": " + r.stderr[-300:]))
    ok &= say(venv_python().exists(), f"venv: {venv_python()}")

    # 4. .env
    ask(cfg, "AGENT_NAME", "Your agent's name (as in SOUL.md)", interactive, "Hermes")
    ask(cfg, "TELEGRAM_TOKEN", "Telegram bot token from @BotFather (a NEW bot, not one Hermes already uses)", interactive)
    ask(cfg, "OWNER_ID", "Your numeric Telegram user id (ask @userinfobot)", interactive)
    ask(cfg, "PRIVATE_PROFILES", "Hermes profiles the clone must NEVER touch (comma separated, empty = none)", interactive)
    ask(cfg, "CRON_DELIVER", "Where crons created by the clone deliver (e.g. telegram:<your id>)", interactive,
        f"telegram:{cfg['OWNER_ID']}" if cfg.get("OWNER_ID") else "origin")
    cfg.setdefault("MAX_TURNS_PER_HOUR", "60")
    if not a.check:
        write_env(cfg)
        say(True, f"config written: {ENV_FILE}")
    say(bool(cfg.get("TELEGRAM_TOKEN") and cfg.get("OWNER_ID")),
        "Telegram configured" if cfg.get("TELEGRAM_TOKEN") else "Telegram not configured (only the desktop/CLI clone will work)")

    # 5. MCP registration
    mcp_env = {"HERMES_HOME": str(home), "HIVEMIND_INSTANCE": f"clone-{h.name}",
               "HIVEMIND_PRIVATE_PROFILES": cfg.get("PRIVATE_PROFILES", ""),
               "HIVEMIND_CRON_DELIVER": cfg.get("CRON_DELIVER", "origin"),
               "HIVEMIND_OWNER_NAME": cfg.get("OWNER_NAME", "the user")}
    if hbin:
        mcp_env["HERMES_BIN"] = hbin
    if hpy:
        mcp_env["HERMES_PYTHON"] = hpy
    mcp_env = {k: v for k, v in mcp_env.items() if v}
    server = str(ROOT / "hivemind" / "mcp_server.py")
    if binary and not a.check:
        r = run(h.mcp_add_cmd(binary, MCP_NAME, str(venv_python()), server, mcp_env))
        ok &= say(r.returncode == 0, f"MCP '{MCP_NAME}' registered in {h.name}"
                  + ("" if r.returncode == 0 else ": " + (r.stderr or r.stdout)[-300:]))
        if h.name == "antigravity":
            ide = Path.home() / ".gemini" / "antigravity-ide" / "mcp_config.json"
            if ide.parent.exists():   # the desktop app keeps its own MCP list
                data = json.loads(ide.read_text(encoding="utf-8")) if ide.exists() else {}
                data.setdefault("mcpServers", {})[MCP_NAME] = {
                    "type": "stdio", "command": str(venv_python()), "args": [server], "env": mcp_env}
                ide.write_text(json.dumps(data, indent=2), encoding="utf-8")
                say(True, "MCP also added to the Antigravity desktop app (restart the app to load it)")

    # 6. soul
    if not a.check:
        r = run([sys.executable, str(ROOT / "clone" / "build_soul.py"), "--harness", h.name])
        ok &= say(r.returncode == 0, (r.stdout or r.stderr).strip()[-200:])

    # 7. smoke test: the MCP server imports and reads memory
    probe = ("import os,sys;sys.path.insert(0,r'%s');os.environ['HERMES_HOME']=r'%s';import mcp_server as m;"
             "print(len(m.core('user')));print(m._profiles())") % (ROOT / "hivemind", home)
    r = run([str(venv_python()), "-c", probe]) if venv_python().exists() else None
    ok &= say(bool(r and r.returncode == 0), "MCP server loads and reads Hermes memory"
              + ("" if r and r.returncode == 0 else ": " + ((r.stderr if r else "no venv") or "")[-300:]))

    print("\n" + ("Ready." if ok else "Finished with problems (see [!!] above)."))
    if ok and not a.check:
        print("  Desktop/CLI: open " + h.name + " and ask: \"who am I and what do you remember about me?\"")
        if cfg.get("TELEGRAM_TOKEN"):
            print("  Telegram:    run start_bridge" + (".bat" if WIN else ".sh") + " and send /start to your bot")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
