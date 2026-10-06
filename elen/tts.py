"""Optional text to speech through any command (spd-say, piper, espeak-ng)."""

from __future__ import annotations

import asyncio
import re
import shutil
from typing import Any


def clean_for_speech(text: str, limit: int) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_`#>]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class Speaker:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def enabled(self) -> bool:
        cmd = self.cfg.get("command") or []
        return bool(self.cfg.get("enabled")) and bool(cmd) and shutil.which(cmd[0]) is not None

    async def say(self, text: str) -> None:
        if not self.enabled:
            return
        spoken = clean_for_speech(text, int(self.cfg.get("max_chars", 400)))
        if not spoken:
            return
        await self.stop()
        cmd = [part.replace("{text}", spoken) for part in self.cfg["command"]]
        stdin = None
        if "{text}" not in " ".join(self.cfg["command"]):
            stdin = asyncio.subprocess.PIPE
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=stdin, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        if stdin:
            self._proc.stdin.write(spoken.encode())
            await self._proc.stdin.drain()
            self._proc.stdin.close()
        await self._proc.wait()

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
