#!/usr/bin/env python3
"""Example custom backend for Chat Hub. Copy it to connect your own chat service.

Run it:   python3 backends/example_custom.py
Chat Hub shows a new "Custom" account with two contacts at once.

Replace the parts marked YOUR NETWORK with calls to your service
(HTTP API, WebSocket, a company chat, a device ...).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gi.repository import GLib  # noqa: E402

from chathub_backend import Backend, new_id  # noqa: E402


class CustomChat(Backend):
    def __init__(self):
        super().__init__(
            "Custom",                      # bus name: io.github.alonlot.ChatHub.Backend.Custom
            account_id="custom:example",   # stable, unique
            display_name="Custom",         # shown in Chat Hub
            protocol="custom",
            self_id="me",
            self_name=os.environ.get("USER", "me"),
            calls=True,
            typing=True,
        )
        self.contacts = {
            "echo": {"id": "echo", "name": "Echo Service", "presence": "online", "statusText": "Sends your text back"},
            "clock": {"id": "clock", "name": "Clock Bot", "presence": "away", "statusText": "Ask me the time"},
        }
        self.calls = {}

    # YOUR NETWORK: read the contact list (roster) from your service.
    def get_contacts(self):
        return list(self.contacts.values())

    # YOUR NETWORK: send the text to your service.
    def send_message(self, contact_id, text):
        mid = new_id("custom")
        if contact_id == "echo":
            answer = text
        else:
            answer = time.strftime("It is %H:%M:%S")

        # Pretend the other side reads it, types, and answers.
        GLib.timeout_add(600, lambda: self.emit_message_status(contact_id, mid, "read") or False)
        GLib.timeout_add(900, lambda: self.emit_typing(contact_id, True) or False)

        def reply():
            self.emit_typing(contact_id, False)
            self.emit_message(contact_id, answer)
            return False
        GLib.timeout_add(1800, reply)
        return {"id": mid, "status": "sent"}

    # YOUR NETWORK: start a call. Here the other side answers after 2 s.
    def start_call(self, contact_id, video):
        call_id = new_id("call")
        self.calls[call_id] = contact_id
        GLib.timeout_add(2000, lambda: call_id in self.calls and self.emit_call(call_id, contact_id, "active") or False)
        return {"id": call_id, "state": "ringing-out"}

    def answer_call(self, call_id):
        self.emit_call(call_id, self.calls[call_id], "active")

    def end_call(self, call_id):
        contact_id = self.calls.pop(call_id, None)
        if contact_id:
            self.emit_call(call_id, contact_id, "ended")

    def set_muted(self, call_id, muted):
        pass  # YOUR NETWORK: mute the microphone

    def set_presence(self, presence, status_text):
        print(f"presence is now {presence}")

    def on_started(self):
        print(f"Custom backend is running as {self.bus_name}")
        # A presence change after 5 s, to show live updates.
        GLib.timeout_add_seconds(5, lambda: self.emit_contact({**self.contacts["clock"], "presence": "online"}) or False)


if __name__ == "__main__":
    CustomChat().run()
