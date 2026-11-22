#!/usr/bin/env python3
"""Jabber / XMPP backend for Chat Hub.

Supports: roster, presence, 1:1 messages, typing (XEP-0085), delivery
receipts (XEP-0184) and messages sent from your other devices (carbons,
XEP-0280). Calls are not supported: XMPP calls (Jingle) need a media stack.
History: Chat Hub keeps a local history of all messages.

Install:  pip install --user slixmpp      (or: sudo apt install python3-slixmpp)
Config:   ~/.config/chathub/jabber.toml
              jid = "me@example.org"
              # one of these two:
              password_command = "secret-tool lookup service chathub-jabber"
              # password = "..."          (the file must then be chmod 600)
              # server = "xmpp.example.org:5222"   (only if DNS SRV records are missing)
          Or use the environment variables CHATHUB_JID, CHATHUB_PASSWORD and
          CHATHUB_XMPP_ADDRESS.
Run:      python3 backends/jabber_backend.py
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import threading
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chathub_backend import Backend, NotSupported, new_id  # noqa: E402

try:
    import slixmpp
except ImportError:  # pragma: no cover - depends on the system
    print("slixmpp is missing. Install it: pip install --user slixmpp", file=sys.stderr)
    sys.exit(1)

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "chathub", "jabber.toml")

SHOW_TO_PRESENCE = {"": "online", "chat": "online", "away": "away", "xa": "away", "dnd": "busy"}
PRESENCE_TO_SHOW = {"online": "", "away": "away", "busy": "dnd"}
CHAT_STATES = {"composing": True, "paused": False, "active": False, "inactive": False, "gone": False}


def load_config() -> dict:
    """Return {jid, password, address, no_tls}."""
    jid = os.environ.get("CHATHUB_JID", "")
    password = os.environ.get("CHATHUB_PASSWORD", "")
    server = os.environ.get("CHATHUB_XMPP_ADDRESS", "")
    # Only for a local test server. Never use it on a real network.
    no_tls = os.environ.get("CHATHUB_XMPP_NO_TLS") == "1"
    if os.path.exists(CONFIG):
        with open(CONFIG, "rb") as f:
            cfg = tomllib.load(f)
        jid = jid or cfg.get("jid", "")
        server = server or cfg.get("server", "")
        if not password and cfg.get("password_command"):
            password = subprocess.run(shlex.split(cfg["password_command"]), check=True,
                                      capture_output=True, text=True).stdout.strip()
        elif not password and cfg.get("password"):
            if os.stat(CONFIG).st_mode & 0o077:
                sys.exit(f"{CONFIG} has a password and other users can read it. Run: chmod 600 {CONFIG}")
            password = cfg["password"]
    if not jid or not password:
        sys.exit(f"No account. Write {CONFIG} or set CHATHUB_JID and CHATHUB_PASSWORD.")
    address = None
    if server:
        host, _, port = server.rpartition(":") if ":" in server else (server, "", "5222")
        address = (host, int(port))
    return {"jid": jid, "password": password, "address": address, "no_tls": no_tls}


class XmppClient(slixmpp.ClientXMPP):
    """Runs in its own thread with an asyncio loop. Talks to the backend with emit_*."""

    def __init__(self, jid, password, backend: "JabberBackend"):
        super().__init__(jid, password)
        self.backend = backend
        for plugin in ("xep_0030", "xep_0085", "xep_0184", "xep_0199", "xep_0280"):
            self.register_plugin(plugin)
        self["xep_0184"].auto_ack = True
        self["xep_0184"].auto_request = True
        self.add_event_handler("session_start", self.on_start)
        self.add_event_handler("disconnected", lambda _e: backend.emit_state("disconnected"))
        self.add_event_handler("failed_auth", lambda _e: backend.emit_state("error", "Wrong user name or password"))
        self.add_event_handler("connection_failed", lambda e: backend.emit_state("error", f"Cannot connect: {e}"))
        self.add_event_handler("message", self.on_message)
        self.add_event_handler("carbon_received", lambda m: self.on_message(m["carbon_received"]))
        self.add_event_handler("carbon_sent", self.on_carbon_sent)
        self.add_event_handler("changed_status", self.on_presence)
        self.add_event_handler("roster_update", lambda _i: backend.emit_contacts(self.contacts()))
        self.add_event_handler("receipt_received", self.on_receipt)
        for state in CHAT_STATES:
            self.add_event_handler(f"chatstate_{state}", self.on_chatstate)

    async def on_start(self, _event):
        self.send_presence()
        await self.get_roster()
        try:
            await self["xep_0280"].enable()
        except Exception:  # noqa: BLE001 - carbons are optional
            pass
        self.backend.emit_self(self.boundjid.bare, self.boundjid.user)
        self.backend.emit_state("connected")
        self.backend.emit_contacts(self.contacts())

    def contact(self, bare: str) -> dict:
        item = self.client_roster[bare]
        resources = self.client_roster.presence(bare)
        presence, status = "offline", ""
        if resources:
            best = max(resources.values(), key=lambda r: r.get("priority", 0))
            presence = SHOW_TO_PRESENCE.get(best.get("show", ""), "online")
            status = best.get("status", "") or ""
        return {"id": bare, "name": item["name"] or bare.split("@")[0], "presence": presence, "statusText": status}

    def contacts(self) -> list[dict]:
        me = self.boundjid.bare
        return [self.contact(j) for j in self.client_roster if j != me]

    def on_presence(self, pres):
        bare = pres["from"].bare
        if bare != self.boundjid.bare and bare in self.client_roster:
            self.backend.emit_contact(self.contact(bare))

    def on_message(self, msg):
        if msg["type"] not in ("chat", "normal") or not msg["body"]:
            return
        self.backend.emit_typing(msg["from"].bare, False)
        self.backend.emit_message(msg["from"].bare, msg["body"], id=msg["id"] or None)

    def on_carbon_sent(self, msg):
        sent = msg["carbon_sent"]
        if sent["body"]:
            self.backend.emit_message(sent["to"].bare, sent["body"], id=sent["id"] or None, outgoing=True)

    def on_receipt(self, msg):
        self.backend.emit_message_status(msg["from"].bare, msg["receipt"], "delivered")

    def on_chatstate(self, msg):
        self.backend.emit_typing(msg["from"].bare, CHAT_STATES.get(msg["chat_state"], False))


class JabberBackend(Backend):
    def __init__(self, jid: str, password: str, address=None, no_tls: bool = False):
        super().__init__("Jabber", account_id=f"xmpp:{jid}", display_name="Jabber", protocol="xmpp",
                         self_id=jid, self_name=jid.split("@")[0], typing=True)
        self.state = "connecting"
        self._jid, self._password = jid, password
        self._address, self._no_tls = address, no_tls
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: XmppClient | None = None
        self._ready = threading.Event()

    # Run a function in the XMPP thread and give Chat Hub a Future.
    def _in_xmpp(self, fn, *args):
        if not self._client:
            raise RuntimeError("Not connected yet")

        async def run():
            return fn(*args)
        return asyncio.run_coroutine_threadsafe(run(), self._loop)

    def _xmpp_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._client = XmppClient(self._jid, self._password, self)
        if self._no_tls:
            self._client.connect(address=self._address, disable_starttls=True, force_starttls=False)
        else:
            self._client.connect(address=self._address)
        self._ready.set()
        self._loop.run_forever()

    def on_started(self):
        threading.Thread(target=self._xmpp_thread, daemon=True, name="xmpp").start()
        self._ready.wait(10)

    def on_stopping(self):
        if self._client and self._loop:
            self._loop.call_soon_threadsafe(self._client.disconnect)

    def get_contacts(self):
        return self._in_xmpp(self._client.contacts)

    def send_message(self, contact_id, text):
        def send():
            mid = new_id("xmpp")
            msg = self._client.make_message(mto=contact_id, mbody=text, mtype="chat")
            msg["id"] = mid
            msg["chat_state"] = "active"
            msg.send()
            return {"id": mid, "status": "sent"}
        return self._in_xmpp(send)

    def set_typing(self, contact_id, typing):
        def send():
            msg = self._client.make_message(mto=contact_id, mtype="chat")
            msg["chat_state"] = "composing" if typing else "paused"
            msg.send()
        return self._in_xmpp(send)

    def set_presence(self, presence, status_text):
        def send():
            if presence == "offline":
                # Closest to "invisible" in plain XMPP. Messages still arrive.
                self._client.send_presence(ptype="unavailable")
            else:
                self._client.send_presence(pshow=PRESENCE_TO_SHOW.get(presence, ""), pstatus=status_text or None)
        return self._in_xmpp(send)

    def start_call(self, contact_id, video):
        raise NotSupported("Calls over Jabber")


if __name__ == "__main__":
    JabberBackend(**load_config()).run()
