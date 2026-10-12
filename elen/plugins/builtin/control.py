"""Screen control: find things on the screen, click, type, press keys and scroll.

The GNOME extension does the input with a virtual mouse and keyboard (Clutter), so it
works on Wayland without extra tools or root rights. The daemon sends positions as
fractions of the screen, so the screenshot resolution and the display scale do not matter.

Positions for the brain are pixels of the last screenshot (find_on_screen gives them).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from elen.plugins import Plugin, ToolResult, tool

BUTTONS = ("left", "right", "middle")
DIRECTIONS = ("up", "down", "left", "right")
STEP_ACTIONS = ("click", "double_click", "right_click", "move", "type", "keys", "scroll", "wait")
MAX_TEXT = 4000

LOCATE_PROMPT = (
    "Find this element on the screenshot: {target}\n"
    "Answer with JSON only, no other text:\n"
    '{{"found": true, "x": X, "y": Y, "label": "the visible text or a short name of the element"}}\n'
    "x and y are the CENTER of the element. {units}\n"
    "If the element is not visible or you are not sure which one it is, answer "
    '{{"found": false, "reason": "why"}}. Never guess a position.'
)
UNITS = {
    "normalized": "Give x and y as numbers from 0 to 1000: 0 is the left / top edge, 1000 the right / bottom edge.",
    "pixels": "Give x and y in pixels of this image ({w} x {h}): 0,0 is the top left corner.",
}

STEPS_SCHEMA = {
    "type": "array",
    "description": (
        "the actions in order. Each item: {\"action\": one of " + ", ".join(STEP_ACTIONS) + "} and its "
        "fields: click/double_click/right_click/move take x, y (screenshot pixels) or target (a "
        "description, found with a new screenshot just before the step); type takes text; keys "
        "takes keys (for example \"ctrl+s\"); scroll takes direction and amount; wait takes ms."
    ),
    "items": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(STEP_ACTIONS)},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "target": {"type": "string"},
            "text": {"type": "string"},
            "keys": {"type": "string"},
            "direction": {"type": "string", "enum": list(DIRECTIONS)},
            "amount": {"type": "integer"},
            "ms": {"type": "integer"},
        },
        "required": ["action"],
    },
}


THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
BOX_RE = re.compile(r"<\|begin_of_box\|>(.*?)<\|end_of_box\|>", re.S)
LIST_RE = re.compile(r"\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*(?:,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*)?\]")


def _answer_data(text: str) -> dict[str, Any] | None:
    """The JSON object in the answer, or a bare box / point list (GLM-V style [x1,y1,x2,y2])."""
    text = THINK_RE.sub("", text or "")
    boxed = BOX_RE.search(text)
    if boxed:
        text = boxed.group(1)
    for match in reversed(list(re.finditer(r"\{[^{}]*\}", text))):
        try:
            data = json.loads(match.group(0))
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    nums = LIST_RE.search(text)
    if nums:
        values = [float(v) for v in nums.groups() if v is not None]
        return {"bbox": values} if len(values) == 4 else {"point": values}
    return None


def parse_location(text: str, units: str, size: tuple[int, int]) -> dict[str, Any]:
    """Read the vision model's answer. Returns {"found", "x", "y", "label"} in pixels."""
    data = _answer_data(text)
    if data is None:
        return {"found": False, "reason": f"The vision model gave no position: {(text or '')[:200]!r}"}
    if not data.get("found", True):
        return {"found": False, "reason": str(data.get("reason") or "not visible")}
    try:
        if isinstance(data.get("bbox"), list) and len(data["bbox"]) == 4:
            x1, y1, x2, y2 = (float(v) for v in data["bbox"])
            x, y = (x1 + x2) / 2, (y1 + y2) / 2
        elif isinstance(data.get("point"), list) and len(data["point"]) == 2:
            x, y = (float(v) for v in data["point"])
        else:
            x, y = float(data["x"]), float(data["y"])
    except (KeyError, TypeError, ValueError):
        return {"found": False, "reason": f"The vision model gave no position: {text[:200]!r}"}
    w, h = size
    if units == "normalized":
        x, y = x * w / 1000, y * h / 1000
    if not (0 <= x <= w and 0 <= y <= h):
        return {"found": False, "reason": "The vision model gave a position outside the screen."}
    return {"found": True, "x": round(x), "y": round(y), "label": str(data.get("label") or "")}


def check_keys(keys: str) -> list[str]:
    """Split "ctrl+l Return" into key combinations. The extension checks the key names."""
    combos = [c for c in re.split(r"[\s,]+", keys or "") if c]
    if not combos:
        raise ValueError("No keys given. Example: ctrl+s, Return, alt+tab.")
    for combo in combos:
        if any(not part for part in combo.split("+")):
            raise ValueError(f"Bad key combination '{combo}'. Example: ctrl+shift+t.")
    return combos


class ControlPlugin(Plugin):
    name = "control"
    description = "Control the screen: click, type, press keys, scroll."

    def prompt_hint(self) -> str:
        return (
            "You can control the user's screen. To click something, first call "
            "control__find_on_screen to get its position, then control__click with x and y "
            "(screenshot pixels). control__type_text types into the focused field, "
            "control__press_keys presses keys such as ctrl+s or Return. For several actions in a "
            "row, use control__do_steps (one approval). After an action, check the result with "
            "screen__look_at_screen before you say it worked."
        )

    async def setup(self) -> None:
        units = str(self.config.get("coordinates", "normalized"))
        if units not in UNITS:
            raise RuntimeError("[plugins.control] coordinates must be 'normalized' or 'pixels'")
        self.units = units

    # ----- helpers -----------------------------------------------------
    async def _screen_size(self) -> tuple[int, int]:
        size = self.ctx.core.last_screen_size
        if size is None:
            await self.ctx.screenshot()
            size = self.ctx.core.last_screen_size
        if size is None:
            raise RuntimeError("Cannot read the screen size. Take a screenshot first.")
        return size

    async def _locate(self, target: str) -> dict[str, Any]:
        path = await self.ctx.screenshot()
        size = self.ctx.core.last_screen_size
        if size is None:
            raise RuntimeError("Cannot read the size of the screenshot.")
        w, h = size
        prompt = LOCATE_PROMPT.format(target=target, units=UNITS[self.units].format(w=w, h=h))
        answer = await self.ctx.look(path, prompt)
        found = parse_location(answer, self.units, size)
        found.update({"target": target, "screen_width": w, "screen_height": h, "screenshot": str(path)})
        return found

    async def _point(self, x: Any, y: Any, target: str = "") -> dict[str, Any]:
        """A position as {"fx", "fy"} for the extension, and in pixels for the reply."""
        if x is None or y is None or x == "" or y == "":
            if not target:
                raise ValueError("Give x and y (from control__find_on_screen) or a target.")
            found = await self._locate(target)
            if not found["found"]:
                raise RuntimeError(f"Could not find '{target}' on the screen: {found['reason']}")
            x, y = found["x"], found["y"]
        w, h = await self._screen_size()
        x, y = float(x), float(y)
        if not (0 <= x <= w and 0 <= y <= h):
            raise ValueError(f"Position {x:.0f},{y:.0f} is outside the screen ({w} x {h}).")
        return {"fx": x / w, "fy": y / h, "x": round(x), "y": round(y)}

    async def _send(self, actions: list[dict[str, Any]]) -> None:
        typed = sum(len(a.get("text", "")) for a in actions)
        delay = int(self.config.get("type_delay_ms", 8))
        await self.ctx.send_input(actions, timeout=10 + typed * (delay + 20) / 1000)

    def _type_action(self, text: str) -> dict[str, Any]:
        if len(text) > MAX_TEXT:
            raise ValueError(f"Text is too long ({len(text)} characters, max {MAX_TEXT}).")
        return {"type": "type", "text": text, "delay_ms": int(self.config.get("type_delay_ms", 8))}

    # ----- tools -------------------------------------------------------
    @tool(
        "Find an element on the screen (a button, a field, a link, an icon) and return its "
        "position in screenshot pixels. Use it before control__click.",
        params={"target": "string: what to find, exact, for example 'the blue Send button in Gmail'"},
        untrusted=True,
    )
    async def find_on_screen(self, target: str):
        return await self._locate(target)

    @tool(
        "Click on the screen at x, y (screenshot pixels from control__find_on_screen). Without x "
        "and y, the target is found on a new screenshot first.",
        params={
            "target": "string: what you click, for example 'Send button'",
            "x": "integer: horizontal position in screenshot pixels",
            "y": "integer: vertical position in screenshot pixels",
            "button": {"type": "string", "enum": list(BUTTONS), "description": "mouse button, default left"},
            "double": "boolean: double click",
        },
        required=["target"],
        risk="write",
        title="Click on screen",
        allow_scope=["target"],
    )
    async def click(self, target: str, x: int | None = None, y: int | None = None, button: str = "left", double: bool = False):
        if button not in BUTTONS:
            raise ValueError(f"button must be one of {BUTTONS}")
        point = await self._point(x, y, target)
        await self._send([{"type": "click", "fx": point["fx"], "fy": point["fy"], "button": button, "count": 2 if double else 1}])
        return {"clicked": True, "x": point["x"], "y": point["y"], "button": button, "double": bool(double)}

    @tool(
        "Type text with the keyboard into the field that has the focus (click the field first). "
        "Newlines press Enter.",
        params={"text": "string: the exact text to type", "press_enter": "boolean: press Enter after the text"},
        risk="write",
        editable=["text"],
        title="Type text",
        allow_scope=["text"],
    )
    async def type_text(self, text: str, press_enter: bool = False):
        actions = [self._type_action(text)]
        if press_enter:
            actions.append({"type": "keys", "combo": "Return"})
        await self._send(actions)
        return {"typed": True, "characters": len(text), "pressed_enter": bool(press_enter)}

    @tool(
        "Press a key or a key combination, for example 'ctrl+s', 'Return', 'Escape', 'alt+tab', "
        "'ctrl+shift+t'. Several in a row: separate them with spaces ('ctrl+a Delete').",
        params={"keys": "string: the keys"},
        risk="write",
        editable=["keys"],
        title="Press keys",
        allow_scope=["keys"],
    )
    async def press_keys(self, keys: str):
        combos = check_keys(keys)
        await self._send([{"type": "keys", "combo": c} for c in combos])
        return {"pressed": combos}

    @tool(
        "Scroll with the mouse wheel. With x and y (screenshot pixels), the mouse moves there first.",
        params={
            "direction": {"type": "string", "enum": list(DIRECTIONS), "description": "scroll direction"},
            "amount": "integer: wheel steps, default 3",
            "x": "integer: horizontal position in screenshot pixels",
            "y": "integer: vertical position in screenshot pixels",
        },
        risk="low",
        title="Scroll",
    )
    async def scroll(self, direction: str, amount: int = 3, x: int | None = None, y: int | None = None):
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        action: dict[str, Any] = {"type": "scroll", "direction": direction, "amount": max(1, min(30, int(amount)))}
        if x is not None and y is not None:
            point = await self._point(x, y)
            action.update(fx=point["fx"], fy=point["fy"])
        await self._send([action])
        return {"scrolled": direction, "amount": action["amount"]}

    @tool(
        "Move the mouse pointer to x, y (screenshot pixels), for example to show a tooltip.",
        params={"x": "integer: horizontal position", "y": "integer: vertical position"},
        risk="low",
        title="Move mouse",
    )
    async def move_mouse(self, x: int, y: int):
        point = await self._point(x, y)
        await self._send([{"type": "move", "fx": point["fx"], "fy": point["fy"]}])
        return {"moved": True, "x": point["x"], "y": point["y"]}

    @tool(
        "Do several screen actions in a row with one approval, for example: click the search "
        "field, type a text, press Return. Stops at the first action that fails.",
        params={"goal": "string: one line, what the steps do", "steps": STEPS_SCHEMA},
        risk="write",
        title="Control the screen",
        allow_scope=["steps"],
    )
    async def do_steps(self, goal: str, steps: list[dict[str, Any]]):
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")
        if len(steps) > 40:
            raise ValueError("Too many steps (max 40). Split the work.")
        done: list[str] = []
        for i, step in enumerate(steps, 1):
            try:
                done.append(await self._step(step))
            except Exception as e:  # noqa: BLE001
                return ToolResult(
                    data={"completed_steps": done, "failed_step": i, "error": f"{type(e).__name__}: {e}",
                          "note": "The steps after the failed one were not done."},
                    summary=f"Stopped at step {i}",
                )
        return ToolResult(data={"completed_steps": done, "goal": goal}, summary=f"{len(done)} steps done")

    async def _step(self, step: dict[str, Any]) -> str:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        action = step.get("action")
        if action in ("click", "double_click", "right_click", "move"):
            point = await self._point(step.get("x"), step.get("y"), str(step.get("target") or ""))
            where = f"{step.get('target') or 'position'} at {point['x']},{point['y']}"
            if action == "move":
                await self._send([{"type": "move", "fx": point["fx"], "fy": point["fy"]}])
                return f"moved to {where}"
            await self._send([{
                "type": "click", "fx": point["fx"], "fy": point["fy"],
                "button": "right" if action == "right_click" else "left",
                "count": 2 if action == "double_click" else 1,
            }])
            return f"{action.replace('_', ' ')} {where}"
        if action == "type":
            text = str(step.get("text") or "")
            await self._send([self._type_action(text)])
            return f"typed {len(text)} characters"
        if action == "keys":
            combos = check_keys(str(step.get("keys") or ""))
            await self._send([{"type": "keys", "combo": c} for c in combos])
            return "pressed " + " ".join(combos)
        if action == "scroll":
            direction = step.get("direction", "down")
            if direction not in DIRECTIONS:
                raise ValueError(f"direction must be one of {DIRECTIONS}")
            amount = max(1, min(30, int(step.get("amount") or 3)))
            await self._send([{"type": "scroll", "direction": direction, "amount": amount}])
            return f"scrolled {direction} {amount}"
        if action == "wait":
            ms = max(0, min(10000, int(step.get("ms") or 500)))
            await asyncio.sleep(ms / 1000)
            return f"waited {ms} ms"
        raise ValueError(f"unknown action '{action}'. Use one of {', '.join(STEP_ACTIONS)}.")
