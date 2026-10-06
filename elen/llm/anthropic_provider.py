"""Claude provider, built on the official Anthropic Python SDK."""

from __future__ import annotations

import json
from typing import Any

import anthropic

from ..secrets import resolve_secret
from .base import LLMError, LLMResponse, ToolCall

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _block_dict(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return dict(block)


def _user_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if part.get("type") == "image":
            parts.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.get("media_type", "image/png"),
                        "data": part["data"],
                    },
                }
            )
        else:
            parts.append({"type": "text", "text": part.get("text", "")})
    return parts


def to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert neutral messages to Messages API format.

    Consecutive tool results are merged into one user message, as the API
    expects all results of one assistant turn together.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            out.append({"role": "user", "content": _user_content(msg["content"])})
        elif role == "assistant":
            raw = msg.get("_raw")
            if raw and raw.get("provider") == "anthropic":
                out.append({"role": "assistant", "content": raw["content"]})
                continue
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call.get("arguments") or {},
                    }
                )
            if not blocks:
                blocks.append({"type": "text", "text": "(no reply)"})
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            result = {
                "type": "tool_result",
                "tool_use_id": msg["tool_call_id"],
                "content": msg.get("content") or "",
            }
            if msg.get("is_error"):
                result["is_error"] = True
            prev = out[-1] if out else None
            if (
                prev
                and prev["role"] == "user"
                and isinstance(prev["content"], list)
                and prev["content"]
                and prev["content"][0].get("type") == "tool_result"
            ):
                prev["content"].append(result)
            else:
                out.append({"role": "user", "content": [result]})
    return out


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.model = cfg.get("model") or "claude-opus-5-5"
        key = resolve_secret(cfg.get("api_key"))
        kwargs: dict[str, Any] = {"max_retries": 2}
        if key:
            kwargs["api_key"] = key
        if cfg.get("base_url"):
            kwargs["base_url"] = cfg["base_url"]
        self.client = anthropic.AsyncAnthropic(**kwargs)

    async def chat(self, system, messages, tools=None) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(self.cfg.get("max_tokens") or 8000),
            "system": system,
            "messages": to_anthropic_messages(messages),
        }
        if tools:
            params["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]
        extra_body: dict[str, Any] = {}
        if self.cfg.get("effort"):
            extra_body["output_config"] = {"effort": self.cfg["effort"]}
        betas: list[str] = []
        if self.cfg.get("fallbacks"):
            extra_body["fallbacks"] = "default"
            betas.append(FALLBACK_BETA)
        try:
            if betas:
                resp = await self.client.beta.messages.create(
                    **params, betas=betas, extra_body=extra_body or None
                )
            else:
                resp = await self.client.messages.create(
                    **params, extra_body=extra_body or None
                )
        except TypeError as e:
            if "authentication" in str(e).lower():
                raise LLMError(
                    "No Claude API key. Set ANTHROPIC_API_KEY or [brain] api_key in ~/.config/elen/config.toml."
                ) from e
            raise
        except anthropic.AuthenticationError as e:
            raise LLMError(f"Claude API key is missing or wrong: {e}") from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"Claude rate limit reached: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Cannot reach the Claude API: {e}") from e

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                calls.append(ToolCall(block.id, block.name, dict(args or {})))
        text = "".join(text_parts).strip()
        if resp.stop_reason == "refusal" and not text:
            text = "I can't help with that request."
        return LLMResponse(
            text=text,
            tool_calls=calls,
            stop_reason=resp.stop_reason or "",
            raw={"provider": "anthropic", "content": [_block_dict(b) for b in resp.content]},
        )
