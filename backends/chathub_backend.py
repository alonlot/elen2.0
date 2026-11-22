"""Chat Hub backend SDK.

Write a chat backend for the Chat Hub GNOME extension in a few lines:

    from chathub_backend import Backend

    class MyChat(Backend):
        def get_contacts(self):
            return [{"id": "bob", "name": "Bob", "presence": "online"}]

        def send_message(self, contact_id, text):
            my_network.send(contact_id, text)
            return {"status": "sent"}

    MyChat("MyChat", account_id="mychat:me", display_name="My chat").run()

When your network gets a message, call self.emit_message(contact_id, text).
Chat Hub finds the backend by itself (no setup in the extension).

The only dependency is PyGObject (python3-gi), installed on Ubuntu by default.

Threads: Chat Hub calls your methods on the main (GLib) thread. If your
network library runs in another thread or in asyncio, the emit_* helpers
are safe to call from any thread. A method can also return a
concurrent.futures.Future; Chat Hub gets the answer when it is done.
"""

from __future__ import annotations

import concurrent.futures
import json
import signal
import sys
import threading
import time
import uuid

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_PREFIX = "io.github.alonlot.ChatHub.Backend"
OBJECT_PATH = "/io/github/alonlot/ChatHub/Backend"
IFACE_NAME = "io.github.alonlot.ChatHub.Backend1"
ERROR_NOT_SUPPORTED = "io.github.alonlot.ChatHub.Error.NotSupported"
ERROR_FAILED = "io.github.alonlot.ChatHub.Error.Failed"

IFACE_XML = f"""
<node>
  <interface name="{IFACE_NAME}">
    <method name="GetInfo"><arg type="s" name="info" direction="out"/></method>
    <method name="GetContacts"><arg type="s" name="contacts" direction="out"/></method>
    <method name="GetHistory">
      <arg type="s" name="contact_id" direction="in"/>
      <arg type="x" name="before" direction="in"/>
      <arg type="u" name="limit" direction="in"/>
      <arg type="s" name="messages" direction="out"/>
    </method>
    <method name="SendMessage">
      <arg type="s" name="contact_id" direction="in"/>
      <arg type="s" name="text" direction="in"/>
      <arg type="s" name="message" direction="out"/>
    </method>
    <method name="StartCall">
      <arg type="s" name="contact_id" direction="in"/>
      <arg type="b" name="video" direction="in"/>
      <arg type="s" name="call" direction="out"/>
    </method>
    <method name="AnswerCall"><arg type="s" name="call_id" direction="in"/></method>
    <method name="EndCall"><arg type="s" name="call_id" direction="in"/></method>
    <method name="SetMuted">
      <arg type="s" name="call_id" direction="in"/><arg type="b" name="muted" direction="in"/>
    </method>
    <method name="SetPresence">
      <arg type="s" name="presence" direction="in"/><arg type="s" name="status_text" direction="in"/>
    </method>
    <method name="SetTyping">
      <arg type="s" name="contact_id" direction="in"/><arg type="b" name="typing" direction="in"/>
    </method>
    <signal name="Event"><arg type="s" name="kind"/><arg type="s" name="payload"/></signal>
  </interface>
</node>
"""

PRESENCES = ("online", "away", "busy", "offline")


class NotSupported(Exception):
    """Raise this from a method your network does not support."""


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str = "m") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


class Backend:
    """Base class for a Chat Hub backend. Override the methods you need."""

    def __init__(
        self,
        name: str,
        *,
        account_id: str | None = None,
        display_name: str | None = None,
        protocol: str = "custom",
        self_id: str = "",
        self_name: str = "",
        calls: bool = False,
        video: bool = False,
        history: bool = False,
        typing: bool = False,
        presence: bool = True,
    ):
        if not name.replace("_", "").isalnum():
            raise ValueError("name must be letters, digits and _ only (it is part of a D-Bus name)")
        self.bus_name = f"{BUS_PREFIX}.{name}"
        self.account_id = account_id or name.lower()
        self.display_name = display_name or name
        self.protocol = protocol
        self.self_id = self_id
        self.self_name = self_name
        self.capabilities = {
            "messages": True,
            "calls": calls,
            "video": video,
            "history": history,
            "typing": typing,
            "presence": presence,
        }
        self.state = "connected"
        self.error = ""
        self._conn: Gio.DBusConnection | None = None
        self._reg_id = 0
        self._loop: GLib.MainLoop | None = None
        self._main_thread = threading.main_thread()

    # ---- Override these ------------------------------------------------

    def get_contacts(self) -> list[dict]:
        """Return [{"id", "name", "presence", "statusText"?, "avatar"?}]."""
        return []

    def get_history(self, contact_id: str, before: int, limit: int) -> list[dict]:
        """Older messages, oldest first. Only used when history=True."""
        return []

    def send_message(self, contact_id: str, text: str) -> dict:
        """Send a text. Return {"id"?, "status"?: "sent"}. Raise to fail."""
        raise NotSupported("Sending messages")

    def start_call(self, contact_id: str, video: bool) -> dict:
        """Start a call. Return {"id": ...}. Then emit_call() as it changes."""
        raise NotSupported("Calls")

    def answer_call(self, call_id: str) -> None:
        raise NotSupported("Calls")

    def end_call(self, call_id: str) -> None:
        raise NotSupported("Calls")

    def set_muted(self, call_id: str, muted: bool) -> None:
        raise NotSupported("Mute")

    def set_presence(self, presence: str, status_text: str) -> None:
        raise NotSupported("Presence")

    def set_typing(self, contact_id: str, typing: bool) -> None:
        pass

    def on_started(self) -> None:
        """Called once the backend is on the bus. Connect to your network here."""

    def on_stopping(self) -> None:
        """Called before the backend exits."""

    # ---- Call these to tell Chat Hub what happened ------------------------

    def emit_state(self, state: str, error: str = "") -> None:
        """state: 'connecting' | 'connected' | 'disconnected' | 'error'."""
        self.state, self.error = state, error
        self._emit("state", {"state": state, "error": error})

    def emit_self(self, self_id: str, self_name: str) -> None:
        self.self_id, self.self_name = self_id, self_name
        self._emit("self", {"id": self_id, "name": self_name})

    def emit_contacts(self, contacts: list[dict]) -> None:
        """Replace the whole contact list."""
        self._emit("contacts", contacts)

    def emit_contact(self, contact: dict) -> None:
        """Add or change one contact (for example a presence change)."""
        self._emit("contact", contact)

    def emit_contact_removed(self, contact_id: str) -> None:
        self._emit("contact-removed", {"id": contact_id})

    def emit_message(
        self,
        contact_id: str,
        text: str,
        *,
        id: str | None = None,
        time_ms: int | None = None,
        outgoing: bool = False,
        sender_name: str | None = None,
    ) -> str:
        """A new message. outgoing=True for a message the user sent from another device."""
        mid = id or new_id()
        self._emit("message", {
            "id": mid,
            "contactId": contact_id,
            "direction": "out" if outgoing else "in",
            "text": text,
            "time": time_ms or now_ms(),
            "status": "sent" if outgoing else "delivered",
            "senderName": sender_name,
        })
        return mid

    def emit_message_status(self, contact_id: str, message_id: str, status: str) -> None:
        """status: 'sent' | 'delivered' | 'read' | 'failed'."""
        self._emit("message-status", {"contactId": contact_id, "id": message_id, "status": status})

    def emit_typing(self, contact_id: str, typing: bool) -> None:
        self._emit("typing", {"contactId": contact_id, "typing": typing})

    def emit_call(self, call_id: str, contact_id: str, state: str, *, video: bool = False, muted: bool = False) -> None:
        """state: 'ringing-in' | 'ringing-out' | 'active' | 'ended'."""
        self._emit("call", {"id": call_id, "contactId": contact_id, "state": state, "video": video, "muted": muted})

    # ---- Running ---------------------------------------------------------

    def info(self) -> dict:
        return {
            "id": self.account_id,
            "name": self.display_name,
            "protocol": self.protocol,
            "capabilities": self.capabilities,
            "self": {"id": self.self_id, "name": self.self_name},
            "state": self.state,
            "error": self.error,
        }

    def call_soon(self, fn, *args) -> None:
        """Run fn(*args) on the GLib main thread. Safe from any thread."""
        def run():
            fn(*args)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(run)

    def run(self, bus: Gio.DBusConnection | None = None) -> None:
        """Own the bus name and serve until SIGINT/SIGTERM."""
        self._conn = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(IFACE_XML)
        self._reg_id = self._conn.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        self._loop = GLib.MainLoop()

        def acquired(*_):
            self.on_started()

        def lost(*_):
            print(f"chathub: cannot own {self.bus_name} (is another copy running?)", file=sys.stderr)
            self._loop.quit()

        owner = Gio.bus_own_name_on_connection(self._conn, self.bus_name, Gio.BusNameOwnerFlags.NONE, acquired, lost)
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.stop)
        try:
            self._loop.run()
        finally:
            try:
                self.on_stopping()
            finally:
                Gio.bus_unown_name(owner)
                self._conn.unregister_object(self._reg_id)
                self._conn.flush_sync(None)

    def stop(self) -> bool:
        if self._loop:
            self._loop.quit()
        return GLib.SOURCE_REMOVE

    # ---- Internals ---------------------------------------------------------

    def _emit(self, kind: str, payload) -> None:
        if threading.current_thread() is not self._main_thread:
            self.call_soon(self._emit, kind, payload)
            return
        if not self._conn:
            return
        self._conn.emit_signal(None, OBJECT_PATH, IFACE_NAME, "Event",
                               GLib.Variant("(ss)", (kind, json.dumps(payload))))

    def _dispatch(self, method: str, args: tuple):
        if method == "GetInfo":
            return self.info()
        if method == "GetContacts":
            return self.get_contacts()
        if method == "GetHistory":
            return self.get_history(*args)
        if method == "SendMessage":
            return self.send_message(*args)
        if method == "StartCall":
            return self.start_call(*args)
        if method == "AnswerCall":
            return self.answer_call(*args)
        if method == "EndCall":
            return self.end_call(*args)
        if method == "SetMuted":
            return self.set_muted(*args)
        if method == "SetPresence":
            return self.set_presence(*args)
        if method == "SetTyping":
            return self.set_typing(*args)
        raise NotSupported(method)

    def _on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            result = self._dispatch(method, tuple(params.unpack()))
        except Exception as e:  # noqa: BLE001 - every error goes back to the caller
            self._fail(invocation, e)
            return
        if isinstance(result, concurrent.futures.Future):
            def done(fut):
                def reply():
                    try:
                        self._reply(invocation, method, fut.result())
                    except Exception as e:  # noqa: BLE001
                        self._fail(invocation, e)
                    return GLib.SOURCE_REMOVE
                GLib.idle_add(reply)
            result.add_done_callback(done)
        else:
            self._reply(invocation, method, result)

    def _reply(self, invocation, method, result):
        has_out = invocation.get_method_info().out_args
        if has_out:
            invocation.return_value(GLib.Variant("(s)", (json.dumps(result if result is not None else None),)))
        else:
            invocation.return_value(None)

    @staticmethod
    def _fail(invocation, e: Exception):
        name = ERROR_NOT_SUPPORTED if isinstance(e, (NotSupported, NotImplementedError)) else ERROR_FAILED
        invocation.return_dbus_error(name, str(e) or e.__class__.__name__)
