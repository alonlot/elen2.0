"""Change single values in config.toml and keep the comments.

Used by the settings window (through D-Bus) and by `elen set`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import config_path, ensure_user_config, load_config

MODEL_SECTIONS = {
    "brain": ("provider", "model", "base_url", "api_key", "auth_token", "effort"),
    "vision": ("provider", "model", "base_url", "api_key", "auth_token", "effort"),
    "checker": ("provider", "model", "base_url", "api_key", "auth_token", "effort"),
    "stt": ("provider", "model", "base_url", "api_key", "language"),
}

PROVIDERS = {
    "brain": ["claude_cli", "anthropic", "openai", "ollama"],
    "vision": ["claude_cli", "anthropic", "openai", "ollama"],
    "checker": ["claude_cli", "anthropic", "openai", "ollama"],
    "stt": ["openai", "faster_whisper", "command", "none"],
}


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
    out: dict[str, Any] = {"providers": PROVIDERS}
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
        if section == "checker" and not values.get("provider"):
            continue  # empty checker = use the brain settings
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
