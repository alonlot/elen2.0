import json

import pytest
from conftest import ScriptedProvider, call, say

from elen.plugins.builtin.highlight import Box, grid_shape, make_grid, parse_json, read_layout

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

# Two monitors side by side, 200 x 100 stage pixels each; the screenshot has 2 pixels per stage pixel.
LAYOUT = {
    "stage": {"width": 400, "height": 100},
    "monitors": [{"x": 0, "y": 0, "width": 200, "height": 100}, {"x": 200, "y": 0, "width": 200, "height": 100}],
}
CFG = {"highlight": {"cells": 20, "zoom_cells": 4, "max_zoom": 1, "min_size": 8, "seconds": 5}}


def test_grid_has_at_least_the_cells_asked_and_covers_the_area():
    area = Box(100, 50, 1920, 1080)
    grid = make_grid(area, 24, "A")
    assert len(grid) >= 24
    assert list(grid)[:2] == ["A1", "A2"]
    cols, rows = grid_shape(24, 1920, 1080)
    last = grid[f"A{cols * rows}"]
    assert round(last.right) == 2020 and round(last.bottom) == 1130


def test_parse_json_inside_text_and_fences():
    assert parse_json('Sure!\n```json\n{"found": true, "cells": ["A3"]}\n```') == {"found": True, "cells": ["A3"]}
    assert parse_json("no json here") is None
    assert parse_json('{bad} then {"correct": false}') == {"correct": False}


def test_layout_from_sidecar_scales_monitors(tmp_path):
    shot = tmp_path / "s.png"
    (tmp_path / "s.png.json").write_text(json.dumps(LAYOUT))
    layout = read_layout(shot, (800, 200))
    assert layout.ratio == 2
    assert layout.monitors[1] == Box(400, 0, 400, 200)
    assert layout.to_stage(Box(400, 0, 20, 10)) == Box(200, 0, 10, 5)
    # no sidecar: the whole image is one monitor
    assert read_layout(tmp_path / "other.png", (800, 200)).monitors == [Box(0, 0, 800, 200)]


async def _core(make_core, env, vision_replies, brain=None):
    core = await make_core(brain or [], plugins=CFG)
    core.vision._provider = ScriptedProvider([say(r) for r in vision_replies])
    shots = []

    async def fake_capture():
        path = env / f"shot{len(shots)}.png"
        Image.new("RGB", (800, 200), "white").save(path)
        (env / f"shot{len(shots)}.png.json").write_text(json.dumps(LAYOUT))
        shots.append(path)
        return path

    core.capture_screen = fake_capture
    events = []
    core.subscribe(lambda kind, payload: events.append((kind, payload)))
    return core, shots, events


def _images(request):
    return [p for p in request["messages"][0]["content"] if p["type"] == "image"]


async def test_highlight_finds_checks_and_shows_box(make_core, env):
    replies = [
        '{"found": true, "cells": ["B8"], "note": "editor line"}',
        '{"found": true, "cells": [1]}',
        '{"correct": true}',
    ]
    core, shots, events = await _core(make_core, env, replies)
    result = await core.plugins["highlight"].highlight("the missing semicolon", label="missing ;")
    assert result.data["found"] and result.data["attempts"] == 1
    requests = core.vision.provider.requests
    assert len(_images(requests[0])) == 2  # one grid image per monitor
    assert "monitor B" in requests[0]["messages"][0]["content"][-1]["text"]
    assert len(shots) == 2  # first look + the check
    (kind, payload), = [e for e in events if e[0] == "highlight"]
    box = payload["boxes"][0]
    # B8 = monitor B, row 2, column 2 of a 6 x 4 grid: stage x 233..267, y 25..50. The zoom
    # looks at B8 plus a margin and the model picks its top-left quarter.
    assert 215 <= box["x"] and box["x"] + box["width"] <= 255
    assert 15 <= box["y"] and box["y"] + box["height"] <= 42
    assert payload["label"] == "missing ;" and payload["duration"] == 5


async def test_wrong_box_is_not_shown_and_is_tried_again(make_core, env):
    replies = [
        '{"found": true, "cells": ["A1"]}',
        '{"found": true, "cells": [4]}',
        '{"correct": false, "hint": "the target is on the other monitor"}',
        '{"found": true, "cells": ["B1"]}',
        '{"found": true, "cells": [1]}',
        '{"correct": true}',
    ]
    core, shots, events = await _core(make_core, env, replies)
    result = await core.plugins["highlight"].highlight("the close button")
    assert result.data["attempts"] == 2
    retry = core.vision.provider.requests[3]["messages"][0]["content"][-1]["text"]
    assert "wrong" in retry and "A1" in retry and "other monitor" in retry
    highlights = [p for k, p in events if k == "highlight"]
    assert len(highlights) == 1 and highlights[0]["boxes"][0]["x"] >= 200


async def test_gives_up_without_showing_anything(make_core, env):
    replies = ['{"found": true, "cells": ["A1"]}', '{"found": true, "cells": [1]}', '{"correct": false}'] * 3
    core, shots, events = await _core(make_core, env, replies)
    result = await core.plugins["highlight"].highlight("a thing")
    assert result["found"] is False and result["attempts"] == 3
    assert not [e for e in events if e[0] == "highlight"]


async def test_not_found(make_core, env):
    core, shots, events = await _core(make_core, env, ['{"found": false, "note": "no code editor visible"}'])
    result = await core.plugins["highlight"].highlight("the missing ;")
    assert result == {"found": False, "attempts": 1, "vision_note": "no code editor visible"}


async def test_brain_calls_highlight_tool(make_core, env):
    brain = [call("highlight__highlight", target="the missing ;", label="here"), say("Done, it is marked.")]
    replies = ['{"found": true, "cells": ["A2"]}', '{"found": true, "cells": [2]}', '{"correct": true}']
    core, shots, events = await _core(make_core, env, replies, brain=brain)
    assert await core.ask("highlight the missing ; in my code") == "Done, it is marked."
    assert any(k == "highlight" for k, _ in events)
