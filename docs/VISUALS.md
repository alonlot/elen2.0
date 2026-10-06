# HUD visual specs

A visual is a JSON object. Plugins return it in `ToolResult(visual=...)`; the brain shows one
with the `ui__show_visual` tool. The GNOME extension draws it in the middle of the screen.

**Nothing has to be defined first.** The `data` type takes any JSON and lays it out by itself.
A spec with an unknown or missing `type` is shown the same way, so no data is lost. The other
types are optional, for a better look.

Fields for every type:

| field      | meaning |
|------------|---------|
| `type`     | one of the types below |
| `title`    | big title (shown in capitals) |
| `subtitle` | small line under the title (default: date and time) |
| `duration` | seconds on screen; `0` = until you close it (default `[ui] visual_seconds`) |

## data (any JSON, automatic layout)
```json
{"type": "data", "title": "Dana Levi",
 "data": {"role": "Project manager", "company": "Acme",
          "contact": {"email": "dana@example.com", "phone": "+972 50 000 0000"},
          "children": ["Noa", "Ari"],
          "recent_meetings": [{"date": "2026-09-30", "topic": "Budget"}, {"date": "2026-10-02", "topic": "Release"}]}}
```
How it is drawn:

| JSON value | On screen |
|------------|-----------|
| object | label / value rows (keys become labels: `recent_meetings` → RECENT MEETINGS) |
| nested object | a sub-section with its own rows |
| list of words or numbers | tags |
| list of objects | a table, columns from the keys |
| true / false | Yes / No |

Depth is limited to 4 levels and lists to 40 items.

## card (one person, place or thing)
```json
{"type": "card", "title": "Dana Levi", "subtitle": "Contact", "image": "/home/me/photos/dana.jpg",
 "fields": [{"label": "Email", "value": "dana@example.com"}, {"label": "Phone", "value": "+972 50 000 0000"}],
 "tags": ["Acme", "VIP"], "body": "Prefers morning meetings."}
```
`contacts__find_contact` shows this card by itself when one contact matches.

## chart
```json
{"type": "chart", "kind": "bar", "title": "Mail per day", "unit": "mails",
 "labels": ["Mon", "Tue", "Wed"], "series": [{"name": "Inbox", "values": [42, 31, 18]}]}
```
`kind` is `bar` or `line`. Up to 4 series.

## timeline
```json
{"type": "timeline", "title": "Order 1182", "items": [{"time": "Oct 2 10:14", "title": "Shipped", "detail": "DHL 39841", "badge": "NOW"}]}
```

## calendar
```json
{"type": "calendar", "title": "Today", "date": "2026-10-06",
 "events": [{"start": "09:00", "end": "09:15", "title": "Standup", "location": "Room 4",
             "start_iso": "2026-10-06T09:00+03:00", "end_iso": "2026-10-06T09:15+03:00"},
            {"title": "Holiday", "all_day": true}]}
```
A timeline. With `start_iso`/`end_iso` the HUD marks past events, the event that is **NOW**
and the **NEXT** event.

## list
```json
{"type": "list", "title": "Inbox", "items": [{"title": "Subject", "subtitle": "From", "meta": "10:42", "badge": "VIP", "unread": true}]}
```

## stats
```json
{"type": "stats", "title": "System", "items": [{"label": "CPU", "value": 12, "unit": "%", "percent": 12}]}
```
`percent` (0 to 100) draws a ring gauge.

## table
```json
{"type": "table", "title": "Flights", "columns": ["Flight", "Time"], "rows": [["LY001", "10:00"]]}
```

## text
```json
{"type": "text", "title": "Summary", "body": "Plain text."}
```

## email
```json
{"type": "email", "from": "...", "to": "...", "cc": "...", "date": "...", "subject": "...", "body": "..."}
```

## image
```json
{"type": "image", "path": "/home/me/picture.png", "caption": "..."}
```

## panels
Up to 4 of the types above (any mix, `data` too), in a 2 x 2 grid:
```json
{"type": "panels", "title": "Morning brief", "panels": [{"type": "calendar", "...": "..."}, {"type": "list", "...": "..."}]}
```
