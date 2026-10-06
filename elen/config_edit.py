"""Change single values in config.toml and keep the comments.

Used by the settings window (through D-Bus) and by `elen set`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import config_path, ensure_user_config, load_config

LLM_KEYS = ("provider", "model", "base_url", "api_key", "tool_mode", "effort", "auth_token")
MODEL_SECTIONS = {
    "brain": LLM_KEYS,
    "vision": LLM_KEYS,
    "checker": LLM_KEYS,
    "stt": ("provider", "model", "base_url", "api_key", "language"),
}

LLM_PROVIDERS = ["openai_compatible", "ollama", "anthropic", "claude_cli"]
PROVIDERS = {
    "brain": LLM_PROVIDERS,
    "vision": [""] + LLM_PROVIDERS,  # "" = same as brain
    "checker": [""] + LLM_PROVIDERS,
    "stt": ["openai", "faster_whisper", "command", "none"],
}

# Ready-made base URLs for the settings window (provider, base_url, example model).
PRESETS = [
    {"name": "Ollama (local)", "provider": "openai_compatible", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:14b"},
    {"name": "LM Studio (local)", "provider": "openai_compatible", "base_url": "http://localhost:1234/v1", "model": ""},
    {"name": "vLLM / llama.cpp (local)", "provider": "openai_compatible", "base_url": "http://localhost:8000/v1", "model": ""},
    {"name": "LiteLLM proxy", "provider": "openai_compatible", "base_url": "http://localhost:4000/v1", "model": ""},
    {"name": "OpenAI", "provider": "openai_compatible", "base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
    {"name": "OpenRouter", "provider": "openai_compatible", "base_url": "https://openrouter.ai/api/v1", "model": ""},
    {"name": "Groq", "provider": "openai_compatible", "base_url": "https://api.groq.com/openai/v1", "model": ""},
    {"name": "Google Gemini", "provider": "openai_compatible", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": ""},
    {"name": "Mistral", "provider": "openai_compatible", "base_url": "https://api.mistral.ai/v1", "model": ""},
    {"name": "DeepSeek", "provider": "openai_compatible", "base_url": "https://api.deepseek.com/v1", "model": ""},
    {"name": "Anthropic API", "provider": "anthropic", "base_url": "", "model": "claude-opus-5-5"},
    {"name": "claude -p (Claude Code)", "provider": "claude_cli", "base_url": "", "model": "opus"},
]


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def set_values(section: str, values: dict[str, Any], path: Path | None = None) -> Path:
    path = path or ensure_user_config()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    header = f"[{section}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        lines += ["", header] + [f"{k} = {toml_value(v)}" for k, v in values.items()]
    else:
        end = next(
            (i for i in range(start + 1, len(lines)) if re.match(r"\s*\[", lines[i])),
            len(lines),
        )
        missing = dict(values)
        for i in range(start + 1, end):
            m = re.match(r"\s*([A-Za-z0-9_]+)\s*=", lines[i])
            if m and m.group(1) in missing:
                comment = ""
                cm = re.search(r"\s+#[^\"']*$", lines[i])
                if cm:
                    comment = cm.group(0)
                lines[i] = f"{m.group(1)} = {toml_value(missing.pop(m.group(1)))}{comment}"
        if missing:
            insert_at = end
            while insert_at > start + 1 and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines[insert_at:insert_at] = [f"{k} = {toml_value(v)}" for k, v in missing.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    load_config(path)  # raises if the result is not valid TOML
    return path


def get_model_settings() -> dict[str, Any]:
    cfg = load_config()
    out: dict[str, Any] = {"providers": PROVIDERS, "presets": PRESETS}
    for section, keys in MODEL_SECTIONS.items():
        src = cfg.get(section) or {}
        out[section] = {k: src.get(k, "") for k in keys}
    return out


def apply_model_settings(data: dict[str, Any]) -> Path:
    path = config_path()
    for section, keys in MODEL_SECTIONS.items():
        values = {k: v for k, v in (data.get(section) or {}).items() if k in keys and v is not None}
        if not values:
            continue
        if "provider" in values and values["provider"] not in PROVIDERS[section]:
            raise ValueError(f"Unknown {section} provider '{values['provider']}'")
        set_values(section, values, path)
    return path


def set_dotted(key: str, raw: str) -> Path:
    """`elen set brain.model opus` -> [brain] model = "opus"."""
    if "." not in key:
        raise ValueError("Use section.key, for example brain.model")
    section, name = key.rsplit(".", 1)
    value: Any = raw
    if raw.lower() in ("true", "false"):
        value = raw.lower() == "true"
    elif re.fullmatch(r"-?\d+(\.\d+)?", raw):
        value = float(raw) if "." in raw else int(raw)
    elif raw.startswith("["):
        value = json.loads(raw)
    return set_values(section, {name: value})
