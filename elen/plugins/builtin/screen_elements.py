"""Exact boxes of the things on the screen, for the highlight plugin.

Vision models read screens well but give bad coordinates. These two sources give exact
boxes, and the vision model only has to pick one of them:

- OCR: every line of text on the screenshot, with a box for each word. RapidOCR (PaddleOCR
  models on ONNX Runtime, `pip install 'elen[ocr]'`) finds text on screens well: dark and
  light themes, small text, buttons. Tesseract is the fallback; it is made for scanned pages
  and misses more on screens.
- AT-SPI (the accessibility tree): buttons, menu items, links, tabs, fields and labels of
  the apps on screen, with their names.

All boxes here are in screenshot pixels.

AT-SPI on Wayland: an app does not know where its window is, so its "screen" coordinates are
often relative to its own window. The extension writes the windows (position, pid, X11 or
Wayland) next to each screenshot, and `place_elements` uses them: X11 apps keep their screen
coordinates, Wayland apps get their window coordinates plus the window position. Boxes that
are outside their window, or under a window that is above it, are dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("elen.highlight")

HELPER = Path(__file__).with_name("atspi_helper.py")


@dataclass
class Candidate:
    """One thing on the screen with an exact box. `words` is for OCR lines only."""

    id: str
    kind: str  # "text" (OCR line) or the AT-SPI role, for example "push button"
    text: str
    box: Any  # highlight.Box, screenshot pixels
    words: list[tuple[str, Any]] = field(default_factory=list)
    source: str = "ocr"


# ----- OCR -----------------------------------------------------------------
def parse_tesseract_tsv(tsv: str, scale: float = 1.0, min_conf: float = 10.0) -> list[dict[str, Any]]:
    """Tesseract TSV -> lines [{"text", "box": (x, y, w, h), "words": [(text, (x, y, w, h))]}].

    `scale` is how much the image was enlarged before OCR; boxes are divided by it.
    """
    lines: dict[tuple, dict[str, Any]] = {}
    rows = tsv.splitlines()
    for row in rows[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
            x, y, w, h = (int(c) / scale for c in cols[6:10])
        except ValueError:
            continue
        if not text or conf < min_conf or w <= 0 or h <= 0:
            continue
        key = tuple(cols[1:5])  # page, block, paragraph, line
        line = lines.setdefault(key, {"words": []})
        line["words"].append((text, (x, y, w, h)))
    out = []
    for line in lines.values():
        words = line["words"]
        x0 = min(b[0] for _, b in words)
        y0 = min(b[1] for _, b in words)
        x1 = max(b[0] + b[2] for _, b in words)
        y1 = max(b[1] + b[3] for _, b in words)
        out.append({"text": " ".join(t for t, _ in words), "box": (x0, y0, x1 - x0, y1 - y0), "words": words})
    out.sort(key=lambda ln: (round(ln["box"][1] / 8), ln["box"][0]))
    return out


async def _tesseract(exe: str, path: Path, lang: str, timeout: float, threshold: int = 0) -> str:
    proc = await asyncio.create_subprocess_exec(
        exe, str(path), "stdout", "-l", lang, "--psm", "3", "-c", f"thresholding_method={threshold}", "tsv",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        # The passes run at the same time; Tesseract's own threads make that much slower.
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        log.warning("OCR took more than %s s; skipped", timeout)
        return ""
    if proc.returncode != 0:
        log.warning("tesseract failed (exit %s)", proc.returncode)
        return ""
    return out.decode("utf-8", "replace")


def _overlap(a, b) -> float:
    """Intersection over the smaller area, for (x, y, w, h) tuples."""
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    small = min(a[2] * a[3], b[2] * b[3])
    return ix * iy / small if small > 0 else 0.0


def join_passes(*passes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lines of all OCR passes. Where two lines are on the same place, keep the longer text."""
    out: list[dict[str, Any]] = []
    for line in (ln for p in passes for ln in p):
        same = [i for i, o in enumerate(out) if _overlap(o["box"], line["box"]) > 0.5]
        if not same:
            out.append(line)
        elif all(len(line["text"]) > len(out[i]["text"]) for i in same):
            for i in reversed(same):
                del out[i]
            out.append(line)
    out.sort(key=lambda ln: (round(ln["box"][1] / 8), ln["box"][0]))
    return out


async def ocr_lines(img, work: Path, lang: str = "eng", timeout: float = 25) -> list[dict[str, Any]]:
    """OCR of the whole screenshot. Empty list if Tesseract is not installed or fails.

    Tesseract reads dark text on a light background, and its page analysis can miss a small
    light button on a dark screen. So it reads the image three times at the same time: as it
    is, inverted (for dark themes), and as it is with local thresholding (for small light
    areas), and keeps the best line of each place.
    """
    exe = shutil.which("tesseract")
    if not exe:
        return []
    from PIL import Image, ImageOps

    gray = img.convert("L")
    # Small screen text reads much better at twice the size; a 4K screen is big enough.
    scale = 2.0 if gray.width <= 2600 else 1.0
    if scale != 1.0:
        gray = gray.resize((round(gray.width * scale), round(gray.height * scale)), Image.LANCZOS)
    normal, inverted = work / "ocr-normal.png", work / "ocr-inverted.png"
    gray.save(normal)
    ImageOps.invert(gray).save(inverted)
    try:
        outs = await asyncio.gather(
            _tesseract(exe, inverted, lang, timeout),
            _tesseract(exe, normal, lang, timeout),
            _tesseract(exe, normal, lang, timeout, threshold=1),
        )
    finally:
        normal.unlink(missing_ok=True)
        inverted.unlink(missing_ok=True)
    return join_passes(*(parse_tesseract_tsv(o, scale) for o in outs if o))


def _quad_box(quad) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def parse_rapidocr(result, min_score: float = 0.5) -> list[dict[str, Any]]:
    """RapidOCR output (boxes, txts, scores, word_results) -> the same lines as Tesseract."""
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    texts = list(getattr(result, "txts", None) or [])
    scores = list(getattr(result, "scores", None) or [1.0] * len(texts))
    word_results = list(getattr(result, "word_results", None) or [])
    out = []
    for i, (quad, text, score) in enumerate(zip(boxes, texts, scores)):
        text = str(text).strip()
        if not text or float(score) < min_score:
            continue
        words = []
        for item in word_results[i] if i < len(word_results) else ():
            try:
                wtext, _, wquad = item
            except (TypeError, ValueError):
                continue
            if str(wtext).strip():
                words.append((str(wtext).strip(), _quad_box(wquad)))
        out.append({"text": text, "box": _quad_box(quad), "words": words})
    out.sort(key=lambda ln: (round(ln["box"][1] / 8), ln["box"][0]))
    return out


_rapid_engine = None


def rapidocr_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("rapidocr") is not None and importlib.util.find_spec("onnxruntime") is not None
    except (ImportError, ValueError):
        return False


async def rapid_lines(img) -> list[dict[str, Any]]:
    """OCR with RapidOCR in a worker thread (about 1 s for a 1080p screen on 4 CPU cores;
    the first call also loads the models)."""
    def run():
        global _rapid_engine
        import numpy as np
        from rapidocr import RapidOCR

        if _rapid_engine is None:
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            _rapid_engine = RapidOCR()
        return _rapid_engine(np.array(img.convert("RGB")), return_word_box=True)

    try:
        return parse_rapidocr(await asyncio.to_thread(run))
    except Exception as e:  # noqa: BLE001 - a broken OCR install must not break highlight
        log.warning("RapidOCR failed: %s", e)
        return []


async def screen_text(img, work: Path, engine: str = "auto", lang: str = "eng") -> list[dict[str, Any]]:
    """Text lines of the screenshot with the configured OCR engine ("auto", "rapidocr", "tesseract")."""
    if engine in ("auto", "rapidocr") and rapidocr_available():
        return await rapid_lines(img)
    if engine == "rapidocr":
        log.warning("RapidOCR is not installed (pip install 'elen[ocr]'); using Tesseract")
    return await ocr_lines(img, work, lang)


# ----- AT-SPI --------------------------------------------------------------
async def atspi_elements(python: str, pids: list[int], timeout: float = 6, max_nodes: int = 4000) -> list[dict[str, Any]]:
    """Run the helper with the system Python. Empty list if AT-SPI is not there."""
    if not python or not Path(python).exists():
        return []
    req = json.dumps({"pids": pids, "max_nodes": max_nodes, "seconds": max(1.0, timeout - 1.5)}).encode()
    try:
        proc = await asyncio.create_subprocess_exec(
            python, "-I", str(HELPER),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(req), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            log.warning("AT-SPI took more than %s s; skipped", timeout)
            return []
        data = json.loads(out.decode("utf-8", "replace") or "{}")
    except (OSError, ValueError) as e:
        log.warning("AT-SPI helper failed: %s", e)
        return []
    if data.get("error"):
        log.info("AT-SPI not available: %s", data["error"])
    return [e for e in data.get("elements", []) if isinstance(e, dict)]


def _rect(d: dict[str, Any]) -> tuple[float, float, float, float]:
    return float(d["x"]), float(d["y"]), float(d["width"]), float(d["height"])


def _inside(box, rect, slack: float = 4) -> bool:
    x, y, w, h = rect
    return box.x >= x - slack and box.y >= y - slack and box.right <= x + w + slack and box.bottom <= y + h + slack


def _covered(box, rect) -> bool:
    x, y, w, h = rect
    cx, cy = box.center
    return x <= cx < x + w and y <= cy < y + h


def place_elements(elements: list[dict[str, Any]], windows: list[dict[str, Any]], box_type) -> list[tuple[dict[str, Any], Any]]:
    """AT-SPI elements -> [(element, box in stage pixels)] for the ones that are really visible.

    `windows` come from the screenshot side file, bottom to top. Without windows (no extension),
    the screen coordinates are used as they are.
    """
    out = []
    for el in elements:
        try:
            sx, sy, sw, sh = (float(v) for v in el["screen"])
        except (KeyError, TypeError, ValueError):
            continue
        if not windows:
            out.append((el, box_type(sx, sy, sw, sh)))
            continue
        mine = [i for i, w in enumerate(windows) if w.get("pid") == el.get("pid")]
        if not mine:
            continue
        titled = [i for i in mine if el.get("window") and windows[i].get("title") == el.get("window")]
        idx = (titled or mine)[-1]
        win = windows[idx]
        if win.get("x11") or not el.get("window_box"):
            box = box_type(sx, sy, sw, sh)
        else:
            wx, wy, ww, wh = (float(v) for v in el["window_box"])
            bx, by, _, _ = _rect(win.get("buffer") or win["frame"])
            box = box_type(bx + wx, by + wy, ww, wh)
        if not _inside(box, _rect(win["frame"])):
            continue
        if any(_covered(box, _rect(w["frame"])) for w in windows[idx + 1 :]):
            continue
        out.append((el, box))
    return out


def read_windows(shot: Path) -> list[dict[str, Any]]:
    try:
        info = json.loads(Path(f"{shot}.json").read_text())
        windows = info.get("windows") or []
        return [w for w in windows if isinstance(w, dict) and "frame" in w]
    except (OSError, ValueError, AttributeError):
        return []
