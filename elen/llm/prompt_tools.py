"""Tool calling for models without native tool support.

Wraps any provider. The tool list goes into the system prompt, and the model
asks for tools by writing a JSON block. This lets Elen use any LLM, also small
local models that do not support the "tools" API field.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .base import LLMResponse, ToolCall

INSTRUCTIONS = """

# Tools
You can use the tools listed below. To use tools, reply with ONLY this JSON, in a ```json block,
and nothing else:
```json
{"tool_calls": [{"name": "<tool name>", "arguments": {<arguments>}}]}
```
You then get the tool results in the next message. To answer the user, write normal text with
no JSON block. Never invent tool results.

Available tools:
"""

BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def describe_tools(tools: list[dict[str, Any]]) -> str:
    lines = []
    for t in tools:
        params = json.dumps(t["parameters"].get("properties", {}), ensure_ascii=False)
        required = ", ".join(t["parameters"].get("required", [])) or "none"
        lines.append(f"- {t['name']}: {t['description']}\n  arguments: {params}\n  required: {required}")
    return "\n".join(lines)


def to_plain_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn tool calls and tool results into ordinary text messages."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        if role == "assistant" and msg.get("tool_calls"):
            calls = [{"name": c["name"], "arguments": c.get("arguments") or {}} for c in msg["tool_calls"]]
            text = (msg.get("content") or "").strip()
            block = "```json\n" + json.dumps({"tool_calls": calls}, ensure_ascii=False) + "\n```"
            out.append({"role": "assistant", "content": f"{text}\n{block}".strip()})
        elif role == "tool":
            text = f"Tool result for {msg.get('name')}{' (ERROR)' if msg.get('is_error') else ''}:\n{msg.get('content')}"
            if out and out[-1]["role"] == "user" and str(out[-1]["content"]).startswith("Tool result"):
                out[-1] = {"role": "user", "content": out[-1]["content"] + "\n\n" + text}
            else:
                out.append({"role": "user", "content": text})
        else:
            out.append({k: v for k, v in msg.items() if k in ("role", "content")})
    return out


def parse_tool_calls(text: str, known: set[str]) -> tuple[str, list[ToolCall]]:
    """Return (text without the JSON block, tool calls)."""
    candidates = [m.group(1) for m in BLOCK_RE.finditer(text)]
    stripped = text.strip()
    if not candidates and stripped.startswith("{") and '"tool_calls"' in stripped:
        candidates = [stripped]
    for raw in candidates:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        calls_data = data.get("tool_calls") if isinstance(data, dict) else None
        if not isinstance(calls_data, list):
            continue
        calls = []
        for c in calls_data:
            if isinstance(c, dict) and c.get("name"):
                args = c.get("arguments") or {}
                calls.append(ToolCall(f"call_{uuid.uuid4().hex[:8]}", str(c["name"]), args if isinstance(args, dict) else {}))
        if calls:
            rest = BLOCK_RE.sub("", text).strip() if raw != stripped else ""
            return rest, calls
    return text.strip(), []


class PromptToolsAdapter:
    """Give any provider tool calling through the prompt."""

    def __init__(self, inner):
        self.inner = inner
        self.name = getattr(inner, "name", "prompt_tools")

    async def chat(self, system, messages, tools=None) -> LLMResponse:
        if not tools:
            return await self.inner.chat(system, to_plain_messages(messages), None)
        resp = await self.inner.chat(system + INSTRUCTIONS + describe_tools(tools), to_plain_messages(messages), None)
        text, calls = parse_tool_calls(resp.text, {t["name"] for t in tools})
        return LLMResponse(text=text, tool_calls=calls, stop_reason=resp.stop_reason)
