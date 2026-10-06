"""Elen plugin API.

A plugin is a Python class that subclasses `Plugin` and marks its tools with
the `@tool` decorator. See docs/PLUGINS.md for the full guide.

    from elen.plugins import Plugin, tool, ToolResult

    class HelloPlugin(Plugin):
        name = "hello"
        description = "Says hello."

        @tool("Say hello to a person.", params={"who": "string: name of the person"})
        async def say_hello(self, who: str):
            return ToolResult(data={"greeting": f"Hello {who}"},
                              visual={"type": "text", "title": "Hello", "body": who})

Risk levels decide what the guard does before a tool runs:
  "read"       reads data only. Runs at once.
  "low"        small, safe, easy to undo action (open an app). Runs at once, logged.
  "write"      changes data or talks to other people (send mail, add event).
               The user must approve a preview first.
  "dangerous"  can change the computer (shell commands, full-control agent).
               The user must approve, with a short arming delay on the button.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Elen

RISK_LEVELS = ("read", "low", "write", "dangerous")


@dataclass
class ToolResult:
    """What a tool returns.

    data     JSON-serialisable result. The model sees this.
    visual   Optional visual spec (see docs/VISUALS.md). Shown on screen when
             ui.auto_visualize is on.
    summary  Optional one-line text for the chat activity log.
    """

    data: Any = None
    visual: dict[str, Any] | None = None
    summary: str = ""


@dataclass
class Notice:
    """Something Elen tells the user without being asked (returned by Plugin.watch).

    key        unique id; a notice with the same key is delivered only once
    text       one or two sentences, shown in the chat and spoken
    visual     optional visual spec, shown on screen
    speak      speak it (outside quiet hours, when speech is on)
    baseline   True: on the first check after start, only remember the key and do
               not deliver (for example old unread mail)
    """

    key: str
    text: str
    visual: dict[str, Any] | None = None
    speak: bool = True
    baseline: bool = False


@dataclass
class ToolSpec:
    plugin: str
    name: str
    description: str
    parameters: dict[str, Any]
    risk: str
    editable: tuple[str, ...]
    recipients: tuple[str, ...]
    func: Callable[..., Awaitable[Any]]
    title: str = ""
    untrusted: bool = False
    allow_scope: tuple[str, ...] | None = None

    @property
    def full_name(self) -> str:
        return f"{self.plugin}__{self.name}"

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.full_name,
            "description": f"[{self.risk}] {self.description}",
            "parameters": self.parameters,
        }


def _param_schema(spec: Any) -> dict[str, Any]:
    """Allow the short form "type: description" or a full JSON schema dict."""
    if isinstance(spec, dict):
        return spec
    text = str(spec)
    kind, _, desc = text.partition(":")
    kind = kind.strip().lower()
    if kind not in ("string", "integer", "number", "boolean", "array", "object"):
        return {"type": "string", "description": text.strip()}
    schema: dict[str, Any] = {"type": kind, "description": desc.strip()}
    if kind == "array":
        schema["items"] = {"type": "string"}
    return schema


def tool(
    description: str,
    params: dict[str, Any] | None = None,
    required: list[str] | None = None,
    risk: str = "read",
    editable: tuple[str, ...] | list[str] = (),
    recipients: tuple[str, ...] | list[str] = (),
    name: str | None = None,
    title: str = "",
    untrusted: bool = False,
    allow_scope: tuple[str, ...] | list[str] | None = None,
):
    """Mark a plugin method as a tool the assistant can call.

    params      {"arg": "string: what it is"} or {"arg": {json schema}}
    required    required arg names. Default: args without a default value.
    risk        "read" | "low" | "write" | "dangerous"
    editable    args the user can edit in the approval dialog (write/dangerous)
    recipients  args that hold email addresses or phone numbers of other
                people. The guard checks them against contacts and the chat.
    untrusted   True if the result holds text written by other people (mail,
                web pages, files, screen). It can contain hidden instructions
                (prompt injection), so after it every action needs approval.
    allow_scope args that define "the same action" for "Approve and don't ask
                again". A URL counts by its domain. Default: the recipient args,
                or the whole tool when it has none. Dangerous tools never get
                an allow rule.
    """
    if risk not in RISK_LEVELS:
        raise ValueError(f"risk must be one of {RISK_LEVELS}")

    def decorator(func):
        func.__elen_tool__ = {
            "description": description,
            "params": params or {},
            "required": required,
            "risk": risk,
            "editable": tuple(editable),
            "recipients": tuple(recipients),
            "name": name or func.__name__,
            "title": title,
            "untrusted": untrusted,
            "allow_scope": tuple(allow_scope) if allow_scope is not None else None,
        }
        return func

    return decorator


@dataclass
class PluginContext:
    """Services the core gives to each plugin."""

    core: "Elen"
    plugin_name: str
    data_dir: Path
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("elen.plugin"))

    async def show(self, visual: dict[str, Any]) -> None:
        """Show a visual on screen now."""
        await self.core.show_visual(visual)

    async def screenshot(self) -> Path:
        """Capture the screen and return the PNG path."""
        return await self.core.capture_screen()

    async def look(self, image: Path, question: str) -> str:
        """Ask the vision model about an image."""
        return await self.core.vision.describe(image, question)

    async def notify(self, text: str) -> None:
        """Add an info line to the chat."""
        await self.core.add_activity(self.plugin_name, text)

    def setting(self, *path: str, default: Any = None) -> Any:
        """Read any value from the full config, for example setting('user', 'name')."""
        node: Any = self.core.config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


class Plugin:
    """Base class for all plugins."""

    name: str = ""
    description: str = ""

    def __init__(self, config: dict[str, Any], ctx: PluginContext):
        self.config = config
        self.ctx = ctx
        self.log = logging.getLogger(f"elen.plugin.{self.name}")

    async def setup(self) -> None:
        """Called once after loading. Raise to disable the plugin."""

    async def teardown(self) -> None:
        """Called when Elen stops or reloads."""

    def prompt_hint(self) -> str:
        """Optional extra line for the system prompt (for example account names)."""
        return ""

    async def watch(self, now) -> list["Notice"]:
        """Optional: called about once a minute. Return notices to show the user
        without being asked (reminders, alerts). Keep it fast; cache slow lookups."""
        return []

    def known_addresses(self) -> set[str]:
        """Email addresses / phone numbers this plugin knows to be real contacts.

        The guard uses these to check recipients before a write action.
        """
        return set()

    def tools(self) -> list[ToolSpec]:
        specs = []
        for attr in dir(type(self)):
            func = getattr(type(self), attr, None)
            meta = getattr(func, "__elen_tool__", None)
            if not meta:
                continue
            bound = getattr(self, attr)
            sig = inspect.signature(bound)
            props = {k: _param_schema(v) for k, v in meta["params"].items()}
            required = meta["required"]
            if required is None:
                required = [
                    p.name
                    for p in sig.parameters.values()
                    if p.default is inspect.Parameter.empty and p.name in props
                ]
            specs.append(
                ToolSpec(
                    plugin=self.name,
                    name=meta["name"],
                    description=meta["description"],
                    parameters={"type": "object", "properties": props, "required": required},
                    risk=meta["risk"],
                    editable=meta["editable"],
                    recipients=meta["recipients"],
                    func=bound,
                    title=meta["title"] or meta["name"].replace("_", " ").capitalize(),
                    untrusted=meta["untrusted"],
                    allow_scope=meta["allow_scope"],
                )
            )
        return specs


__all__ = ["Notice", "Plugin", "PluginContext", "ToolResult", "ToolSpec", "tool", "RISK_LEVELS"]
