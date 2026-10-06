from datetime import datetime, timedelta

from conftest import ScriptedProvider, say
from elen.plugins import Notice, Plugin, PluginContext
from elen.proactive import Proactive, in_window, parse_hhmm


def test_windows():
    assert in_window(parse_hhmm("23:00", None), parse_hhmm("22:00", None), parse_hhmm("07:00", None))
    assert in_window(parse_hhmm("06:59", None), parse_hhmm("22:00", None), parse_hhmm("07:00", None))
    assert not in_window(parse_hhmm("12:00", None), parse_hhmm("22:00", None), parse_hhmm("07:00", None))


async def test_meeting_reminder_once_with_visual(make_core, env, tmp_path):
    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    start = now + timedelta(minutes=5)
    ics = tmp_path / "c.ics"
    utc = start.astimezone(datetime.now().astimezone().tzinfo)
    ics.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nBEGIN:VEVENT\r\nUID:1\r\nSUMMARY:Design review\r\n"
        f"DTSTART:{utc.astimezone().strftime('%Y%m%dT%H%M%S')}\r\n"
        f"DTEND:{(utc + timedelta(minutes=45)).strftime('%Y%m%dT%H%M%S')}\r\n"
        "LOCATION:Room 4\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    core = await make_core([], plugins={"calendar": {"enabled": True, "sources": [{"name": "t", "type": "ics", "path": str(ics)}]}})
    shown = []
    core.subscribe(lambda k, p: k == "visual" and shown.append(p))
    engine = Proactive(core)
    await engine.tick(now)
    await engine.tick(now + timedelta(minutes=1))
    notes = [m for m in core.history.display if m.get("meta", {}).get("source") == "proactive"]
    assert len(notes) == 1
    assert notes[0]["text"].startswith("Reminder: Design review starts in 5 minutes") and "Room 4" in notes[0]["text"]
    assert shown and shown[0]["type"] == "card" and notes[0]["meta"]["visuals"][0]["id"] == shown[0]["id"]


class Watcher(Plugin):
    name = "watcher"

    def __init__(self, cfg, ctx, notices):
        super().__init__(cfg, ctx)
        self.notices = notices

    async def watch(self, now):
        return self.notices


async def test_baseline_and_quiet_hours(make_core, tmp_path):
    core = await make_core([], proactive={"quiet_hours": ["00:00", "23:59"]},
                           tts={"enabled": True, "command": ["sh", "-c", f"echo $0 >> {tmp_path}/s", "{text}"]})
    w = Watcher({}, PluginContext(core, "watcher", core.data_dir), [Notice("old", "old mail", baseline=True)])
    core.plugins["watcher"] = w
    engine = Proactive(core)
    await engine.tick(datetime.now().astimezone())
    assert not any(m["text"] == "old mail" for m in core.history.display)  # baseline: remembered, not shown
    w.notices = [Notice("old", "old mail", baseline=True), Notice("new", "new mail", baseline=True)]
    await engine.tick(datetime.now().astimezone())
    assert [m["text"] for m in core.history.display] == ["new mail"]
    assert not (tmp_path / "s").exists()  # quiet hours: shown, not spoken


async def test_morning_briefing_once_a_day_after_login(make_core):
    core = await make_core([say("Here is your morning.")])
    engine = Proactive(core)
    morning = datetime.now().astimezone().replace(hour=8, minute=0)
    assert await engine.maybe_briefing(morning) is False  # nobody logged in yet
    core.ui_present = True
    assert await engine.maybe_briefing(morning.replace(hour=5)) is False  # too early
    assert await engine.maybe_briefing(morning) is True
    assert await engine.maybe_briefing(morning.replace(hour=9)) is False  # once a day
    import asyncio
    for _ in range(50):
        if len(core.history.display) >= 2:
            break
        await asyncio.sleep(0.02)
    assert core.history.display[0]["text"] == "☀ Morning briefing"
    assert "morning briefing" in core.brain.requests[0]["messages"][-1]["content"]


async def test_vip_mail_watch(make_core, env):
    (env / "cfg" / "contacts.toml").write_text('[[contact]]\nname = "Dana"\nemails = ["dana@example.com"]\nvip = true\n')
    core = await make_core([])
    from elen.plugins.builtin.email import EmailPlugin

    mail = EmailPlugin({"accounts": [{"name": "w", "address": "me@x.io", "imap_host": "h"}]}, PluginContext(core, "email", core.data_dir))
    await mail.setup()
    mail._fetch_headers = lambda acc, folder, crit, limit: [
        {"uid": "7", "from": "Dana <dana@example.com>", "subject": "Budget", "unread": True},
        {"uid": "8", "from": "spam@ads.com", "subject": "Win", "unread": True},
    ]
    notices = await mail.watch(datetime.now())
    assert [n.text for n in notices] == ["New mail from Dana: Budget"] and notices[0].baseline
