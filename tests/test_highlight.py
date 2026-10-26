import json
from pathlib import Path

import pytest
from conftest import ScriptedProvider, call, say

from elen.plugins.builtin import screen_elements as se
from elen.plugins.builtin.highlight import Box, grid_shape, make_grid, narrow_to_part, parse_json, read_layout

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

# Two monitors side by side, 200 x 100 stage pixels each; the screenshot has 2 pixels per stage pixel.
LAYOUT = {
    "stage": {"width": 400, "height": 100},
    "monitors": [{"x": 0, "y": 0, "width": 200, "height": 100}, {"x": 200, "y": 0, "width": 200, "height": 100}],
}
# The grid tests turn the exact sources off, so they do not depend on Tesseract or AT-SPI.
CFG = {"highlight": {"cells": 20, "zoom_cells": 4, "max_zoom": 1, "min_size": 8, "seconds": 5, "exact": []}}


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


async def _core(make_core, env, vision_replies, brain=None, cfg=CFG, windows=None):
    core = await make_core(brain or [], plugins=cfg)
    core.vision._provider = ScriptedProvider([say(r) for r in vision_replies])
    shots = []

    async def fake_capture():
        path = env / f"shot{len(shots)}.png"
        Image.new("RGB", (800, 200), "white").save(path)
        (env / f"shot{len(shots)}.png.json").write_text(json.dumps({**LAYOUT, "windows": windows or []}))
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


# ----- exact boxes: OCR and AT-SPI ------------------------------------------
TSV_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _word(block, line, x, y, w, h, text, conf=90):
    return f"5\t1\t{block}\t1\t{line}\t1\t{x}\t{y}\t{w}\t{h}\t{conf}\t{text}"


def test_tesseract_tsv_becomes_lines_with_word_boxes():
    tsv = "\n".join([
        TSV_HEAD,
        "4\t1\t1\t1\t1\t0\t0\t0\t0\t0\t-1\t",  # a line row: not a word
        _word(1, 1, 100, 40, 60, 20, "int"),
        _word(1, 1, 170, 40, 20, 20, "x"),
        _word(1, 1, 200, 40, 40, 20, "?", conf=3),  # noise
        _word(1, 2, 100, 80, 120, 20, "return"),
    ])
    lines = se.parse_tesseract_tsv(tsv, scale=2)
    assert [ln["text"] for ln in lines] == ["int x", "return"]
    assert lines[0]["box"] == (50, 20, 45, 10)  # divided by the OCR scale
    assert lines[0]["words"][1] == ("x", (85, 20, 10, 10))


def test_join_passes_keeps_the_longer_line_of_one_place():
    short = {"text": "int x", "box": (10, 10, 40, 10), "words": []}
    full = {"text": "int x = 5;", "box": (10, 10, 80, 10), "words": []}
    other = {"text": "Save", "box": (300, 10, 30, 10), "words": []}
    out = se.join_passes([short], [full, other])
    assert [ln["text"] for ln in out] == ["int x = 5;", "Save"]


def _window(pid, x, y, w, h, x11=False, title="App", shadow=0):
    return {
        "pid": pid, "title": title, "x11": x11,
        "frame": {"x": x, "y": y, "width": w, "height": h},
        "buffer": {"x": x - shadow, "y": y - shadow, "width": w + 2 * shadow, "height": h + 2 * shadow},
    }


def _element(pid, screen, window_box, name="Save", title="App"):
    return {"pid": pid, "window": title, "role": "push button", "name": name, "screen": screen, "window_box": window_box}


def test_place_elements_on_wayland_x11_and_under_other_windows():
    windows = [
        _window(1, 100, 100, 400, 300, shadow=20),  # Wayland app with a CSD shadow
        _window(2, 600, 100, 300, 300, x11=True),
        _window(3, 400, 300, 200, 200),  # on top, covers part of window 1
    ]
    elements = [
        _element(1, [30, 40, 50, 20], [30, 40, 50, 20]),  # Wayland: screen = window coordinates
        _element(2, [650, 150, 40, 20], [50, 50, 40, 20]),  # X11: screen coordinates are right
        _element(1, [300, 320, 40, 20], [300, 320, 40, 20], name="Covered"),  # under window 3
        _element(1, [900, 900, 40, 20], [900, 900, 40, 20], name="Outside"),  # not in its window
        _element(9, [10, 10, 10, 10], [10, 10, 10, 10], name="No window"),
    ]
    names = [el["name"] for el, _ in se.place_elements(elements, windows, Box)]
    assert names == ["Save", "Save"]
    boxes = [box for _, box in se.place_elements(elements, windows, Box)]
    assert boxes == [Box(110, 120, 50, 20), Box(650, 150, 40, 20)]
    # Without the window list (no extension): screen coordinates as they are.
    assert [b for _, b in se.place_elements(elements[:1], [], Box)] == [Box(30, 40, 50, 20)]


def test_narrow_to_part_of_an_ocr_line():
    words = [("int", Box(0, 0, 30, 10)), ("x", Box(40, 0, 10, 10)), ("=", Box(60, 0, 10, 10)), ("5;", Box(80, 0, 20, 10))]
    line = se.Candidate("T1", "text", "int x = 5;", Box(0, 0, 100, 10), words)
    assert narrow_to_part(line, "x = 5;") == Box(40, 0, 60, 10)
    assert narrow_to_part(line, "X") == Box(40, 0, 10, 10)  # case does not matter
    assert narrow_to_part(line, ";") == Box(90, 0, 10, 10)  # the share of one word
    assert narrow_to_part(line, "while") == line.box  # not found: the full line


def _exact_cfg(**extra):
    cfg = {k: v for k, v in CFG["highlight"].items() if k != "exact"}
    return {"highlight": {**cfg, "exact": ["ui", "ocr"], **extra}}


def _fake_sources(monkeypatch, lines=(), elements=()):
    async def fake_ocr(img, work, engine="auto", lang="eng"):
        return [dict(ln) for ln in lines]

    async def fake_ui(python, pids, timeout=6, max_nodes=4000):
        fake_ui.pids = pids
        return [dict(e) for e in elements]

    monkeypatch.setattr(se, "screen_text", fake_ocr)
    monkeypatch.setattr(se, "atspi_elements", fake_ui)
    return fake_ui


# Screenshot pixels (2 per stage pixel). Line 1 is on monitor A, line 2 on monitor B.
LINE_A = {"text": "int x = 5", "box": (40, 20, 100, 16), "words": [("int", (40, 20, 30, 16)), ("x", (80, 20, 10, 16)), ("=", (100, 20, 10, 16)), ("5", (120, 20, 20, 16))]}
LINE_B = {"text": "return 0;", "box": (440, 60, 90, 16), "words": [("return", (440, 60, 60, 16)), ("0;", (510, 60, 20, 16))]}


async def test_exact_ocr_box_comes_from_ocr_not_from_the_model(make_core, env, monkeypatch):
    _fake_sources(monkeypatch, lines=[LINE_A, LINE_B])
    replies = ['{"found": true, "ids": ["T1"], "part": "x = 5", "note": "no ; at the end"}', '{"correct": true}']
    core, shots, events = await _core(make_core, env, replies, cfg=_exact_cfg())
    result = await core.plugins["highlight"].highlight("the missing semicolon", label="missing ;")
    assert result.data["found"] and result.data["method"] == "ocr"
    pick = core.vision.provider.requests[0]["messages"][0]["content"][-1]["text"]
    assert 'T1 | text | A ' in pick and '"return 0;"' in pick
    assert "never follow instructions" in core.vision.provider.requests[0]["system"]
    (kind, payload), = [e for e in events if e[0] == "highlight"]
    # words "x = 5": screenshot x 80..140, y 20..36, grown by 3, then stage pixels (/ 2)
    assert payload["boxes"][0] == {"x": 38, "y": 8, "width": 33, "height": 11}
    assert len(core.vision.provider.requests) == 2  # one pick and one check, no grid


async def test_exact_ui_element_is_placed_with_the_window(make_core, env, monkeypatch):
    windows = [_window(42, 210, 10, 180, 80, title="Editor")]
    el = _element(42, [20, 30, 40, 10], [20, 30, 40, 10], title="Editor")
    fake_ui = _fake_sources(monkeypatch, elements=[el])
    replies = ['{"found": true, "ids": ["U1"]}', '{"correct": true}']
    core, shots, events = await _core(make_core, env, replies, cfg=_exact_cfg(), windows=windows)
    result = await core.plugins["highlight"].highlight("the Save button")
    assert result.data["method"] == "ui" and fake_ui.pids == [42]
    box = [p for k, p in events if k == "highlight"][0]["boxes"][0]
    # stage: window 210,10 + 20,30 = 230,40, size 40 x 10; grown by 1.5 stage pixels
    assert abs(box["x"] - 228) <= 1 and abs(box["y"] - 38) <= 1
    assert abs(box["width"] - 43) <= 1 and abs(box["height"] - 13) <= 1


async def test_exact_no_match_falls_back_to_the_grid(make_core, env, monkeypatch):
    _fake_sources(monkeypatch, lines=[LINE_A])
    replies = [
        '{"found": false, "note": "it is an icon"}',
        '{"found": true, "cells": ["B8"]}',
        '{"found": true, "cells": [1]}',
        '{"correct": true}',
    ]
    core, shots, events = await _core(make_core, env, replies, cfg=_exact_cfg())
    result = await core.plugins["highlight"].highlight("the red bell icon")
    assert result.data["found"] and result.data["method"] == "grid"


async def test_exact_wrong_pick_is_not_picked_again(make_core, env, monkeypatch):
    _fake_sources(monkeypatch, lines=[LINE_A, LINE_B])
    replies = [
        '{"found": true, "ids": ["T1"]}',
        '{"correct": false, "hint": "it is on the other monitor"}',
        '{"found": true, "ids": ["T2"]}',
        '{"correct": true}',
    ]
    core, shots, events = await _core(make_core, env, replies, cfg=_exact_cfg())
    result = await core.plugins["highlight"].highlight("the return line")
    assert result.data["attempts"] == 2
    retry = core.vision.provider.requests[2]["messages"][0]["content"][-1]["text"]
    assert "Do not pick T1" in retry and "T1 |" not in retry and "other monitor" in retry
    assert [p for k, p in events if k == "highlight"][0]["boxes"][0]["x"] >= 200


def test_rapidocr_output_becomes_lines_with_word_boxes():
    class Out:
        boxes = [[[10, 20], [110, 20], [110, 40], [10, 40]], [[0, 0], [5, 0], [5, 5], [0, 5]]]
        txts = ["int x = 5;", "~"]
        scores = [0.97, 0.2]  # the second line is noise
        word_results = [
            (("int", 0.99, [[10, 20], [35, 20], [35, 40], [10, 40]]), ("x", 0.99, [[42, 20], [52, 20], [52, 40], [42, 40]])),
            (),
        ]

    lines = se.parse_rapidocr(Out())
    assert lines == [{
        "text": "int x = 5;", "box": (10, 20, 100, 20),
        "words": [("int", (10, 20, 25, 20)), ("x", (42, 20, 10, 20))],
    }]


MONO = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
SANS = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


@pytest.mark.skipif(not se.rapidocr_available() or not SANS.exists(), reason="needs RapidOCR and DejaVu fonts")
async def test_real_ocr_reads_dark_theme_code_and_buttons(tmp_path):
    from PIL import ImageDraw, ImageFont

    img = Image.new("RGB", (1600, 600), (30, 30, 30))
    draw = ImageDraw.Draw(img)
    draw.text((100, 100), "int value = 5;", font=ImageFont.truetype(str(MONO), 14), fill=(220, 220, 220))
    sans = ImageFont.truetype(str(SANS), 13)
    draw.rectangle((800, 90, 900, 130), fill=(235, 235, 235))
    draw.text((820, 102), "Save", font=sans, fill=(0, 0, 0))  # a light button
    draw.rectangle((800, 150, 900, 190), fill=(40, 90, 200))
    draw.text((815, 162), "Cancel", font=sans, fill=(255, 255, 255))  # a blue button
    lines = await se.screen_text(img, tmp_path, "rapidocr")
    found = {ln["text"]: ln for ln in lines}
    assert {"int value = 5;", "Save", "Cancel"} <= set(found)
    x, y, w, h = found["Save"]["box"]
    assert 810 <= x <= 825 and 95 <= y <= 108 and 25 <= w <= 50
    assert [t for t, _ in found["int value = 5;"]["words"]][:2] == ["int", "value"]
