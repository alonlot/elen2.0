// ChatManager joins all providers (accounts) into one list of conversations.
// It keeps the local history, unread counts, typing state and calls.
// The UI only talks to this class, never to a provider directly.
//
// Signals:
//   'accounts-changed'      ()
//   'conversations-changed' ()                 list, order or a contact changed
//   'message'               (conv, message)    a new message in a conversation
//   'message-updated'       (conv, message, oldId)
//   'history-loaded'        (conv)
//   'typing'                (conv)
//   'unread-changed'        (total)
//   'call-changed'          (call, conv)
//   'error'                 (text)
import {CallState, Emitter, ProviderState, makeId, normalizeMessage} from './api.js';

const PRESENCE_RANK = {online: 0, busy: 1, away: 2, offline: 3};
const PAGE = 50;

export function conversationKey(accountId, contactId) {
    return JSON.stringify([accountId, contactId]);
}

export class ChatManager extends Emitter {
    constructor({history = null, historyLimit = 500} = {}) {
        super();
        this._history = history;
        this._historyLimit = historyLimit;
        this._providers = new Map(); // id -> {provider, ids: []}
        this._convs = new Map(); // key -> conversation
        this._calls = new Map(); // callId -> {call, accountId}
        this._declined = new Set();
        this._activeKey = null;
    }

    // ---- Accounts ------------------------------------------------------

    addProvider(provider) {
        if (this._providers.has(provider.id))
            this.removeProvider(provider.id);
        const ids = [
            provider.connect('state-changed', () => {
                this.emit('accounts-changed');
                if (provider.state === ProviderState.CONNECTED)
                    this._reloadContacts(provider);
            }),
            provider.connect('self-changed', () => this.emit('accounts-changed')),
            provider.connect('contacts-reset', (_p, contacts) => this._resetContacts(provider, contacts)),
            provider.connect('contact-changed', (_p, contact) => this._upsertContact(provider, contact, true)),
            provider.connect('contact-removed', (_p, contactId) => this._removeContact(provider, contactId)),
            provider.connect('message', (_p, msg) => this._onProviderMessage(provider, msg)),
            provider.connect('message-status', (_p, contactId, id, status) =>
                this._onMessageStatus(provider, contactId, id, status)),
            provider.connect('typing', (_p, contactId, typing) => this._onTyping(provider, contactId, typing)),
            provider.connect('call-changed', (_p, call) => this._onCall(provider, call)),
        ];
        this._providers.set(provider.id, {provider, ids});
        this.emit('accounts-changed');
        return (async () => {
            try {
                await provider.start();
                if (provider.state === ProviderState.DISCONNECTED)
                    provider.setState(ProviderState.CONNECTED);
            } catch (e) {
                provider.setState(ProviderState.ERROR, e.message ?? String(e));
                this.emit('error', `${provider.name}: ${e.message ?? e}`);
            }
        })();
    }

    removeProvider(id) {
        const entry = this._providers.get(id);
        if (!entry)
            return;
        this._providers.delete(id);
        for (const sid of entry.ids)
            entry.provider.disconnect(sid);
        try {
            entry.provider.destroy();
        } catch (e) {
            logError(e);
        }
        for (const [key, conv] of this._convs) {
            if (conv.accountId === id)
                this._convs.delete(key);
        }
        for (const [callId, c] of this._calls) {
            if (c.accountId === id) {
                this._calls.delete(callId);
                this.emit('call-changed', {...c.call, state: CallState.ENDED}, null);
            }
        }
        this.emit('accounts-changed');
        this.emit('conversations-changed');
        this._emitUnread();
    }

    get accounts() {
        return [...this._providers.values()].map(({provider: p}) => ({
            id: p.id,
            name: p.name,
            protocol: p.protocol,
            state: p.state,
            errorText: p.errorText,
            self: p.self,
            capabilities: p.capabilities,
        }));
    }

    account(id) {
        return this.accounts.find(a => a.id === id) ?? null;
    }

    provider(id) {
        return this._providers.get(id)?.provider ?? null;
    }

    async setPresence(presence, statusText = '') {
        for (const {provider} of this._providers.values()) {
            if (!provider.capabilities.presence || provider.state !== ProviderState.CONNECTED)
                continue;
            try {
                await provider.setPresence(presence, statusText);
            } catch (e) {
                if (e.name !== 'NotSupportedError')
                    this.emit('error', `${provider.name}: ${e.message ?? e}`);
            }
        }
    }

    // ---- Conversations -------------------------------------------------

    _newConv(provider, contact) {
        return {
            key: conversationKey(provider.id, contact.id),
            accountId: provider.id,
            accountName: provider.name,
            contact,
            messages: [],
            loaded: false,
            loading: null,
            hasMore: false,
            unread: 0,
            typing: false,
            lastTime: 0,
            lastText: '',
            lastDirection: null,
        };
    }

    async _reloadContacts(provider) {
        try {
            const contacts = await provider.getContacts();
            this._resetContacts(provider, contacts.map(c => ({...c})));
        } catch (e) {
            this.emit('error', `${provider.name}: ${e.message ?? e}`);
        }
    }

    _resetContacts(provider, contacts) {
        const keep = new Set();
        for (const c of contacts) {
            const conv = this._upsertContact(provider, c, false);
            if (conv)
                keep.add(conv.key);
        }
        for (const [key, conv] of this._convs) {
            // Keep conversations with messages even if the contact left the roster.
            if (conv.accountId === provider.id && !keep.has(key) && conv.messages.length === 0 && conv.lastTime === 0)
                this._convs.delete(key);
        }
        this.emit('conversations-changed');
    }

    _upsertContact(provider, contact, notify) {
        if (!contact?.id)
            return null;
        const key = conversationKey(provider.id, contact.id);
        let conv = this._convs.get(key);
        if (conv) {
            conv.contact = {...conv.contact, ...contact};
        } else {
            conv = this._newConv(provider, contact);
            this._convs.set(key, conv);
            this._preview(conv);
        }
        if (notify)
            this.emit('conversations-changed');
        return conv;
    }

    _removeContact(provider, contactId) {
        const key = conversationKey(provider.id, contactId);
        const conv = this._convs.get(key);
        if (conv && conv.lastTime === 0) {
            this._convs.delete(key);
            this.emit('conversations-changed');
        }
    }

    // Read the last message from local history so the list can show a preview.
    async _preview(conv) {
        if (!this._history)
            return;
        try {
            const msgs = await this._history.load(conv.accountId, conv.contact.id, 1);
            const last = msgs[msgs.length - 1];
            if (last && last.time > conv.lastTime) {
                this._setLast(conv, last);
                this.emit('conversations-changed');
            }
        } catch (e) {
            logError(e);
        }
    }

    _setLast(conv, m) {
        conv.lastTime = m.time;
        conv.lastText = m.text;
        conv.lastDirection = m.direction;
    }

    _ensureConv(provider, contactId) {
        const key = conversationKey(provider.id, contactId);
        let conv = this._convs.get(key);
        if (!conv) {
            conv = this._newConv(provider, {id: contactId, name: contactId, presence: 'offline', statusText: '', avatar: null});
            this._convs.set(key, conv);
            this.emit('conversations-changed');
        }
        return conv;
    }

    // Sorted for the contact list: recent chats first, then by presence and name.
    get conversations() {
        return [...this._convs.values()].sort((a, b) => {
            if (a.lastTime !== b.lastTime)
                return b.lastTime - a.lastTime;
            const pa = PRESENCE_RANK[a.contact.presence] ?? 9;
            const pb = PRESENCE_RANK[b.contact.presence] ?? 9;
            if (pa !== pb)
                return pa - pb;
            return a.contact.name.localeCompare(b.contact.name);
        });
    }

    conversation(key) {
        return this._convs.get(key) ?? null;
    }

    findConversation(accountId, contactId) {
        return this._convs.get(conversationKey(accountId, contactId)) ?? null;
    }

    // The conversation the user looks at now. Its messages do not count as unread.
    setActive(key) {
        this._activeKey = key;
        if (key)
            this.markRead(key);
    }

    get activeKey() {
        return this._activeKey;
    }

    // Load the conversation history (local store first, then the provider).
    loadHistory(key) {
        const conv = this._convs.get(key);
        if (!conv)
            return Promise.resolve(null);
        if (conv.loaded)
            return Promise.resolve(conv);
        if (conv.loading)
            return conv.loading;
        conv.loading = (async () => {
            let msgs = [];
            if (this._history) {
                try {
                    msgs = await this._history.load(conv.accountId, conv.contact.id, this._historyLimit);
                } catch (e) {
                    logError(e);
                }
            }
            const provider = this.provider(conv.accountId);
            if (msgs.length === 0 && provider?.capabilities.history) {
                try {
                    const remote = (await provider.getHistory(conv.contact.id, {before: 0, limit: PAGE}))
                        .map(m => normalizeMessage({contactId: conv.contact.id, ...m}));
                    for (const m of remote)
                        this._history?.append(conv.accountId, m);
                    msgs = remote;
                    conv.hasMore = remote.length >= PAGE;
                } catch (e) {
                    logError(e);
                }
            }
            this._mergeMessages(conv, msgs);
            conv.loaded = true;
            conv.loading = null;
            this.emit('history-loaded', conv);
            return conv;
        })();
        return conv.loading;
    }

    // Get older messages from the provider (only for accounts with server history).
    async loadEarlier(key) {
        const conv = this._convs.get(key);
        const provider = conv && this.provider(conv.accountId);
        if (!conv || !provider?.capabilities.history)
            return 0;
        const before = conv.messages[0]?.time ?? 0;
        const older = (await provider.getHistory(conv.contact.id, {before, limit: PAGE}))
            .map(m => normalizeMessage({contactId: conv.contact.id, ...m}));
        const added = this._mergeMessages(conv, older);
        for (const m of older)
            this._history?.append(conv.accountId, m);
        conv.hasMore = older.length >= PAGE;
        this.emit('history-loaded', conv);
        return added;
    }

    _mergeMessages(conv, msgs) {
        const ids = new Set(conv.messages.map(m => m.id));
        let added = 0;
        for (const m of msgs) {
            if (ids.has(m.id))
                continue;
            conv.messages.push(m);
            ids.add(m.id);
            added++;
        }
        conv.messages.sort((a, b) => a.time - b.time);
        const last = conv.messages[conv.messages.length - 1];
        if (last && last.time >= conv.lastTime)
            this._setLast(conv, last);
        return added;
    }

    markRead(key) {
        const conv = this._convs.get(key);
        if (conv && conv.unread) {
            conv.unread = 0;
            this.emit('conversations-changed');
            this._emitUnread();
        }
    }

    get unreadTotal() {
        let n = 0;
        for (const c of this._convs.values())
            n += c.unread;
        return n;
    }

    _emitUnread() {
        this.emit('unread-changed', this.unreadTotal);
    }

    async clearHistory(key) {
        const conv = this._convs.get(key);
        if (!conv)
            return;
        await this._history?.clear(conv.accountId, conv.contact.id);
        conv.messages = [];
        conv.lastTime = 0;
        conv.lastText = '';
        conv.unread = 0;
        this.emit('history-loaded', conv);
        this.emit('conversations-changed');
        this._emitUnread();
    }

    async clearAllHistory() {
        await this._history?.flush();
        this._history?.clearAll();
        for (const conv of this._convs.values()) {
            conv.messages = [];
            conv.loaded = false;
            conv.lastTime = 0;
            conv.lastText = '';
            conv.unread = 0;
        }
        for (const conv of this._convs.values())
            this.emit('history-loaded', conv);
        this.emit('conversations-changed');
        this._emitUnread();
    }

    // ---- Messages ------------------------------------------------------

    _onProviderMessage(provider, msg) {
        const conv = this._ensureConv(provider, msg.contactId);
        const existing = conv.messages.find(m => m.id === msg.id);
        if (existing) {
            Object.assign(existing, msg);
            this.emit('message-updated', conv, existing, existing.id);
            return;
        }
        conv.messages.push(msg);
        this._setLast(conv, msg);
        if (conv.typing && msg.direction === 'in')
            conv.typing = false;
        if (msg.direction === 'in' && this._activeKey !== conv.key) {
            conv.unread++;
            this._emitUnread();
        }
        this._history?.append(provider.id, msg);
        this.emit('message', conv, msg);
        this.emit('conversations-changed');
    }

    _onMessageStatus(provider, contactId, id, status) {
        const conv = this.findConversation(provider.id, contactId);
        const m = conv?.messages.find(x => x.id === id);
        if (!m || m.status === status)
            return;
        m.status = status;
        this._history?.setStatus(provider.id, contactId, id, status);
        this.emit('message-updated', conv, m, id);
    }

    async send(key, text) {
        text = String(text ?? '').trim();
        const conv = this._convs.get(key);
        const provider = conv && this.provider(conv.accountId);
        if (!text || !conv || !provider)
            return null;
        const local = normalizeMessage({
            id: makeId('local'),
            contactId: conv.contact.id,
            direction: 'out',
            text,
            time: Date.now(),
            status: 'sending',
        });
        conv.messages.push(local);
        this._setLast(conv, local);
        this.emit('message', conv, local);
        this.emit('conversations-changed');
        await this._deliver(conv, provider, local);
        return local;
    }

    async _deliver(conv, provider, local) {
        const oldId = local.id;
        try {
            if (provider.state !== ProviderState.CONNECTED)
                throw new Error(`${provider.name} is not connected`);
            const sent = await provider.sendMessage(conv.contact.id, local.text);
            const finalId = sent?.id ? String(sent.id) : local.id;
            const dup = conv.messages.find(m => m !== local && m.id === finalId);
            if (dup) {
                // The provider already reported this message through a signal.
                conv.messages.splice(conv.messages.indexOf(local), 1);
                this.emit('message-updated', conv, dup, oldId);
                return;
            }
            local.id = finalId;
            local.status = sent?.status && sent.status !== 'sending' ? sent.status : 'sent';
            if (sent?.time)
                local.time = Number(sent.time);
        } catch (e) {
            local.status = 'failed';
            this.emit('error', `${provider.name}: ${e.message ?? e}`);
        }
        this._history?.append(conv.accountId, local);
        this.emit('message-updated', conv, local, oldId);
    }

    // Send a failed message again.
    async retry(key, messageId) {
        const conv = this._convs.get(key);
        const provider = conv && this.provider(conv.accountId);
        const m = conv?.messages.find(x => x.id === messageId && x.status === 'failed');
        if (!m || !provider)
            return;
        const oldId = m.id;
        m.status = 'sending';
        m.id = makeId('local');
        this._history?.setStatus(conv.accountId, conv.contact.id, oldId, 'retried');
        this.emit('message-updated', conv, m, oldId);
        await this._deliver(conv, provider, m);
    }

    setTyping(key, typing) {
        const conv = this._convs.get(key);
        const provider = conv && this.provider(conv.accountId);
        if (provider?.capabilities.typing && provider.state === ProviderState.CONNECTED)
            provider.setTyping(conv.contact.id, typing).catch(() => {});
    }

    _onTyping(provider, contactId, typing) {
        const conv = this.findConversation(provider.id, contactId);
        if (!conv || conv.typing === typing)
            return;
        conv.typing = typing;
        this.emit('typing', conv);
    }

    // ---- Calls ---------------------------------------------------------

    get calls() {
        return [...this._calls.values()].map(c => c.call);
    }

    callForConversation(key) {
        const conv = this._convs.get(key);
        if (!conv)
            return null;
        for (const {call, accountId} of this._calls.values()) {
            if (accountId === conv.accountId && call.contactId === conv.contact.id)
                return call;
        }
        return null;
    }

    conversationForCall(callId) {
        const c = this._calls.get(callId);
        return c ? this.findConversation(c.accountId, c.call.contactId) : null;
    }

    _onCall(provider, call) {
        const conv = this._ensureConv(provider, call.contactId);
        const prev = this._calls.get(call.id)?.call;
        if (call.state === CallState.ACTIVE && !call.startedAt)
            call.startedAt = prev?.startedAt ?? Date.now();
        if (call.state === CallState.ENDED) {
            this._calls.delete(call.id);
            if (prev)
                this._logCall(provider, conv, prev);
        } else {
            this._calls.set(call.id, {call, accountId: provider.id});
        }
        this.emit('call-changed', call, conv);
    }

    // Put a short line in the chat ("Call, 2:31" / "Missed call").
    _logCall(provider, conv, call) {
        let text;
        if (call.state === CallState.ACTIVE && call.startedAt) {
            const s = Math.max(0, Math.round((Date.now() - call.startedAt) / 1000));
            text = `${call.video ? 'Video call' : 'Call'} · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
        } else if (call.state === CallState.RINGING_IN) {
            text = this._declined.has(call.id) ? 'Declined call' : 'Missed call';
            this._declined.delete(call.id);
        } else {
            text = `${call.video ? 'Video call' : 'Call'} · not answered`;
        }
        const m = normalizeMessage({
            id: `call-${call.id}`,
            contactId: conv.contact.id,
            direction: call.state === CallState.RINGING_IN ? 'in' : 'out',
            text: `📞 ${text}`,
            time: Date.now(),
            status: 'read',
        });
        if (conv.messages.some(x => x.id === m.id))
            return;
        conv.messages.push(m);
        this._setLast(conv, m);
        this._history?.append(provider.id, m);
        this.emit('message', conv, m);
        this.emit('conversations-changed');
    }

    async _callAction(callId, fn) {
        const c = this._calls.get(callId);
        const provider = c && this.provider(c.accountId);
        if (!provider)
            return;
        try {
            await fn(provider, c.call);
        } catch (e) {
            this.emit('error', `${provider.name}: ${e.message ?? e}`);
        }
    }

    async startCall(key, {video = false} = {}) {
        const conv = this._convs.get(key);
        const provider = conv && this.provider(conv.accountId);
        if (!provider)
            return null;
        try {
            const call = await provider.startCall(conv.contact.id, {video});
            if (call?.id && !this._calls.has(String(call.id)))
                provider.updateCall({state: CallState.RINGING_OUT, video, contactId: conv.contact.id, ...call});
            return call;
        } catch (e) {
            this.emit('error', `${provider.name}: ${e.message ?? e}`);
            return null;
        }
    }

    answerCall(callId) {
        return this._callAction(callId, p => p.answerCall(callId));
    }

    endCall(callId) {
        if (this._calls.get(callId)?.call.state === CallState.RINGING_IN)
            this._declined.add(callId);
        return this._callAction(callId, p => p.endCall(callId));
    }

    setMuted(callId, muted) {
        return this._callAction(callId, async (p, call) => {
            await p.setMuted(callId, muted);
            if (this._calls.has(callId) && call.muted !== muted)
                p.updateCall({...call, muted});
        });
    }

    destroy() {
        for (const id of [...this._providers.keys()])
            this.removeProvider(id);
        this.disconnectAll();
    }
}
