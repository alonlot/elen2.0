// D-Bus bridge: external chat backends (any language) become providers.
//
// A backend owns a bus name that starts with BUS_PREFIX, for example
// "io.github.alonlot.ChatHub.Backend.Jabber", and exports BACKEND_IFACE at
// OBJECT_PATH. Chat Hub finds it by itself, also when it starts later.
// All complex values are JSON strings, so a backend is easy to write.
// See docs/API.md and backends/chathub_backend.py.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {ChatProvider, NotSupportedError, ProviderState, normalizeCall, normalizeMessage} from './api.js';

export const BUS_PREFIX = 'io.github.alonlot.ChatHub.Backend';
export const OBJECT_PATH = '/io/github/alonlot/ChatHub/Backend';
export const IFACE_NAME = 'io.github.alonlot.ChatHub.Backend1';

export const BACKEND_IFACE = `
<node>
  <interface name="${IFACE_NAME}">
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
</node>`;

const CALL_TIMEOUT_MS = 30000;

function busCall(bus, dest, path, iface, method, params, replyType, timeout = CALL_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
        bus.call(dest, path, iface, method, params,
            replyType ? new GLib.VariantType(replyType) : null,
            Gio.DBusCallFlags.NO_AUTO_START, timeout, null, (conn, res) => {
                try {
                    resolve(conn.call_finish(res));
                } catch (e) {
                    reject(e);
                }
            });
    });
}

function cleanError(e) {
    // "GDBus.Error:org.freedesktop.DBus.Python.ValueError: bad id" -> "bad id"
    return String(e?.message ?? e).replace(/^GDBus\.Error:[\w.]+:\s*/, '');
}

// Returns what is not supported, or null for other errors.
function notSupported(e, method) {
    if (!(e instanceof GLib.Error))
        return null;
    if (e.matches(Gio.DBusError, Gio.DBusError.UNKNOWN_METHOD))
        return method;
    const m = /^GDBus\.Error:io\.github\.alonlot\.ChatHub\.Error\.NotSupported:\s*(.*)$/s.exec(e.message);
    return m ? m[1] || method : null;
}

export class DBusProvider extends ChatProvider {
    // Create a provider for a running backend. Throws when it does not answer.
    static async create(bus, busName) {
        const [owner] = (await busCall(bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetNameOwner', new GLib.Variant('(s)', [busName]), '(s)')).deepUnpack();
        const [json] = (await busCall(bus, owner, OBJECT_PATH, IFACE_NAME, 'GetInfo', null, '(s)')).deepUnpack();
        const info = JSON.parse(json);
        return new DBusProvider(bus, busName, owner, info);
    }

    constructor(bus, busName, owner, info) {
        super({
            id: info.id || busName,
            name: info.name || busName.slice(BUS_PREFIX.length + 1),
            protocol: info.protocol || 'dbus',
            capabilities: info.capabilities ?? {},
        });
        this.busName = busName;
        this.owner = owner;
        this._bus = bus;
        if (info.self)
            this.self = {id: String(info.self.id ?? ''), name: String(info.self.name ?? '')};
        this._initialState = info.state || ProviderState.CONNECTED;
        this._initialError = info.error || '';
        this._sub = bus.signal_subscribe(owner, IFACE_NAME, 'Event', OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE, (_c, _s, _p, _i, _n, params) => {
                const [kind, payload] = params.deepUnpack();
                let data;
                try {
                    data = JSON.parse(payload || 'null');
                } catch (e) {
                    logError(e, `Chat Hub: bad event from ${busName}`);
                    return;
                }
                try {
                    this._onEvent(kind, data);
                } catch (e) {
                    logError(e, `Chat Hub: cannot handle "${kind}" from ${busName}`);
                }
            });
    }

    _onEvent(kind, d) {
        switch (kind) {
        case 'state':
            this.setState(d.state, d.error ?? '');
            break;
        case 'self':
            this.setSelf(d);
            break;
        case 'info':
            if (d.capabilities)
                Object.assign(this.capabilities, d.capabilities);
            if (d.name)
                this.name = d.name;
            if (d.self)
                this.setSelf(d.self);
            else
                this.emit('self-changed', this.self);
            break;
        case 'contacts':
            this.resetContacts(d ?? []);
            break;
        case 'contact':
            this.updateContact(d);
            break;
        case 'contact-removed':
            this.removeContact(d.id);
            break;
        case 'message':
            this.emit('message', normalizeMessage(d));
            break;
        case 'message-status':
            this.updateMessageStatus(d.contactId, d.id, d.status);
            break;
        case 'typing':
            this.setTypingState(d.contactId, d.typing);
            break;
        case 'call':
            this.updateCall(d);
            break;
        default:
            console.debug(`Chat Hub: unknown event "${kind}" from ${this.busName}`);
        }
    }

    async _call(method, sig, args, replyType = null) {
        try {
            const res = await busCall(this._bus, this.owner, OBJECT_PATH, IFACE_NAME, method,
                sig ? new GLib.Variant(sig, args) : null, replyType);
            return replyType ? res.deepUnpack()[0] : null;
        } catch (e) {
            const what = notSupported(e, method);
            if (what)
                throw new NotSupportedError(what);
            throw new Error(cleanError(e));
        }
    }

    async _json(method, sig, args) {
        const s = await this._call(method, sig, args, '(s)');
        return s ? JSON.parse(s) : null;
    }

    async start() {
        this.setState(this._initialState, this._initialError);
    }

    destroy() {
        if (this._sub) {
            this._bus.signal_unsubscribe(this._sub);
            this._sub = 0;
        }
        super.destroy();
    }

    async getContacts() {
        return (await this._json('GetContacts')) ?? [];
    }

    async getHistory(contactId, {before = 0, limit = 50} = {}) {
        return (await this._json('GetHistory', '(sxu)', [contactId, before, limit])) ?? [];
    }

    async sendMessage(contactId, text) {
        return this._json('SendMessage', '(ss)', [contactId, text]);
    }

    async startCall(contactId, {video = false} = {}) {
        const call = await this._json('StartCall', '(sb)', [contactId, video]);
        return call?.id ? normalizeCall({contactId, video, state: 'ringing-out', ...call}) : call;
    }

    answerCall(callId) {
        return this._call('AnswerCall', '(s)', [callId]);
    }

    endCall(callId) {
        return this._call('EndCall', '(s)', [callId]);
    }

    setMuted(callId, muted) {
        return this._call('SetMuted', '(sb)', [callId, muted]);
    }

    setPresence(presence, statusText = '') {
        return this._call('SetPresence', '(ss)', [presence, statusText]);
    }

    setTyping(contactId, typing) {
        return this._call('SetTyping', '(sb)', [contactId, typing]);
    }
}

// Watches the session bus for backends and tells the caller when one
// appears or goes away.
export class BackendWatcher {
    constructor({onAdded, onRemoved, autostart = true, bus = Gio.DBus.session}) {
        this._bus = bus;
        this._onAdded = onAdded;
        this._onRemoved = onRemoved;
        this._known = new Map(); // busName -> owner
        this._sub = bus.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
            'NameOwnerChanged', '/org/freedesktop/DBus', BUS_PREFIX,
            Gio.DBusSignalFlags.MATCH_ARG0_NAMESPACE, (_c, _s, _p, _i, _n, params) => {
                const [name, , newOwner] = params.deepUnpack();
                this._changed(name, newOwner);
            });
        this._scan(autostart).catch(e => logError(e, 'Chat Hub: cannot list backends'));
    }

    static isBackendName(name) {
        return name.startsWith(`${BUS_PREFIX}.`) && name.length > BUS_PREFIX.length + 1;
    }

    async _scan(autostart) {
        const bus = this._bus;
        const call = (method, params = null) => busCall(bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', method, params, '(as)');
        const [names] = (await call('ListNames')).deepUnpack();
        for (const name of names.filter(BackendWatcher.isBackendName))
            this._changed(name, 'scan');
        if (!autostart)
            return;
        // Start backends installed as D-Bus services (a .service file).
        const [activatable] = (await call('ListActivatableNames')).deepUnpack();
        for (const name of activatable.filter(BackendWatcher.isBackendName)) {
            if (names.includes(name))
                continue;
            bus.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'StartServiceByName', new GLib.Variant('(su)', [name, 0]), null,
                Gio.DBusCallFlags.NONE, 60000, null, (c, r) => {
                    try {
                        c.call_finish(r);
                    } catch (e) {
                        console.warn(`Chat Hub: cannot start ${name}: ${e.message}`);
                    }
                });
        }
    }

    _changed(name, newOwner) {
        if (!BackendWatcher.isBackendName(name) || !this._sub)
            return;
        if (this._known.has(name)) {
            this._known.delete(name);
            this._onRemoved(name);
        }
        if (newOwner) {
            this._known.set(name, newOwner);
            this._onAdded(name);
        }
    }

    destroy() {
        if (this._sub) {
            this._bus.signal_unsubscribe(this._sub);
            this._sub = 0;
        }
        this._known.clear();
    }
}
