from __future__ import annotations

from typing import Any

from .base import LLMError, LLMProvider, LLMResponse, ToolCall


GENERIC = ("openai_compatible", "openai", "ollama", "lmstudio", "vllm", "litellm", "groq", "openrouter")


def make_provider(cfg: dict[str, Any]) -> LLMProvider:
    """Create a model provider from a [brain], [vision] or [checker] config section.

    The default is "openai_compatible": any LLM server with the OpenAI chat API.
    "anthropic" (Claude API) and "claude_cli" (`claude -p` as the agent) are options.
    """
    kind = (cfg.get("provider") or "openai_compatible").lower()
    if kind in GENERIC:
        from .openai_provider import OpenAICompatProvider

        if kind == "ollama" and not cfg.get("base_url"):
            cfg = {**cfg, "base_url": "http://localhost:11434/v1"}
        return OpenAICompatProvider(cfg)
    if kind in ("claude_cli", "claude_code", "claude-p"):
        from .claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(cfg)
    if kind in ("anthropic", "claude"):
        from .anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(cfg)
        if (cfg.get("tool_mode") or "").lower() == "prompt":
            from .prompt_tools import PromptToolsAdapter

            return PromptToolsAdapter(provider)
        return provider
    raise LLMError(
        f"Unknown model provider '{kind}'. Use 'openai_compatible' (any LLM server), "
        "'ollama', 'anthropic' or 'claude_cli'."
    )


__all__ = ["LLMError", "LLMProvider", "LLMResponse", "ToolCall", "make_provider"]
