# -*- coding: utf-8 -*-
"""Telegram bridge: your own Telegram bot -> a headless coding harness (Antigravity / Claude Code / Codex),
running with your Hermes agent's personality and memory.

- Answers ONLY the owner (OWNER_ID). Every other chat is ignored silently.
- Each Telegram topic = one harness conversation (enable Threaded Mode in @BotFather's mini app).
  Without topics, the main chat keeps one conversation; /new starts another.
- Voice notes: optional OpenAI-compatible Whisper endpoint (WHISPER_URL).
- Optional SENSITIVE_REGEX: matching messages go to a Hermes profile (e.g. a local model) instead of the harness.
- Hourly turn cap (MAX_TURNS_PER_HOUR); /stop kills the running turn.
- Antigravity only: conversations sync with the desktop app (/conversations, /continue <id>).
- Single instance (lock on 127.0.0.1:LOCK_PORT).

Run: python bridge.py   (or start_bridge.bat / start_bridge.sh)
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harnesses  # noqa: E402
from settings import CFG  # noqa: E402

H = harnesses.get(CFG.get("HARNESS", "antigravity"))
if H.name == "antigravity":
    import conv_sync
else:
    conv_sync = None

WS = Path(CFG.get("WORKSPACE") or HERE / "workspace").expanduser()
INBOX, LOGS, STATE = WS / "inbox", HERE / "logs", HERE / "state.json"
TOKEN = CFG.get("TELEGRAM_TOKEN", "")
OWNER = int(CFG.get("OWNER_ID") or 0)
MAX_TURNS = int(CFG.get("MAX_TURNS_PER_HOUR", "60"))
MODEL = CFG.get("MODEL") or None
FALLBACK_MODEL = CFG.get("FALLBACK_MODEL") or None
DIRS = [d.strip() for d in CFG.get("EXTRA_DIRS", "").split(",") if d.strip()]
WHISPER = CFG.get("WHISPER_URL", "").strip()
WHISPER_LANG = CFG.get("WHISPER_LANGUAGE", "").strip()
SENSITIVE = re.compile(CFG["SENSITIVE_REGEX"], re.I) if CFG.get("SENSITIVE_REGEX") else None
SENSITIVE_PROFILE = CFG.get("SENSITIVE_PROFILE", "")
LOCK_PORT = int(CFG.get("LOCK_PORT", "9131"))
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FIRST_TURN = ("[System: first message of a new Telegram conversation. Before answering, call core() "
              "from the hivemind MCP server, silently. Answer short, phone-friendly.]\n\n")
RESUME = "Continue where you stopped, without redoing finished work, and end with the result."
TOOL_LABELS = {"run_command": "💻 terminal", "Bash": "💻 terminal", "command_execution": "💻 terminal",
               "read_file": "📖 reading", "view_file": "📖 reading", "Read": "📖 reading",
               "write_to_file": "✍️ writing", "Write": "✍️ writing", "Edit": "✍️ editing",
               "replace_file_content": "✍️ editing", "file_change": "✍️ editing",
               "search_web": "🔎 web", "WebSearch": "🔎 web", "web_search": "🔎 web",
               "grep_search": "🔎 searching", "Grep": "🔎 searching"}

state_lock = threading.Lock()
queue, queue_cv = deque(), threading.Condition()
turns = deque()
current = {"proc": None, "start": 0.0}
TOPICS = False


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOGS / f"bridge-{datetime.now():%Y%m%d}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("offset", 0)
    s.setdefault("main", None)       # session of the main chat (no topic)
    s.setdefault("topics", {})       # topic id -> session id
    s.setdefault("model", MODEL)
    return s


def save_state():
    with state_lock:
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(ST, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE)


def session_of(topic):
    return ST["topics"].get(str(topic)) if topic else ST["main"]


def set_session(topic, sid):
    if topic:
        ST["topics"][str(topic)] = sid
    else:
        ST["main"] = sid
    save_state()


def topic_of_session(sid):
    return next((int(t) for t, s in ST["topics"].items() if s == sid), None)


# ----------------------------------------------------------------- Telegram
def tg(method, **kw):
    for _ in range(3):
        try:
            j = requests.post(f"{API}/{method}", json=kw, timeout=70 if method == "getUpdates" else 30).json()
            if j.get("ok"):
                return j["result"]
            if j.get("error_code") == 429:
                time.sleep(int(j.get("parameters", {}).get("retry_after", 3)) + 1)
                continue
            if "not modified" not in str(j.get("description")):
                log(f"tg {method}: {j.get('description')}")
            return None
        except Exception as e:
            log(f"tg {method} failed: {e}")
            time.sleep(2)
    return None


def send(text, reply_to=None, topic=None):
    text = (text or "").strip() or "(empty)"
    last = None
    while text:
        chunk = text[:4000]
        if len(text) > 4000:
            cut = chunk.rfind("\n")
            chunk = chunk[:cut] if cut > 1500 else chunk
        kw = {"chat_id": OWNER, "text": chunk, "disable_web_page_preview": True}
        if topic:
            kw["message_thread_id"] = topic
        if reply_to:
            kw["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
            reply_to = None
        last = tg("sendMessage", **kw)
        text = text[len(chunk):].lstrip("\n")
    return last


def download(file_id, name=None):
    info = tg("getFile", file_id=file_id)
    if not info:
        return None
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name or Path(info["file_path"]).name)
    dest = INBOX / f"{datetime.now():%Y%m%d-%H%M%S}-{name}"
    dest.write_bytes(requests.get(f"{FILE_API}/{info['file_path']}", timeout=120).content)
    return dest


def transcribe(path: Path):
    if not WHISPER:
        return ""
    try:
        with open(path, "rb") as f:
            data = {"model": CFG.get("WHISPER_MODEL", "whisper-1")}
            if WHISPER_LANG:
                data["language"] = WHISPER_LANG
            r = requests.post(WHISPER, files={"file": (path.name, f)}, data=data, timeout=300,
                              headers={"Authorization": f"Bearer {CFG['WHISPER_KEY']}"} if CFG.get("WHISPER_KEY") else {})
        return (r.json().get("text") or "").strip()
    except Exception as e:
        log(f"whisper failed: {e}")
        return ""


# ----------------------------------------------------------------- harness
def run_turn(prompt, session, model, status_id, topic):
    cmd = H.turn_cmd(H.find_bin(), prompt, session, model, DIRS)
    env = dict(os.environ, HIVEMIND_INSTANCE=CFG.get("INSTANCE_NAME", f"clone-{H.name}"))
    env.pop("PYTHONPATH", None)
    st, steps, last_edit = {"session": session, "text": "", "ok": False, "error": "", "timeout": False}, [], 0.0
    with open(LOGS / f"{H.name}-{datetime.now():%Y%m%d}.jsonl", "a", encoding="utf-8") as raw:
        p = subprocess.Popen(cmd, cwd=str(WS), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                             creationflags=NO_WINDOW)
        current["proc"] = p
        tail = deque(maxlen=20)
        for line in p.stdout:
            raw.write(line)
            if not line.startswith("{"):
                tail.append(line.strip())
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            tool = H.parse(ev, st)
            if tool:
                label = TOOL_LABELS.get(tool, "🧠 memory" if "hivemind" in tool else f"⚙️ {tool}")
                if not steps or steps[-1] != label:
                    steps.append(label)
                if status_id and time.time() - last_edit > 4:
                    last_edit = time.time()
                    tg("editMessageText", chat_id=OWNER, message_id=status_id,
                       text=f"⏳ {int(time.time() - current['start'])}s · " + " → ".join(steps[-6:]))
        p.wait()
        current["proc"] = None
    if not st["text"] and not st["error"]:
        st["error"] = f"{H.name} exited with code {p.returncode}. " + " ".join(tail)[-400:]
    return st


def ask_hermes_profile(profile, text):
    hermes = CFG.get("HERMES_BIN") or "hermes"
    try:
        r = subprocess.run([hermes, "-p", profile, "chat", "-Q", "--source", "tool", "-q", text],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return re.sub(r"\n*session_id:\s*\S+\s*$", "", (r.stdout or "").strip()) or (r.stderr or "")[-500:]
    except subprocess.TimeoutExpired:
        return f"{profile} did not finish in 15 min."


def answer(text, msg_id, topic):
    now = time.time()
    while turns and now - turns[0] > 3600:
        turns.popleft()
    if len(turns) >= MAX_TURNS:
        send(f"🔴 Hourly cap of {MAX_TURNS} turns reached. Frees up in {int((3600 - (now - turns[0])) / 60) + 1} min.",
             msg_id, topic)
        return
    if SENSITIVE and SENSITIVE_PROFILE and SENSITIVE.search(text):
        log(f"sensitive -> {SENSITIVE_PROFILE}")
        send(f"[{SENSITIVE_PROFILE}] " + ask_hermes_profile(SENSITIVE_PROFILE, text), msg_id, topic)
        return

    turns.append(now)
    status = tg("sendMessage", chat_id=OWNER, text="⏳ thinking…", **({"message_thread_id": topic} if topic else {}))
    status_id = status["message_id"] if status else None
    current["start"] = time.time()
    session = session_of(topic)
    model = ST.get("model")
    prompt = text if session else FIRST_TURN + text
    if session and conv_sync:
        try:
            conv_sync.to_cli(session)
        except Exception as e:
            log(f"sync app->cli failed: {e}")

    st = run_turn(prompt, session, model, status_id, topic)
    if not st["ok"] and st["timeout"] and st["session"]:
        st = run_turn(RESUME, st["session"], model, status_id, topic)
    if not st["ok"] and not st["text"] and FALLBACK_MODEL and re.search(
            r"quota|exhaust|429|rate|capacity|unavailable|overloaded|invalid model", st["error"], re.I):
        log(f"{model} failed ({st['error'][:120]}), trying {FALLBACK_MODEL}")
        st = run_turn(text if st["session"] else prompt, st["session"], FALLBACK_MODEL, status_id, topic)

    sid = st["session"]
    if sid and sid != session:
        if topic and not session and TOPICS:
            name = re.sub(r"\[[^\]]*\]", "", text).strip().split("\n")[0][:60] or "Conversation"
            tg("editForumTopic", chat_id=OWNER, message_thread_id=topic, name=name)
        set_session(topic, sid)
    if sid and conv_sync:
        try:
            conv_sync.to_app(sid)
        except Exception as e:
            log(f"sync cli->app failed: {e}")
    if status_id:
        tg("deleteMessage", chat_id=OWNER, message_id=status_id)
    log(f"turn ok={st['ok']} {time.time() - current['start']:.0f}s session={str(sid)[:8]} tokens={st.get('tokens')}")
    send(st["text"] or f"🔴 Failed: {st['error'][:600] or 'no answer'}", msg_id, topic)


# ----------------------------------------------------------------- commands
HELP = (f"Your Hermes agent, running on {H.name}. Same memory.\n"
        "Each topic = a separate conversation (write in 'All messages' to open a new one).\n"
        "/new [name] — new topic / conversation\n/stop — stop the current turn\n"
        "/model [id] — show or change the model\n/status — state, queue and hourly cap\n/soul — rebuild the personality"
        + ("\n/conversations — recent conversations (desktop app + Telegram)\n/continue <id> — bring an app conversation here"
           if conv_sync else ""))


def command(txt, msg_id, topic):
    global TOPICS
    if not TOPICS:
        TOPICS = bool((tg("getMe") or {}).get("has_topics_enabled"))
    c, _, arg = txt.partition(" ")
    c = c.split("@")[0].lower()
    if c in ("/start", "/help"):
        send(HELP, msg_id, topic)
    elif c == "/new":
        name = arg.strip()[:60] or f"Conversation {datetime.now():%m-%d %H:%M}"
        t = tg("createForumTopic", chat_id=OWNER, name=name) if TOPICS else None
        if t:
            ST["topics"][str(t["message_thread_id"])] = None
            save_state()
            send(f"✅ New topic: {name}. Write here.", None, t["message_thread_id"])
        else:
            set_session(topic, None)
            send("✅ New conversation. Memory stays the same.", msg_id, topic)
    elif c == "/stop":
        p = current.get("proc")
        if p and p.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                p.kill()
            send("✅ Stopped.", msg_id, topic)
        else:
            send("Nothing running.", msg_id, topic)
        with queue_cv:
            queue.clear()
    elif c == "/model":
        if arg.strip():
            ST["model"] = arg.strip()
            save_state()
        send(f"Model: {ST.get('model') or '(harness default)'}\nFallback: {FALLBACK_MODEL or '-'}", msg_id, topic)
    elif c == "/status":
        now = time.time()
        send(f"Harness: {H.name}{' (experimental)' if H.experimental else ''}\nModel: {ST.get('model') or 'default'}\n"
             f"Session: {str(session_of(topic))[:8]}\nTopics: {'on' if TOPICS else 'off'}\n"
             f"Running: {'yes' if current.get('proc') else 'no'}\nQueue: {len(queue)}\n"
             f"Turns last hour: {sum(1 for t in turns if now - t < 3600)}/{MAX_TURNS}", msg_id, topic)
    elif c == "/soul":
        r = subprocess.run([sys.executable, str(HERE / "build_soul.py")], capture_output=True, text=True)
        send("✅ " + (r.stdout or r.stderr).strip()[-300:], msg_id, topic)
    elif c == "/conversations" and conv_sync:
        rows = conv_sync.recent(10)
        lines = [f"{'👉 ' if r['id'] == session_of(topic) else ''}{'📱' if r['origin'] == 'cli' else '🖥️'} "
                 f"{r['id'][:8]} · {r['when'][5:]} · {r['steps']} steps\n   {r['title']}" for r in rows]
        send("Recent conversations (🖥️ app · 📱 Telegram/CLI):\n\n" + "\n".join(lines or ["(none)"])
             + "\n\nContinue one here: /continue <id>", msg_id, topic)
    elif c == "/continue" and conv_sync:
        sid = conv_sync.resolve(arg)
        if not sid:
            send("Conversation not found. See /conversations.", msg_id, topic)
            return True
        conv_sync.to_cli(sid)
        target = topic_of_session(sid)
        if not target and TOPICS:
            title = next((r["title"] for r in conv_sync.recent(60) if r["id"] == sid), sid[:8])
            t = tg("createForumTopic", chat_id=OWNER, name=("🖥️ " + title)[:120])
            target = t and t["message_thread_id"]
        if target:
            ST["topics"][str(target)] = sid
            save_state()
            send(f"✅ Conversation {sid[:8]} lives in this topic now. Continue here.", None, target)
        else:
            set_session(topic, sid)
            send(f"✅ Continuing conversation {sid[:8]}.", msg_id, topic)
    else:
        return False
    return True


# ----------------------------------------------------------------- loop
def prepare(m, topic):
    parts = []
    voice = m.get("voice") or m.get("audio") or m.get("video_note")
    if voice:
        f = download(voice["file_id"], voice.get("file_name") or "voice.ogg")
        t = transcribe(f) if f else ""
        if t:
            send(f"🎙️ {t}", m["message_id"], topic)
            parts.append(t)
        elif f:
            parts.append(f"[Voice message saved at {f.as_posix()} (no transcription configured)]")
    if m.get("photo"):
        f = download(m["photo"][-1]["file_id"], "photo.jpg")
        if f:
            parts.append(f"[Image attached by the user: {f.as_posix()}. Open and look at it before answering]")
    if m.get("document"):
        f = download(m["document"]["file_id"], m["document"].get("file_name"))
        if f:
            parts.append(f"[File attached by the user: {f.as_posix()}]")
    rt = m.get("reply_to_message") or {}
    if rt.get("text") and not rt.get("forum_topic_created") and rt.get("message_id") != m.get("message_thread_id"):
        parts.insert(0, f"[Replying to: \"{rt['text'][:600]}\"]")
    if m.get("text") or m.get("caption"):
        parts.append(m.get("text") or m.get("caption"))
    return "\n".join(parts).strip() or None


def worker():
    while True:
        with queue_cv:
            while not queue:
                queue_cv.wait()
            text, msg_id, topic = queue.popleft()
        try:
            answer(text, msg_id, topic)
        except Exception:
            log("turn error:\n" + traceback.format_exc())
            send("🔴 Internal bridge error (see logs).", msg_id, topic)


def hourly_soul():
    while True:
        try:
            subprocess.run([sys.executable, str(HERE / "build_soul.py")], capture_output=True, timeout=60)
        except Exception:
            pass
        time.sleep(3600)


def main():
    global TOPICS
    if not TOKEN or not OWNER:
        sys.exit("Set TELEGRAM_TOKEN and OWNER_ID in clone/.env (see .env.example).")
    if not H.find_bin():
        sys.exit(f"{H.name} binary not found. Install it or set HARNESS_BIN in clone/.env.")
    try:
        lock = socket.socket()
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
    except OSError:
        sys.exit(f"Bridge already running (port {LOCK_PORT} busy).")
    TOPICS = bool((tg("getMe") or {}).get("has_topics_enabled"))
    log(f"bridge up: harness={H.name} topics={'on' if TOPICS else 'off'}")
    cmds = [("new", "New conversation (memory stays)"), ("stop", "Stop the current turn"),
            ("model", "Show/change the model"), ("status", "State, queue and hourly cap"), ("soul", "Rebuild the personality")]
    if conv_sync:
        cmds += [("conversations", "Recent desktop/Telegram conversations"), ("continue", "Continue an app conversation here")]
    tg("setMyCommands", commands=[{"command": c, "description": d} for c, d in cmds])
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=hourly_soul, daemon=True).start()
    while True:
        updates = tg("getUpdates", offset=ST["offset"], timeout=50, allowed_updates=["message"]) or []
        for u in updates:
            ST["offset"] = u["update_id"] + 1
            save_state()
            m = u.get("message") or {}
            if (m.get("from") or {}).get("id") != OWNER or (m.get("chat") or {}).get("id") != OWNER:
                continue
            if m.get("forum_topic_created") or m.get("forum_topic_edited"):
                continue
            topic = m.get("message_thread_id") if m.get("is_topic_message") else None
            txt = (m.get("text") or "").strip()
            if txt.startswith("/") and command(txt, m["message_id"], topic):
                continue
            text = prepare(m, topic)
            if not text:
                continue
            with queue_cv:
                if current.get("proc") is not None or queue:
                    send(f"📥 Queued ({len(queue) + 1}).", m["message_id"], topic)
                queue.append((text, m["message_id"], topic))
                queue_cv.notify()
        if not updates:
            time.sleep(0.5)


for d in (WS, INBOX, LOGS):
    d.mkdir(parents=True, exist_ok=True)
ST = load_state()

if __name__ == "__main__":
    while True:
        try:
            main()
            break
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            log("crash, restarting in 10 s:\n" + traceback.format_exc())
            time.sleep(10)
