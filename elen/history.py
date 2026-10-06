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
        # Tool results from claude -p turns (they are not in the transcript).
        self.data_texts: list[str] = []
        # Small state, for example the claude -p session id.
        self.meta: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.display = data.get("display", [])
            self.transcript = data.get("transcript", [])
            self.data_texts = data.get("data_texts", [])
            self.meta = data.get("meta", {})
        except (OSError, ValueError):
            self.display, self.transcript, self.data_texts, self.meta = [], [], [], {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "display": self.display,
                    "transcript": self.transcript,
                    "data_texts": self.data_texts[-200:],
                    "meta": self.meta,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def clear(self) -> None:
        self.display, self.transcript, self.data_texts, self.meta = [], [], [], {}
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
        texts = [m.get("content") or "" for m in self.transcript if m.get("role") == "tool"]
        return texts + self.data_texts

    def recap(self, limit: int = 12, skip_last: bool = True) -> str:
        """Recent chat as plain text, to start a new claude -p session with context."""
        shown = self.display[:-1] if skip_last else self.display
        items = [m for m in shown if m["role"] in ("user", "assistant")][-limit:]
        if not items:
            return ""
        lines = [f"{'User' if m['role'] == 'user' else 'Elen'}: {m['text'][:1500]}" for m in items]
        return "Earlier conversation, for context:\n" + "\n".join(lines) + "\n\nNew message:\n"
