# HUD visual specs

A visual is a JSON object. Plugins return it in `ToolResult(visual=...)`; the brain shows one
with the `ui__show_visual` tool. The GNOME extension draws it in the middle of the screen.

Fields for every type:

| field      | meaning |
|------------|---------|
| `type`     | one of the types below |
| `title`    | big title (shown in capitals) |
| `subtitle` | small line under the title (default: date and time) |
| `duration` | seconds on screen; `0` = until you close it (default `[ui] visual_seconds`) |

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
Up to 4 of the types above, in a 2 x 2 grid:
```json
{"type": "panels", "title": "Morning brief", "panels": [{"type": "calendar", "...": "..."}, {"type": "list", "...": "..."}]}
```
