# SETUP.md: instructions for the AI agent doing the install

You (Hermes, Claude Code, Codex or Antigravity) are installing **hermes-harness-clone** for the user.
Follow these steps in order. Run commands yourself; ask the user only for what is marked **ASK**.

## 0. Decide the harness

Use what the user said. If unclear, **ASK**: "Which harness should run your clone: antigravity (recommended),
claude or codex (both experimental)?"

## 1. Check prerequisites

- `hermes --version` works. If not, stop: Hermes Agent must be installed first.
- The harness CLI is installed and logged in:
  - antigravity: `agy --help` (on Windows often `%USERPROFILE%\.gemini\bin\agy.exe`)
  - claude: `claude --version`
  - codex: `codex --version`
  If it is not on PATH, find it and pass it as `HARNESS_BIN` in `clone/.env`.
- Python 3.10+.
- Recommended: Hermes uses the holographic memory provider (`memory.provider: holographic` in
  `$HERMES_HOME/config.yaml`), otherwise `search()`/`remember()` are unavailable (everything else still works).

## 2. Telegram (optional but it is the main point)

**ASK** the user for:
1. A **new** bot token from @BotFather (never a token Hermes already uses: one token = one poller).
2. Their numeric Telegram user id (they can get it from @userinfobot).
3. Hermes profiles that must stay private (may be none).

Do not print the token back in chat.

## 3. Install

Write the answers into `clone/.env` (copy `clone/.env.example` first), then run:

```bash
python setup.py --harness <antigravity|claude|codex> --yes
```

Every line must be `[ok]`. Fix any `[!!]` before continuing (the message says what is missing).
`Telegram not configured` is fine only if the user does not want Telegram.

## 4. Verify (mandatory, do not skip)

1. Memory through the harness, in a NEW harness session:
   - antigravity: `agy -p "Use the hivemind tool core('user') and tell me my first name in one sentence." --dangerously-skip-permissions`
   - claude: `claude -p "..." --dangerously-skip-permissions`
   - codex: `codex exec --skip-git-repo-check "..."`
   The answer must contain the user's real name.
2. Telegram: start the bridge (`start_bridge.bat` on Windows, `./start_bridge.sh` elsewhere), wait 5 s,
   check `clone/logs/bridge-*.log` shows `bridge up`. Ask the user to send `/status` to the bot.
3. Topics: tell the user to enable **Threaded Mode** in @BotFather's **mini app** (search BotFather → Open →
   My bots → the bot → Bot Settings → Threads Settings). The text menu of /mybots does not show this option.

## 5. Autostart (ask first)

**ASK** if the bridge should start with the computer. If yes:
- Windows: create a shortcut to `start_bridge.bat` in `shell:startup`.
- Linux: a systemd user service or `@reboot` crontab line running `start_bridge.sh`.
- macOS: a LaunchAgent.

## 6. Tell the user

- The clone already knows who they are; on the desktop app it applies after restarting the app.
- One topic per conversation; `/new` creates one.
- Antigravity: `/conversations` and `/continue <id>` move a conversation between the desktop app and Telegram.
  Do not use the same conversation on both sides at the same time.
- Personality is rebuilt every hour from Hermes' SOUL.md and USER.md (`/soul` forces it).

## Do not

- Do not edit Hermes' config, memory files or databases. The clone only reads them and writes through the MCP tools.
- Do not reuse Hermes' Telegram token.
- Do not commit `clone/.env`.
