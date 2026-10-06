"""Claude Code as a tool: the brain (any LLM) can hand a task to `claude -p`.

The brain stays a normal LLM. When a request needs real work on the computer
(files, code, packages, settings, multi-step shell work), the brain calls
claude_code__run_task. Elen shows you the exact task in an approval dialog.
After you approve, `claude -p` runs it. With bypass_permissions = true it adds
--dangerously-skip-permissions, so Claude Code can run any command and edit any
file without asking again. Progress lines appear in the chat while it works.

[plugins.claude_code]
enabled = true
binary = "claude"
bypass_permissions = true
model = ""            # empty = your Claude Code default; or "opus", "sonnet", a full id
base_url = ""         # optional: ANTHROPIC_BASE_URL for Claude Code (gateway / proxy)
workdir = "~"
timeout = 900
extra_args = []
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from elen.plugins import Plugin, ToolResult, tool
from elen.secrets import resolve_secret

# Variables of a parent Claude Code session must not leak into the child.
DROP_ENV_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_SESSION", "CLAUDE_CODE_ENTRYPOINT")


def describe_tool_use(block: dict[str, Any]) -> str:
    name = block.get("name", "tool")
    args = block.get("input") or {}
    detail = args.get("command") or args.get("file_path") or args.get("pattern") or args.get("url") or ""
    detail = " ".join(str(detail).split())
    return f"{name} {detail[:90]}".strip()


class ClaudeCodePlugin(Plugin):
    name = "claude_code"
    description = "Hand computer tasks to Claude Code (claude -p)."

    async def setup(self) -> None:
        self.binary = shutil.which(self.config.get("binary", "claude")) or ""
        if not self.binary:
            raise RuntimeError("the 'claude' CLI is not installed (npm install -g @anthropic-ai/claude-code)")
        self.state_file = self.ctx.data_dir / "last_session.json"

    def prompt_hint(self) -> str:
        mode = "full control, no permission checks" if self.config.get("bypass_permissions", True) else "normal permission checks"
        return (
            f"claude_code__run_task gives a task to Claude Code ({mode}). Use it for real work on the "
            "computer that your other tools cannot do: files, code, packages, system settings, "
            "multi-step shell work. Write a complete, specific task. Set continue_previous=true to "
            "continue the last Claude Code task with its context. Report its result honestly."
        )

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(DROP_ENV_PREFIXES)}
        if self.config.get("base_url"):
            env["ANTHROPIC_BASE_URL"] = self.config["base_url"]
        key = resolve_secret(self.config.get("api_key"))
        if key:
            env["ANTHROPIC_API_KEY"] = key
        return env

    def _last_session(self) -> str:
        try:
            return json.loads(self.state_file.read_text()).get("session_id", "")
        except (OSError, ValueError):
            return ""

    @tool(
        "Give a task to Claude Code, an agent that reads and edits files and runs commands on "
        "this computer. Returns its final report.",
        params={
            "task": "string: full task description with every needed detail",
            "workdir": "string: working folder (default from config)",
            "continue_previous": "boolean: continue the last Claude Code task with its memory",
        },
        required=["task"],
        risk="dangerous",
        editable=["task", "workdir"],
        title="Claude Code task (full control)",
        untrusted=True,
    )
    async def run_task(self, task: str, workdir: str = "", continue_previous: bool = False):
        cwd = Path(os.path.expanduser(workdir or self.config.get("workdir", "~")))
        if not cwd.is_dir():
            return {"ok": False, "error": f"Folder {cwd} does not exist."}
        cmd = [self.binary, "-p", "--output-format", "stream-json", "--verbose"]
        if self.config.get("bypass_permissions", True):
            cmd.append("--dangerously-skip-permissions")
        if self.config.get("model"):
            cmd += ["--model", str(self.config["model"])]
        session = self._last_session() if continue_previous else ""
        if session:
            cmd += ["--resume", session]
        cmd += [str(a) for a in self.config.get("extra_args", [])]

        await self.ctx.notify(f"Claude Code started in {cwd}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=self._env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        proc.stdin.write(task.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        result: dict[str, Any] = {}
        steps: list[str] = []

        async def read() -> None:
            async for raw in proc.stdout:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get("type") == "assistant":
                    for block in (ev.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use":
                            step = describe_tool_use(block)
                            steps.append(step)
                            await self.ctx.notify(f"Claude Code › {step}")
                elif ev.get("type") == "result":
                    result.update(ev)

        try:
            await asyncio.wait_for(read(), float(self.config.get("timeout", 900)))
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "Claude Code timed out and was stopped.", "steps": steps[-30:]}

        if not result:
            err = (await proc.stderr.read()).decode(errors="replace")
            return {"ok": False, "error": f"Claude Code stopped without a result: {err[-2000:]}", "steps": steps[-30:]}
        if result.get("session_id"):
            self.state_file.write_text(json.dumps({"session_id": result["session_id"]}))
        ok = not result.get("is_error") and result.get("subtype") in (None, "success")
        report = str(result.get("result") or "").strip()
        data = {"ok": ok, "report": report[-12000:], "steps": steps[-30:], "step_count": len(steps)}
        return ToolResult(
            data=data,
            visual={
                "type": "text",
                "title": "Claude Code report",
                "subtitle": f"{len(steps)} steps · {'done' if ok else 'failed'}",
                "body": report[-3500:] or "(no output)",
            },
            summary="Claude Code finished" if ok else "Claude Code failed",
        )
