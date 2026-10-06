"""Command line entry point.

  elen daemon     run the background service for the GNOME extension
  elen chat       chat in the terminal
  elen ask TEXT   ask one question
  elen status     show models, plugins and tools
  elen config     print the config file path
  elen set KEY VALUE   change a setting, for example:
                  elen set brain.base_url http://localhost:11434/v1
                  elen set brain.model qwen2.5:14b
                  elen set plugins.claude_code.model sonnet
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys


def main() -> None:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "daemon"
    logging.basicConfig(
        level=os.environ.get("ELEN_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if cmd == "daemon":
        from .core import Elen
        from .dbus_service import serve

        async def run() -> None:
            core = Elen()
            await core.start()
            try:
                await serve(core)
            finally:
                await core.stop()

        asyncio.run(run())
    elif cmd == "chat":
        from .cli import run_chat

        run_chat()
    elif cmd == "ask":
        from .cli import run_ask

        run_ask(argv[1:])
    elif cmd == "status":
        from .core import Elen

        async def show() -> None:
            core = Elen()
            await core.start()
            print(json.dumps(core.status(), indent=2))

        asyncio.run(show())
    elif cmd == "set" and len(argv) == 3:
        from .config_edit import set_dotted

        path = set_dotted(argv[1], argv[2])
        print(f"Saved to {path}. Restart Elen: systemctl --user restart elen")
    elif cmd == "config":
        from .config import ensure_user_config

        print(ensure_user_config())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
