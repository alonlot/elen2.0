"""Configuration loading.

The user config lives at ~/.config/elen/config.toml. Values that are not set
there fall back to DEFAULTS. On first run the example config is copied there.
"""

from __future__ import annotations

import copy
import os
import shutil
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(os.environ.get("ELEN_CONFIG_DIR", Path(base) / "elen"))


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(os.environ.get("ELEN_DATA_DIR", Path(base) / "elen"))


EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config" / "config.example.toml"

DEFAULTS: dict[str, Any] = {
    "user": {"name": "", "language": "en", "timezone": ""},
    "brain": {
        # Any LLM, called through its API. "openai_compatible" talks to every server with the
        # OpenAI chat API (Ollama, LM Studio, vLLM, LiteLLM, OpenAI, OpenRouter, Groq, Gemini,
        # Mistral...). "anthropic" = Claude API. Claude Code is a tool, see plugins.claude_code.
        "provider": "openai_compatible",
        "model": "qwen2.5:14b",
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "headers": {},
        "extra_body": {},
        "tool_mode": "auto",
        "timeout": 300,
        "max_tokens": 8000,
        "max_steps": 12,
        # anthropic only:
        "effort": "",
        "fallbacks": False,
    },
    # Empty provider = use the [brain] settings (fields set here override them).
    "vision": {"provider": ""},
    "checker": {"provider": ""},
    "stt": {
        "provider": "openai",
        "model": "whisper-1",
        "api_key": "env:OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "language": "",
        "command": [],
        "record_command": [],
        "silence_seconds": 1.5,
        "silence_threshold": 600,
        "max_seconds": 30,
        "auto_send": True,
    },
    "tts": {
        "enabled": False,
        "command": ["spd-say", "-w", "{text}"],
        "speak_on": "voice",
        "max_chars": 400,
    },
    "ui": {"auto_visualize": True, "visual_seconds": 25},
    "history": {"max_context_messages": 40, "max_tool_result_chars": 12000},
    "guard": {
        "confirm_timeout": 300,
        "action_claim_check": True,
        "verify_recipients": True,
        "rule_check": "actions",
        "rule_check_replies": True,
        "overrides": {},
    },
    "plugins": {
        "paths": [],
        "system": {"enabled": True},
        "screen": {"enabled": True},
        "memory": {"enabled": True, "max_facts_in_prompt": 60},
        "contacts": {"enabled": True, "vcf_paths": []},
        "email": {"enabled": False, "accounts": []},
        "calendar": {"enabled": False, "sources": []},
        "claude_code": {
            "enabled": True,
            "binary": "claude",
            "bypass_permissions": True,
            "workdir": "~",
            "timeout": 900,
            "extra_args": [],
        },
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def config_path() -> Path:
    return Path(os.environ.get("ELEN_CONFIG", config_dir() / "config.toml"))


def ensure_user_config() -> Path:
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        if EXAMPLE_CONFIG.exists():
            shutil.copy(EXAMPLE_CONFIG, path)
        else:
            path.write_text("# Elen 2.0 config. See config.example.toml in the repo.\n")
        path.chmod(0o600)
    return path


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or ensure_user_config()
    user: dict[str, Any] = {}
    if path.exists():
        with open(path, "rb") as f:
            user = tomllib.load(f)
    return deep_merge(DEFAULTS, user)
