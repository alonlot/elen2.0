"""Desktop basics: time, apps, links, notifications, volume, system status, shell."""

from __future__ import annotations

import asyncio
import configparser
import os
import shutil
from datetime import datetime
from pathlib import Path

from elen.plugins import Plugin, ToolResult, tool

APP_DIRS = [
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path.home() / ".local/share/applications",
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/snapd/desktop/applications"),
]


def find_desktop_apps() -> dict[str, str]:
    """Return {lower-case app name: desktop id}."""
    apps: dict[str, str] = {}
    for base in APP_DIRS:
        if not base.is_dir():
            continue
        for f in base.glob("*.desktop"):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(f, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if entry.get("NoDisplay", "false").lower() == "true":
                continue
            name = entry.get("Name", f.stem)
            apps.setdefault(name.lower(), f.stem)
            apps.setdefault(f.stem.lower(), f.stem)
    return apps


async def run(cmd: list[str], timeout: float = 20) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out"
    except asyncio.CancelledError:  # the user pressed Stop
        proc.kill()
        raise
    return proc.returncode or 0, out.decode(errors="replace")


class SystemPlugin(Plugin):
    name = "system"
    description = "Desktop basics."

    @tool("Get the current local date and time.")
    async def get_datetime(self):
        now = datetime.now().astimezone()
        return {"iso": now.isoformat(timespec="seconds"), "weekday": now.strftime("%A"), "tz": now.strftime("%Z")}

    @tool(
        "Open an installed desktop application by name (for example 'Firefox', 'Files', 'Terminal').",
        params={"name": "string: application name"},
        risk="low",
        allow_scope=["name"],
    )
    async def open_application(self, name: str):
        apps = await asyncio.to_thread(find_desktop_apps)
        key = name.lower().strip()
        desktop_id = apps.get(key)
        if not desktop_id:
            matches = [v for k, v in apps.items() if key in k]
            if len(matches) == 1 or (matches and len(set(matches)) == 1):
                desktop_id = matches[0]
            elif matches:
                return {"opened": False, "error": "More than one app matches. Ask the user.", "matches": sorted(set(matches))[:10]}
        if not desktop_id:
            return {"opened": False, "error": f"No installed application named '{name}'."}
        code, out = await run(["gtk-launch", desktop_id])
        return {"opened": code == 0, "app": desktop_id, "output": out[-300:]}

    @tool(
        "Open a web link or a file in its default application.",
        params={"target": "string: URL or file path"},
        risk="low",
        allow_scope=["target"],
    )
    async def open_link(self, target: str):
        target = os.path.expanduser(target)
        code, out = await run(["xdg-open", target])
        return {"opened": code == 0, "output": out[-300:]}

    @tool(
        "Show a desktop notification.",
        params={"title": "string: short title", "body": "string: message text"},
        risk="low",
    )
    async def notify(self, title: str, body: str = ""):
        code, _ = await run(["notify-send", "-a", "Elen 2.0", title, body])
        return {"shown": code == 0}

    @tool("Set the speaker volume in percent (0-150).", params={"percent": "integer: volume percent"}, risk="low")
    async def set_volume(self, percent: int):
        percent = max(0, min(150, int(percent)))
        if shutil.which("wpctl"):
            code, out = await run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent / 100:.2f}"])
        else:
            code, out = await run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"])
        return {"ok": code == 0, "percent": percent, "output": out[-200:]}

    @tool("Get CPU load, memory, disk and battery status, and show it on screen.")
    async def system_status(self):
        load1, load5, _ = os.getloadavg()
        cpus = os.cpu_count() or 1
        mem = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, val = line.split(":", 1)
                mem[key] = int(val.split()[0])
        except OSError:
            pass
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", 0)
        mem_pct = round(100 * (total - avail) / total) if total else None
        disk = shutil.disk_usage(Path.home())
        disk_pct = round(100 * disk.used / disk.total)
        battery = None
        for bat in Path("/sys/class/power_supply").glob("BAT*"):
            try:
                battery = int((bat / "capacity").read_text().strip())
            except (OSError, ValueError):
                pass
        cpu_pct = min(100, round(100 * load1 / cpus))
        data = {
            "cpu_load_percent": cpu_pct,
            "load_avg": [round(load1, 2), round(load5, 2)],
            "memory_used_percent": mem_pct,
            "memory_total_gb": round(total / 1048576, 1) if total else None,
            "disk_home_used_percent": disk_pct,
            "disk_free_gb": round(disk.free / 1e9, 1),
            "battery_percent": battery,
        }
        items = [
            {"label": "CPU", "value": cpu_pct, "unit": "%", "percent": cpu_pct},
            {"label": "Memory", "value": mem_pct, "unit": "%", "percent": mem_pct or 0},
            {"label": "Disk", "value": disk_pct, "unit": "%", "percent": disk_pct},
        ]
        if battery is not None:
            items.append({"label": "Battery", "value": battery, "unit": "%", "percent": battery})
        return ToolResult(data=data, visual={"type": "stats", "title": "System status", "items": items})

    @tool(
        "Run a shell command on the user's computer and return its output. Use only when the "
        "user asked for it or no other tool can do the job.",
        params={
            "command": "string: the bash command",
            "reason": "string: one line why this command is needed",
        },
        risk="dangerous",
        editable=["command"],
        title="Run shell command",
        untrusted=True,
    )
    async def run_command(self, command: str, reason: str = ""):
        timeout = float(self.config.get("command_timeout", 120))
        code, out = await run(["bash", "-lc", command], timeout)
        return {"exit_code": code, "output": out[-8000:]}
