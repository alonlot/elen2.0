# Writing Elen plugins

A plugin gives Elen tools. A tool is an `async` Python method with a description. The brain
model reads the description and decides when to call it.

## 1. Where plugins go

```
~/.config/elen/plugins/
├── weather/
│   └── plugin.py        # a folder per plugin (recommended)
└── jira.py              # or a single file
```

A plugin in this folder is **on** by default. To turn it off:
`[plugins.<name>] enabled = false`. Restart: `systemctl --user restart elen`.
Check that it loaded: `~/.local/share/elen/venv/bin/elen status`.

## 2. The smallest plugin

```python
from elen.plugins import Plugin, tool

class HelloPlugin(Plugin):
    name = "hello"                    # unique; tools are called hello__<tool>
    description = "Says hello."

    @tool("Greet a person by name.", params={"who": "string: the person's name"})
    async def greet(self, who: str):
        return {"text": f"Hello {who}"}
```

## 3. The `@tool` decorator

```python
@tool(
    description,                     # what it does, for the model. Be exact.
    params={...},                    # arguments (see below)
    required=[...],                  # default: arguments without a default value
    risk="read",                     # "read" | "low" | "write" | "dangerous"
    editable=[...],                  # arguments the user can edit in the approval dialog
    recipients=[...],                # arguments that hold other people's addresses / numbers
    title="Send message",            # name in the approval dialog
)
```

**Arguments.** Short form `"type: description"` with type `string`, `integer`, `number`,
`boolean`, `array` (of strings) or `object`. Or a full JSON schema:

```python
params={
    "query": "string: text to search",
    "limit": "integer: max results",
    "status": {"type": "string", "enum": ["open", "closed"], "description": "ticket status"},
}
```

**Risk** decides what the guard does:

| risk        | use for                                        | guard |
|-------------|------------------------------------------------|-------|
| `read`      | reading data                                   | runs at once |
| `low`       | small action, easy to undo (open a page)       | runs at once, logged |
| `write`     | changes data or talks to people                | approval dialog with preview |
| `dangerous` | can change the computer                        | approval dialog, red, 3 s arming delay |

Choose the higher level when you are not sure.

**Recipients.** If the tool sends something to a person (mail, chat message, SMS, a call),
list the argument in `recipients=[...]`. The guard then labels each address or number in
the dialog (`IN CONTACTS`, `TYPED BY YOU`, `FROM EMAIL DATA`, or a red `UNVERIFIED`).
Implement `known_addresses()` if your plugin knows trusted addresses.

## 4. Return values

Return any JSON-serialisable value, or a `ToolResult` to also show something on screen:

```python
from elen.plugins import ToolResult

return ToolResult(
    data={"tickets": tickets},                  # the model sees this
    visual={"type": "list", "title": "Open tickets",
            "items": [{"title": t["title"], "subtitle": t["owner"], "meta": t["id"]} for t in tickets]},
    summary="3 open tickets",                   # optional activity line
)
```

Visual types are in [VISUALS.md](VISUALS.md). Raise an exception for an error. Elen reports
it to the model as a failed tool, and the model is told not to guess a result.

## 5. Settings and secrets

Everything under `[plugins.<name>]` in `config.toml` is `self.config`:

```toml
[plugins.jira]
url = "https://jira.example.com"
token = "cmd:secret-tool lookup elen jira"
```

```python
from elen.secrets import resolve_secret

class JiraPlugin(Plugin):
    name = "jira"

    async def setup(self):
        self.url = self.config["url"]
        self.token = resolve_secret(self.config.get("token"))
        if not self.token:
            raise RuntimeError("no Jira token")   # plugin is disabled, error shown in `elen status`
```

## 6. Hooks and services

| Method / attribute            | Use |
|-------------------------------|-----|
| `async setup()`               | connect, check config. Raise to disable the plugin. |
| `async teardown()`            | close connections |
| `prompt_hint() -> str`        | one line for the system prompt ("accounts: work, home") |
| `known_addresses() -> set`    | trusted addresses / numbers for the recipient check |
| `self.config`                 | your `[plugins.<name>]` table |
| `self.log`                    | logger (shows in `journalctl --user -u elen`) |
| `await self.ctx.show(spec)`   | show a visual now |
| `await self.ctx.screenshot()` | capture the screen, returns a `Path` |
| `await self.ctx.look(path, q)`| ask the vision model about an image |
| `await self.ctx.notify(text)` | add an activity line in the chat |
| `self.ctx.data_dir`           | a private folder for your plugin's files |
| `self.ctx.setting("user", "name")` | read any config value |

Blocking libraries (imaplib, requests, caldav): call them with
`await asyncio.to_thread(func, ...)` so Elen does not freeze.

## 7. A full example: send a message to a chat server

```python
import httpx
from elen.plugins import Plugin, tool
from elen.secrets import resolve_secret

class MattermostPlugin(Plugin):
    name = "mattermost"
    description = "Team chat."

    async def setup(self):
        self.url = self.config["url"].rstrip("/")
        self.token = resolve_secret(self.config["token"])

    @tool(
        "Send a direct message to a colleague. Write the complete message.",
        params={"user": "string: username, for example @dana", "text": "string: the message"},
        risk="write",
        editable=["user", "text"],
        recipients=["user"],
        title="Send chat message",
    )
    async def send_dm(self, user: str, text: str):
        ...  # call the API
        return {"sent": True}
```

The user sees the full message, the recipient check and an Approve button before anything is
sent. See also `examples/plugins/weather/plugin.py` and the built-in plugins in
`elen/plugins/builtin/`.
