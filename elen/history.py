"""Chat history that survives restarts until the user clears it.

Two lists are kept in one JSON file:
  display     what the chat window shows (user, assistant, activity lines)
  transcript  neutral model messages, used as context for the next request
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class History:
    def __init__(self, path: Path, max_context: int = 40, max_tool_chars: int = 12000):
        self.path = path
        self.max_context = max_context
        self.max_tool_chars = max_tool_chars
        self.display: list[dict[str, Any]] = []
        self.transcript: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.display = data.get("display", [])
            self.transcript = data.get("transcript", [])
        except (OSError, ValueError):
            self.display, self.transcript = [], []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"display": self.display, "transcript": self.transcript}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def clear(self) -> None:
        self.display, self.transcript = [], []
        self.save()

    def add_display(self, role: str, text: str, **meta: Any) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex[:12], "role": role, "text": text, "time": time.time()}
        if meta:
            item["meta"] = meta
        self.display.append(item)
        return item

    def add_turn(self, messages: list[dict[str, Any]]) -> None:
        """Append the messages of one finished turn to the transcript."""
        for msg in messages:
            clean = {k: v for k, v in msg.items() if not k.startswith("_")}
            if clean.get("role") == "tool":
                content = clean.get("content") or ""
                if len(content) > self.max_tool_chars:
                    clean["content"] = content[: self.max_tool_chars] + "\n...[truncated]"
            self.transcript.append(clean)

    def context(self) -> list[dict[str, Any]]:
        """The last messages, cut so the context starts at a user message."""
        msgs = self.transcript[-self.max_context :]
        while msgs and msgs[0].get("role") != "user":
            msgs = msgs[1:]
        return [dict(m) for m in msgs]

    def user_texts(self) -> list[str]:
        return [
            m["content"]
            for m in self.transcript
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]

    def tool_texts(self) -> list[str]:
        return [m.get("content") or "" for m in self.transcript if m.get("role") == "tool"]
