# Elen 2.0

A Jarvis-like personal assistant for Ubuntu 24.04 (GNOME 46, Wayland).

- An animated orb with the label **ELEN** sits in the **top middle** of the GNOME panel.
- Click it (or press **Super+J**) to open the chat. The chat **keeps its history**, also after a
  restart, until you press the **half-circle CLEAR button** that hangs from the top edge of the
  chat window.
- **Voice**: press the mic button or **Super+Shift+J**, speak, stop talking. Elen records until
  you are silent, transcribes, and acts.
- **On-screen HUD**: Elen draws Jarvis-style panels directly on your desktop (no browser tab,
  no window): today's calendar as a timeline, mail lists, an opened email, system gauges,
  tables, text, screenshots. Any plugin can show any of these.
- **Screen vision**: Elen can take a screenshot and ask the vision model about it when it
  decides that helps ("what is this error?").
- **Separate models** for the brain, for vision and for speech to text. Claude, OpenAI, Ollama
  or any OpenAI-compatible server.
- **Plugins** give Elen tools: mail (any IMAP/SMTP server), calendar (CalDAV / ICS), contacts,
  memory, desktop control, and full PC control through `claude -p`. Adding your own plugin is
  one Python file. See [docs/PLUGINS.md](docs/PLUGINS.md).
- **Guard rails** against hallucinations and against acting for you without your OK. See below.

## Architecture

```
 GNOME Shell extension (JavaScript)          Elen daemon (Python, systemd user service)
 ┌──────────────────────────────┐   D-Bus    ┌──────────────────────────────────────┐
 │ panel orb + chat + CLEAR tab │◄──────────►│ agent loop (brain model + tools)      │
 │ HUD visuals                  │  session   │ guard: approvals, recipient checks,   │
 │ approval dialogs             │    bus     │        action-claim check, audit log  │
 │ screenshots (Shell API)      │            │ plugins: mail, calendar, contacts...  │
 │ shortcuts Super+J / +Shift+J │            │ STT recorder + transcriber, TTS       │
 └──────────────────────────────┘            │ vision model                          │
                                              └──────────────────────────────────────┘
```

GNOME on Wayland does not let a normal app place its own window at the top of the screen. For
this reason the UI is a GNOME Shell extension, and the logic is a separate daemon. The
extension only draws; all decisions and all data stay in the daemon.

## Install

```bash
git clone https://github.com/alonlot/elen2.0 && cd elen2.0
./install.sh
```

Then:

1. Put your keys in `~/.config/elen/env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   OPENAI_API_KEY=sk-...        # only for the default voice transcription
   ```
2. Edit `~/.config/elen/config.toml` (models, mail, calendar, plugins).
3. `systemctl --user restart elen`
4. Log out and log in again. GNOME on Wayland loads a new extension only at login. Then:
   `gnome-extensions enable elen2@alonlot.github.io`

Test without GNOME: `~/.local/share/elen/venv/bin/elen chat`
Logs: `journalctl --user -u elen -f`

## Configure the models

All in `~/.config/elen/config.toml`:

| Section    | What it does                         | Examples |
|------------|--------------------------------------|----------|
| `[brain]`  | thinks, picks tools, writes replies  | `anthropic` + `claude-opus-5-5`; `ollama` + `qwen2.5:14b` |
| `[vision]` | looks at screenshots                 | `anthropic` + `claude-opus-5-5`; `openai` + `gpt-4o` |
| `[stt]`    | speech to text                       | `openai` + `whisper-1`; Groq; `faster_whisper` (offline); any command |
| `[tts]`    | optional spoken replies              | `spd-say`, `piper`, `espeak-ng` |

For Claude, `effort = "low"` gives faster answers; `"medium"` is the default.

## Guard rails

Elen must not invent facts and must not act for you without your OK. This is what the code
does, not only what the prompt asks:

1. **Risk level on every tool.** `read` runs at once. `low` (open an app) runs and is logged.
   `write` (send mail, add an event, add a contact) and `dangerous` (shell command, Claude Code)
   **always stop and show an approval dialog**. Nothing happens until you press Approve.
2. **You see the real thing.** The dialog shows the full email (to, cc, subject, complete body)
   or the exact command. You can **edit** the fields before you approve. Elen is told the
   final version that was really sent.
3. **Right person check.** Every recipient is checked and labelled in the dialog:
   `IN CONTACTS`, `TYPED BY YOU`, `FROM EMAIL DATA` (for example the sender you reply to), or a
   red **UNVERIFIED**/**INVALID** warning when the address came from nowhere.
4. **Arming delay.** The Approve button is locked for 1 second (3 seconds for dangerous
   actions), so a stray click or Enter key cannot approve.
5. **No answer = no action.** Unanswered approvals expire (default 5 minutes) as "rejected".
6. **Action-claim check.** If a reply says "I sent / scheduled / deleted ..." but no action
   tool succeeded in that turn, the reply gets a visible warning that nothing was done.
7. **Truth rules** in the system prompt: facts about your mail, calendar, files and screen
   only from tool results; no invented names, addresses, numbers; say "I do not know".
   The vision model is told to quote exactly and to say when text is unreadable.
8. **Audit log** of every action, approval and rejection: `~/.local/share/elen/audit.log`.

You can change the level of any tool or plugin in `[guard.overrides]`. Lowering a `write` or
`dangerous` tool removes its approval step. Do that only if you accept the risk.

## Full PC control with Claude Code

Enable `[plugins.claude_code]` and install the Claude Code CLI. Elen can then hand a task to
`claude -p "<task>" --dangerously-skip-permissions`. Inside that task, Claude Code runs
commands and edits files **without asking**. Elen still shows you the exact task text (you
can edit it) and asks once before it starts. Use this plugin only if you understand that an
approved task has full access to your user account.

## Visuals

The HUD types are `calendar`, `list`, `stats`, `table`, `text`, `email`, `image` and
`panels`. See [docs/VISUALS.md](docs/VISUALS.md).

## Files

| Path | Content |
|------|---------|
| `~/.config/elen/config.toml` | settings |
| `~/.config/elen/env` | API keys (chmod 600) |
| `~/.config/elen/contacts.toml` | trusted contacts |
| `~/.config/elen/plugins/` | your plugins |
| `~/.local/share/elen/history.json` | chat history (deleted by CLEAR) |
| `~/.local/share/elen/plugins/memory/memories.json` | long-term memory (not deleted by CLEAR) |
| `~/.local/share/elen/audit.log` | action log |

## Development

```bash
uv venv .venv && . .venv/bin/activate
uv pip install -e '.[dev,calendar]'
pytest
```

After you change the extension: copy it again (`./install.sh`), then log out and in.
For a quick test of the extension in a nested shell on GNOME 46:
`dbus-run-session -- gnome-shell --nested --wayland`.

## Known limits

- No wake word yet ("Hey Elen"). Voice starts with the shortcut or the mic button.
- Google Calendar is read only (through its secret ICS address). Write access needs CalDAV
  (Nextcloud, iCloud, Fastmail and others).
- There is no phone/call plugin yet. Any plugin that marks its number argument in
  `recipients=[...]` gets the same "right person" check as email.
