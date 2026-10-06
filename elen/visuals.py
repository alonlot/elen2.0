"""Visual specs: the JSON the GNOME extension draws as a HUD on screen.

See docs/VISUALS.md for every type. This module checks and trims specs so a
bad spec from a model or plugin cannot break the HUD.
"""

from __future__ import annotations

from typing import Any

TYPES = (
    "data", "card", "chart", "timeline",
    "calendar", "list", "stats", "table", "text", "email", "image", "panels",
)
MAX_ITEMS = 40
MAX_DEPTH = 4


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


def clean_data(value: Any, depth: int = 0) -> Any:
    """Any JSON value, trimmed so the HUD can lay it out safely."""
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if depth >= MAX_DEPTH:
        return _s(value if isinstance(value, str) else str(value), 200)
    if isinstance(value, dict):
        return {_s(k, 60): clean_data(v, depth + 1) for k, v in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, (list, tuple)):
        return [clean_data(v, depth + 1) for v in list(value)[:MAX_ITEMS]]
    return _s(value, 600)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise(spec: dict[str, Any], default_seconds: int = 25, depth: int = 0) -> dict[str, Any]:
    if not isinstance(spec, dict):
        spec = {"type": "data", "data": spec}
    kind = spec.get("type")
    if kind not in TYPES:
        # Unknown or missing type: show everything that was given, laid out automatically.
        rest = {k: v for k, v in spec.items() if k not in ("type", "title", "subtitle", "duration")}
        spec = {**spec, "data": rest.get("data", rest)}
        kind = "data"
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
    if kind == "data":
        out["data"] = clean_data(spec.get("data"))
    elif kind == "card":
        out["image"] = _s(spec.get("image"), 500)
        out["fields"] = _items(spec.get("fields"), ("label", "value"))
        out["tags"] = [_s(t, 40) for t in (spec.get("tags") or [])[:12]]
        out["body"] = _s(spec.get("body"), 2000)
    elif kind == "chart":
        out["kind"] = spec.get("kind") if spec.get("kind") in ("bar", "line") else "bar"
        out["unit"] = _s(spec.get("unit"), 20)
        labels = [_s(x, 30) for x in (spec.get("labels") or [])[:MAX_ITEMS]]
        series = []
        for ser in (spec.get("series") or [])[:4]:
            if isinstance(ser, dict):
                values = [_number(v) for v in (ser.get("values") or [])[: len(labels) or MAX_ITEMS]]
                series.append({"name": _s(ser.get("name"), 40), "values": values})
        out["labels"], out["series"] = labels, series
    elif kind == "timeline":
        out["items"] = _items(spec.get("items"), ("time", "title", "detail", "badge"))
    elif kind == "calendar":
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
                "Visual spec. The easiest way for ANY data: {type:'data', title, data:<any JSON object "
                "or array>}; the screen lays it out by itself (objects as label/value fields, lists of "
                "objects as tables, lists of words as tags). Other types for a better look: "
                "card {image (file path), fields:[{label, value}], tags:[...], body} for one person, "
                "place or thing; "
                "chart {kind:'bar'|'line', labels:[...], series:[{name, values:[numbers]}], unit}; "
                "timeline {items:[{time, title, detail, badge}]}; "
                "calendar {date, events:[{start:'HH:MM', end, title, location, all_day}]}, "
                "list {items:[{title, subtitle, meta, badge}]}, "
                "stats {items:[{label, value, unit, percent(0-100 optional)}]}, "
                "table {columns:[...], rows:[[...]]}, "
                "text {body}, "
                "email {from, to, subject, date, body}, "
                "image {path, caption}, "
                "panels {panels:[up to 4 specs]} to combine several. "
                "All types take title, subtitle, duration (seconds on screen, 0 = until closed). "
                "Only put data you got from tools or the user. Never invent data."
            ),
        }
    },
    "required": ["visual"],
}
