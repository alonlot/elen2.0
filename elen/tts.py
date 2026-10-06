"""Optional text to speech through any command (spd-say, piper, espeak-ng).

Speech starts at the first complete sentence: text is fed in while the model
writes it, and each sentence goes to a queue that one worker speaks in order.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from typing import Any

SENTENCE_END = re.compile(r"[.!?…:;]\s|\n")


def clean_for_speech(text: str, limit: int = 100000) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_`#>|⚠ℹ]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class Speaker:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[str] | None = None
        self._worker: asyncio.Task | None = None
        self._buffer = ""
        self._budget = int(cfg.get("max_chars", 400))
        self._in_code = False

    @property
    def enabled(self) -> bool:
        cmd = self.cfg.get("command") or []
        return bool(self.cfg.get("enabled")) and bool(cmd) and shutil.which(cmd[0]) is not None

    def begin(self) -> None:
        """Start a new reply: reset the length budget."""
        self._buffer = ""
        self._in_code = False
        self._budget = int(self.cfg.get("max_chars", 400))

    def feed(self, text: str) -> None:
        """Add streamed text. Each complete sentence is queued for speech."""
        if not self.enabled:
            return
        self._buffer += text
        while True:
            m = SENTENCE_END.search(self._buffer)
            if not m:
                break
            sentence, self._buffer = self._buffer[: m.end()], self._buffer[m.end():]
            self._queue_sentence(sentence)

    def flush(self) -> None:
        if self._buffer.strip():
            self._queue_sentence(self._buffer)
        self._buffer = ""

    async def say(self, text: str) -> None:
        """Speak a whole text and wait until it is spoken."""
        if not self.enabled:
            return
        self.begin()
        self.feed(text)
        self.flush()
        if self._queue is not None:
            await self._queue.join()

    def _queue_sentence(self, sentence: str) -> None:
        if sentence.count("```") % 2:
            self._in_code = not self._in_code
            return
        if self._in_code or self._budget <= 0:
            return
        spoken = clean_for_speech(sentence)
        if not spoken:
            return
        if len(spoken) > self._budget:
            if self._budget < int(self.cfg.get("max_chars", 400)):
                self._budget = 0  # stop after the last whole sentence
                return
            spoken = spoken[: self._budget].rsplit(" ", 1)[0]  # first sentence: cut at a word
        self._budget -= len(spoken)
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run())
        self._queue.put_nowait(spoken)

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            sentence = await self._queue.get()
            try:
                await self._speak_one(sentence)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._queue.task_done()

    async def _speak_one(self, text: str) -> None:
        template = self.cfg["command"]
        uses_arg = any("{text}" in part for part in template)
        cmd = [part.replace("{text}", text) for part in template]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=None if uses_arg else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if not uses_arg:
            self._proc.stdin.write(text.encode())
            await self._proc.stdin.drain()
            self._proc.stdin.close()
        await self._proc.wait()

    async def stop(self) -> None:
        """Stop speaking now and forget queued sentences."""
        self._buffer = ""
        if self._queue is not None:
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
