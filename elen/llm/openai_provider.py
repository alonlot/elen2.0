"""Generic provider for any OpenAI-compatible chat API.

This is the default brain. Almost every LLM server speaks this API: OpenAI,
Ollama (http://localhost:11434/v1), LM Studio, vLLM, llama.cpp server, LiteLLM,
OpenRouter, Groq, Together, Mistral, DeepSeek, Google Gemini (OpenAI endpoint),
Azure OpenAI and others.

Config keys: model, base_url, api_key, headers (extra HTTP headers),
extra_body (extra request fields, for example {"temperature": 0.3}),
tool_mode: "auto" (native tools, prompt tools if the server rejects them),
"native" or "prompt".
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..secrets import resolve_secret
from .base import LLMError, LLMResponse, ToolCall, emit_text


def _user_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if part.get("type") == "image":
            url = f"data:{part.get('media_type', 'image/png')};base64,{part['data']}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            parts.append({"type": "text", "text": part.get("text", "")})
    return parts


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg["role"]
        if role == "user":
            out.append({"role": "user", "content": _user_content(msg["content"])})
        elif role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                item["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("arguments") or {}),
                        },
                    }
                    for c in msg["tool_calls"]
                ]
            out.append(item)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg.get("content") or "",
                }
            )
    return out


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.model = cfg.get("model") or "gpt-4o"
        self.base_url = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = resolve_secret(cfg.get("api_key"))
        self.tool_mode = (cfg.get("tool_mode") or "auto").lower()
        self._prompt_tools = None
        self._transport = None  # tests can set an httpx transport

    async def chat(self, system, messages, tools=None, on_text=None) -> LLMResponse:
        if tools and self.tool_mode == "prompt":
            if self._prompt_tools is None:
                from .prompt_tools import PromptToolsAdapter

                self._prompt_tools = PromptToolsAdapter(_NoTools(self))
            return await self._prompt_tools.chat(system, messages, tools, on_text)
        try:
            return await self._request(system, messages, tools, on_text)
        except LLMError as e:
            text = str(e).lower()
            if tools and self.tool_mode == "auto" and " 4" in text[:30] and "tool" in text:
                # The server or model does not support native tools: use prompt tools from now on.
                self.tool_mode = "prompt"
                return await self.chat(system, messages, tools, on_text)
            raise

    async def _request(self, system, messages, tools=None, on_text=None) -> LLMResponse:
        stream = on_text is not None and self.cfg.get("stream", True)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(system, messages),
            "max_tokens": int(self.cfg.get("max_tokens") or 4000),
        }
        body.update(self.cfg.get("extra_body") or {})
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for k, v in (self.cfg.get("headers") or {}).items():
            headers[str(k)] = resolve_secret(str(v))
        if stream:
            body["stream"] = True
            return await self._stream(body, headers, on_text)
        try:
            async with httpx.AsyncClient(
                timeout=float(self.cfg.get("timeout") or 300), transport=self._transport
            ) as client:
                r = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach {self.base_url}: {e}") from e
        if r.status_code >= 400:
            raise LLMError(f"Model API error {r.status_code}: {r.text[:500]}")
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(c.get("id") or fn.get("name", "call"), fn.get("name", ""), args))
        return LLMResponse(
            text=(msg.get("content") or "").strip(),
            tool_calls=calls,
            stop_reason=choice.get("finish_reason") or "",
        )


    async def _stream(self, body, headers, on_text) -> LLMResponse:
        """Server-sent events: text pieces go to on_text, tool calls are put together."""
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish = ""
        try:
            async with httpx.AsyncClient(
                timeout=float(self.cfg.get("timeout") or 300), transport=self._transport
            ) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/completions", json=body, headers=headers
                ) as r:
                    if r.status_code >= 400:
                        err = (await r.aread()).decode(errors="replace")
                        raise LLMError(f"Model API error {r.status_code}: {err[:500]}")
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue
                        choice = (event.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            await emit_text(on_text, delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            cur = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "args": ""})
                            fn = tc.get("function") or {}
                            cur["id"] = tc.get("id") or cur["id"]
                            if fn.get("name"):
                                cur["name"] = fn["name"] if not cur["name"] else cur["name"] + fn["name"]
                            cur["args"] += fn.get("arguments") or ""
                        finish = choice.get("finish_reason") or finish
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach {self.base_url}: {e}") from e
        tool_calls = []
        for i in sorted(calls):
            c = calls[i]
            try:
                args = json.loads(c["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(c["id"] or f"call_{i}", c["name"], args if isinstance(args, dict) else {}))
        return LLMResponse(text="".join(text_parts).strip(), tool_calls=tool_calls, stop_reason=finish)


class _NoTools:
    """Calls the server without the tools field (used by prompt tools)."""

    def __init__(self, provider: OpenAICompatProvider):
        self.provider = provider
        self.name = provider.name

    async def chat(self, system, messages, tools=None, on_text=None) -> LLMResponse:
        return await self.provider._request(system, messages, None, on_text)
