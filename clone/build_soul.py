# -*- coding: utf-8 -*-
"""Build the clone's instruction file from Hermes' own personality and memory.

Writes ONE managed block into the harness' global instruction file
(GEMINI.md / CLAUDE.md / AGENTS.md). Anything you wrote outside the block is kept.

  python build_soul.py               # uses HARNESS from .env
  python build_soul.py --dry-run     # print, don't write
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harnesses  # noqa: E402
from settings import CFG  # noqa: E402

HERMES = Path(CFG.get("HERMES_HOME") or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
BEGIN, END = "<!-- hermes-harness-clone:begin -->", "<!-- hermes-harness-clone:end -->"

CLONE_RULES = """\
## You are a clone of {name}

You are {name}, the same agent the user talks to in Hermes Agent, now running inside {harness}.
Same personality, same memory, same user. You are not a new assistant and you do not introduce yourself as one.

### Shared memory (MCP server `hivemind`)
- Start of every non-trivial task: call `core()` once, silently. Before answering about the user, their
  projects or their machine: `search()` / `about()`; if empty, `conversations()`.
- Something that will matter in a future session (decision, preference, port, path, account, measured result):
  `remember()` right away. One standalone sentence with a date. Search first to avoid duplicates.
- After every action that changed something (file, config, service, install): `note()` with what, where and why.
- Before editing shared config or scripts: `file_lock(path)`; unlock when done. If LOCKED by someone else, stop and tell the user.
- Never store secrets, passwords, tokens or keys in memory or notes.

### Other agents
- `ask_profile(profile, request)` sends a self-contained request to a Hermes profile and returns its answer.
  `profile='default'` is the main Hermes agent: use it for what only Hermes can reach (gateway tools,
  phone/calendar integrations, plugins, accounts you don't have).
- `crons()`, `create_cron()`, `cron_action()` manage the Hermes scheduler (jobs keep running when you are closed).
  Only create or change jobs when the user asks.
{private}
### Style on chat
When the request comes from Telegram, answer short and phone-friendly: no big tables, no long code unless asked.
"""


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def build(h) -> str:
    name = CFG.get("AGENT_NAME") or "the Hermes agent"
    private = CFG.get("PRIVATE_PROFILES", "").strip()
    priv = (f"- Private profiles, never read, ask or schedule: {private}.\n") if private else ""
    soul = read(HERMES / "SOUL.md")
    user = read(HERMES / "memories" / "USER.md")
    rules = CLONE_RULES.format(name=name, harness=h.name, private=priv)
    parts = [BEGIN, "# Personality (from Hermes SOUL.md)\n\n" + (soul or "(no SOUL.md found)"), rules,
             "# Who the user is (from Hermes USER.md)\n\n" + (user or "(no USER.md found)"), END]
    text = "\n\n".join(parts)
    budget = h.soul_limit - 1500   # room for whatever the user keeps outside the block
    if len(text.encode("utf-8")) > budget and user:
        keep = budget - len("\n\n".join(parts[:3] + [END]).encode("utf-8")) - 200
        user_cut = user.encode("utf-8")[:max(0, keep)].decode("utf-8", "ignore").rsplit("\n", 1)[0]
        parts[3] = "# Who the user is (from Hermes USER.md, trimmed: call core('user') for all of it)\n\n" + user_cut
        text = "\n\n".join(parts)
    return text


def write(h, block: str) -> Path:
    f = h.soul_file
    f.parent.mkdir(parents=True, exist_ok=True)
    old = read(f)
    if BEGIN in old and END in old:
        new = old[:old.index(BEGIN)] + block + old[old.index(END) + len(END):]
    elif old:
        bak = f.with_name(f.name + ".before-clone")
        if not bak.exists():
            bak.write_text(old, encoding="utf-8")
        new = block + "\n\n" + old
    else:
        new = block
    if new.strip() != old:
        f.write_text(new.strip() + "\n", encoding="utf-8")
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", default=CFG.get("HARNESS", "antigravity"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    h = harnesses.get(a.harness)
    block = build(h)
    if a.dry_run:
        print(block)
        return
    f = write(h, block)
    size = f.stat().st_size
    warn = f"  WARNING: above {h.soul_limit} bytes" if size > h.soul_limit else ""
    print(f"{f} updated ({size} bytes){warn}")


if __name__ == "__main__":
    main()
