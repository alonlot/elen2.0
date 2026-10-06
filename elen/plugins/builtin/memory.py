"""Long-term memory: facts and permanent rules.

Two kinds of memory are kept separate from the chat. Clearing the chat does not
clear them.

  facts  things about the user and their world ("My wife is Dana").
         The newest ones are in every system prompt; older ones are found
         with memory__recall.
  rules  lessons from mistakes and standing orders ("Never send mail without
         my signature"). ALL rules are in every system prompt, at the top.
         Before every action, the core also checks the action against the
         rules in code (see Elen.check_rules). Only the user can delete a rule,
         through an approval dialog.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from elen.embeddings import Embedder, cosine
from elen.plugins import Plugin, ToolResult, tool

WORD_RE = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> set[str]:
    return {w for w in WORD_RE.findall(text.lower()) if len(w) > 2}


class MemoryPlugin(Plugin):
    name = "memory"
    description = "Long-term memory: facts and permanent rules."

    async def setup(self) -> None:
        self.path = self.ctx.data_dir / "memories.json"
        try:
            self.items: list[dict[str, Any]] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.items = []
        for item in self.items:
            item.setdefault("kind", "fact")
        # Search by meaning: [plugins.memory] embeddings = {model, base_url, api_key}.
        # base_url defaults to the brain URL when the brain is an OpenAI-compatible server.
        emb_cfg = dict(self.config.get("embeddings") or {})
        brain = self.ctx.setting("brain", default={}) or {}
        if not emb_cfg.get("base_url") and brain.get("provider", "openai_compatible") not in ("anthropic", "claude"):
            emb_cfg["base_url"] = brain.get("base_url", "")
            emb_cfg.setdefault("api_key", brain.get("api_key", ""))
        self.embedder = Embedder(emb_cfg)
        self.vec_path = self.ctx.data_dir / "vectors.json"
        try:
            stored = json.loads(self.vec_path.read_text())
            self.vectors = stored["vectors"] if stored.get("model") == self.embedder.model else {}
        except (OSError, ValueError, KeyError):
            self.vectors = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def _add(self, kind: str, text: str, **extra: Any) -> dict[str, Any]:
        text = " ".join(text.split())
        for item in self.items:
            if item["kind"] == kind and item["text"].lower() == text.lower():
                return item  # already known
        item = {"id": uuid.uuid4().hex[:6], "kind": kind, "text": text, "time": time.time(), **extra}
        self.items.append(item)
        self._save()
        return item

    def facts(self) -> list[dict[str, Any]]:
        return [m for m in self.items if m["kind"] == "fact"]

    def rules(self) -> list[dict[str, Any]]:
        return [m for m in self.items if m["kind"] == "rule"]

    # Read by the core when it builds the system prompt.
    def memories(self) -> list[str]:
        limit = int(self.config.get("max_facts_in_prompt", 60))
        return [f"[{m['id']}] {m['text']}" for m in self.facts()[-limit:]]

    def rule_lines(self) -> list[str]:
        return [f"[{m['id']}] {m['text']}" for m in self.rules()]

    def prompt_hint(self) -> str:
        older = max(0, len(self.facts()) - int(self.config.get("max_facts_in_prompt", 60)))
        if older:
            return f"{older} older facts are not in this prompt. Use memory__recall to find them."
        return ""

    # ---------------------------------------------------------------- tools
    @tool(
        "Save a lasting fact about the user or their world to long-term memory. Use it when the "
        "user asks you to remember something, or tells you a stable personal fact (family, "
        "preferences, accounts, places). Tell the user that you saved it.",
        params={"fact": "string: the fact, in one clear sentence"},
        risk="low",
    )
    async def remember(self, fact: str):
        item = self._add("fact", fact)
        await self.ctx.notify(f"Remembered: {item['text']}")
        return {"saved": True, "id": item["id"]}

    @tool(
        "Save a PERMANENT RULE. Use it every time the user corrects a mistake you made, or says "
        "'never', 'always', 'don't do that again', 'from now on'. Write the rule as a general, "
        "clear instruction that will prevent the same mistake in other situations too, for "
        "example 'Never send an email without showing me the final text first'. Rules are "
        "always followed and are checked before every action.",
        params={
            "rule": "string: the rule, as an instruction to yourself",
            "mistake": "string: the mistake that caused this rule, in one sentence",
        },
        required=["rule"],
        risk="low",
    )
    async def add_rule(self, rule: str, mistake: str = ""):
        item = self._add("rule", rule, mistake=mistake.strip())
        await self.ctx.notify(f"New permanent rule [{item['id']}]: {item['text']}")
        return {"saved": True, "id": item["id"], "total_rules": len(self.rules())}

    async def _semantic_scores(self, query: str) -> dict[str, float]:
        """Similarity of the query to each memory, by meaning. Empty when not set up."""
        if not self.embedder.enabled or not self.items:
            return {}
        missing = [m for m in self.items if m["id"] not in self.vectors]
        if missing:
            vecs = await self.embedder.embed([m["text"] for m in missing])
            for m, v in zip(missing, vecs):
                self.vectors[m["id"]] = v
            self.vec_path.write_text(json.dumps({"model": self.embedder.model, "vectors": self.vectors}))
        (qv,) = await self.embedder.embed([query])
        return {m["id"]: cosine(qv, self.vectors[m["id"]]) for m in self.items if m["id"] in self.vectors}

    @tool(
        "Search long-term memory (facts and rules) by meaning and by words. Use it before you "
        "answer a question about the user's life or preferences that is not in the prompt.",
        params={"query": "string: what you look for, in your own words", "limit": "integer: max results, default 8"},
        required=["query"],
    )
    async def recall(self, query: str, limit: int = 8):
        q = words(query)
        try:
            semantic = await self._semantic_scores(query)
            mode = "meaning" if semantic else "words"
        except Exception as e:  # noqa: BLE001
            self.log.warning("search by meaning failed, using words: %s", e)
            semantic, mode = {}, "words (search by meaning failed)"
        min_sim = float(self.config.get("min_similarity", 0.45))
        scored = []
        for m in self.items:
            text = m["text"] + " " + m.get("mistake", "")
            overlap = len(q & words(text))
            sim = semantic.get(m["id"], 0.0)
            if overlap or query.lower() in text.lower() or sim >= min_sim:
                scored.append((sim + 0.1 * overlap, m["time"], m))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        hits = [
            {"id": m["id"], "kind": m["kind"], "text": m["text"], "saved": time.strftime("%Y-%m-%d", time.localtime(m["time"]))}
            for _, _, m in scored[: max(1, min(int(limit), 30))]
        ]
        result: dict[str, Any] = {"query": query, "matches": hits, "search": mode}
        if not hits:
            result["note"] = "Nothing in memory matches. Say that you do not know."
        return result

    @tool(
        "Delete a fact from long-term memory by id.",
        params={"id": "string: fact id"},
        risk="low",
    )
    async def forget_fact(self, id: str):
        item = next((m for m in self.facts() if m["id"] == id), None)
        if not item:
            return {"deleted": False, "error": f"No fact with id {id}."}
        self.items.remove(item)
        self.vectors.pop(id, None)
        self._save()
        return {"deleted": True, "text": item["text"]}

    @tool(
        "Delete a permanent rule by id. Only when the user clearly asks to remove that rule. "
        "The user must approve it.",
        params={"id": "string: rule id", "text": "string: the rule text, for the approval dialog"},
        required=["id"],
        risk="write",
        title="Delete permanent rule",
    )
    async def delete_rule(self, id: str, text: str = ""):
        item = next((m for m in self.rules() if m["id"] == id), None)
        if not item:
            return {"deleted": False, "error": f"No rule with id {id}."}
        self.items.remove(item)
        self._save()
        return {"deleted": True, "text": item["text"]}

    @tool("Show all rules and saved facts on screen.")
    async def list_memories(self):
        rules, facts = self.rules(), self.facts()
        return ToolResult(
            data={"rules": rules, "facts": facts},
            visual={
                "type": "panels",
                "title": "Memory",
                "subtitle": f"{len(rules)} rules · {len(facts)} facts",
                "panels": [
                    {
                        "type": "list",
                        "title": "Permanent rules",
                        "items": [{"title": m["text"], "subtitle": m.get("mistake", ""), "meta": m["id"]} for m in rules],
                    },
                    {
                        "type": "list",
                        "title": "Facts",
                        "items": [{"title": m["text"], "meta": m["id"]} for m in facts[-40:]],
                    },
                ],
            },
        )
