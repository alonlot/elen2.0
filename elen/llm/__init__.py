from __future__ import annotations

from typing import Any

from .base import LLMError, LLMProvider, LLMResponse, ToolCall


def make_provider(cfg: dict[str, Any]) -> LLMProvider:
    """Create a model provider from a [brain] or [vision] config section."""
    kind = (cfg.get("provider") or "claude_cli").lower()
    if kind in ("claude_cli", "claude_code", "claude-p"):
        from .claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(cfg)
    if kind in ("anthropic", "claude"):
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if kind in ("openai", "openai_compatible", "ollama", "lmstudio", "groq", "openrouter"):
        from .openai_provider import OpenAICompatProvider

        if kind == "ollama" and not cfg.get("base_url"):
            cfg = {**cfg, "base_url": "http://localhost:11434/v1"}
        return OpenAICompatProvider(cfg)
    raise LLMError(f"Unknown model provider '{kind}'. Use 'claude_cli', 'anthropic', 'openai' or 'ollama'.")


__all__ = ["LLMError", "LLMProvider", "LLMResponse", "ToolCall", "make_provider"]
