"""Terminal front end. Useful to test Elen without GNOME:

    elen chat            interactive chat in the terminal
    elen ask "question"  one question, print the answer
"""

from __future__ import annotations

import asyncio
import json
import sys

from .core import Elen

CYAN = "\033[96m"
AMBER = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
RESET = "\033[0m"


def _print_visual(spec: dict) -> None:
    print(f"{CYAN}┌─ {spec.get('title') or spec['type'].upper()} {DIM}{spec.get('subtitle', '')}{RESET}")
    body = {k: v for k, v in spec.items() if k not in ("type", "title", "subtitle", "duration")}
    for line in json.dumps(body, indent=1, ensure_ascii=False).splitlines()[:40]:
        print(f"{CYAN}│{RESET} {line}")
    print(f"{CYAN}└─{RESET}")


async def _confirm_in_terminal(core: Elen, conf: dict) -> None:
    color = RED if conf["risk"] == "dangerous" else AMBER
    print(f"\n{color}=== ELEN NEEDS YOUR APPROVAL: {conf['title']} ({conf['risk']}) ==={RESET}")
    for w in conf["warnings"]:
        print(f"{RED}! {w}{RESET}")
    for k, v in conf["arguments"].items():
        print(f"  {k}: {v}")
    hint = "/a=approve and don't ask again" if conf.get("allow_label") else ""
    answer = await asyncio.to_thread(input, f"{color}Approve? [y/N/e=edit{hint}] {RESET}")
    args = dict(conf["arguments"])
    if answer.strip().lower() == "e":
        for key in conf["editable"]:
            new = await asyncio.to_thread(input, f"  {key} [{args.get(key, '')}]: ")
            if new:
                args[key] = new
        answer = await asyncio.to_thread(input, f"{color}Approve edited version? [y/N] {RESET}")
    choice = answer.strip().lower()
    remember = choice == "a" and bool(conf.get("allow_label"))
    core.resolve_confirmation(conf["id"], choice in ("y", "yes") or remember, args, "", remember)


def attach_terminal(core: Elen) -> None:
    loop = asyncio.get_running_loop()

    def on_event(kind: str, payload: dict) -> None:
        if kind == "visual":
            _print_visual(payload)
        elif kind == "tool":
            print(f"{DIM}· {payload['title']}: {payload['status']}{RESET}")
        elif kind == "message" and payload["role"] == "activity":
            print(f"{DIM}· {payload['text']}{RESET}")
        elif kind == "confirm":
            loop.create_task(_confirm_in_terminal(core, payload))
        elif kind == "error":
            print(f"{RED}{payload['text']}{RESET}")

    core.subscribe(on_event)


async def chat() -> None:
    core = Elen()
    await core.start()
    attach_terminal(core)
    print(f"{CYAN}ELEN 2.0{RESET} online. Plugins: {', '.join(core.plugins)}. Type /clear or /quit.")
    while True:
        try:
            text = await asyncio.to_thread(input, "you › ")
        except EOFError:
            break
        if text.strip() == "/quit":
            break
        if text.strip() == "/clear":
            core.clear_history()
            print("History cleared.")
            continue
        reply = await core.ask(text)
        print(f"{CYAN}elen ›{RESET} {reply}")
    await core.stop()


async def ask_once(question: str) -> None:
    core = Elen()
    await core.start()
    attach_terminal(core)
    print(await core.ask(question))
    await core.stop()


def run_chat() -> None:
    asyncio.run(chat())


def run_ask(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage: elen ask \"question\"")
    asyncio.run(ask_once(" ".join(argv)))
