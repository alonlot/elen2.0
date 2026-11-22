// Chat Hub provider API.
//
// A provider connects Chat Hub to one chat network (Jabber/XMPP, a company
// chat, a bot, ...). To add a network, write a class that extends
// ChatProvider and implement the methods you need. Call the protected
// helpers (this.receiveMessage(), this.updateContact(), ...) when the
// network tells you something new. Chat Hub does the rest: UI, local
// history, unread counts, notifications and the call window.
//
// This file has no GNOME Shell imports, so you can test providers with
// plain `gjs -m`.
//
// Data types (plain objects):
//
//   Contact  { id, name, presence, statusText?, avatar? }
//            presence: 'online' | 'away' | 'busy' | 'offline'
//            avatar:   absolute path to an image file, or null
//
//   Message  { id, contactId, direction, text, time, status?, senderName? }
//            direction: 'in' | 'out'
//            time:      milliseconds since the epoch
//            status:    'sending' | 'sent' | 'delivered' | 'read' | 'failed'
//
//   Call     { id, contactId, video, state, startedAt?, muted? }
//            state: 'ringing-in' | 'ringing-out' | 'active' | 'ended'
//
//   Self     { id, name }   the user on this account

export const Presence = Object.freeze({
    ONLINE: 'online',
    AWAY: 'away',
    BUSY: 'busy',
    OFFLINE: 'offline',
});

export const PRESENCE_ORDER = ['online', 'busy', 'away', 'offline'];

export const ProviderState = Object.freeze({
    DISCONNECTED: 'disconnected',
    CONNECTING: 'connecting',
    CONNECTED: 'connected',
    ERROR: 'error',
});

export const CallState = Object.freeze({
    RINGING_IN: 'ringing-in',
    RINGING_OUT: 'ringing-out',
    ACTIVE: 'active',
    ENDED: 'ended',
});

// Error a provider throws for a feature it does not have.
export class NotSupportedError extends Error {
    constructor(what) {
        super(`This account does not support ${String(what).toLowerCase()}`);
        this.name = 'NotSupportedError';
    }
}

// Small event emitter. GNOME Shell has its own, but this one also works
// outside the shell (tests, tools).
export class Emitter {
    constructor() {
        this._handlers = new Map();
        this._nextId = 1;
    }

    connect(name, callback) {
        const id = this._nextId++;
        this._handlers.set(id, {name, callback});
        return id;
    }

    disconnect(id) {
        this._handlers.delete(id);
    }

    disconnectAll() {
        this._handlers.clear();
    }

    emit(name, ...args) {
        for (const {name: n, callback} of [...this._handlers.values()]) {
            if (n !== name)
                continue;
            try {
                callback(this, ...args);
            } catch (e) {
                logError(e, `Chat Hub: handler for "${name}" failed`);
            }
        }
    }
}

let _counter = 0;
export function makeId(prefix = 'm') {
    _counter = (_counter + 1) % 1e6;
    return `${prefix}-${Date.now().toString(36)}-${_counter.toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

const PRESENCES = new Set(Object.values(Presence));
const STATUSES = new Set(['sending', 'sent', 'delivered', 'read', 'failed']);
const CALL_STATES = new Set(Object.values(CallState));

export function normalizeContact(c) {
    if (!c || c.id === undefined || c.id === null)
        throw new Error('Contact needs an id');
    const id = String(c.id);
    return {
        id,
        name: String(c.name || id),
        presence: PRESENCES.has(c.presence) ? c.presence : Presence.OFFLINE,
        statusText: c.statusText ? String(c.statusText) : '',
        avatar: c.avatar ? String(c.avatar) : null,
    };
}

export function normalizeMessage(m) {
    if (!m || !m.contactId)
        throw new Error('Message needs a contactId');
    return {
        id: String(m.id || makeId()),
        contactId: String(m.contactId),
        direction: m.direction === 'out' ? 'out' : 'in',
        text: String(m.text ?? ''),
        time: Number.isFinite(Number(m.time)) && Number(m.time) > 0 ? Number(m.time) : Date.now(),
        status: STATUSES.has(m.status) ? m.status : (m.direction === 'out' ? 'sent' : 'delivered'),
        senderName: m.senderName ? String(m.senderName) : null,
    };
}

export function normalizeCall(c) {
    if (!c || !c.id || !c.contactId)
        throw new Error('Call needs an id and a contactId');
    return {
        id: String(c.id),
        contactId: String(c.contactId),
        video: !!c.video,
        state: CALL_STATES.has(c.state) ? c.state : CallState.ENDED,
        startedAt: c.startedAt ? Number(c.startedAt) : null,
        muted: !!c.muted,
    };
}

// Base class for all providers.
//
// Signals (connect with provider.connect(name, (provider, ...args) => {})):
//   'state-changed'    (state, errorText)
//   'contacts-reset'   (contacts[])        full list replaced
//   'contact-changed'  (contact)           one contact added or changed
//   'contact-removed'  (contactId)
//   'message'          (message)           new incoming or outgoing message
//   'message-status'   (contactId, messageId, status)
//   'typing'           (contactId, isTyping)
//   'call-changed'     (call)
//   'self-changed'     (self)
export class ChatProvider extends Emitter {
    // id:       unique and stable, for example 'jabber:me@example.org'
    // name:     shown in the UI, for example 'Jabber'
    // protocol: short tag, for example 'xmpp'
    constructor({id, name, protocol = 'custom', capabilities = {}} = {}) {
        super();
        if (!id)
            throw new Error('Provider needs an id');
        this.id = String(id);
        this.name = String(name || id);
        this.protocol = String(protocol);
        this.capabilities = {
            messages: true,
            calls: false,
            video: false,
            history: false,
            typing: false,
            presence: true,
            ...capabilities,
        };
        this.state = ProviderState.DISCONNECTED;
        this.errorText = '';
        this.self = {id: '', name: ''};
    }

    // ---- Lifecycle. Override. -------------------------------------------
    // (connect()/disconnect() are the signal methods, so these are start/stop.)

    // Log in to the network. Call setState('connected') when ready, or
    // throw. If start() returns without setting a state, Chat Hub
    // takes the account as connected.
    async start() {
        this.setState(ProviderState.CONNECTED);
    }

    // Called when the extension is disabled. Log out and free resources.
    destroy() {
        this.disconnectAll();
    }

    // ---- Data. Override. ------------------------------------------------

    // Return Contact[].
    async getContacts() {
        return [];
    }

    // Return older messages for one contact, oldest first. Only called when
    // capabilities.history is true. `before` is a time in ms (or 0 = now).
    async getHistory(_contactId, {before: _before = 0, limit: _limit = 50} = {}) {
        return [];
    }

    // ---- Actions. Override what your network supports. -------------------

    // Send a text. Return the Message as sent (at least its id), or throw.
    async sendMessage(_contactId, _text) {
        throw new NotSupportedError('Sending messages');
    }

    // Start a call. Return the Call object (state 'ringing-out' or 'active').
    async startCall(_contactId, {video: _video = false} = {}) {
        throw new NotSupportedError('Calls');
    }

    async answerCall(_callId) {
        throw new NotSupportedError('Calls');
    }

    // Hang up or decline.
    async endCall(_callId) {
        throw new NotSupportedError('Calls');
    }

    async setMuted(_callId, _muted) {
        throw new NotSupportedError('Mute');
    }

    async setPresence(_presence, _statusText = '') {
        throw new NotSupportedError('Presence');
    }

    // Tell the other side that the user is typing. Optional.
    async setTyping(_contactId, _typing) {}

    // ---- Helpers for implementers. Call these, do not override. ---------

    setState(state, errorText = '') {
        this.state = state;
        this.errorText = errorText;
        this.emit('state-changed', state, errorText);
    }

    setSelf(self) {
        this.self = {id: String(self.id ?? ''), name: String(self.name ?? '')};
        this.emit('self-changed', this.self);
    }

    resetContacts(contacts) {
        this.emit('contacts-reset', contacts.map(normalizeContact));
    }

    updateContact(contact) {
        this.emit('contact-changed', normalizeContact(contact));
    }

    removeContact(contactId) {
        this.emit('contact-removed', String(contactId));
    }

    // A message arrived from the network.
    receiveMessage(message) {
        this.emit('message', normalizeMessage({direction: 'in', ...message}));
    }

    // A message was sent from another device of the same user.
    // Chat Hub shows it as outgoing.
    echoMessage(message) {
        this.emit('message', normalizeMessage({...message, direction: 'out'}));
    }

    updateMessageStatus(contactId, messageId, status) {
        this.emit('message-status', String(contactId), String(messageId), status);
    }

    setTypingState(contactId, typing) {
        this.emit('typing', String(contactId), !!typing);
    }

    updateCall(call) {
        this.emit('call-changed', normalizeCall(call));
    }
}

// ---- Registry of provider types. ---------------------------------------
//
// registerProvider('demo', settings => new DemoProvider(settings));
//
// The extension creates one instance of each registered type that is
// enabled. External programs do not need this: they use the D-Bus API
// (see docs/API.md) and Chat Hub finds them by themselves.

const _factories = new Map();

export function registerProvider(type, factory) {
    _factories.set(type, factory);
}

export function unregisterProvider(type) {
    _factories.delete(type);
}

export function providerTypes() {
    return [..._factories.keys()];
}

export function createProvider(type, ...args) {
    const factory = _factories.get(type);
    if (!factory)
        throw new Error(`Unknown provider type "${type}"`);
    return factory(...args);
}
