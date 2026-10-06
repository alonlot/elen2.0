"""Elen core: the agent loop, plugins, approvals, screen and voice.

Front ends (the D-Bus service for the GNOME extension, or the terminal CLI)
subscribe to events and call the public methods. Events:

  state            {"state": idle|thinking|working|listening|transcribing|waiting}
  message          a chat item {"id","role","text","time","meta"}
  tool             {"name","title","status": running|done|error|rejected,"summary"}
  visual           a visual spec (see visuals.py)
  visual_hide      {}
  confirm          a Confirmation dict; answer with resolve_confirmation()
  confirm_closed   {"id","reason"}
  screenshot_request {"id","path"}; answer with screenshot_done()
  transcript       {"text"}
  history_cleared  {}
  error            {"text"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .config import config_dir, data_dir, deep_merge, load_config
from .guard import Confirmation, Guard
from .history import History
from .llm import LLMError, ToolCall, make_provider
from .plugins import Plugin, PluginContext, ToolResult, ToolSpec, tool
from .plugins.loader import BUILTIN, discover
from .prompts import build_system_prompt
from .stt import Listener
from .tts import Speaker
from .vision import Vision
from .visuals import VISUAL_TOOL_SCHEMA, normalise

log = logging.getLogger("elen.core")

Listener_t = Callable[[str, dict], Any]

CORRECTION_RE = re.compile(
    r"(?:^|[.!?]\s*)never\b(?!\s*mind)"            # a sentence that starts with "never"
    r"|\bnever\b.{0,80}\bagain\b"                   # "never ... again"
    r"|\b(?:don'?t|do not) (?:ever|do (?:that|this|it) again)\b"
    r"|\b(?:stop doing|from now on|next time,|in (?:the )?future,)"
    r"|\byou (?:made a|did a) mistake\b|\bthat was wrong\b|\bwrong again\b"
    r"|\balways (?:do|use|ask|check|remember|write|reply|answer)\b",
    re.IGNORECASE,
)

CHECKER_SYSTEM = (
    "You are a strict compliance checker for a personal assistant. You get the user's permanent "
    "rules and one item the assistant plans to do or say. Decide if the item breaks any rule. "
    "Flag only a clear conflict, not a weak association. Answer with JSON only, no other text: "
    '{"violates": true or false, "rule_ids": ["id", ...], "explanation": "one sentence"}'
)


CLI_NOTE = (
    "\nIn this session your Elen tools are named mcp__elen__<plugin>__<tool> (for example "
    "mcp__elen__ui__show_visual, mcp__elen__memory__add_rule). The instructions above use the "
    "short names without the mcp__elen__ prefix.\n"
)


def parse_check(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or "violates" not in data:
        return None
    return {
        "violates": bool(data.get("violates")),
        "rule_ids": [str(r) for r in data.get("rule_ids") or []],
        "explanation": str(data.get("explanation") or ""),
    }


class UIPlugin(Plugin):
    """Always-on tools that drive the on-screen HUD."""

    name = "ui"
    description = "Show visuals on the user's screen."

    @tool(
        "Show a Jarvis-style visual on the user's screen (calendar, list, stats, table, "
        "text, email, image or several panels). Use it whenever a picture is clearer than text.",
        params=VISUAL_TOOL_SCHEMA["properties"],
        required=["visual"],
    )
    async def show_visual(self, visual: dict):
        spec = normalise(visual, self.ctx.setting("ui", "visual_seconds", default=25))
        await self.ctx.core.show_visual(spec, already_normalised=True)
        return {"shown": True, "type": spec["type"]}

    @tool("Remove the visual from the screen.")
    async def hide_visual(self):
        self.ctx.core.emit("visual_hide", {})
        return {"hidden": True}


class Elen:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_config()
        self.data_dir = data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._listeners: list[Listener_t] = []
        self._pending_confirm: dict[str, tuple[Confirmation, asyncio.Future]] = {}
        self._pending_shots: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.plugins: dict[str, Plugin] = {}
        self.tools: dict[str, ToolSpec] = {}
        self.plugin_errors: dict[str, str] = {}
        self.ui_present = False
        self.state = "idle"
        self._configure()

    # ----- setup -------------------------------------------------------
    def _configure(self) -> None:
        cfg = self.config
        self.history = History(
            self.data_dir / "history.json",
            int(cfg["history"]["max_context_messages"]),
            int(cfg["history"]["max_tool_result_chars"]),
        )
        self.guard = Guard(cfg, self.data_dir / "audit.log")
        self.vision = Vision(cfg["vision"])
        self.listener = Listener(cfg["stt"])
        self.speaker = Speaker(cfg["tts"])
        self._brain = None
        self._checker = None

    @property
    def brain(self):
        if self._brain is None:
            self._brain = make_provider(self.config["brain"])
        return self._brain

    @brain.setter
    def brain(self, provider) -> None:
        self._brain = provider

    @property
    def checker(self):
        """Model for the rule check. [checker] in config, else the brain model at low effort."""
        if self._checker is None:
            own = self.config.get("checker") or {}
            base = {**self.config["brain"], "effort": "low", "max_tokens": 2000}
            self._checker = make_provider(deep_merge(base, own))
        return self._checker

    @checker.setter
    def checker(self, provider) -> None:
        self._checker = provider

    @property
    def agent_mode(self) -> bool:
        """True when the brain is `claude -p`, which runs its own agent loop."""
        return bool(getattr(self.brain, "agent_mode", False))

    async def start(self) -> None:
        await self._load_plugins()
        if self.agent_mode:
            await self._start_tool_server()

    async def _start_tool_server(self) -> None:
        if getattr(self, "_tool_server", None) is not None:
            return
        base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        sock_dir = Path(base) / f"elen-{os.getuid()}"
        sock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(sock_dir, 0o700)
        self.tool_socket = sock_dir / f"tools-{os.getpid()}.sock"
        self.tool_socket.unlink(missing_ok=True)
        self._tool_server = await asyncio.start_unix_server(
            self._serve_tool_client, path=str(self.tool_socket), limit=16 * 1024 * 1024
        )
        os.chmod(self.tool_socket, 0o600)
        self.mcp_config = self.data_dir / "claude-mcp.json"
        self.mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "elen": {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": ["-m", "elen.mcp_bridge", "--socket", str(self.tool_socket)],
                            "env": {"PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
                        }
                    }
                }
            )
        )
        log.info("tool socket for claude -p: %s", self.tool_socket)

    async def _stop_tool_server(self) -> None:
        server = getattr(self, "_tool_server", None)
        if server is not None:
            server.close()
            await server.wait_closed()
            self._tool_server = None
            self.tool_socket.unlink(missing_ok=True)

    async def _serve_tool_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = json.loads(await reader.readline() or b"{}")
            if req.get("op") == "list":
                resp: dict[str, Any] = {"tools": [t.schema() for t in self.tools.values()]}
            elif req.get("op") == "call":
                resp = await self.call_tool_from_agent(str(req.get("name", "")), req.get("arguments") or {})
            else:
                resp = {"content": "bad request", "is_error": True}
        except Exception as e:  # noqa: BLE001
            log.exception("tool socket request failed")
            resp = {"content": json.dumps({"error": str(e)}), "is_error": True}
        try:
            writer.write(json.dumps(resp, ensure_ascii=False, default=str).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()

    async def call_tool_from_agent(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """A tool call from `claude -p`, through the MCP bridge. The guard applies as usual."""
        ctx = getattr(self, "_agent_ctx", None) or {"turn": [{"role": "user", "content": ""}], "executed": []}
        call = ToolCall(id=uuid.uuid4().hex[:12], name=name, arguments=dict(arguments))
        self.set_state("working")
        msg = await self.execute_tool(call, ctx["turn"], ctx["executed"])
        ctx["turn"].append(msg)
        self.set_state("thinking")
        return {"content": msg["content"], "is_error": msg["is_error"]}

    async def stop(self) -> None:
        await self._stop_tool_server()
        for plugin in self.plugins.values():
            try:
                await plugin.teardown()
            except Exception:  # noqa: BLE001
                log.exception("teardown of %s failed", plugin.name)

    async def reload(self) -> dict[str, Any]:
        await self.stop()
        self.config = load_config()
        self._configure()
        await self.start()
        return self.status()

    async def _load_plugins(self) -> None:
        self.plugins.clear()
        self.tools.clear()
        self.plugin_errors.clear()
        pcfg = self.config.get("plugins", {})
        classes: dict[str, type[Plugin]] = {"ui": UIPlugin}
        classes.update(discover(self.config, config_dir() / "plugins"))
        for name, cls in classes.items():
            section = pcfg.get(name, {}) if isinstance(pcfg.get(name), dict) else {}
            default_on = name == "ui" or name not in BUILTIN
            if not section.get("enabled", default_on):
                continue
            ctx = PluginContext(core=self, plugin_name=name, data_dir=self.data_dir / "plugins" / name)
            ctx.data_dir.mkdir(parents=True, exist_ok=True)
            try:
                plugin = cls(section, ctx)
                await plugin.setup()
            except Exception as e:  # noqa: BLE001
                log.error("plugin %s disabled: %s", name, e)
                self.plugin_errors[name] = str(e)
                continue
            self.plugins[name] = plugin
            for spec in plugin.tools():
                self.tools[spec.full_name] = spec
        log.info("plugins: %s", ", ".join(self.plugins))

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "brain": f"{self.config['brain']['provider']}:{self.config['brain'].get('model') or 'default'}",
            "brain_url": self.config["brain"].get("base_url") or "default",
            "vision": f"{self.config['vision']['provider']}:{self.config['vision']['model']}",
            "stt": f"{self.config['stt']['provider']}:{self.config['stt'].get('model', '')}",
            "plugins": sorted(self.plugins),
            "plugin_errors": self.plugin_errors,
            "tools": sorted(self.tools),
        }

    # ----- events ------------------------------------------------------
    def subscribe(self, callback: Listener_t) -> None:
        self._listeners.append(callback)

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        for cb in list(self._listeners):
            try:
                cb(kind, payload)
            except Exception:  # noqa: BLE001
                log.exception("event listener failed")

    def set_state(self, state: str) -> None:
        self.state = state
        self.emit("state", {"state": state})

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def add_activity(self, source: str, text: str) -> None:
        item = self.history.add_display("activity", text, source=source)
        self.history.save()
        self.emit("message", item)

    # ----- chat --------------------------------------------------------
    def submit(self, text: str, source: str = "text") -> str:
        """Queue a user message. Returns a request id at once."""
        rid = uuid.uuid4().hex[:12]
        self._spawn(self.ask(text, source=source, request_id=rid))
        return rid

    async def ask(self, text: str, source: str = "text", request_id: str = "") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        async with self._lock:
            item = self.history.add_display("user", text, source=source)
            self.history.save()
            self.emit("message", item)
            self.set_state("thinking")
            try:
                reply, meta = await self.run_turn(text)
            except LLMError as e:
                reply, meta = f"I could not reach my brain model: {e}", {"error": True}
            except Exception as e:  # noqa: BLE001
                log.exception("turn failed")
                reply, meta = f"Something went wrong: {e}", {"error": True}
            item = self.history.add_display("assistant", reply, request_id=request_id, **meta)
            self.history.save()
            self.emit("message", item)
            self.set_state("idle")
        speak_on = self.config["tts"].get("speak_on", "voice")
        if reply and (speak_on == "always" or (speak_on == "voice" and source == "voice")):
            self._spawn(self.speaker.say(reply))
        return reply

    def rules(self) -> list[str]:
        memory = self.plugins.get("memory")
        return memory.rule_lines() if memory is not None and hasattr(memory, "rule_lines") else []

    def _system_prompt(self, text: str = "") -> str:
        hints, memories = [], []
        for plugin in self.plugins.values():
            try:
                hint = plugin.prompt_hint()
                if hint:
                    hints.append(f"{plugin.name}: {hint}")
                if hasattr(plugin, "memories"):
                    memories.extend(plugin.memories())
            except Exception:  # noqa: BLE001
                log.exception("prompt hint of %s failed", plugin.name)
        user = self.config["user"]
        return build_system_prompt(
            user.get("name", ""),
            user.get("language", "en"),
            hints,
            memories,
            user.get("timezone", ""),
            rules=self.rules(),
            correction_hint=bool(CORRECTION_RE.search(text)) and "memory" in self.plugins,
        )

    async def run_turn(self, text: str) -> tuple[str, dict[str, Any]]:
        """Run the agent loop for one user message."""
        if self.agent_mode:
            return await self._run_turn_cli(text)
        turn: list[dict[str, Any]] = [{"role": "user", "content": text}]
        executed: list[dict[str, Any]] = []
        system = self._system_prompt(text)
        tools = [t.schema() for t in self.tools.values()]
        max_steps = int(self.config["brain"].get("max_steps", 12))
        final = ""
        for _ in range(max_steps):
            resp = await self.brain.chat(system, self.history.context() + turn, tools)
            msg: dict[str, Any] = {"role": "assistant", "content": resp.text}
            if resp.tool_calls:
                msg["tool_calls"] = [c.to_dict() for c in resp.tool_calls]
            if resp.raw:
                msg["_raw"] = resp.raw
            turn.append(msg)
            if not resp.tool_calls:
                final = resp.text
                conflict = await self._reply_rule_conflict(text, final)
                if conflict:
                    try:
                        # Tools stay declared: the API needs them when the history has tool calls.
                        again = await self.brain.chat(
                            system, self.history.context() + turn + [self._rule_note(conflict)], tools
                        )
                        if again.text:
                            turn[-1] = {"role": "assistant", "content": again.text}
                            final = again.text
                    except Exception as e:  # noqa: BLE001
                        log.warning("reply rewrite failed: %s", e)
                break
            if resp.text:
                await self.add_activity("elen", resp.text)
            for call in resp.tool_calls:
                turn.append(await self.execute_tool(call, turn, executed))
        else:
            final = "I stopped because the task needed too many steps. Tell me how to continue."
            turn.append({"role": "assistant", "content": final})
        self.history.add_turn(turn)
        return self._finish_turn(text, final, executed)

    async def _run_turn_cli(self, text: str) -> tuple[str, dict[str, Any]]:
        """One turn with `claude -p` as the agent. Tools come back through the MCP bridge."""
        turn: list[dict[str, Any]] = [{"role": "user", "content": text}]
        executed: list[dict[str, Any]] = []
        self._agent_ctx = {"turn": turn, "executed": executed}
        system = self._system_prompt(text) + CLI_NOTE
        pending: list[str] = []

        async def on_event(kind: str, value: Any) -> None:
            if kind == "text":
                pending.append(value)
            elif kind == "tool_use":
                for t in pending:  # text said before a tool call is progress, not the answer
                    await self.add_activity("elen", t)
                pending.clear()

        try:
            session = self.history.meta.get("cli_session", "")
            prompt = text if session else self.history.recap() + text
            res = await self.brain.run_agent(prompt, system, self.mcp_config, session, on_event)
            if res.is_error and session:
                log.warning("claude -p session %s failed (%s); starting a new one", session, res.error[:200])
                pending.clear()
                res = await self.brain.run_agent(
                    self.history.recap() + text, system, self.mcp_config, "", on_event
                )
            if res.is_error:
                raise LLMError(f"claude -p: {res.error[:500]}")
            self.history.meta["cli_session"] = res.session_id
            final = res.text
            conflict = await self._reply_rule_conflict(text, final)
            if conflict:
                note = self._rule_note(conflict)["content"]
                again = await self.brain.run_agent(note, system, self.mcp_config, res.session_id, None)
                if not again.is_error and again.text:
                    final = again.text
                    self.history.meta["cli_session"] = again.session_id
        finally:
            self._agent_ctx = None
        self.history.data_texts.extend(m["content"] for m in turn if m["role"] == "tool")
        self.history.add_turn([turn[0], {"role": "assistant", "content": final}])
        return self._finish_turn(text, final, executed)

    def _finish_turn(self, text: str, final: str, executed: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        meta: dict[str, Any] = {}
        if executed:
            meta["actions"] = [f"{a['tool']}:{'ok' if a['ok'] else a.get('status', 'failed')}" for a in executed]
        warning = self.guard.action_claim_warning(final, executed)
        if warning:
            meta["warning"] = warning
            final = f"{final}\n\n⚠ {warning}"
        if (
            "memory" in self.plugins
            and CORRECTION_RE.search(text)
            and not any(a["tool"] == "memory__add_rule" and a["ok"] for a in executed)
        ):
            meta["rule_not_saved"] = True
            final += (
                "\n\nℹ No permanent rule was saved from this message. "
                "Say \"make this a rule\" if Elen must remember it."
            )
        return final or "(no reply)", meta

    # ----- tools -------------------------------------------------------
    def known_addresses(self) -> set[str]:
        known: set[str] = set()
        for plugin in self.plugins.values():
            try:
                known |= set(plugin.known_addresses())
            except Exception:  # noqa: BLE001
                log.exception("known_addresses of %s failed", plugin.name)
        return known

    async def execute_tool(
        self, call: ToolCall, turn: list[dict[str, Any]], executed: list[dict[str, Any]]
    ) -> dict[str, Any]:
        def reply(payload: Any, error: bool = False) -> dict[str, Any]:
            content = json.dumps(payload, ensure_ascii=False, default=str)
            return {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": content,
                "is_error": error,
            }

        spec = self.tools.get(call.name)
        if spec is None:
            return reply({"error": f"Unknown tool '{call.name}'. Use only the listed tools."}, True)
        args = dict(call.arguments or {})
        props = spec.parameters.get("properties", {})
        unknown = [k for k in args if k not in props]
        for k in unknown:
            args.pop(k)
        missing = [k for k in spec.parameters.get("required", []) if k not in args]
        if missing:
            return reply({"error": f"Missing required arguments: {', '.join(missing)}"}, True)

        risk = self.guard.effective_risk(spec)
        self.emit("tool", {"name": spec.full_name, "title": spec.title, "status": "running", "risk": risk})
        conflict = await self.check_rules_for_action(spec, risk, args, turn)
        if conflict and risk not in ("write", "dangerous"):
            self.guard.audit("blocked_by_rule", tool=spec.full_name, args=args, **conflict)
            self.emit("tool", {"name": spec.full_name, "title": spec.title, "status": "blocked"})
            executed.append({"tool": spec.full_name, "risk": risk, "ok": False, "status": "blocked_by_rule"})
            return reply(
                {
                    "status": "blocked_by_rule",
                    "rule_ids": conflict["rule_ids"],
                    "explanation": conflict["explanation"],
                    "note": "Nothing was done. Follow the rule, or ask the user.",
                }
            )
        user_edited = False
        if risk in ("write", "dangerous"):
            user_texts = self.history.user_texts() + [
                m["content"] for m in turn if m["role"] == "user" and isinstance(m["content"], str)
            ]
            tool_texts = self.history.tool_texts() + [m["content"] for m in turn if m["role"] == "tool"]
            report, warnings = self.guard.check_recipients(
                spec, args, self.known_addresses(), user_texts, tool_texts
            )
            if conflict:
                ids = ", ".join(conflict["rule_ids"]) or "?"
                warnings.insert(0, f"RULE CONFLICT (rule {ids}): {conflict['explanation']}")
            conf = Confirmation(
                id=uuid.uuid4().hex[:12],
                tool=spec.full_name,
                title=spec.title,
                plugin=spec.plugin,
                risk=risk,
                arguments=args,
                editable=[e for e in spec.editable if e in props],
                warnings=warnings,
                recipients=report,
            )
            decision = await self.request_confirmation(conf)
            if not decision.get("approved"):
                self.guard.audit("rejected", tool=spec.full_name, args=args, reason=decision.get("reason"))
                self.emit("tool", {"name": spec.full_name, "title": spec.title, "status": "rejected"})
                executed.append({"tool": spec.full_name, "risk": risk, "ok": False, "status": "rejected"})
                return reply(
                    {
                        "status": "rejected_by_user",
                        "reason": decision.get("reason") or "The user did not approve. Nothing was done.",
                    }
                )
            edited = decision.get("arguments") or {}
            for key in conf.editable:
                if key in edited and edited[key] != args.get(key):
                    args[key] = edited[key]
                    user_edited = True

        try:
            result = await spec.func(**args)
            ok = True
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", spec.full_name)
            result, ok = {"error": f"{type(e).__name__}: {e}"}, False

        payload: dict[str, Any]
        summary = ""
        if isinstance(result, ToolResult):
            payload = {"result": result.data}
            summary = result.summary
            if result.visual and ok and self.config["ui"].get("auto_visualize", True):
                await self.show_visual(result.visual)
                payload["visual_shown"] = True
        elif ok:
            payload = {"result": result}
        else:
            payload = result
        if risk in ("write", "dangerous"):
            payload["status"] = "done" if ok else "failed"
            if user_edited:
                payload["user_edited"] = True
                payload["final_arguments"] = args
        executed.append({"tool": spec.full_name, "risk": risk, "ok": ok})
        if risk != "read":
            self.guard.audit("executed" if ok else "failed", tool=spec.full_name, args=args, risk=risk)
        self.emit(
            "tool",
            {"name": spec.full_name, "title": spec.title, "status": "done" if ok else "error", "summary": summary},
        )
        return reply(payload, not ok)

    # ----- permanent rules -------------------------------------------
    def _rule_check_mode(self) -> str:
        return str(self.config["guard"].get("rule_check", "actions"))

    async def _check(self, item: str) -> dict[str, Any] | None:
        rules = self.rules()
        if not rules:
            return None
        prompt = "Permanent rules:\n" + "\n".join(rules) + "\n\n" + item
        try:
            resp = await self.checker.chat(CHECKER_SYSTEM, [{"role": "user", "content": prompt}], None)
        except Exception as e:  # noqa: BLE001
            log.warning("rule check failed: %s", e)
            return None
        result = parse_check(resp.text)
        return result if result and result["violates"] else None

    async def check_rules_for_action(
        self, spec: ToolSpec, risk: str, args: dict[str, Any], turn: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        mode = self._rule_check_mode()
        if mode == "off" or spec.plugin in ("memory", "ui"):
            return None
        if mode != "all" and risk == "read":
            return None
        request = turn[0]["content"] if turn and isinstance(turn[0].get("content"), str) else ""
        item = (
            f"User request this turn: {request}\n\n"
            f"Planned action: {spec.full_name} ({spec.title}), risk {risk}\n"
            f"Arguments:\n{json.dumps(args, ensure_ascii=False, default=str)[:6000]}"
        )
        return await self._check(item)

    async def _reply_rule_conflict(self, request: str, reply_text: str) -> dict[str, Any] | None:
        if not reply_text or not self.config["guard"].get("rule_check_replies", True):
            return None
        if self._rule_check_mode() == "off":
            return None
        conflict = await self._check(
            f"User request this turn: {request}\n\nPlanned reply to the user:\n{reply_text[:6000]}"
        )
        if conflict:
            self.guard.audit("reply_rewritten_for_rule", **conflict)
            await self.add_activity("guard", f"Reply broke rule {', '.join(conflict['rule_ids'])}; rewriting.")
        return conflict

    @staticmethod
    def _rule_note(conflict: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "user",
            "content": (
                f"[Elen guard] Your reply breaks permanent rule(s) {', '.join(conflict['rule_ids'])}: "
                f"{conflict['explanation']} Write the reply to the user again so that it follows "
                "every rule. Do not mention this note."
            ),
        }

    # ----- approvals ---------------------------------------------------
    async def request_confirmation(self, conf: Confirmation) -> dict[str, Any]:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_confirm[conf.id] = (conf, fut)
        self.set_state("waiting")
        self.emit("confirm", conf.to_dict())
        timeout = float(self.config["guard"].get("confirm_timeout", 300))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.emit("confirm_closed", {"id": conf.id, "reason": "timeout"})
            return {"approved": False, "reason": "No answer from the user in time. Nothing was done."}
        finally:
            self._pending_confirm.pop(conf.id, None)
            self.set_state("working")

    def pending_confirmations(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c, _ in self._pending_confirm.values()]

    def resolve_confirmation(self, conf_id: str, approved: bool, arguments: Any = None, reason: str = "") -> bool:
        entry = self._pending_confirm.get(conf_id)
        if not entry:
            return False
        conf, fut = entry
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except ValueError:
                arguments = {}
        if not fut.done():
            fut.set_result({"approved": bool(approved), "arguments": arguments or {}, "reason": reason})
        self.emit("confirm_closed", {"id": conf_id, "reason": "approved" if approved else "rejected"})
        return True

    # ----- visuals -----------------------------------------------------
    async def show_visual(self, spec: dict[str, Any], already_normalised: bool = False) -> None:
        if not already_normalised:
            spec = normalise(spec, int(self.config["ui"].get("visual_seconds", 25)))
        self.emit("visual", spec)

    # ----- screen ------------------------------------------------------
    async def capture_screen(self) -> Path:
        shots = self.data_dir / "screens"
        shots.mkdir(parents=True, exist_ok=True)
        for old in sorted(shots.glob("*.png"))[:-20]:
            old.unlink(missing_ok=True)
        path = shots / f"screen-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.png"
        if self.ui_present:
            sid = uuid.uuid4().hex[:12]
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending_shots[sid] = fut
            self.emit("screenshot_request", {"id": sid, "path": str(path)})
            try:
                ok = await asyncio.wait_for(fut, 6)
                if ok and path.exists():
                    return path
            except asyncio.TimeoutError:
                log.warning("GNOME extension did not answer the screenshot request")
            finally:
                self._pending_shots.pop(sid, None)
        for cmd in (
            ["gnome-screenshot", "-f", str(path)],
            ["grim", str(path)],
            ["scrot", "-o", str(path)],
            ["import", "-window", "root", str(path)],
        ):
            if not shutil.which(cmd[0]):
                continue
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            if proc.returncode == 0 and path.exists():
                return path
        raise RuntimeError(
            "Could not take a screenshot. Enable the Elen GNOME extension, or install gnome-screenshot."
        )

    def screenshot_done(self, sid: str, ok: bool) -> None:
        fut = self._pending_shots.get(sid)
        if fut and not fut.done():
            fut.set_result(ok)

    # ----- voice -------------------------------------------------------
    def toggle_listening(self) -> str:
        if self.listener.recorder.active:
            self.listener.recorder.stop()
            return "stopping"
        self._spawn(self._listen_flow())
        return "listening"

    async def _listen_flow(self) -> None:
        await self.speaker.stop()
        self.set_state("listening")
        try:
            import tempfile

            with tempfile.TemporaryDirectory(prefix="elen-") as tmp:
                wav = await self.listener.recorder.record(Path(tmp) / "speech.wav")
                self.set_state("transcribing")
                text = await self.listener.transcriber.transcribe(wav)
        except Exception as e:  # noqa: BLE001
            self.emit("error", {"text": f"Voice input failed: {e}"})
            self.set_state("idle")
            return
        self.set_state("idle")
        self.emit("transcript", {"text": text})
        if text and self.config["stt"].get("auto_send", True):
            await self.ask(text, source="voice")

    # ----- history -----------------------------------------------------
    def clear_history(self) -> None:
        self.history.clear()  # also forgets the claude -p session
        self.guard.audit("history_cleared")
        self.emit("history_cleared", {})
