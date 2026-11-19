# -*- coding: utf-8 -*-
"""Antigravity only: move a conversation between the desktop app and the agy CLI.

Desktop app:  ~/.gemini/antigravity/conversations/<id>.db
agy CLI:      ~/.gemini/antigravity-cli/conversations/<id>.db
Same SQLite format. Copying the .db lets the other side continue the conversation
(`agy --conversation <id>` resumes a conversation started in the app, and vice versa).

Never regresses: copies only from the copy with MORE steps. Uses the sqlite backup API (WAL-safe).
"""
import sqlite3
from pathlib import Path

GEM = Path.home() / ".gemini"
APP = GEM / "antigravity" / "conversations"
CLI = GEM / "antigravity-cli" / "conversations"
INDEX = GEM / "antigravity" / "conversation_summaries.db"


def steps(p: Path) -> int:
    if not p.exists():
        return -1
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
        n = con.execute("select count(*) from steps").fetchone()[0]
        con.close()
        return n
    except Exception:
        return -1


def _copy(src: Path, dst: Path) -> bool:
    if steps(src) <= steps(dst):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    a = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=10)
    b = sqlite3.connect(dst.as_posix(), timeout=10)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    return True


def to_app(conv_id: str) -> bool:
    """After a Telegram turn: make the conversation visible/continuable in the desktop app."""
    return _copy(CLI / f"{conv_id}.db", APP / f"{conv_id}.db")


def to_cli(conv_id: str) -> bool:
    """Before a Telegram turn: bring the newest version from the desktop app."""
    return _copy(APP / f"{conv_id}.db", CLI / f"{conv_id}.db")


def resolve(prefix: str):
    prefix = (prefix or "").strip().lower()
    if len(prefix) < 4:
        return None
    for d in (APP, CLI):
        for p in d.glob(f"{prefix}*.db"):
            return p.stem
    return None


def recent(n: int = 10):
    """Most recent conversations (app + CLI) from the desktop app's index."""
    if not INDEX.exists():
        return []
    con = sqlite3.connect(f"file:{INDEX.as_posix()}?mode=ro", uri=True, timeout=5)
    rows = con.execute(
        "select conversation_id, title, preview, step_count, last_modified_time, source "
        "from conversation_summaries where coalesce(parent_conversation_id,'') = '' "
        "order by last_modified_time desc limit ?", (n,)).fetchall()
    con.close()
    return [{"id": cid, "title": (t or p or "(untitled)").strip()[:60], "steps": s,
             "when": (w or "")[:16].replace("T", " "), "origin": "cli" if "CLI" in (src or "") else "app"}
            for cid, t, p, s, w, src in rows]
