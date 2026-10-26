"""Point at things on the screen: find a target with the vision model and draw a box on it.

How Elen finds the target ("highlight the missing ; in my code"):
 1. Screenshot. Each monitor gets a grid of labelled cells (A1, A2, ... on monitor A, B1, ...
    on monitor B). The vision model names the cells that hold the target.
 2. Zoom: crop these cells, enlarge them, draw a finer grid and ask again. Repeat until the
    area is small.
 3. Check: take a new screenshot, draw the box on it and ask the vision model if the box
    marks the target. If not, try again and tell the model which guess was wrong.
 4. Only a box that passed the check is shown on screen ("highlight" event to the extension).

Coordinates: the extension writes the monitor layout next to each screenshot
(<shot>.png.json, logical stage pixels). Without it, the full image is one monitor.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from elen.plugins import Plugin, ToolResult, tool

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

LOCATE_SYSTEM = (
    "You locate things on screenshots of a computer screen. A grid of labelled cells is drawn "
    "over each image; each label is in the top-left corner of its cell. Look at the content "
    "under the grid, not at the grid. Never guess: if the target is not visible, say so. "
    "Answer ONLY with one JSON object and no other text."
)

CHECK_SYSTEM = (
    "You check a pointer that was drawn on a screenshot of a computer screen. Be strict. "
    "Answer ONLY with one JSON object and no other text."
)

MARK = (255, 0, 200)  # magenta: rare on screens, easy to see on light and dark themes


@dataclass(frozen=True)
class Box:
    """A rectangle in screenshot pixels."""

    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def union(self, other: "Box") -> "Box":
        x, y = min(self.x, other.x), min(self.y, other.y)
        return Box(x, y, max(self.right, other.right) - x, max(self.bottom, other.bottom) - y)

    def clip(self, other: "Box") -> "Box":
        x, y = max(self.x, other.x), max(self.y, other.y)
        return Box(x, y, max(0, min(self.right, other.right) - x), max(0, min(self.bottom, other.bottom) - y))

    def grow(self, fraction: float = 0.0, pixels: float = 0.0, minimum: float = 0.0) -> "Box":
        """Make the box bigger on all sides, around the same center."""
        w = max(self.w * (1 + 2 * fraction) + 2 * pixels, minimum)
        h = max(self.h * (1 + 2 * fraction) + 2 * pixels, minimum)
        cx, cy = self.center
        return Box(cx - w / 2, cy - h / 2, w, h)

    def scaled(self, factor: float) -> "Box":
        return Box(self.x * factor, self.y * factor, self.w * factor, self.h * factor)

    def as_dict(self) -> dict[str, int]:
        return {"x": round(self.x), "y": round(self.y), "width": round(self.w), "height": round(self.h)}


def union_of(boxes: list[Box]) -> Box:
    out = boxes[0]
    for b in boxes[1:]:
        out = out.union(b)
    return out


def grid_shape(cells: int, width: float, height: float) -> tuple[int, int]:
    """Columns and rows for about `cells` cells that are close to square."""
    cells = max(4, int(cells))
    aspect = width / height if height else 1.0
    cols = max(2, round(math.sqrt(cells * aspect)))
    rows = max(2, math.ceil(cells / cols))
    return cols, rows


def make_grid(area: Box, cells: int, prefix: str = "") -> dict[str, Box]:
    """Label -> cell box. Labels count left to right, then top to bottom: A1, A2, ..."""
    cols, rows = grid_shape(cells, area.w, area.h)
    cw, ch = area.w / cols, area.h / rows
    out = {}
    for r in range(rows):
        for c in range(cols):
            out[f"{prefix}{r * cols + c + 1}"] = Box(area.x + c * cw, area.y + r * ch, cw, ch)
    return out


def parse_json(text: str) -> dict[str, Any] | None:
    """The first JSON object in a model reply (also inside ``` fences or after other text)."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : i + 1])
                    except ValueError:
                        break
                    return value if isinstance(value, dict) else None
        start = text.find("{", start + 1)
    return None


def chosen_cells(answer: dict[str, Any] | None, grid: dict[str, Box]) -> list[str]:
    if not answer or answer.get("found") is False:
        return []
    raw = answer.get("cells") or ([answer["cell"]] if answer.get("cell") else [])
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    labels = []
    for item in raw:
        label = re.sub(r"[^A-Z0-9]", "", str(item).upper())
        if label in grid and label not in labels:
            labels.append(label)
    return labels


@dataclass
class Layout:
    """Monitors in screenshot pixels, and how many screenshot pixels one stage pixel is."""

    monitors: list[Box]
    ratio: float = 1.0

    def to_stage(self, box: Box) -> Box:
        return box.scaled(1 / self.ratio)

    def to_image(self, box: Box) -> Box:
        return box.scaled(self.ratio)

    def monitor_of(self, box: Box) -> Box:
        cx, cy = box.center
        for mon in self.monitors:
            if mon.contains(cx, cy):
                return mon
        return self.monitors[0]


def read_layout(shot: Path, size: tuple[int, int]) -> Layout:
    full = Box(0, 0, size[0], size[1])
    try:
        info = json.loads(Path(f"{shot}.json").read_text())
        stage_w = float(info["stage"]["width"])
        ratio = size[0] / stage_w if stage_w > 0 else 1.0
        monitors = [
            Box(float(m["x"]), float(m["y"]), float(m["width"]), float(m["height"])).scaled(ratio).clip(full)
            for m in info.get("monitors", [])
        ]
        monitors = [m for m in monitors if m.w > 0 and m.h > 0]
        if monitors:
            return Layout(monitors, ratio)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return Layout([full], 1.0)


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _crop(img, area: Box, max_side: int, max_zoom: float):
    """Crop `area` and scale it so the long side is near max_side. Returns (image, scale)."""
    from PIL import Image

    left, top = int(area.x), int(area.y)
    right, bottom = math.ceil(area.right), math.ceil(area.bottom)
    part = img.crop((left, top, right, bottom)).convert("RGB")
    scale = min(max_zoom, max_side / max(1, part.width, part.height))
    if abs(scale - 1) > 0.01:
        part = part.resize((max(1, round(part.width * scale)), max(1, round(part.height * scale))), Image.LANCZOS)
    return part, scale, left, top


def render_grid(img, area: Box, grid: dict[str, Box], out: Path, max_side: int = 1600, max_zoom: float = 4.0) -> Path:
    """Save the crop of `area` with the grid and its labels drawn on it."""
    from PIL import Image, ImageDraw

    part, scale, left, top = _crop(img, area, max_side, max_zoom)
    overlay = Image.new("RGBA", part.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cell_h = min(b.h for b in grid.values()) * scale
    font = _font(max(10, min(18, int(cell_h / 9))))
    for label, b in grid.items():
        x0, y0 = (b.x - left) * scale, (b.y - top) * scale
        x1, y1 = (b.right - left) * scale, (b.bottom - top) * scale
        draw.rectangle((x0, y0, x1, y1), outline=(255, 40, 40, 150), width=2)
        tb = draw.textbbox((x0 + 3, y0 + 2), label, font=font)
        draw.rectangle((tb[0] - 2, tb[1] - 1, tb[2] + 2, tb[3] + 1), fill=(0, 0, 0, 140))
        draw.text((x0 + 3, y0 + 2), label, font=font, fill=(255, 235, 60, 255))
    Image.alpha_composite(part.convert("RGBA"), overlay).convert("RGB").save(out)
    return out


def render_check(img, box: Box, monitor: Box, out: Path, max_side: int = 1400) -> Path:
    """Save the screen around `box` with the box drawn on it, for the check."""
    from PIL import ImageDraw

    context = box.grow(fraction=1.0, minimum=480).clip(monitor)
    part, scale, left, top = _crop(img, context, max_side, 3.0)
    draw = ImageDraw.Draw(part)
    pad = 3
    draw.rectangle(
        ((box.x - left) * scale - pad, (box.y - top) * scale - pad, (box.right - left) * scale + pad, (box.bottom - top) * scale + pad),
        outline=MARK,
        width=4,
    )
    part.save(out)
    return out


class HighlightPlugin(Plugin):
    name = "highlight"
    description = "Find something on the screen and draw a box around it."

    async def setup(self):
        try:
            import PIL  # noqa: F401
        except ImportError as e:
            raise RuntimeError("Pillow is not installed (pip install Pillow)") from e
        c = self.config
        self.cells = max(20, int(c.get("cells", 24)))
        self.zoom_cells = max(4, int(c.get("zoom_cells", 16)))
        self.max_zoom = max(0, int(c.get("max_zoom", 3)))
        self.min_size = max(8, int(c.get("min_size", 48)))
        self.attempts = max(1, int(c.get("attempts", 3)))
        self.seconds = int(c.get("seconds", 12))
        self.image_side = max(512, int(c.get("image_side", 1600)))
        self.verify = bool(c.get("verify", True))

    def prompt_hint(self) -> str:
        return (
            "To point at something on the user's screen ('highlight', 'show me where', 'circle the "
            "error', 'the missing ; in my code'), call highlight__highlight with a full description "
            "of the target. It finds, checks and marks it; do not call screen__look_at_screen first."
        )

    # ----- the steps ---------------------------------------------------
    def _tmp(self, kind: str) -> Path:
        folder = self.ctx.data_dir / "work"
        folder.mkdir(parents=True, exist_ok=True)
        for old in sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime)[:-30]:
            old.unlink(missing_ok=True)
        return folder / f"{kind}-{uuid.uuid4().hex[:8]}.png"

    async def _shot(self):
        from PIL import Image

        path = await self.ctx.screenshot()
        img = Image.open(path)
        img.load()
        return img, read_layout(path, img.size)

    async def _ask(self, images: list[Path], question: str, system: str) -> dict[str, Any] | None:
        text = await self.ctx.core.vision.ask(images, question, system=system)
        self.log.debug("vision: %s", text)
        return parse_json(text)

    def _feedback(self, wrong: list[Box], hints: list[str], grid: dict[str, Box]) -> str:
        if not wrong and not hints:
            return ""
        labels = [lbl for lbl, b in grid.items() if any(b.contains(*w.center) for w in wrong)]
        lines = ["\nEarlier tries were wrong."]
        if labels:
            lines.append(f"The wrong guesses were in cells {', '.join(labels)}. Choose them again only if the target is really there.")
        lines += [f"Checker said: {h}" for h in hints if h]
        return "\n".join(lines)

    async def _locate(self, img, layout: Layout, target: str, wrong: list[Box], hints: list[str]) -> tuple[Box | None, str]:
        """Steps 1 and 2. Returns (box in screenshot pixels, note)."""
        grid: dict[str, Box] = {}
        images = []
        names = []
        for i, mon in enumerate(layout.monitors[: len(LETTERS)]):
            g = make_grid(mon, self.cells, LETTERS[i])
            grid.update(g)
            images.append(render_grid(img, mon, g, self._tmp("grid"), self.image_side))
            names.append(f"image {i + 1} = monitor {LETTERS[i]} (cells {LETTERS[i]}1 to {LETTERS[i]}{len(g)})")
        question = (
            f"Target: {target}\n"
            f"There are {len(images)} images, one per monitor: {'; '.join(names)}.\n"
            "Which cells hold the target? Give every cell the target touches (at most 6), in one "
            "monitor only. If the target itself is too small to read here, give the cells of the "
            "place where it is (for example the code line or the part of the editor that has it).\n"
            'Reply: {"found": true, "cells": ["A7", "A8"], "note": "what you see there"} '
            'or {"found": false, "note": "why not"}'
            + self._feedback(wrong, hints, grid)
        )
        answer = await self._ask(images, question, LOCATE_SYSTEM)
        labels = chosen_cells(answer, grid)
        note = str((answer or {}).get("note", ""))
        if not labels:
            return None, note or "the vision model did not find the target"
        first = grid[labels[0]]
        monitor = layout.monitor_of(first)
        area = union_of([grid[lbl] for lbl in labels if layout.monitor_of(grid[lbl]) == monitor])

        for _ in range(self.max_zoom):
            if max(area.w, area.h) <= self.min_size:
                break
            region = area.grow(fraction=0.15, pixels=8).clip(monitor)
            g = make_grid(region, self.zoom_cells)
            shot = render_grid(img, region, g, self._tmp("zoom"), min(self.image_side, 1200))
            question = (
                f"Target: {target}\n"
                f"This is an enlarged part of the screen with cells 1 to {len(g)}. "
                "Which cells hold the target? Give only the cells the target itself touches (at "
                "most 4); for a missing character, give the cell where it should be.\n"
                'Reply: {"found": true, "cells": [5, 6], "note": "what you see"} or '
                '{"found": false, "note": "why not"}'
            )
            answer = await self._ask([shot], question, LOCATE_SYSTEM)
            labels = chosen_cells(answer, g)
            if not labels:
                return None, str((answer or {}).get("note", "")) or "not found when zoomed in"
            area = union_of([g[lbl] for lbl in labels])
            note = str((answer or {}).get("note", note))
        return area.grow(pixels=4).clip(monitor), note

    async def _check(self, target: str, stage_box: Box) -> tuple[bool, str, Any, Layout]:
        """Step 3: a new screenshot, the box drawn on it, and a yes/no from the vision model."""
        img, layout = await self._shot()
        box = layout.to_image(stage_box)
        shot = render_check(img, box, layout.monitor_of(box), self._tmp("check"))
        question = (
            f"Target: {target}\n"
            "A thick magenta rectangle is drawn on this screenshot. Is the target inside the "
            "rectangle, with the rectangle on it and not on something next to it?\n"
            'Reply: {"correct": true} or {"correct": false, "hint": "where the target really '
            'is, relative to the rectangle (for example: two lines lower, at the end of the line)"}'
        )
        answer = await self._ask([shot], question, CHECK_SYSTEM) or {}
        return answer.get("correct") is True, str(answer.get("hint", "")), img, layout

    # ----- tools -------------------------------------------------------
    @tool(
        "Find something on the user's screen and draw a box around it, so the user sees where "
        "it is. The position is checked with a new screenshot before the box is shown, and "
        "found again when it is wrong. Describe the target in full, with what makes it "
        "different from similar things (for example 'the line in the code editor where a "
        "semicolon is missing at the end of a statement').",
        params={
            "target": "string: what to find on the screen, in full",
            "label": "string: short text to show next to the box, for example 'missing ;'",
        },
        untrusted=True,
    )
    async def highlight(self, target: str, label: str = ""):
        img, layout = await self._shot()
        wrong: list[Box] = []
        hints: list[str] = []
        note = ""
        for attempt in range(1, self.attempts + 1):
            box, note = await self._locate(img, layout, target, wrong, hints)
            if box is None:
                if attempt < self.attempts and wrong:
                    continue  # it found something before; give it one more look
                return {"found": False, "attempts": attempt, "vision_note": note}
            stage_box = layout.to_stage(box)
            if self.verify:
                ok, hint, img, layout = await self._check(target, stage_box)
                if not ok:
                    wrong.append(box)
                    hints.append(hint)
                    await self.ctx.notify(f"Highlight try {attempt} was not on the target; trying again")
                    continue
            hid = uuid.uuid4().hex[:12]
            self.ctx.core.emit(
                "highlight",
                {"id": hid, "boxes": [stage_box.as_dict()], "label": label, "duration": self.seconds},
            )
            return ToolResult(
                data={"found": True, "shown": True, "attempts": attempt, "box": stage_box.as_dict(), "vision_note": note},
                summary=f"Highlighted {label or target}",
            )
        return {
            "found": False,
            "attempts": self.attempts,
            "reason": "the box did not pass the check; nothing was shown",
            "checker_hints": [h for h in hints if h],
        }

    @tool("Remove the highlight box from the screen.")
    async def clear_highlight(self):
        self.ctx.core.emit("highlight_hide", {})
        return {"cleared": True}
