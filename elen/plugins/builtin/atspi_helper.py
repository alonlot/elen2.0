"""List the visible elements of the apps on screen through AT-SPI (the accessibility tree).

The highlight plugin runs this file with the system Python (`/usr/bin/python3 -I`), because
the AT-SPI bindings (python3-gi, gir1.2-atspi-2.0) come from Ubuntu and are not in Elen's
virtual environment. It runs in its own process with a time limit: an app that does not
answer cannot block Elen.

stdin:  {"pids": [123, ...], "max_nodes": 4000, "seconds": 4}
stdout: {"elements": [{"pid", "window", "role", "name", "screen": [x, y, w, h],
                       "window_box": [x, y, w, h]}, ...]}

The daemon decides which box is right (this file does not know where windows are on Wayland).
Do not import anything from elen here.
"""

import json
import sys
import time

ROLES = {
    "push button", "toggle button", "check box", "radio button", "menu item", "check menu item",
    "radio menu item", "menu", "link", "entry", "password text", "text", "label", "heading",
    "page tab", "list item", "tree item", "table cell", "combo box", "icon", "image", "slider",
    "spin button", "tool bar", "status bar", "paragraph", "section", "static", "notification",
    "alert", "dialog", "document web", "document text", "terminal", "button", "switch",
}


def main() -> None:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    req = json.loads(sys.stdin.read() or "{}")
    pids = set(req.get("pids") or [])
    budget = int(req.get("max_nodes", 4000))
    deadline = time.monotonic() + float(req.get("seconds", 4))
    Atspi.set_timeout(800, 1500)

    def extents(node, kind):
        try:
            r = node.get_extents(kind)
            return [r.x, r.y, r.width, r.height]
        except Exception:
            return None

    def text_of(node):
        try:
            name = node.get_name() or ""
        except Exception:
            name = ""
        if name:
            return name[:200]
        try:
            t = node.get_text_iface()
            if t is not None:
                return (t.get_text(0, min(200, t.get_character_count())) or "").strip()
        except Exception:
            pass
        return ""

    out = []
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        if time.monotonic() > deadline or budget <= 0:
            break
        try:
            app = desktop.get_child_at_index(i)
            pid = app.get_process_id()
        except Exception:
            continue
        if pids and pid not in pids:
            continue
        for j in range(app.get_child_count()):
            try:
                win = app.get_child_at_index(j)
                states = win.get_state_set()
                if not states.contains(Atspi.StateType.SHOWING):
                    continue
                title = win.get_name() or ""
            except Exception:
                continue
            stack = [win]
            while stack and budget > 0 and time.monotonic() < deadline:
                node = stack.pop()
                budget -= 1
                try:
                    states = node.get_state_set()
                    if not (states.contains(Atspi.StateType.SHOWING) and states.contains(Atspi.StateType.VISIBLE)):
                        continue
                    role = node.get_role_name() or ""
                    count = node.get_child_count()
                except Exception:
                    continue
                if node is not win and role in ROLES:
                    name = text_of(node)
                    screen = extents(node, Atspi.CoordType.SCREEN)
                    if name and screen and screen[2] > 0 and screen[3] > 0:
                        out.append({
                            "pid": pid, "window": title, "role": role, "name": name,
                            "screen": screen, "window_box": extents(node, Atspi.CoordType.WINDOW),
                        })
                # Children last to first, so the stack walks them in reading order.
                for k in range(min(count, 500) - 1, -1, -1):
                    try:
                        stack.append(node.get_child_at_index(k))
                    except Exception:
                        pass
    json.dump({"elements": out}, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report, do not crash: the daemon falls back to other sources
        json.dump({"elements": [], "error": f"{type(e).__name__}: {e}"}, sys.stdout)
