"""Calendar: CalDAV (read and write) and ICS feeds (read only).

[[plugins.calendar.sources]]
name = "work"
type = "caldav"                 # Nextcloud, iCloud, Fastmail, Radicale, SOGo, Zimbra...
url = "https://cloud.example.com/remote.php/dav"
username = "me"
password = "cmd:secret-tool lookup elen caldav"
calendars = []                  # empty = all calendars
default = true                  # new events go here

[[plugins.calendar.sources]]
name = "google"
type = "ics"                    # Google: Settings > calendar > "Secret address in iCal format"
url = "https://calendar.google.com/calendar/ical/.../basic.ics"

Needs: pip install caldav icalendar recurring-ical-events
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from elen.plugins import Notice, Plugin, ToolResult, tool
from elen.secrets import resolve_secret


def local_tz():
    return datetime.now().astimezone().tzinfo


def parse_day(value: str) -> date:
    v = (value or "today").strip().lower()
    today = datetime.now().astimezone().date()
    if v in ("today", ""):
        return today
    if v == "tomorrow":
        return today + timedelta(days=1)
    if v == "yesterday":
        return today - timedelta(days=1)
    return date.fromisoformat(v[:10])


def to_local(value: Any) -> tuple[datetime, bool]:
    """Return (local datetime, all_day)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=local_tz())
        return value.astimezone(local_tz()), False
    return datetime(value.year, value.month, value.day, tzinfo=local_tz()), True


def event_dict(component, source: str) -> dict[str, Any] | None:
    start_prop = component.get("dtstart")
    if start_prop is None:
        return None
    start, all_day = to_local(start_prop.dt)
    end_prop = component.get("dtend")
    if end_prop is not None:
        end, _ = to_local(end_prop.dt)
    elif component.get("duration") is not None:
        end = start + component.get("duration").dt
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
    return {
        "title": str(component.get("summary", "(no title)")),
        "start_iso": start.isoformat(timespec="minutes"),
        "end_iso": end.isoformat(timespec="minutes"),
        "start": "" if all_day else start.strftime("%H:%M"),
        "end": "" if all_day else end.strftime("%H:%M"),
        "all_day": all_day,
        "location": str(component.get("location", "") or ""),
        "description": str(component.get("description", "") or "")[:500],
        "calendar": source,
    }


class CalendarPlugin(Plugin):
    name = "calendar"
    description = "Read and add calendar events."

    async def setup(self) -> None:
        self.sources = list(self.config.get("sources", []))
        if not self.sources:
            raise RuntimeError("no [[plugins.calendar.sources]] configured")
        import icalendar  # noqa: F401  (fail early with a clear message)

    async def watch(self, now: datetime) -> list[Notice]:
        """Reminder before each meeting (built from the calendar data, no LLM)."""
        minutes = int(self.ctx.setting("proactive", "meeting_reminder_minutes", default=10) or 0)
        if minutes <= 0:
            return []
        cache = getattr(self, "_watch_cache", None)
        if cache is None or (now - cache[0]).total_seconds() > 300:
            events, _ = await asyncio.to_thread(self._read_all, now - timedelta(minutes=1), now + timedelta(hours=2))
            cache = (now, events)
            self._watch_cache = cache
        notices = []
        for ev in cache[1]:
            if ev["all_day"]:
                continue
            start = datetime.fromisoformat(ev["start_iso"])
            left = (start - now).total_seconds() / 60
            if 0 <= left <= minutes:
                when = f"in {max(1, round(left))} minutes" if left >= 1 else "now"
                text = f"Reminder: {ev['title']} starts {when}, at {ev['start']}."
                if ev["location"]:
                    text += f" Location: {ev['location']}."
                fields = [{"label": "Starts", "value": f"{ev['start']} ({when})"}, {"label": "Ends", "value": ev["end"]}]
                if ev["location"]:
                    fields.append({"label": "Where", "value": ev["location"]})
                fields.append({"label": "Calendar", "value": ev["calendar"]})
                notices.append(
                    Notice(
                        key=f"cal:{ev['calendar']}:{ev['title']}:{ev['start_iso']}",
                        text=text,
                        visual={"type": "card", "title": ev["title"], "subtitle": "Upcoming meeting", "fields": fields},
                    )
                )
        return notices

    def prompt_hint(self) -> str:
        return "calendars: " + ", ".join(f"{s.get('name')} ({s.get('type')})" for s in self.sources)

    # -- sync helpers --
    def _caldav_calendars(self, src: dict[str, Any]):
        import caldav

        client = caldav.DAVClient(
            url=src["url"], username=src.get("username"), password=resolve_secret(src.get("password"))
        )
        cals = client.principal().calendars()
        wanted = src.get("calendars") or []
        if wanted:
            cals = [c for c in cals if (c.name or "") in wanted]
        return cals

    def _read_caldav(self, src: dict[str, Any], start: datetime, end: datetime) -> list[dict]:
        out = []
        for cal in self._caldav_calendars(src):
            for ev in cal.search(start=start, end=end, event=True, expand=True):
                comp = ev.icalendar_component
                d = event_dict(comp, f"{src.get('name')}/{cal.name}")
                if d:
                    out.append(d)
        return out

    def _read_ics(self, src: dict[str, Any], start: datetime, end: datetime) -> list[dict]:
        import icalendar

        loc = src.get("url") or src.get("path", "")
        if loc.startswith(("http://", "https://", "webcal://")):
            r = httpx.get(loc.replace("webcal://", "https://"), timeout=30, follow_redirects=True)
            r.raise_for_status()
            raw = r.content
        else:
            raw = Path(loc).expanduser().read_bytes()
        cal = icalendar.Calendar.from_ical(raw)
        try:
            import recurring_ical_events

            comps = recurring_ical_events.of(cal).between(start, end)
        except ImportError:
            comps = [c for c in cal.walk("VEVENT")]
        out = []
        for comp in comps:
            d = event_dict(comp, src.get("name", "ics"))
            if d and d["start_iso"] < end.isoformat() and d["end_iso"] > start.isoformat():
                out.append(d)
        return out

    def _read_all(self, start: datetime, end: datetime) -> tuple[list[dict], list[str]]:
        events, errors = [], []
        for src in self.sources:
            try:
                if src.get("type") == "caldav":
                    events += self._read_caldav(src, start, end)
                else:
                    events += self._read_ics(src, start, end)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src.get('name')}: {e}")
        events.sort(key=lambda e: (not e["all_day"], e["start_iso"]))
        return events, errors

    # -- tools --
    @tool(
        "Get calendar events for a day or a range of days, and show them on screen.",
        params={
            "start_date": "string: 'today', 'tomorrow' or YYYY-MM-DD",
            "days": "integer: number of days, default 1",
        },
        untrusted=True,
    )
    async def get_events(self, start_date: str = "today", days: int = 1):
        day = parse_day(start_date)
        days = max(1, min(int(days or 1), 31))
        start = datetime(day.year, day.month, day.day, tzinfo=local_tz())
        end = start + timedelta(days=days)
        events, errors = await asyncio.to_thread(self._read_all, start, end)
        if days == 1:
            title = "Today" if day == datetime.now().date() else day.strftime("%A")
            visual = {
                "type": "calendar",
                "title": title,
                "subtitle": day.strftime("%A %d %B %Y"),
                "date": day.isoformat(),
                "events": events,
            }
        else:
            visual = {
                "type": "list",
                "title": f"Calendar · {days} days",
                "subtitle": f"{day:%d %b} – {(day + timedelta(days=days - 1)):%d %b}",
                "items": [
                    {
                        "title": e["title"],
                        "subtitle": e["location"],
                        "meta": e["start_iso"][:10] + (" all day" if e["all_day"] else f" {e['start']}–{e['end']}"),
                    }
                    for e in events
                ],
            }
        data: dict[str, Any] = {"from": start.isoformat(), "to": end.isoformat(), "events": events}
        if errors:
            data["errors"] = errors
        return ToolResult(data=data, visual=visual)

    @tool(
        "Add an event to the calendar. The user approves the event first.",
        params={
            "title": "string",
            "start": "string: local start time, YYYY-MM-DDTHH:MM",
            "end": "string: local end time, YYYY-MM-DDTHH:MM",
            "location": "string",
            "description": "string",
            "calendar": "string: calendar source name (empty = default)",
        },
        required=["title", "start", "end"],
        risk="write",
        editable=["title", "start", "end", "location", "description"],
        title="Add calendar event",
    )
    async def create_event(self, title: str, start: str, end: str, location: str = "", description: str = "", calendar: str = ""):
        srcs = [s for s in self.sources if s.get("type") == "caldav"]
        if calendar:
            srcs = [s for s in srcs if s.get("name") == calendar]
        if not srcs:
            return {"created": False, "error": "No writable CalDAV calendar is configured."}
        src = next((s for s in srcs if s.get("default")), srcs[0])
        dt_start = datetime.fromisoformat(start).replace(tzinfo=None).astimezone(local_tz()) if "T" in start else None
        dt_end = datetime.fromisoformat(end).replace(tzinfo=None).astimezone(local_tz()) if "T" in end else None
        if not dt_start or not dt_end or dt_end <= dt_start:
            return {"created": False, "error": "start and end must be YYYY-MM-DDTHH:MM and end after start."}

        def save():
            cal = self._caldav_calendars(src)[0]
            cal.save_event(dtstart=dt_start, dtend=dt_end, summary=title, location=location, description=description)
            return cal.name

        cal_name = await asyncio.to_thread(save)
        return {"created": True, "calendar": f"{src.get('name')}/{cal_name}", "start": dt_start.isoformat(), "end": dt_end.isoformat()}
