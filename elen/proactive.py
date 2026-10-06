"""Proactive Elen: reminders, alerts and a morning briefing, without being asked.

Every minute the engine asks each plugin's watch() for notices and delivers the
new ones: a chat message (a note at the bottom of the screen when the chat is
closed), an optional visual, and speech outside quiet hours.

Reminders and alerts are built by the plugins from real data, without the LLM.
The morning briefing is a normal request to the brain, once a day after login.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .core import Elen

log = logging.getLogger("elen.proactive")

BRIEFING_PROMPT = (
    "Give me my morning briefing: today's calendar, important unread mail, and anything I asked "
    "you to remember for today. Use only data from your tools. Keep it short, and show it on my "
    "screen as one panels visual."
)


def parse_hhmm(value: str, default: dtime) -> dtime:
    try:
        h, m = str(value).split(":")
        return dtime(int(h), int(m))
    except (ValueError, TypeError):
        return default


def in_window(now: dtime, start: dtime, end: dtime) -> bool:
    """True if now is in [start, end). The window may pass midnight (22:00-07:00)."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


class Proactive:
    def __init__(self, core: "Elen"):
        self.core = core
        self.cfg: dict[str, Any] = core.config.get("proactive", {})
        self.path: Path = core.data_dir / "proactive.json"
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = {}
        self.seen: dict[str, float] = state.get("seen", {})
        self.last_briefing: str = state.get("last_briefing", "")
        self._checked_once: set[str] = set()
        self._task: asyncio.Task | None = None

    def _save(self) -> None:
        week_ago = time.time() - 7 * 86400
        self.seen = {k: v for k, v in self.seen.items() if v > week_ago}
        self.path.write_text(json.dumps({"seen": self.seen, "last_briefing": self.last_briefing}))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                await self.tick(datetime.now().astimezone())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("proactive check failed")
            await asyncio.sleep(float(self.cfg.get("interval", 60)))

    def quiet(self, now: datetime) -> bool:
        hours = self.cfg.get("quiet_hours") or []
        if len(hours) != 2:
            return False
        return in_window(now.time(), parse_hhmm(hours[0], dtime(22)), parse_hhmm(hours[1], dtime(7)))

    async def tick(self, now: datetime) -> None:
        for name, plugin in list(self.core.plugins.items()):
            try:
                notices = await asyncio.wait_for(plugin.watch(now), 90)
            except asyncio.TimeoutError:
                log.warning("watch() of %s took too long", name)
                continue
            except Exception:  # noqa: BLE001
                log.exception("watch() of %s failed", name)
                continue
            first = name not in self._checked_once
            self._checked_once.add(name)
            for notice in notices or []:
                if notice.key in self.seen:
                    continue
                self.seen[notice.key] = time.time()
                if notice.baseline and first:
                    continue
                await self.core.deliver_notice(notice, quiet=self.quiet(now))
        self._save()
        await self.maybe_briefing(now)

    async def maybe_briefing(self, now: datetime) -> bool:
        if not self.cfg.get("morning_briefing", True) or not self.core.ui_present:
            return False
        today = now.date().isoformat()
        if self.last_briefing == today:
            return False
        start = parse_hhmm(self.cfg.get("briefing_after", "06:00"), dtime(6))
        end = parse_hhmm(self.cfg.get("briefing_until", "12:00"), dtime(12))
        if not in_window(now.time(), start, end):
            return False
        self.last_briefing = today
        self._save()
        prompt = self.cfg.get("briefing_prompt") or BRIEFING_PROMPT
        self.core.submit(prompt, source="briefing", display_text="☀ Morning briefing")
        return True
