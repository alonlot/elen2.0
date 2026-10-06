"""Resolve secret values from config.

A secret value in config.toml can be one of:
  "env:VAR_NAME"                     read an environment variable
  "cmd:secret-tool lookup app elen"  run a command, use its stdout
  "file:~/.config/elen/key.txt"      read a file
  "plain text"                       used as is (not recommended)
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


def resolve_secret(value: str | None) -> str:
    if not value:
        return ""
    value = str(value)
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if value.startswith("cmd:"):
        try:
            out = subprocess.run(
                shlex.split(value[4:]), capture_output=True, text=True, timeout=15
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    if value.startswith("file:"):
        try:
            return Path(value[5:]).expanduser().read_text().strip()
        except OSError:
            return ""
    return value
