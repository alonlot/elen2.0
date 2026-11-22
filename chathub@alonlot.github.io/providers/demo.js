// Demo account. It shows what a provider looks like and lets you try the
// UI without a real network. Contacts answer, type and accept calls.
// Write "call me" to any contact to get an incoming call.
import GLib from 'gi://GLib';

import {CallState, ChatProvider, ProviderState, makeId} from '../lib/api.js';

const CONTACTS = [
    {id: 'maya', name: 'Maya Levi', presence: 'online', statusText: 'Designing things ✏️'},
    {id: 'omer', name: 'Omer Katz', presence: 'busy', statusText: 'In a meeting until 16:00'},
    {id: 'noa', name: 'Noa Ben-David', presence: 'away', statusText: 'Back soon'},
    {id: 'daniel', name: 'Daniel Cohen', presence: 'online', statusText: ''},
    {id: 'echo', name: 'Echo Bot', presence: 'online', statusText: 'I repeat what you say'},
    {id: 'yael', name: 'Yael Friedman', presence: 'offline', statusText: ''},
];

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

// Seed history, as [contactId, direction, text, age in ms].
const SEED = [
    ['maya', 'in', 'Hi! Did you see the new mockups?', DAY + 3 * HOUR],
    ['maya', 'out', 'Yes, they look great. The dark theme is very clean.', DAY + 3 * HOUR - 4 * MIN],
    ['maya', 'in', 'Thanks 😊 I will send the final files tomorrow', DAY + 2 * HOUR],
    ['maya', 'in', 'Can we talk about the icons later?', 25 * MIN],
    ['omer', 'out', 'Lunch at 13:00?', 2 * DAY],
    ['omer', 'in', 'Sure, the usual place', 2 * DAY - 10 * MIN],
    ['noa', 'in', 'The build passed on all machines 🎉', 5 * HOUR],
    ['noa', 'out', 'Excellent, thank you!', 5 * HOUR - 2 * MIN],
    ['daniel', 'in', 'Are you coming to the meetup on Thursday?', 3 * DAY],
];

const REPLIES = [
    'Sounds good 👍',
    'Let me check and come back to you.',
    'Haha, true 😄',
    'OK, I will do it now.',
    'Can we talk about it tomorrow?',
    'Perfect, thanks!',
];

export class DemoProvider extends ChatProvider {
    constructor() {
        super({
            id: 'demo',
            name: 'Demo',
            protocol: 'demo',
            capabilities: {calls: true, video: true, history: true, typing: true, presence: true},
        });
        this._timers = new Set();
        this._contacts = CONTACTS.map(c => ({...c}));
        this._calls = new Map();
        const now = Date.now();
        this._seed = SEED.map(([contactId, direction, text, age], i) => ({
            id: `demo-seed-${i}`, contactId, direction, text, time: now - age,
            status: direction === 'out' ? 'read' : 'delivered',
        }));
    }

    _later(ms, fn) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._timers.delete(id);
            fn();
            return GLib.SOURCE_REMOVE;
        });
        this._timers.add(id);
    }

    async start() {
        this.setState(ProviderState.CONNECTING);
        this.setSelf({id: 'me@demo', name: GLib.get_real_name() || GLib.get_user_name()});
        await new Promise(r => this._later(300, r));
        this.setState(ProviderState.CONNECTED);
        // Someone comes online a bit later, like on a real network.
        this._later(20000, () => this.updateContact({...this._contacts[5], presence: 'online'}));
    }

    destroy() {
        for (const id of this._timers)
            GLib.source_remove(id);
        this._timers.clear();
        super.destroy();
    }

    async getContacts() {
        return this._contacts;
    }

    async getHistory(contactId, {before = 0, limit = 50} = {}) {
        return this._seed
            .filter(m => m.contactId === contactId && (!before || m.time < before))
            .slice(-limit);
    }

    async sendMessage(contactId, text) {
        const id = makeId('demo');
        await new Promise(r => this._later(250, r));
        const contact = this._contacts.find(c => c.id === contactId);
        if (contact?.presence === 'offline')
            this._later(1500, () => this.updateMessageStatus(contactId, id, 'delivered'));
        else
            this._reply(contactId, id, text);
        return {id, status: 'sent'};
    }

    _reply(contactId, id, text) {
        this._later(700, () => this.updateMessageStatus(contactId, id, 'delivered'));
        this._later(1400, () => {
            this.updateMessageStatus(contactId, id, 'read');
            this.setTypingState(contactId, true);
        });
        this._later(3200, () => {
            this.setTypingState(contactId, false);
            let answer;
            if (contactId === 'echo')
                answer = text;
            else if (/call me/i.test(text))
                answer = 'Calling you now…';
            else
                answer = REPLIES[Math.floor(Math.random() * REPLIES.length)];
            this.receiveMessage({id: makeId('demo-in'), contactId, text: answer});
            if (/call me/i.test(text))
                this._later(1200, () => this._incoming(contactId));
        });
    }

    _incoming(contactId) {
        const call = {id: makeId('call'), contactId, video: false, state: CallState.RINGING_IN};
        this._calls.set(call.id, call);
        this.updateCall(call);
        // Stop ringing after 30 s.
        this._later(30000, () => {
            if (this._calls.get(call.id)?.state === CallState.RINGING_IN)
                this.endCall(call.id);
        });
    }

    async startCall(contactId, {video = false} = {}) {
        const call = {id: makeId('call'), contactId, video, state: CallState.RINGING_OUT};
        this._calls.set(call.id, call);
        this.updateCall(call);
        const contact = this._contacts.find(c => c.id === contactId);
        this._later(2500, () => {
            const c = this._calls.get(call.id);
            if (!c || c.state !== CallState.RINGING_OUT)
                return;
            if (contact?.presence === 'offline' || contact?.presence === 'busy') {
                this._calls.delete(call.id);
                this.updateCall({...c, state: CallState.ENDED});
            } else {
                c.state = CallState.ACTIVE;
                this.updateCall(c);
            }
        });
        return call;
    }

    async answerCall(callId) {
        const c = this._calls.get(callId);
        if (c) {
            c.state = CallState.ACTIVE;
            this.updateCall(c);
        }
    }

    async endCall(callId) {
        const c = this._calls.get(callId);
        if (c) {
            this._calls.delete(callId);
            this.updateCall({...c, state: CallState.ENDED});
        }
    }

    async setMuted(callId, muted) {
        const c = this._calls.get(callId);
        if (c)
            c.muted = muted;
    }

    async setPresence(_presence, _statusText) {}
}
