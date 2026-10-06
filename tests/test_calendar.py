from datetime import datetime, timedelta

from elen.plugins import PluginContext
from elen.plugins.builtin.calendar import CalendarPlugin


async def test_ics_today_with_recurring_event(tmp_path, make_core):
    today = datetime.now().astimezone().date()
    start = today - timedelta(days=7)
    ics = tmp_path / "cal.ics"
    ics.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n"
        "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:Standup\r\n"
        f"DTSTART:{start:%Y%m%d}T090000\r\nDTEND:{start:%Y%m%d}T091500\r\n"
        "RRULE:FREQ=DAILY\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:2\r\nSUMMARY:Holiday\r\n"
        f"DTSTART;VALUE=DATE:{today:%Y%m%d}\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    core = await make_core([])
    plugin = CalendarPlugin({"sources": [{"name": "t", "type": "ics", "path": str(ics)}]},
                            PluginContext(core, "calendar", tmp_path))
    await plugin.setup()
    res = await plugin.get_events("today")
    titles = [e["title"] for e in res.data["events"]]
    assert titles == ["Holiday", "Standup"]
    assert res.data["events"][1]["start"] == "09:00"
    assert res.visual["type"] == "calendar"
