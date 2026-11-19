# hermes-harness-clone

Run your **Hermes Agent** inside a coding harness (Google **Antigravity**, Claude Code, Codex) as the
**same agent**: same personality, same memory, same bots, and talk to it from **Telegram** with one topic per conversation.

```
           Telegram (your own bot, 1 topic = 1 conversation)
                         │
                   clone/bridge.py ── hourly cap · owner-only · voice (optional)
                         │
     ┌───────────────────┼────────────────────┐
     │   harness (agy / claude / codex) headless, resumes sessions
     │        │ reads instruction file ← clone/build_soul.py ← Hermes SOUL.md + USER.md
     │        │ calls MCP
     │   hivemind/mcp_server.py ── reads/writes Hermes memory, asks Hermes profiles, manages crons
     └───────────────────┴──────────── $HERMES_HOME (memory_store.db, state.db, profiles/, cron)
```

| Harness | Status | Instruction file | Notes |
|---|---|---|---|
| Antigravity (`agy`) | **stable**, tested | `~/.gemini/GEMINI.md` | Also applies to the desktop app. Telegram ↔ desktop conversation hand-off. |
| Claude Code (`claude`) | experimental | `~/.claude/CLAUDE.md` | Adapter written, not tested end to end. |
| Codex (`codex`) | experimental | `~/.codex/AGENTS.md` | Adapter written, not tested end to end. |

The harness is the official, unmodified program. This repo only adds an MCP server, an instruction file and a
Telegram front end that calls the harness' documented headless mode.

## Why

- You already pay for a harness subscription with a big model; Hermes already knows you. Now both are one agent.
- Whatever the clone learns goes into Hermes' fact store **immediately**; whatever Hermes knows, the clone can search.
- From the clone you can still reach everything that only Hermes has (`ask_profile('default', ...)`): phone
  integrations, gateway tools, plugins, specialist profiles.
- Zero extra infrastructure: no server, no vector DB, no daemon. One Python process for Telegram, one MCP server
  the harness starts itself. Cost = your harness subscription.

## Quick start (let your agent do it)

Clone the repo and tell Hermes, Claude Code or Codex:

> Read SETUP.md in this folder and set it up for me with the **antigravity** harness.

It asks you for the Telegram bot token and your Telegram id, runs `setup.py`, and tests everything.

## Quick start (by hand)

Requirements: Hermes Agent installed (holographic memory provider recommended), Python 3.10+, the harness CLI logged in.

```bash
git clone https://github.com/<you>/hermes-harness-clone
cd hermes-harness-clone
python setup.py --harness antigravity      # or: claude | codex  (experimental)
start_bridge.bat                            # Windows  (./start_bridge.sh on Linux/macOS)
```

Then send `/start` to your bot. To get one topic per conversation, open **@BotFather → Open (mini app) →
your bot → Bot Settings → Threads Settings → Threaded Mode ON**.

## What the clone can do (MCP `hivemind`)

| Tool | What |
|---|---|
| `core()` | Hermes' core memory (USER.md + MEMORY.md) |
| `search()`, `about()` | Fact store search / everything about an entity |
| `conversations()`, `read_session()` | Full-text search and read of every Hermes session, all profiles |
| `profile_memory()` | Core memory of a specialist profile |
| `remember()` | Writes a fact straight into the fact store (same API Hermes uses) |
| `note()`, `notes()` | Journal of what the clone did, for Hermes to consolidate |
| `file_lock()` | Advisory locks so Hermes and the clone don't edit the same file at once |
| `ask_profile()` | Runs a request on a Hermes profile (`default` = main Hermes) and returns the answer |
| `crons()`, `create_cron()`, `cron_action()` | Hermes scheduler |

Profiles in `PRIVATE_PROFILES` are invisible to every tool.

## Telegram commands

`/new [name]` new topic · `/stop` stop the turn · `/model [id]` · `/status` · `/soul` rebuild the personality ·
Antigravity only: `/conversations`, `/continue <id>` (continue a desktop-app conversation in Telegram, and back).

## Safety defaults

- Owner-only bot: every other user and chat is ignored silently.
- `MAX_TURNS_PER_HOUR=60` cap on harness turns started from Telegram.
- `remember()` / `note()` refuse text that looks like a secret.
- Optional `SENSITIVE_REGEX` + `SENSITIVE_PROFILE`: matching messages go to a Hermes profile (e.g. a local model) and never reach the harness.
- Use a **new** Telegram bot. One token can only be polled by one process.

## Uninstall

```bash
python setup.py --uninstall     # removes the MCP registration and the instruction-file block
```

Your Hermes memory is never deleted.

## Português (resumo)

Roda o seu **Hermes Agent** dentro do Antigravity (estável) ou do Claude Code / Codex (experimentais) como **o mesmo
agente**: mesma personalidade, mesma memória e mesmos bots, com conversa pelo Telegram (um tópico por conversa).
O jeito mais fácil: clone o repositório e peça ao seu Hermes ou Claude: *"Leia o SETUP.md e configure com o antigravity"*.
Ele pede o token de um bot **novo** do Telegram e o seu ID, roda o `setup.py` e testa tudo.
No Antigravity, `/conversations` e `/continue <id>` levam uma conversa do app desktop para o Telegram e de volta.

## License

MIT
