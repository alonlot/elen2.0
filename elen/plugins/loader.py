"""Find and load plugins.

Built-in plugins live in elen/plugins/builtin/. User plugins are loaded from:
  ~/.config/elen/plugins/<name>/plugin.py   (a folder per plugin)
  ~/.config/elen/plugins/<name>.py          (a single file)
  any folder listed in [plugins] paths = [...]

A plugin is loaded only when [plugins.<name>] enabled = true, except the
built-ins that are enabled by default. The [plugins.<name>] table is passed
to the plugin as self.config.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from . import Plugin

log = logging.getLogger("elen.plugins")

BUILTIN = ["system", "screen", "memory", "contacts", "email", "calendar", "claude_code"]


def _plugin_classes(module) -> list[type[Plugin]]:
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Plugin) and obj is not Plugin and obj.__module__ == module.__name__
    ]


def _load_file(path: Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def discover(config: dict[str, Any], user_dir: Path) -> dict[str, type[Plugin]]:
    """Return {plugin name: class} for all plugins that can be found."""
    found: dict[str, type[Plugin]] = {}
    for name in BUILTIN:
        try:
            module = importlib.import_module(f"elen.plugins.builtin.{name}")
        except Exception as e:  # noqa: BLE001
            log.warning("built-in plugin %s failed to import: %s", name, e)
            continue
        for cls in _plugin_classes(module):
            found[cls.name] = cls

    dirs = [user_dir] + [Path(p).expanduser() for p in config.get("plugins", {}).get("paths", [])]
    for base in dirs:
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (entry / "plugin.py").exists():
                path = entry / "plugin.py"
            elif entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                path = entry
            else:
                continue
            mod_name = f"elen_user_plugin_{entry.stem}"
            try:
                module = _load_file(path, mod_name)
            except Exception as e:  # noqa: BLE001
                log.error("plugin %s failed to load: %s", path, e)
                continue
            for cls in _plugin_classes(module):
                if not cls.name:
                    log.error("plugin class %s in %s has no name", cls.__name__, path)
                    continue
                found[cls.name] = cls
    return found
