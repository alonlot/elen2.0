"""Visual specs: the JSON the GNOME extension draws as a HUD on screen.

See docs/VISUALS.md for every type. This module checks and trims specs so a
bad spec from a model or plugin cannot break the HUD.
"""

from __future__ import annotations

from typing import Any

TYPES = ("calendar", "list", "stats", "table", "text", "email", "image", "panels")
MAX_ITEMS = 40


def _s(value: Any, limit: int = 400) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _items(items: Any, keys: tuple[str, ...], limits: dict[str, int] | None = None) -> list[dict]:
    out = []
    for item in (items or [])[:MAX_ITEMS]:
        if not isinstance(item, dict):
            item = {keys[0]: item}
        clean = {}
        for k in keys:
            if k in item and item[k] not in (None, ""):
                v = item[k]
                clean[k] = v if isinstance(v, (bool, int, float)) else _s(v, (limits or {}).get(k, 300))
        out.append(clean)
    return out


def normalise(spec: dict[str, Any], default_seconds: int = 25, depth: int = 0) -> dict[str, Any]:
    if not isinstance(spec, dict):
        spec = {"type": "text", "body": str(spec)}
    kind = spec.get("type")
    if kind not in TYPES:
        kind = "text"
    out: dict[str, Any] = {
        "type": kind,
        "title": _s(spec.get("title"), 120),
        "subtitle": _s(spec.get("subtitle"), 200),
    }
    if depth == 0:
        try:
            out["duration"] = max(0, int(spec.get("duration", default_seconds)))
        except (TypeError, ValueError):
            out["duration"] = default_seconds
    if kind == "calendar":
        out["date"] = _s(spec.get("date"), 40)
        out["events"] = _items(
            spec.get("events"),
            ("start", "end", "title", "location", "all_day", "start_iso", "end_iso", "calendar"),
        )
    elif kind == "list":
        out["items"] = _items(spec.get("items"), ("title", "subtitle", "meta", "badge", "unread"))
    elif kind == "stats":
        out["items"] = _items(spec.get("items"), ("label", "value", "unit", "percent"))
    elif kind == "table":
        cols = [_s(c, 60) for c in (spec.get("columns") or [])][:8]
        rows = [[_s(c, 120) for c in (r or [])][: len(cols) or 8] for r in (spec.get("rows") or [])[:MAX_ITEMS]]
        out["columns"], out["rows"] = cols, rows
    elif kind == "text":
        out["body"] = _s(spec.get("body") or spec.get("text"), 4000)
    elif kind == "email":
        for k in ("from", "to", "cc", "date", "subject"):
            out[k] = _s(spec.get(k), 300)
        out["body"] = _s(spec.get("body"), 6000)
    elif kind == "image":
        out["path"] = _s(spec.get("path"), 500)
        out["caption"] = _s(spec.get("caption"), 300)
    elif kind == "panels":
        out["panels"] = (
            [normalise(p, default_seconds, depth + 1) for p in (spec.get("panels") or [])[:4]]
            if depth == 0
            else []
        )
    return out


VISUAL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "visual": {
            "type": "object",
            "description": (
                "Visual spec. Field 'type' is one of: "
                "calendar {date, events:[{start:'HH:MM', end, title, location, all_day}]}, "
                "list {items:[{title, subtitle, meta, badge}]}, "
                "stats {items:[{label, value, unit, percent(0-100 optional)}]}, "
                "table {columns:[...], rows:[[...]]}, "
                "text {body}, "
                "email {from, to, subject, date, body}, "
                "image {path, caption}, "
                "panels {panels:[up to 4 specs]}. "
                "All types take title, subtitle, duration (seconds on screen, 0 = until closed). "
                "Only put data you got from tools or the user. Never invent data."
            ),
        }
    },
    "required": ["visual"],
}
