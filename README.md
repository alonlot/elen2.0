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
- **The brain is any LLM**, called through its normal API. The default provider speaks the
  OpenAI chat API, which almost every server uses: Ollama, LM Studio, vLLM, LiteLLM, OpenAI,
  OpenRouter, Groq, Gemini, Mistral, DeepSeek. The Claude API is also a provider. Models without
  native tool calling still work (prompt-based tools).
- **Claude Code is the brain's hands.** To act on the computer, the brain calls `claude -p` as a
  tool. You approve each task, then Claude Code does the work and its steps appear in the chat.
- **Separate models** for the brain, vision, the rule checker and speech to text. Each one has
  a free-text model name and a custom URL. Change them in the settings window (gear button in
  the chat, with presets) or with `elen set brain.model llama3.1:70b`.
- **Plugins** give Elen tools: mail (any IMAP/SMTP server), calendar (CalDAV / ICS), contacts,
  memory, desktop control, and full PC control through `claude -p`. Adding your own plugin is
  one Python file. See [docs/PLUGINS.md](docs/PLUGINS.md).
- **Memory and permanent rules.** Elen saves facts about you and recalls them later. When you
  correct a mistake ("never do that again"), Elen saves a permanent rule and checks every later
  action and reply against it. See below.
- **Guard rails** against hallucinations and against acting for you without your OK. See below.

## Architecture

```
 GNOME Shell extension (JavaScript)          Elen daemon (Python, systemd user service)
 ┌──────────────────────────────┐   D-Bus    ┌──────────────────────────────────────────┐
 │ panel orb + chat + CLEAR tab │◄──────────►│ agent loop                               │
 │ HUD visuals                  │  session   │   brain = any LLM (normal API call) ─────┼──► OpenAI-API server
 │ approval dialogs             │    bus     │     │ asks for a tool                    │    (Ollama, OpenAI, ...)
 │ screenshots (Shell API)      │            │     ▼                                    │    or Claude API
 │ settings window              │            │ guard: approval, recipients, rules       │
 └──────────────────────────────┘            │     │ approved                           │
                                              │     ▼                                    │
                                              │ plugins: mail, calendar, contacts,       │
                                              │   memory, screen, system,                │
                                              │   claude_code ──► `claude -p` task       │
                                              └──────────────────────────────────────────┘
```

GNOME on Wayland does not let a normal app place its own window at the top of the screen. For
this reason the UI is a GNOME Shell extension, and the logic is a separate daemon. The
extension only draws; all decisions and all data stay in the daemon.

The brain only asks for a tool. The daemon runs it, and the guard (approval dialog, recipient
check, rule check) decides first. Claude Code is one of the tools: the brain writes a task,
you approve it, and `claude -p` does the work on the computer.

## Install

```bash
git clone https://github.com/alonlot/elen2.0 && cd elen2.0
./install.sh
```

Then:

1. Choose the brain. The default is a local [Ollama](https://ollama.com) server:
   `ollama pull qwen2.5:14b`. For any other LLM, use the gear button in the chat (presets for
   LM Studio, vLLM, LiteLLM, OpenAI, OpenRouter, Groq, Gemini, Mistral, DeepSeek, Claude API) or
   edit `[brain]` in the config. Put keys in `~/.config/elen/env` and refer to them as
   `env:NAME`. For the default voice transcription, set `OPENAI_API_KEY` (or choose
   `faster_whisper` for offline speech to text).
2. Edit `~/.config/elen/config.toml` (models, mail, calendar, plugins).
3. `systemctl --user restart elen`
4. Log out and log in again. GNOME on Wayland loads a new extension only at login. Then:
   `gnome-extensions enable elen2@alonlot.github.io`

Test without GNOME: `~/.local/share/elen/venv/bin/elen chat`
Logs: `journalctl --user -u elen -f`

## Configure the models

In the settings window (gear button in the chat header, or the Extensions app), with
`elen set section.key value`, or in `~/.config/elen/config.toml`:

| Section     | What it does                         | Default |
|-------------|--------------------------------------|---------|
| `[brain]`   | the agent: thinks, picks tools, writes replies | `openai_compatible`, local Ollama |
| `[vision]`  | looks at screenshots (needs an image model) | same as brain |
| `[checker]` | checks actions and replies against your rules | same as brain |
| `[stt]`     | speech to text                       | `openai` + `whisper-1` |
| `[tts]`     | optional spoken replies              | off |

Every model section has:

- `provider`: `openai_compatible` (any LLM server), `ollama` or `anthropic`.
  Empty in `[vision]` / `[checker]` means "same as the brain".
- `model`: free text, any name your server knows.
- `base_url`: custom URL of the server.
- `api_key`: `env:NAME`, `cmd:...`, the key itself, or empty.
- `tool_mode`: `auto` uses native tool calling and switches to prompt-based tools when the
  server or model rejects them. `prompt` forces prompt-based tools (for small local models).
- `headers` / `extra_body`: extra HTTP headers and request fields for special servers.

Examples:

```bash
elen set brain.base_url https://openrouter.ai/api/v1
elen set brain.model meta-llama/llama-3.3-70b-instruct
elen set brain.api_key env:OPENROUTER_API_KEY
elen set vision.model qwen2.5vl:7b
systemctl --user restart elen        # the settings window restarts Elen by itself
```

Tool use is the hard part for a model: small local models (below about 14B parameters) often
pick wrong tools or invent arguments. The guard still stops every action for your approval,
but a stronger model makes far fewer mistakes.

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

## Memory and permanent rules

Elen has two kinds of long-term memory. **CLEAR** in the chat does not delete them.

| Kind  | Example | How it is used |
|-------|---------|----------------|
| Fact  | "My wife's name is Dana." | The newest 60 facts are in every prompt. Older facts are found with `memory__recall`. |
| Rule  | "Never send an email without my signature." | **All** rules are in every prompt and are checked in code. |

How a mistake becomes a rule:

1. You say, for example: *"You booked it for the wrong time zone. Never do that again."*
2. Elen saves a general rule (`memory__add_rule`) and tells you the rule text. A line in the
   chat shows "New permanent rule [id]: ...". If Elen does not save a rule after a message that
   looks like a correction, the reply shows a note, so the miss is not silent.
3. From then on, the rule is enforced in three places:
   - **Prompt:** all rules are in a top-priority section of every request.
   - **Actions:** before every action (not only approval actions), a checker model compares the
     planned action with your rules. A small action that breaks a rule is **blocked**. A
     write or dangerous action shows a red **RULE CONFLICT** line in the approval dialog.
   - **Replies:** each final reply is checked too. A reply that breaks a rule is rewritten
     once before you see it.
4. Only you can remove a rule: deleting a rule always opens an approval dialog.

Say "show my memory" to see all rules and facts on screen.

What this can and cannot do: the code check makes repeat mistakes much less likely, and it
always stops an action it detects as a conflict. It is still a model that judges the conflict,
so it is not a mathematical guarantee. For a rule that must never be broken, also use a hard
setting, for example `[guard.overrides]` or disabling the plugin. The checker can use a
cheaper model: `[checker]` in the config. To turn the checks off: `[guard] rule_check = "off"`.

## Claude Code: the brain's hands

`[plugins.claude_code]` is on by default (it turns itself off with a message if the `claude` CLI
is not installed). The brain uses it for real work on the computer: files, code, packages,
settings, multi-step shell work.

1. The brain writes a complete task, for example "Install htop and add an alias top=htop to
   ~/.bashrc".
2. The approval dialog shows the exact task text, in red, with a 3 second arming delay. You can
   edit the task.
3. After you approve, `claude -p` runs it with `--dangerously-skip-permissions` (setting
   `bypass_permissions`): no more questions inside that task. Each step appears in the chat
   ("Claude Code › Bash apt install htop").
4. The report comes back to the brain and appears on screen. "Continue the last task" resumes
   the same Claude Code session.

Claude Code has its own model and URL settings (`model`, `base_url`), separate from the brain.
Use this plugin only if you understand that an approved task has full access to your user
account.

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
| `~/.local/share/elen/plugins/memory/memories.json` | long-term facts and rules (not deleted by CLEAR) |
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
