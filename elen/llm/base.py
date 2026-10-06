"""Provider-neutral message format used by the brain.

Messages:
  {"role": "user", "content": "text"}
  {"role": "assistant", "content": "text", "tool_calls": [ToolCall dicts]}
  {"role": "tool", "tool_call_id": "...", "name": "...", "content": "...", "is_error": bool}

An in-progress assistant message can also carry "_raw": {"provider": name,
"content": [...]}. A provider replays that raw content unchanged (for example
Claude thinking blocks). Raw content is never stored in the history file.

Images for vision calls are user content parts:
  {"type": "text", "text": "..."}
  {"type": "image", "media_type": "image/png", "data": "<base64>"}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    raw: dict[str, Any] | None = None


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    async def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...
