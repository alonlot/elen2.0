"""Brain backed by the Claude Code CLI (`claude -p`), not the API.

In agent mode, `claude -p` runs the whole agent loop. Elen's plugins reach it
as MCP tools (server name "elen", see elen/mcp_bridge.py). Each tool call goes
back into the Elen daemon, so the guard (approvals, recipient checks, rule
checks) still decides about every action.

Built-in Claude Code tools (Bash, Edit, Read...) are off by default
(builtin_tools = []). With them on, Claude Code could act outside Elen's guard.

Custom model and URL:
  model     -> --model (alias such as "opus" / "sonnet", a full model id, or any
               model name your custom endpoint accepts)
  base_url  -> ANTHROPIC_BASE_URL for the CLI (proxy, gateway, compatible server)
  api_key   -> ANTHROPIC_API_KEY   (empty = use your `claude` login)
  auth_token-> ANTHROPIC_AUTH_TOKEN (for gateways that want a bearer token)
  env       -> any other environment variables for the CLI
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..secrets import resolve_secret
from .base import LLMError, LLMResponse

log = logging.getLogger("elen.claude_cli")

# Environment of a parent Claude Code session must not leak into the child.
DROP_ENV_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_SESSION", "CLAUDE_CODE_ENTRYPOINT")


@dataclass
class CLIResult:
    text: str
    session_id: str
    is_error: bool
    error: str = ""


def _flatten(messages: list[dict[str, Any]], image_dir: Path | None) -> tuple[str, bool]:
    """Turn neutral messages into one prompt. Images are saved as files."""
    lines: list[str] = []
    has_image = False
    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for j, part in enumerate(content):
                if part.get("type") == "image" and image_dir is not None:
                    ext = (part.get("media_type") or "image/png").split("/")[-1]
                    path = image_dir / f"image-{i}-{j}.{ext}"
                    path.write_bytes(base64.b64decode(part["data"]))
                    parts.append(f"[Image file: {path} - open it with the Read tool and look at it]")
                    has_image = True
                else:
                    parts.append(part.get("text", ""))
            content = "\n".join(parts)
        if role == "tool":
            lines.append(f"Tool result ({msg.get('name')}): {content}")
        elif role == "assistant":
            lines.append(f"Assistant: {content or ''}")
        else:
            lines.append(content if len(messages) == 1 else f"User: {content}")
    return "\n\n".join(lines), has_image


class ClaudeCLIProvider:
    name = "claude_cli"
    agent_mode = True

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.binary = shutil.which(cfg.get("binary") or "claude") or ""
        self.model = cfg.get("model") or ""
        # Own timeout: one request includes approval waits and tool runs.
        self.timeout = float(cfg.get("cli_timeout") or 1800)
        self._proc: asyncio.subprocess.Process | None = None

    # ------------------------------------------------------------ helpers
    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(DROP_ENV_PREFIXES)}
        if self.cfg.get("base_url"):
            env["ANTHROPIC_BASE_URL"] = self.cfg["base_url"]
        key = resolve_secret(self.cfg.get("api_key"))
        if key:
            env["ANTHROPIC_API_KEY"] = key
        token = resolve_secret(self.cfg.get("auth_token"))
        if token:
            env["ANTHROPIC_AUTH_TOKEN"] = token
        for k, v in (self.cfg.get("env") or {}).items():
            env[str(k)] = resolve_secret(str(v))
        # Approvals and long tasks can take minutes; do not let the MCP call time out first.
        env.setdefault("MCP_TOOL_TIMEOUT", str(int(self.timeout * 1000)))
        return env

    def _base_cmd(self, system: str) -> list[str]:
        if not self.binary:
            raise LLMError(
                "The 'claude' CLI is not installed. Install Claude Code "
                "(npm install -g @anthropic-ai/claude-code) and run 'claude' once to log in."
            )
        cmd = [self.binary, "-p", "--output-format", "stream-json", "--verbose"]
        cmd += ["--setting-sources", str(self.cfg.get("setting_sources", ""))]
        if self.cfg.get("replace_system_prompt", True):
            cmd += ["--system-prompt", system]
        else:
            cmd += ["--append-system-prompt", system]
        cmd += ["--system-prompt-snapshot", "off"]
        if self.model:
            cmd += ["--model", self.model]
        if self.cfg.get("effort"):
            cmd += ["--effort", str(self.cfg["effort"])]
        return cmd

    async def _run(
        self,
        cmd: list[str],
        prompt: str,
        cwd: str,
        on_event: Callable[[str, Any], Any] | None = None,
    ) -> CLIResult:
        cmd = cmd + [str(a) for a in self.cfg.get("extra_args", [])]
        log.debug("running %s", " ".join(cmd[:3]))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=self.env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        self._proc = proc
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        result: CLIResult | None = None
        session_id = ""

        async def read_stream() -> None:
            nonlocal result, session_id
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                session_id = ev.get("session_id") or session_id
                kind = ev.get("type")
                if kind == "assistant" and on_event:
                    for block in (ev.get("message") or {}).get("content") or []:
                        if block.get("type") == "text" and block.get("text"):
                            await _maybe_await(on_event("text", block["text"]))
                        elif block.get("type") == "tool_use":
                            await _maybe_await(on_event("tool_use", block.get("name", "")))
                elif kind == "result":
                    is_error = bool(ev.get("is_error")) or ev.get("subtype") not in (None, "success")
                    result = CLIResult(
                        text=str(ev.get("result") or "").strip(),
                        session_id=ev.get("session_id") or session_id,
                        is_error=is_error,
                        error=str(ev.get("result") or ev.get("subtype") or "") if is_error else "",
                    )

        timeout = self.timeout
        try:
            await asyncio.wait_for(read_stream(), timeout)
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            proc.kill()
            raise LLMError(f"claude -p did not finish in {int(timeout)} s and was stopped.")
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            self._proc = None
        if result is None:
            err = (await proc.stderr.read()).decode(errors="replace").strip()
            raise LLMError(f"claude -p stopped without a result (exit {proc.returncode}): {err[-800:]}")
        return result

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()

    # ------------------------------------------------------------ agent mode
    async def run_agent(
        self,
        prompt: str,
        system: str,
        mcp_config: Path,
        session_id: str = "",
        on_event: Callable[[str, Any], Any] | None = None,
    ) -> CLIResult:
        cmd = self._base_cmd(system)
        cmd += ["--mcp-config", str(mcp_config), "--strict-mcp-config"]
        builtin = [str(t) for t in self.cfg.get("builtin_tools", [])]
        cmd += ["--tools", ",".join(builtin)]
        cmd += ["--allowedTools", ",".join(["mcp__elen", *builtin])]
        cmd += ["--permission-mode", "dontAsk"]
        if session_id:
            cmd += ["--resume", session_id]
        cwd = os.path.expanduser(self.cfg.get("workdir") or "~")
        return await self._run(cmd, prompt, cwd, on_event)

    # ------------------------------------------------------------ simple calls
    async def chat(self, system, messages, tools=None) -> LLMResponse:
        """One answer, no Elen tools. Used for the rule checker and for vision."""
        if tools:
            raise LLMError("claude_cli runs tools in agent mode only.")
        with tempfile.TemporaryDirectory(prefix="elen-cli-") as tmp:
            prompt, has_image = _flatten(messages, Path(tmp))
            cmd = self._base_cmd(system) + ["--strict-mcp-config", "--no-session-persistence"]
            if has_image:
                cmd += ["--tools", "Read", "--allowedTools", "Read", "--add-dir", tmp]
                cmd += ["--permission-mode", "dontAsk"]
            else:
                cmd += ["--tools", ""]
            res = await self._run(cmd, prompt, tmp)
        if res.is_error:
            raise LLMError(f"claude -p error: {res.error[:500]}")
        return LLMResponse(text=res.text, stop_reason="end_turn")


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value):
        await value
