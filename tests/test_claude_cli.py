import asyncio
import json
import os

from elen.llm.claude_cli import CLIResult, ClaudeCLIProvider
from elen.mcp_bridge import handle


class FakeClaudeP:
    """Acts like `claude -p`: talks to Elen only through the MCP bridge."""

    agent_mode = True
    name = "claude_cli"

    def __init__(self, script):
        self.script = script  # list of (tool_name, args) then a final text
        self.calls = []

    async def run_agent(self, prompt, system, mcp_config, session_id="", on_event=None):
        self.calls.append({"prompt": prompt, "system": system, "session": session_id})
        server = json.loads(open(mcp_config).read())["mcpServers"]["elen"]
        sock = server["args"][-1]

        def rpc(method, params=None):
            return handle(sock, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

        listed = await asyncio.to_thread(rpc, "tools/list")
        names = {t["name"] for t in listed["result"]["tools"]}
        results = []
        for tool, args in self.script[:-1]:
            assert tool in names
            if on_event:
                await on_event("tool_use", f"mcp__elen__{tool}")
            r = await asyncio.to_thread(rpc, "tools/call", {"name": tool, "arguments": args})
            results.append(r["result"])
        self.results = results
        return CLIResult(text=self.script[-1], session_id="sess-1", is_error=False)

    async def chat(self, system, messages, tools=None):
        raise AssertionError("not used")


async def test_claude_p_turn_uses_guarded_tools(make_core, env):
    from elen.core import Elen

    core = await make_core([])
    await core.stop()
    fake = FakeClaudeP([
        ("contacts__add_contact", {"name": "Bob", "emails": ["bob@x.io"]}),
        ("ui__show_visual", {"visual": {"type": "text", "body": "hi"}}),
        "Bob was added.",
    ])
    core.brain = fake
    await core.start()
    confirms = []

    def on_event(kind, payload):
        if kind == "confirm":
            confirms.append(payload)
            core.resolve_confirmation(payload["id"], True)

    core.subscribe(on_event)
    reply = await core.ask("add bob")
    assert reply == "Bob was added."
    assert confirms and confirms[0]["tool"] == "contacts__add_contact"
    assert json.loads(fake.results[0]["content"][0]["text"])["status"] == "done"
    assert "mcp__elen__" in fake.calls[0]["system"]
    # session continues on the next turn; clear starts a new one
    fake.script = ["ok"]
    await core.ask("again")
    assert fake.calls[1]["session"] == "sess-1"
    core.clear_history()
    await core.ask("fresh")
    assert fake.calls[2]["session"] == ""
    await core.stop()
    assert not os.path.exists(core.tool_socket)


def test_cli_command_has_model_url_and_no_builtin_tools(tmp_path):
    p = ClaudeCLIProvider({
        "binary": "sh", "model": "my-model", "base_url": "https://gw.example/v1",
        "api_key": "k1", "auth_token": "t1", "effort": "low", "env": {"X_Y": "z"},
        "builtin_tools": [],
    })
    cmd = p._base_cmd("SYS")
    assert cmd[cmd.index("--model") + 1] == "my-model"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    env = p.env()
    assert env["ANTHROPIC_BASE_URL"] == "https://gw.example/v1"
    assert env["ANTHROPIC_API_KEY"] == "k1" and env["ANTHROPIC_AUTH_TOKEN"] == "t1" and env["X_Y"] == "z"

    captured = {}

    async def fake_run(cmd, prompt, cwd, on_event=None):
        captured["cmd"] = cmd
        return CLIResult("x", "s", False)

    p._run = fake_run
    asyncio.run(p.run_agent("hi", "SYS", tmp_path / "m.json"))
    c = captured["cmd"]
    assert c[c.index("--tools") + 1] == ""
    assert c[c.index("--allowedTools") + 1] == "mcp__elen"
    assert c[c.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in c
