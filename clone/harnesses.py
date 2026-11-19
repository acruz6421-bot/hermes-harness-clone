# -*- coding: utf-8 -*-
"""One small adapter per coding harness. Everything harness-specific lives here.

  antigravity  stable        Google Antigravity CLI (agy), tested end to end
  claude       experimental  Claude Code CLI (claude)
  codex        experimental  OpenAI Codex CLI (codex)

Each adapter knows: where its binary is, how to run one headless turn (and resume a session),
how to read its JSON event stream, where its global instruction file lives, and how to register
an MCP server.
"""
import os
import shutil
from pathlib import Path

HOME = Path.home()


class Harness:
    name = ""
    experimental = False
    soul_file: Path = HOME
    soul_limit = 40_000          # bytes kept in the instruction file
    bin_names = ()
    extra_bins = ()

    def find_bin(self):
        explicit = os.environ.get("HARNESS_BIN")
        if explicit:
            return explicit
        for n in self.bin_names:
            b = shutil.which(n)
            if b:
                return b
        for p in self.extra_bins:
            if Path(p).exists():
                return str(p)
        return None

    # one headless turn; session=None starts a new conversation
    def turn_cmd(self, binary, prompt, session=None, model=None, dirs=()):
        raise NotImplementedError

    def parse(self, ev: dict, st: dict):
        """Update st (session, text, error, ok, timeout, tokens) from one JSON event.
        Return a tool name when a tool call starts (for the live status line), else None."""
        raise NotImplementedError

    def mcp_add_cmd(self, binary, name, python, script, env: dict):
        raise NotImplementedError

    def mcp_remove_cmd(self, binary, name):
        return [binary, "mcp", "remove", name]


class Antigravity(Harness):
    name = "antigravity"
    soul_file = HOME / ".gemini" / "GEMINI.md"   # read by the agy CLI AND the desktop app
    soul_limit = 23_000                           # bigger files get ignored
    bin_names = ("agy", "agy.exe")
    extra_bins = (HOME / ".gemini" / "bin" / "agy.exe", HOME / ".gemini" / "bin" / "agy")

    def turn_cmd(self, binary, prompt, session=None, model=None, dirs=()):
        cmd = [binary, "-p", prompt, "--dangerously-skip-permissions", "--print-timeout", "1500s",
               "--output-format", "stream-json"]
        if model:
            cmd += ["--model", model]
        for d in dirs:
            cmd += ["--add-dir", d]
        if session:
            cmd += ["--conversation", session]
        return cmd

    def parse(self, ev, st):
        st["session"] = ev.get("conversation_id") or (ev.get("result") or {}).get("conversation_id") or st.get("session")
        if ev.get("event") == "result":
            r = ev.get("result") or {}
            st["text"] = (r.get("response") or "").strip()
            st["ok"] = r.get("status") == "SUCCESS"
            st["error"] = str(r.get("error") or "")
            st["timeout"] = r.get("finished_reason") == "print_timeout"
            st["tokens"] = (r.get("usage") or {}).get("total_tokens")
        su = ev.get("step_update") or {}
        if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
            return su.get("tool_name") or "tool"
        return None

    def mcp_add_cmd(self, binary, name, python, script, env):
        cmd = [binary, "mcp", "add"]
        for k, v in env.items():
            cmd += ["--env", f"{k}={v}"]
        return cmd + [name, python, script]


class ClaudeCode(Harness):
    name = "claude"
    experimental = True
    soul_file = HOME / ".claude" / "CLAUDE.md"
    bin_names = ("claude", "claude.exe", "claude.cmd")

    def turn_cmd(self, binary, prompt, session=None, model=None, dirs=()):
        cmd = [binary, "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        for d in dirs:
            cmd += ["--add-dir", d]
        if session:
            cmd += ["--resume", session]
        return cmd

    def parse(self, ev, st):
        st["session"] = ev.get("session_id") or st.get("session")
        if ev.get("type") == "result":
            st["text"] = (ev.get("result") or "").strip()
            st["ok"] = not ev.get("is_error") and ev.get("subtype") == "success"
            st["error"] = "" if st["ok"] else str(ev.get("subtype") or ev.get("result") or "error")
            u = ev.get("usage") or {}
            st["tokens"] = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0) or None
        if ev.get("type") == "assistant":
            for c in (ev.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    return c.get("name") or "tool"
        return None

    def mcp_add_cmd(self, binary, name, python, script, env):
        cmd = [binary, "mcp", "add", "--scope", "user"]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        return cmd + [name, "--", python, script]

    def mcp_remove_cmd(self, binary, name):
        return [binary, "mcp", "remove", "--scope", "user", name]


class Codex(Harness):
    name = "codex"
    experimental = True
    soul_file = HOME / ".codex" / "AGENTS.md"
    bin_names = ("codex", "codex.exe", "codex.cmd")

    def turn_cmd(self, binary, prompt, session=None, model=None, dirs=()):
        cmd = [binary, "exec"]
        if session:
            cmd += ["resume", session]
        cmd += ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
        if model:
            cmd += ["--model", model]
        return cmd + [prompt]

    def parse(self, ev, st):
        t = ev.get("type")
        if t == "thread.started":
            st["session"] = ev.get("thread_id") or st.get("session")
        item = ev.get("item") or {}
        if t == "item.completed" and item.get("type") == "agent_message":
            st["text"] = (item.get("text") or "").strip()
        if t == "turn.completed":
            st["ok"] = True
            u = ev.get("usage") or {}
            st["tokens"] = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0) or None
        if t in ("turn.failed", "error"):
            st["ok"] = False
            st["error"] = str((ev.get("error") or {}).get("message") or ev.get("message") or "error")
        if t == "item.started" and item.get("type") in ("command_execution", "mcp_tool_call", "file_change", "web_search"):
            return item.get("tool") or item.get("type")
        return None

    def mcp_add_cmd(self, binary, name, python, script, env):
        cmd = [binary, "mcp", "add", name]
        for k, v in env.items():
            cmd += ["--env", f"{k}={v}"]
        return cmd + ["--", python, script]


HARNESSES = {h.name: h for h in (Antigravity(), ClaudeCode(), Codex())}


def get(name: str) -> Harness:
    h = HARNESSES.get((name or "antigravity").strip().lower())
    if not h:
        raise SystemExit(f"Unknown harness '{name}'. Use: {', '.join(HARNESSES)}")
    return h
