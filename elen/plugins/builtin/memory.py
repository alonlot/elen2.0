"""Long-term memory: facts the user asks Elen to remember.

These are kept separate from the chat. Clearing the chat does not clear them.
"""

from __future__ import annotations

import json
import time
import uuid

from elen.plugins import Plugin, ToolResult, tool


class MemoryPlugin(Plugin):
    name = "memory"
    description = "Remember facts the user tells you to remember."

    async def setup(self) -> None:
        self.path = self.ctx.data_dir / "memories.json"
        try:
            self.items = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.items = []

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1))

    def memories(self) -> list[str]:
        limit = int(self.config.get("max_in_prompt", 60))
        return [f"[{m['id']}] {m['text']}" for m in self.items[-limit:]]

    @tool(
        "Save a fact to long-term memory. Only when the user asks you to remember something.",
        params={"fact": "string: the fact, in one sentence"},
        risk="low",
    )
    async def remember(self, fact: str):
        item = {"id": uuid.uuid4().hex[:6], "text": fact.strip(), "time": time.time()}
        self.items.append(item)
        self._save()
        return {"saved": True, "id": item["id"]}

    @tool("Delete a fact from long-term memory by id.", params={"id": "string: memory id"}, risk="low")
    async def forget(self, id: str):
        before = len(self.items)
        self.items = [m for m in self.items if m["id"] != id]
        self._save()
        return {"deleted": before != len(self.items)}

    @tool("List all saved memories and show them on screen.")
    async def list_memories(self):
        return ToolResult(
            data=self.items,
            visual={
                "type": "list",
                "title": "Memory",
                "items": [{"title": m["text"], "meta": m["id"]} for m in self.items],
            },
        )
