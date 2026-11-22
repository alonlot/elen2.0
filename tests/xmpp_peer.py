#!/usr/bin/env python3
"""Test peer for the Jabber test: logs in as bob, adds alice, answers "pong: <text>"."""
import asyncio
import sys

import slixmpp


class Peer(slixmpp.ClientXMPP):
    def __init__(self, jid, password, friend):
        super().__init__(jid, password)
        self.friend = friend
        for p in ("xep_0030", "xep_0085", "xep_0184", "xep_0199"):
            self.register_plugin(p)
        self["xep_0184"].auto_ack = True
        self.add_event_handler("session_start", self.start)
        self.add_event_handler("message", self.message)

    async def start(self, _):
        self.send_presence(pstatus="Test bot")
        await self.get_roster()
        self.send_presence_subscription(pto=self.friend)
        print("peer ready", flush=True)

    def message(self, msg):
        if msg["type"] not in ("chat", "normal") or not msg["body"]:
            return
        to = msg["from"].bare
        state = self.make_message(mto=to, mtype="chat")
        state["chat_state"] = "composing"
        state.send()

        def reply():
            self.send_message(mto=to, mbody=f"pong: {msg['body']}", mtype="chat")
        asyncio.get_event_loop().call_later(1.0, reply)


if __name__ == "__main__":
    peer = Peer(sys.argv[1], sys.argv[2], sys.argv[3])
    peer.connect(address=("127.0.0.1", 5222), disable_starttls=True, force_starttls=False)
    asyncio.get_event_loop().run_forever()
