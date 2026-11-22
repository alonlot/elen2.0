# Chat Hub API

Chat Hub shows the chat UI. A **provider** connects it to one chat network.
You can write a provider in two ways:

| Way | Language | Runs in | Use it when |
|---|---|---|---|
| **D-Bus backend** (recommended) | Any (Python SDK included) | Its own process | You use a library (XMPP, HTTP, WebSocket), or you want crashes to stay out of GNOME Shell |
| **In-process provider** | GJS (JavaScript) | GNOME Shell | The network is simple and you need no extra libraries |

Both ways use the same data model and the same events.
Chat Hub does the rest: contact list, local history, unread counts,
notifications, sounds and the call window.

---

## Data model

All values are plain JSON objects.

**Contact**

```json
{"id": "bob@example.org", "name": "Bob", "presence": "online", "statusText": "At work", "avatar": "/path/to/bob.png"}
```

- `id`: unique in the account. Required.
- `presence`: `online`, `busy`, `away` or `offline`.
- `avatar`: path to an image file, or `null` (Chat Hub then shows initials).

**Message**

```json
{"id": "m-123", "contactId": "bob@example.org", "direction": "in", "text": "Hi", "time": 1732280000000, "status": "delivered"}
```

- `direction`: `in` (from the contact) or `out` (from the user).
- `time`: milliseconds since 1970 (UTC).
- `status`: `sending`, `sent`, `delivered`, `read` or `failed`.

**Call**

```json
{"id": "call-1", "contactId": "bob@example.org", "state": "ringing-in", "video": false, "muted": false}
```

- `state`: `ringing-in`, `ringing-out`, `active` or `ended`.

Chat Hub shows call controls and a timer. **The backend owns the audio and
video.** For example, it can start a SIP or WebRTC client. Chat Hub only
tells it "start", "answer", "mute" and "hang up".

**Capabilities**

```json
{"messages": true, "calls": false, "video": false, "history": false, "typing": false, "presence": true}
```

Chat Hub hides the buttons for features that are `false`.
If `history` is `true`, Chat Hub asks your backend for old messages when the
local history of a conversation is empty, and when the user clicks
"Load earlier messages".

---

## D-Bus backend

### How Chat Hub finds a backend

1. The backend owns a bus name on the **session bus** that starts with
   `io.github.alonlot.ChatHub.Backend.` For example
   `io.github.alonlot.ChatHub.Backend.Jabber`.
2. It exports the object `/io/github/alonlot/ChatHub/Backend` with the
   interface `io.github.alonlot.ChatHub.Backend1`.
3. Chat Hub sees the name when it appears and adds the account. When the
   process stops, Chat Hub removes the account. Chat history stays.

To start a backend with the desktop session, install a D-Bus service file
(`./install.sh --jabber` does this). Chat Hub starts all installed backends
when the setting "Start installed backends" is on.

### Methods

Complex values are JSON strings. This keeps backends simple in every language.

| Method | In | Out (JSON) | Notes |
|---|---|---|---|
| `GetInfo` | | `{id, name, protocol, capabilities, self: {id, name}, state, error}` | `id` must be stable. It is the key of the chat history. |
| `GetContacts` | | `Contact[]` | |
| `GetHistory` | `s contact_id, x before, u limit` | `Message[]` oldest first | `before` = 0 means "now". |
| `SendMessage` | `s contact_id, s text` | `{id?, status?, time?}` | Return the network's message id if you have one. |
| `StartCall` | `s contact_id, b video` | `{id, state?}` | Then send `call` events. |
| `AnswerCall` | `s call_id` | | |
| `EndCall` | `s call_id` | | Hang up or decline. |
| `SetMuted` | `s call_id, b muted` | | |
| `SetPresence` | `s presence, s status_text` | | `offline` means "invisible". |
| `SetTyping` | `s contact_id, b typing` | | Optional. |

For a feature you do not support, return the D-Bus error
`io.github.alonlot.ChatHub.Error.NotSupported` with a short text
(for example `Calls`). Chat Hub shows "This account does not support calls".
For other failures return any D-Bus error. Chat Hub shows its message.

### Events

One signal: `Event(s kind, s payload)`. `payload` is JSON.

| kind | payload | Meaning |
|---|---|---|
| `state` | `{state, error}` | `connecting`, `connected`, `disconnected` or `error` |
| `self` | `{id, name}` | The user on this account changed |
| `info` | `{name?, capabilities?, self?}` | Account details changed |
| `contacts` | `Contact[]` | Replace the full contact list |
| `contact` | `Contact` | Add or change one contact (presence, name) |
| `contact-removed` | `{id}` | |
| `message` | `Message` | New message. Use `direction: "out"` for a message the user sent on another device |
| `message-status` | `{contactId, id, status}` | For example `delivered` or `read` |
| `typing` | `{contactId, typing}` | |
| `call` | `Call` | Every call state change, `ended` included |

### Python SDK

`backends/chathub_backend.py` does all the D-Bus work. You only need PyGObject
(`python3-gi`), which Ubuntu has by default.

```python
from chathub_backend import Backend, NotSupported

class MyChat(Backend):
    def __init__(self):
        super().__init__("MyChat", account_id="mychat:me", display_name="My chat",
                         calls=True, typing=True)

    def get_contacts(self):
        return [{"id": "bob", "name": "Bob", "presence": "online"}]

    def send_message(self, contact_id, text):
        my_network.send(contact_id, text)
        return {"status": "sent"}

    def on_started(self):
        my_network.on_message(lambda who, text: self.emit_message(who, text))

MyChat().run()
```

Methods you can override: `get_contacts`, `get_history`, `send_message`,
`start_call`, `answer_call`, `end_call`, `set_muted`, `set_presence`,
`set_typing`, `on_started`, `on_stopping`.

Helpers you call: `emit_state`, `emit_self`, `emit_contacts`, `emit_contact`,
`emit_contact_removed`, `emit_message`, `emit_message_status`, `emit_typing`,
`emit_call`.

**Threads.** Chat Hub calls your methods on the main thread. The `emit_*`
helpers are safe from every thread. A method can return a
`concurrent.futures.Future` (for example from
`asyncio.run_coroutine_threadsafe`). Chat Hub gets the answer when the
future is done. `jabber_backend.py` uses this with an asyncio library.

### Examples

- `backends/example_custom.py`: a template for your own service.
  The places to change are marked `YOUR NETWORK`.
- `backends/jabber_backend.py`: Jabber/XMPP with
  [slixmpp](https://slixmpp.readthedocs.io/).

### Try it without Chat Hub

```sh
python3 backends/example_custom.py &
gdbus call --session -d io.github.alonlot.ChatHub.Backend.Custom \
  -o /io/github/alonlot/ChatHub/Backend -m io.github.alonlot.ChatHub.Backend1.GetContacts
gdbus monitor --session -d io.github.alonlot.ChatHub.Backend.Custom
```

---

## In-process provider (GJS)

1. Create `providers/myprovider.js`:

```js
import {ChatProvider, ProviderState} from '../lib/api.js';

export class MyProvider extends ChatProvider {
    constructor() {
        super({id: 'mine', name: 'Mine', protocol: 'custom', capabilities: {typing: true}});
    }

    async start() {
        // Log in. Throw an Error to show a failure.
        this.setSelf({id: 'me', name: 'Me'});
        this.setState(ProviderState.CONNECTED);
    }

    async getContacts() {
        return [{id: 'bob', name: 'Bob', presence: 'online'}];
    }

    async sendMessage(contactId, text) {
        // send it ...
        return {id: 'server-id-1'};
    }

    destroy() {
        // log out, stop timers
        super.destroy();
    }
}
```

2. Register it in `providers/index.js`:

```js
registerProvider('mine', settings => new MyProvider(settings));
```

3. Turn it on:

```sh
gsettings --schemadir ~/.local/share/gnome-shell/extensions/chathub@alonlot.github.io/schemas \
  set org.gnome.shell.extensions.chathub providers "['demo', 'mine']"
```

Helpers: `setState`, `setSelf`, `resetContacts`, `updateContact`,
`removeContact`, `receiveMessage`, `echoMessage`, `updateMessageStatus`,
`setTypingState`, `updateCall`.

**Rules for code in GNOME Shell.** Do not block: use async Gio calls, not
`_sync` calls. Remove every GLib timeout in `destroy()`.
`providers/demo.js` is a full example with calls and typing.

> `connect()` and `disconnect()` are the signal methods (GNOME style).
> The login and logout methods are `start()` and `destroy()`.

---

## Where the data is

| What | Where |
|---|---|
| Chat history | `~/.local/share/chathub/history/<account>/<contact>.jsonl` (one JSON message per line; file names are hashes) |
| Settings | `gsettings list-recursively org.gnome.shell.extensions.chathub` (use `--schemadir` as above) |
| Logs | `journalctl --user -f /usr/bin/gnome-shell` (look for `Chat Hub`) |
