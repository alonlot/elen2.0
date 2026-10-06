"""Text embeddings for search by meaning (memory recall).

Uses any OpenAI-compatible /embeddings API: Ollama (model "nomic-embed-text"),
LM Studio, vLLM, LiteLLM, OpenAI ("text-embedding-3-small") and others.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from .secrets import resolve_secret


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Embedder:
    def __init__(self, cfg: dict[str, Any]):
        self.model = cfg.get("model") or ""
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.api_key = resolve_secret(cfg.get("api_key"))
        self._transport = None  # tests can set an httpx transport

    @property
    def enabled(self) -> bool:
        return bool(self.model and self.base_url)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as client:
            r = await client.post(
                f"{self.base_url}/embeddings", json={"model": self.model, "input": texts}, headers=headers
            )
        if r.status_code >= 400:
            raise RuntimeError(f"Embeddings API error {r.status_code}: {r.text[:300]}")
        data = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]
