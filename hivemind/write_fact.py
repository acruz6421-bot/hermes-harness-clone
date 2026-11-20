# -*- coding: utf-8 -*-
"""Write one fact into the Hermes fact store (memory_store.db) through the holographic plugin's API.

Runs with the PYTHON OF THE HERMES VENV (the plugin needs Hermes' dependencies). Called by
mcp_server.remember(); you never call it by hand except to test:

    <hermes-python> write_fact.py --json '{"content": "...", "category": "general", "origin": "said"}'

Before storing, it asks the store's own retriever whether a current fact already says nearly the same
(near-duplicate: nothing is stored unless "force" is true). After storing, it runs the store's write check
and returns facts that may conflict with the new one (the new fact is kept; Hermes settles the conflict).

Output: {"ok": true, "fact_id": N | null, "created": bool,
         "near_duplicate": {...} | null, "possible_conflicts": [...], "similar": [...]}
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

CATS = {"user_pref", "project", "tool", "general"}
ORIGINS = {"said", "confirmed", "inferred"}


def find_plugin(hermes_home: Path):
    """Return the folder that CONTAINS the 'holographic' package."""
    agent = Path(sys.executable).resolve().parent.parent.parent  # <hermes-agent>/venv/Scripts|bin/python
    for parent in (hermes_home / "plugins", agent / "plugins" / "memory", agent / "plugins"):
        if (parent / "holographic" / "store.py").exists():
            return parent, agent
    return None, agent


def _brief(rows, limit=3):
    return [{"fact_id": r["fact_id"], "content": r["content"][:300]} for r in list(rows or [])[:limit]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--db")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    a = ap.parse_args()
    home = Path(a.hermes_home).expanduser()
    d = json.loads(a.json)
    content = (d.get("content") or "").strip()
    if len(content) < 15:
        print(json.dumps({"ok": False, "error": "fact too short"}))
        return 1
    parent, agent = find_plugin(home)
    if not parent:
        print(json.dumps({"ok": False, "error": "holographic memory plugin not found (enable it in Hermes)"}))
        return 1
    sys.path[:0] = [str(agent), str(parent)]
    logging.disable(logging.WARNING)
    from holographic.store import MemoryStore
    try:
        from holographic.retrieval import FactRetriever
    except Exception:  # older plugin: store without the checks
        FactRetriever = None

    origin = d.get("origin") if d.get("origin") in ORIGINS else "inferred"
    kws = [k.strip() for k in (d.get("keywords") or []) if k and k.strip()][:12]
    store = MemoryStore(db_path=a.db or str(home / "memory_store.db"))
    retriever = FactRetriever(store=store) if FactRetriever else None
    out = {"ok": True, "fact_id": None, "created": False, "near_duplicate": None,
           "possible_conflicts": [], "similar": []}

    exact = store.read("SELECT fact_id FROM facts WHERE content = ?", (content,))
    if not exact and not d.get("force") and retriever is not None and hasattr(retriever, "near_duplicate"):
        try:
            dup = retriever.near_duplicate(content)
        except Exception:
            dup = None
        if dup is not None:
            row = store.read("SELECT fact_id, content FROM facts WHERE fact_id = ?", (dup,))
            out["near_duplicate"] = _brief(row, 1)[0] if row else {"fact_id": dup, "content": ""}
            print(json.dumps(out, ensure_ascii=False))
            return 0

    fid, created = store.add_fact_status(
        content, category=d.get("category") if d.get("category") in CATS else "general",
        tags=("keywords: " + ", ".join(kws)) if kws else "", trust=store.start_trust(origin),
        entities=[e.strip() for e in (d.get("entities") or []) if e and e.strip()][:8],
        source={"via": "hivemind", "platform": d.get("instance") or "clone", "origin": origin})
    out.update(fact_id=fid, created=created)
    if created and retriever is not None and hasattr(retriever, "write_check"):
        try:
            check = retriever.write_check(fid)
            out["possible_conflicts"] = _brief(check.get("possible_conflicts"))
            out["similar"] = _brief(check.get("similar"))
        except Exception as e:  # the fact is stored; the check is only advisory
            out["check_error"] = str(e)[:200]
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
