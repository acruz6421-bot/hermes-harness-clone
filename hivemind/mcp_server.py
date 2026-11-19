# -*- coding: utf-8 -*-
"""hivemind MCP server: gives an external coding harness (Antigravity, Claude Code, Codex)
the SAME memory and the same bots as your Hermes Agent.

Reads (read-only SQLite):
  - core memory:   $HERMES_HOME/memories/USER.md and MEMORY.md
  - fact store:    $HERMES_HOME/memory_store.db   (holographic memory plugin)
  - conversations: $HERMES_HOME/state.db and $HERMES_HOME/profiles/<p>/state.db
Writes:
  - remember():    one durable fact into the fact store, through the plugin's own API
                   (write_fact.py runs with Hermes' python), so Hermes sees it immediately
  - note():        a journal row in $HERMES_HOME/hivemind_notes.db (Hermes can consolidate it)
  - file_lock():   advisory locks so Hermes and the clone don't edit the same file at once
Acts:
  - ask_profile(): runs `hermes [-p profile] chat -q ...` and returns the answer
  - crons / run_cron / create_cron / cron_action: the Hermes scheduler

Privacy: profiles listed in HIVEMIND_PRIVATE_PROFILES are never read, asked or scheduled.

Run (stdio, the harness starts it):  python mcp_server.py
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERMES = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
FACT_DB = Path(os.environ.get("HIVEMIND_FACT_DB") or HERMES / "memory_store.db")
NOTES_DB = Path(os.environ.get("HIVEMIND_NOTES_DB") or HERMES / "hivemind_notes.db")
MEM = HERMES / "memories"
PROFILES = HERMES / "profiles"
PRIVATE = {p.strip().lower() for p in os.environ.get("HIVEMIND_PRIVATE_PROFILES", "").split(",") if p.strip()}
INSTANCE = os.environ.get("HIVEMIND_INSTANCE", "clone")
CRON_DELIVER = os.environ.get("HIVEMIND_CRON_DELIVER", "origin")  # e.g. telegram:123456789
OWNER = os.environ.get("HIVEMIND_OWNER_NAME", "the user")
HERE = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NOTE_TYPES = {"action", "decision", "fact", "error", "todo"}
SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|ya29\.[0-9A-Za-z_-]{20,}|"
                    r"\d{8,10}:AA[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|"
                    r"(password|passwd|token|api[_-]?key|secret)\s*[:=]\s*\S{6,})", re.I)

mcp = FastMCP("hivemind")


# ----------------------------------------------------------------- helpers
def hermes_bin() -> str:
    b = os.environ.get("HERMES_BIN") or shutil.which("hermes")
    if not b:
        raise RuntimeError("hermes executable not found (set HERMES_BIN in .env)")
    return b


def hermes_python() -> str:
    """Python of the Hermes venv (the fact-store plugin needs Hermes' dependencies)."""
    if os.environ.get("HERMES_PYTHON"):
        return os.environ["HERMES_PYTHON"]
    d = Path(hermes_bin()).resolve().parent
    for c in (d / "python.exe", d / "python", d / "python3"):
        if c.exists():
            return str(c)
    return sys.executable


def _env():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("HERMES_HOME", str(HERMES))
    return env


def _run(cmd, timeout=90):
    """Subprocess safe for an MCP stdio server: no inherited stdin, no console window."""
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=_env(), stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


def _ro(path: Path):
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def _profiles():
    names = ["default"]
    if PROFILES.exists():
        names += sorted(p.name for p in PROFILES.iterdir()
                        if p.is_dir() and p.name.lower() not in PRIVATE and (p / "state.db").exists())
    return names


def _profile(p: str):
    p = (p or "default").strip().lower()
    if p in ("main", "general", "hermes"):
        p = "default"
    if p in PRIVATE:
        return None, "That profile is private. Never access it."
    if p not in _profiles():
        return None, "Available profiles: " + ", ".join(_profiles())
    return p, ""


def _state_db(p: str):
    p, _ = _profile(p)
    if not p:
        return None
    db = HERMES / "state.db" if p == "default" else PROFILES / p / "state.db"
    return db if db.exists() else None


def _date(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


def _snippet(text: str, terms, radius: int = 220) -> str:
    text = text or ""
    low = text.lower()
    pos = next((low.find(t.lower()) for t in terms if low.find(t.lower()) >= 0), 0)
    a, b = max(0, pos - radius), min(len(text), pos + radius)
    s = re.sub(r"\s{2,}", " ", text[a:b].replace("\r", " ").replace("\n", " "))
    return ("…" if a else "") + s + ("…" if b < len(text) else "")


def _notes():
    con = sqlite3.connect(NOTES_DB.as_posix(), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("create table if not exists notes (id integer primary key autoincrement,"
                " created_at text not null default (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),"
                " instance text not null, type text not null, project text not null default '',"
                " text text not null, files text not null default '', consolidated integer not null default 0)")
    con.execute("create table if not exists locks (path text primary key, owner text not null,"
                " reason text not null default '', created_at text not null default"
                " (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')), expires_at real not null)")
    return con


# ----------------------------------------------------------------- read
@mcp.tool()
def core(which: str = "both") -> str:
    """The core memory Hermes carries in every conversation. Call ONCE at the start of every
    non-trivial task. which = 'user' (USER.md: who the user is), 'agent' (MEMORY.md: hard rules
    and environment state) or 'both'. Read-only."""
    files = {"user": ["USER.md"], "agent": ["MEMORY.md"]}.get(which.strip().lower(), ["USER.md", "MEMORY.md"])
    parts = [f"# {n}\n\n" + (MEM / n).read_text(encoding="utf-8").strip() for n in files if (MEM / n).exists()]
    return "\n\n---\n\n".join(parts) or "Core memory not found."


@mcp.tool()
def search(query: str, limit: int = 8) -> str:
    """Search durable facts in the deep memory (fact store) with the words the user would use:
    a project, tool, number, decision, port, path. Returns facts with id and date. Read-only.
    If nothing is found, try conversations() with the same words."""
    if not FACT_DB.exists():
        return "No fact store (holographic memory plugin not enabled). Use conversations()."
    terms = [t for t in (re.sub(r"[\"']", "", w) for w in query.split() if len(w) > 2) if t][:8]
    if not terms:
        return "Empty query."
    sql = ("select f.fact_id, f.content, f.category, f.updated_at from facts_fts "
           "join facts f on f.fact_id = facts_fts.rowid where facts_fts match ? order by rank limit ?")
    with _ro(FACT_DB) as con:
        rows = con.execute(sql, (" OR ".join(f'"{t}"' for t in terms), max(1, min(int(limit), 25)))).fetchall()
    if not rows:
        return f"No facts for: {query}. Try conversations()."
    return "\n\n".join(f"#{r['fact_id']} [{r['category']}] ({str(r['updated_at'])[:10]})\n{r['content'].strip()}"
                       for r in rows)


@mcp.tool()
def about(name: str, limit: int = 15) -> str:
    """Everything the memory knows about a person, project or thing, by name. Read-only."""
    if not FACT_DB.exists():
        return "No fact store. Use conversations()."
    sql = ("select f.fact_id, f.content, f.category from facts f join fact_entities fe on fe.fact_id = f.fact_id "
           "join entities e on e.entity_id = fe.entity_id where lower(e.name) = lower(?) "
           "order by f.trust_score desc limit ?")
    with _ro(FACT_DB) as con:
        rows = con.execute(sql, (name.strip(), max(1, min(int(limit), 40)))).fetchall()
    if not rows:
        return f"No entity named '{name}'. Try search() or conversations()."
    return "\n\n".join(f"#{r['fact_id']} [{r['category']}]\n{r['content'].strip()}" for r in rows)


@mcp.tool()
def conversations(query: str, profile: str = "all", limit: int = 8) -> str:
    """Search the FULL conversation history of Hermes and its profiles (all sessions).
    Every term needs 3+ letters; all terms must appear in the same message.
    profile = 'all', 'default' or a profile name. Use read_session() to read one. Read-only."""
    terms = [t for t in (w.replace('"', "") for w in query.split()) if len(t) >= 3][:6]
    if not terms:
        return "Use terms with 3+ letters."
    limit = max(1, min(int(limit), 20))
    targets = _profiles() if (profile or "all").lower() == "all" else [profile]
    found = []
    for p in targets:
        db = _state_db(p)
        if not db:
            if len(targets) == 1:
                return f"Profile '{profile}' does not exist or cannot be read."
            continue
        try:
            with _ro(db) as con:
                fts = con.execute("select 1 from sqlite_master where name='messages_fts_trigram'").fetchone()
                if fts:
                    sql = ("select m.session_id sid, m.role, m.content, m.timestamp ts, s.title "
                           "from messages_fts_trigram f join messages m on m.id = f.rowid "
                           "left join sessions s on s.id = m.session_id where messages_fts_trigram match ? "
                           "and m.role in ('user','assistant') order by m.timestamp desc limit ?")
                    args = (" AND ".join(f'"{t}"' for t in terms), limit * 4)
                else:
                    sql = ("select m.session_id sid, m.role, m.content, m.timestamp ts, s.title from messages m "
                           "left join sessions s on s.id = m.session_id where m.role in ('user','assistant') and "
                           + " and ".join("m.content like ?" for _ in terms) + " order by m.timestamp desc limit ?")
                    args = (*[f"%{t}%" for t in terms], limit * 4)
                for r in con.execute(sql, args).fetchall():
                    if not str(r["content"] or "").startswith("[CONTEXT COMPACTION"):
                        found.append((r["ts"] or 0, p, dict(r)))
        except sqlite3.Error as e:
            found.append((0, p, {"sid": "-", "role": "error", "content": str(e), "title": "read error"}))
    if not found:
        return f"Nothing in conversations for: {query}"
    found.sort(key=lambda x: x[0], reverse=True)
    seen, out = {}, []
    for ts, p, r in found:
        k = (p, r["sid"])
        if seen.get(k, 0) >= 2:
            continue
        seen[k] = seen.get(k, 0) + 1
        who = OWNER if r["role"] == "user" else "assistant"
        out.append(f"[{_date(ts)}] profile={p} session={r['sid']} \"{(r.get('title') or '')[:60]}\"\n"
                   f"{who}: {_snippet(r['content'], terms)}")
        if len(out) >= limit:
            break
    return "\n\n".join(out)


@mcp.tool()
def read_session(session: str, profile: str = "default", start: int = 0, limit: int = 30) -> str:
    """Read one Hermes conversation by session id (from conversations()). Only user and assistant
    turns, each cut to ~1500 chars. Use start to paginate. Read-only."""
    db = _state_db(profile)
    if not db:
        return f"Profile '{profile}' does not exist or cannot be read."
    limit, start = max(1, min(int(limit), 60)), max(0, int(start))
    with _ro(db) as con:
        s = con.execute("select title, started_at, source from sessions where id = ?", (session,)).fetchone()
        if not s:
            return f"Session '{session}' not found in profile {profile}."
        rows = con.execute("select role, content, timestamp from messages where session_id = ? and role in "
                           "('user','assistant') and coalesce(content,'') <> '' order by id limit ? offset ?",
                           (session, limit, start)).fetchall()
    body = []
    for r in rows:
        t = (r["content"] or "").strip()
        body.append(f"[{_date(r['timestamp'])}] {OWNER if r['role'] == 'user' else 'assistant'}: "
                    + (t[:1500] + " …[cut]" if len(t) > 1500 else t))
    return f"Session {session} \"{s['title'] or ''}\" ({s['source']}, {_date(s['started_at'])})\n\n" + "\n\n".join(body)


@mcp.tool()
def profile_memory(profile: str) -> str:
    """Core memory (USER.md and MEMORY.md) of one Hermes profile / specialist bot. Read-only."""
    p, err = _profile(profile)
    if not p or p == "default":
        return err or core("both")
    parts = [f"# {p}/{n}\n\n" + (PROFILES / p / "memories" / n).read_text(encoding="utf-8").strip()
             for n in ("USER.md", "MEMORY.md") if (PROFILES / p / "memories" / n).exists()]
    return "\n\n---\n\n".join(parts) or f"{p} has no saved memory."


# ----------------------------------------------------------------- write
@mcp.tool()
def remember(fact: str, category: str = "general", origin: str = "inferred",
             entities: str = "", keywords: str = "") -> str:
    """Store ONE durable fact straight into the shared deep memory. Hermes sees it immediately.
    Use for what matters in any future session: a decision or preference of the user, an
    environment fact (port, path, account, version), a measured result. NOT task progress (use note()).
    One standalone sentence (100-250 chars) starting with the term someone would search, with a date.
    category: user_pref | project | tool | general. origin: said | confirmed | inferred.
    entities / keywords: comma separated. Run search() first to avoid duplicates. Never store secrets."""
    fact = (fact or "").strip()
    if len(fact) < 15:
        return "Too short: write one standalone sentence."
    if SECRET.search(fact):
        return "Refused: looks like it contains a secret."
    payload = {"content": fact[:600], "category": category, "origin": origin, "instance": INSTANCE,
               "entities": [e.strip() for e in entities.split(",") if e.strip()],
               "keywords": [k.strip() for k in keywords.split(",") if k.strip()]}
    cmd = [hermes_python(), str(HERE / "write_fact.py"), "--json", json.dumps(payload, ensure_ascii=False),
           "--db", str(FACT_DB), "--hermes-home", str(HERMES)]
    try:
        r = _run(cmd, timeout=60)
    except subprocess.TimeoutExpired:
        return "Failed: the fact store did not answer in 60 s (busy). Try again in a minute."
    line = next((l for l in reversed(r.stdout.splitlines()) if l.startswith("{")), "")
    try:
        out = json.loads(line)
    except Exception:
        return f"Failed to store: {(r.stderr or r.stdout)[-400:]}"
    if not out.get("ok"):
        return f"Refused: {out.get('error')}"
    return f"Stored as fact #{out['fact_id']}." if out.get("created") else f"Already existed (#{out['fact_id']})."


@mcp.tool()
def note(text: str, type: str = "action", project: str = "", files: str = "") -> str:
    """Journal what you did so Hermes learns it. Call after every action that changed something
    (file edited, config/service changed, install), every decision with the user, every error solved.
    1-3 standalone sentences: what, where (path/port/service) and why.
    type: action | decision | fact | error | todo. No secrets."""
    text = (text or "").strip()
    if len(text) < 10:
        return "Too short: say what, where and why."
    if SECRET.search(text):
        return "Refused: looks like it contains a secret."
    with _notes() as con:
        cur = con.execute("insert into notes (instance, type, project, text, files) values (?,?,?,?,?)",
                          (INSTANCE, type if type in NOTE_TYPES else "action", project[:80], text[:2000], files[:500]))
        return f"Noted #{cur.lastrowid}."


@mcp.tool()
def notes(query: str = "", limit: int = 15) -> str:
    """Read the journal written by the clone(s). Without query: the most recent. Read-only."""
    limit = max(1, min(int(limit), 50))
    with _notes() as con:
        terms = [t for t in query.split() if len(t) > 2][:6]
        if terms:
            cond = " and ".join("(text like ? or project like ? or files like ?)" for _ in terms)
            rows = con.execute(f"select * from notes where {cond} order by id desc limit ?",
                               (*[x for t in terms for x in (f"%{t}%",) * 3], limit)).fetchall()
        else:
            rows = con.execute("select * from notes order by id desc limit ?", (limit,)).fetchall()
    return "\n".join(f"#{r['id']} [{r['created_at'][:16]}] ({r['instance']}/{r['type']}) {r['text']}"
                     + (f" | files: {r['files']}" if r["files"] else "") for r in rows) or "No notes yet."


@mcp.tool()
def file_lock(path: str, action: str = "lock", reason: str = "", minutes: int = 30) -> str:
    """Advisory lock so Hermes and the clone don't edit the same file/folder at the same time.
    Lock before editing shared config or scripts; unlock when done. action: lock | unlock | list.
    If it is LOCKED by another owner, do not edit: tell the user."""
    target = (path or "").strip().replace("\\", "/").rstrip("/").lower()
    now = time.time()
    with _notes() as con:
        con.execute("delete from locks where expires_at < ?", (now,))
        if action == "list" or not target:
            rows = con.execute("select * from locks order by created_at").fetchall()
            return "\n".join(f"{r['path']} — {r['owner']} since {r['created_at'][11:16]} ({r['reason']})"
                             for r in rows) or "No active locks."
        if action == "unlock":
            con.execute("delete from locks where path = ? and owner = ?", (target, INSTANCE))
            return "Unlocked."
        cur = con.execute("select * from locks where ? like path || '%' or path like ? || '%'", (target, target)).fetchone()
        if cur and cur["owner"] != INSTANCE:
            return f"LOCKED by {cur['owner']} since {cur['created_at'][11:16]} ({cur['reason']}). Do not edit."
        con.execute("insert or replace into locks (path, owner, reason, expires_at) values (?,?,?,?)",
                    (target, INSTANCE, reason[:200], now + max(1, min(int(minutes), 240)) * 60))
        return f"Locked for {INSTANCE} for {minutes} min."


# ----------------------------------------------------------------- act
@mcp.tool()
def ask_profile(profile: str, request: str, timeout_s: int = 300) -> str:
    """Send a request to a Hermes profile (a specialist bot, or 'default' = the main Hermes agent)
    and return its answer. It runs with its own tools, memory and accounts, in a fresh session.
    Use 'default' for what only Hermes can reach (its gateway tools, phone integrations, plugins).
    The request must be self-contained. Takes 20 s to a few minutes."""
    p, err = _profile(profile)
    if not p:
        return err
    if len((request or "").strip()) < 5:
        return "Empty request."
    limit = max(30, min(int(timeout_s), 900))
    cmd = [hermes_bin()] + ([] if p == "default" else ["-p", p]) + ["chat", "-Q", "--source", "tool", "-q", request.strip()]
    try:
        r = _run(cmd, timeout=limit)
    except subprocess.TimeoutExpired:
        return f"{p} did not finish in {limit} s; it may still be running. Check later with conversations(profile='{p}')."
    txt = re.sub(r"\n*session_id:\s*\S+\s*$", "", (r.stdout or "").strip()).strip()
    return f"[{p}] {txt[-8000:]}" if txt else f"{p} returned no text (exit {r.returncode}): {(r.stderr or '')[-400:]}"


@mcp.tool()
def crons(profile: str = "default") -> str:
    """List scheduled jobs (cron) of a Hermes profile: id, name, schedule, state. Read-only."""
    p, err = _profile(profile)
    if not p:
        return err
    r = _run([hermes_bin()] + ([] if p == "default" else ["-p", p]) + ["cron", "list"])
    return (r.stdout or r.stderr or "no output").strip()[-8000:]


@mcp.tool()
def create_cron(schedule: str, prompt: str, name: str, profile: str = "default", script: str = "") -> str:
    """Create a job in the Hermes scheduler (runs even when this harness is closed). Only when the user asks.
    schedule: '30m', 'every 2h' or cron '0 9 * * *'. prompt: self-contained instruction the profile's agent
    runs each time (may be empty when script is set). script: optional script in $HERMES_HOME/scripts whose
    stdout is delivered without an agent. Delivery target comes from HIVEMIND_CRON_DELIVER."""
    p, err = _profile(profile)
    if not p:
        return err
    if not schedule.strip() or not (prompt.strip() or script.strip()):
        return "Missing schedule or prompt/script."
    cmd = [hermes_bin()] + ([] if p == "default" else ["-p", p]) + [
        "cron", "create", "--name", (name or "clone-job")[:60], "--deliver", CRON_DELIVER]
    if script.strip():
        cmd += ["--script", script.strip(), "--no-agent"]
    cmd += [schedule.strip()] + ([prompt.strip()] if prompt.strip() else [])
    r = _run(cmd)
    return (r.stdout or r.stderr or "no output").strip()[-2000:]


@mcp.tool()
def cron_action(job_id: str, action: str, profile: str = "default") -> str:
    """Run now, pause, resume or remove an existing Hermes cron job. action: run | pause | resume | remove.
    Only when the user asks."""
    p, err = _profile(profile)
    if not p:
        return err
    if action not in ("run", "pause", "resume", "remove"):
        return "action: run | pause | resume | remove"
    if not re.fullmatch(r"[0-9a-f]{6,32}", (job_id or "").strip()):
        return "Invalid job_id (take it from crons())."
    r = _run([hermes_bin()] + ([] if p == "default" else ["-p", p]) + ["cron", action, job_id.strip()])
    return (r.stdout or r.stderr or "no output").strip()[-2000:]


if __name__ == "__main__":
    mcp.run()
