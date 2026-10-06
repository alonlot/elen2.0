"""Full computer control through Claude Code (`claude -p`).

Elen hands a task to the Claude Code CLI in headless mode. With
bypass_permissions = true it adds --dangerously-skip-permissions, so Claude Code
can run any command and edit any file without asking. Elen's own guard still
shows you the exact task and asks you to approve it once before it starts.

[plugins.claude_code]
enabled = true
binary = "claude"
bypass_permissions = true
workdir = "~"
timeout = 900
extra_args = []        # for example ["--model", "claude-opus-5-5"]
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from elen.plugins import Plugin, ToolResult, tool


class ClaudeCodePlugin(Plugin):
    name = "claude_code"
    description = "Delegate computer tasks to Claude Code."

    async def setup(self) -> None:
        self.binary = shutil.which(self.config.get("binary", "claude")) or ""
        if not self.binary:
            raise RuntimeError("the 'claude' CLI is not installed (npm install -g @anthropic-ai/claude-code)")

    def prompt_hint(self) -> str:
        mode = "with all permission checks bypassed" if self.config.get("bypass_permissions", True) else "in normal permission mode"
        return (
            f"claude_code__run_task runs a coding / computer agent {mode}. Use it for multi-step "
            "work on files, code, packages or settings that other tools cannot do. Give it a "
            "complete, specific task description."
        )

    @tool(
        "Give a task to the Claude Code agent, which can read and edit files and run commands "
        "on this computer. Returns its final report.",
        params={
            "task": "string: full task description with every needed detail",
            "workdir": "string: working folder (default from config)",
        },
        required=["task"],
        risk="dangerous",
        editable=["task", "workdir"],
        title="Claude Code task (full control)",
    )
    async def run_task(self, task: str, workdir: str = ""):
        cwd = Path(os.path.expanduser(workdir or self.config.get("workdir", "~")))
        if not cwd.is_dir():
            return {"ok": False, "error": f"Folder {cwd} does not exist."}
        cmd = [self.binary, "-p", task, "--output-format", "text"]
        if self.config.get("bypass_permissions", True):
            cmd.append("--dangerously-skip-permissions")
        cmd += list(self.config.get("extra_args", []))
        await self.ctx.notify(f"Claude Code is working in {cwd} …")
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), float(self.config.get("timeout", 900)))
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "Claude Code timed out and was stopped."}
        report = out.decode(errors="replace").strip()
        result = {"ok": proc.returncode == 0, "exit_code": proc.returncode, "report": report[-12000:]}
        if proc.returncode != 0:
            result["stderr"] = err.decode(errors="replace")[-3000:]
        return ToolResult(
            data=result,
            visual={"type": "text", "title": "Claude Code report", "body": report[-3500:] or "(no output)"},
            summary="Claude Code finished" if proc.returncode == 0 else "Claude Code failed",
        )
