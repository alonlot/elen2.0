import struct
import zlib

import pytest

from elen.core import png_size
from elen.plugins.builtin.control import check_keys, parse_location

from conftest import call, say


def write_png(path, w, h):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b""))


def test_png_size(tmp_path):
    write_png(tmp_path / "a.png", 1920, 1080)
    assert png_size(tmp_path / "a.png") == (1920, 1080)
    (tmp_path / "b.png").write_bytes(b"not a png")
    assert png_size(tmp_path / "b.png") is None


def test_parse_location_units():
    size = (2000, 1000)
    got = parse_location('Sure: {"found": true, "x": 500, "y": 250, "label": "Send"}', "normalized", size)
    assert got == {"found": True, "x": 1000, "y": 250, "label": "Send"}
    got = parse_location('{"x": 300, "y": 40}', "pixels", size)
    assert (got["x"], got["y"]) == (300, 40)
    got = parse_location('{"bbox": [100, 100, 200, 300]}', "pixels", size)
    assert (got["x"], got["y"]) == (150, 200)


def test_parse_location_refuses_guesses():
    size = (100, 100)
    assert not parse_location('{"found": false, "reason": "no button"}', "pixels", size)["found"]
    assert not parse_location("It is at the top.", "pixels", size)["found"]
    assert not parse_location('{"x": 500, "y": 5}', "pixels", size)["found"]  # outside the screen


def test_check_keys():
    assert check_keys("ctrl+a Delete") == ["ctrl+a", "Delete"]
    assert check_keys("ctrl+shift+t") == ["ctrl+shift+t"]
    with pytest.raises(ValueError):
        check_keys("ctrl+")
    with pytest.raises(ValueError):
        check_keys("  ")


@pytest.fixture
def screen(make_core, tmp_path):
    """A core with a fake screen (1000 x 500) and a fake GNOME extension that records input."""

    async def factory(responses, vision_answer='{"found": true, "x": 500, "y": 500, "label": "OK"}'):
        core = await make_core(responses)
        core.ui_present = True
        sent = []

        async def capture_screen():
            path = tmp_path / "shot.png"
            write_png(path, 1000, 500)
            core.last_screen_size = (1000, 500)
            return path

        async def describe(image, question):
            return vision_answer

        async def send_input(actions, timeout=10):
            sent.append(actions)

        core.capture_screen = capture_screen
        core.vision.describe = describe
        core.send_input = send_input
        return core, sent

    return factory


def approve_all(core):
    def on_event(kind, payload):
        if kind == "confirm":
            core.resolve_confirmation(payload["id"], True, "{}", "", False)

    core.subscribe(on_event)


async def test_click_on_target_asks_and_clicks_in_screen_fractions(screen):
    core, sent = await screen([call("control__click", target="OK button"), say("Clicked OK.")])
    confirms = []
    core.subscribe(lambda k, p: confirms.append(p) if k == "confirm" else None)
    approve_all(core)
    await core.ask("press OK")
    assert confirms and confirms[0]["tool"] == "control__click"
    (actions,) = sent
    assert actions == [{"type": "click", "fx": 0.5, "fy": 0.5, "button": "left", "count": 1}]


async def test_rejected_click_does_nothing(screen):
    core, sent = await screen([call("control__click", target="OK", x=10, y=10), say("Not done.")])
    core.subscribe(
        lambda k, p: core.resolve_confirmation(p["id"], False, "{}", "no", False) if k == "confirm" else None
    )
    await core.ask("press OK")
    assert sent == []


async def test_find_on_screen_is_untrusted_and_returns_pixels(screen):
    core, _ = await screen(
        [call("control__find_on_screen", target="Send"), say("It is there.")],
        vision_answer='{"found": true, "x": 100, "y": 200, "label": "Send"}',
    )
    await core.ask("where is send")
    tool_msg = next(m for m in core.history.transcript if m.get("role") == "tool")
    assert '"untrusted_content": true' in tool_msg["content"]
    assert '"x": 100' in tool_msg["content"] and '"y": 100' in tool_msg["content"]


async def test_do_steps_one_approval_and_stops_at_error(screen):
    steps = [
        {"action": "click", "x": 250, "y": 250},
        {"action": "type", "text": "hello"},
        {"action": "keys", "keys": "ctrl+"},
        {"action": "keys", "keys": "Return"},
    ]
    core, sent = await screen([call("control__do_steps", goal="search", steps=steps), say("Stopped.")])
    confirms = []
    core.subscribe(lambda k, p: confirms.append(p) if k == "confirm" else None)
    approve_all(core)
    await core.ask("search hello")
    assert len(confirms) == 1
    assert [a[0]["type"] for a in sent] == ["click", "type"]
    tool_msg = next(m for m in core.history.transcript if m.get("role") == "tool")
    assert '"failed_step": 3' in tool_msg["content"]


async def test_input_without_extension_fails_clearly(make_core):
    core = await make_core([])
    with pytest.raises(RuntimeError, match="extension"):
        await core.send_input([{"type": "keys", "combo": "Return"}])
