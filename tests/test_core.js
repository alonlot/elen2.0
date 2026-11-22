// Unit tests for the parts that do not need GNOME Shell.
// Run: gjs -m tests/test_core.js
import GLib from 'gi://GLib';

import {ChatProvider, ProviderState, normalizeMessage, normalizeContact} from '../chathub@alonlot.github.io/lib/api.js';
import {HistoryStore} from '../chathub@alonlot.github.io/lib/history.js';
import {ChatManager, conversationKey} from '../chathub@alonlot.github.io/lib/manager.js';

let failed = 0;
let passed = 0;
async function test(name, fn) {
    try {
        await fn();
        passed++;
        print(`ok   ${name}`);
    } catch (e) {
        failed++;
        print(`FAIL ${name}\n     ${e}\n${e.stack}`);
    }
}
function eq(a, b, msg = '') {
    const ja = JSON.stringify(a), jb = JSON.stringify(b);
    if (ja !== jb)
        throw new Error(`${msg} expected ${jb}, got ${ja}`);
}
function ok(v, msg = 'expected true') {
    if (!v)
        throw new Error(msg);
}
const tick = (ms = 0) => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    r();
    return GLib.SOURCE_REMOVE;
}));

class FakeProvider extends ChatProvider {
    constructor(opts = {}) {
        super({id: 'fake', name: 'Fake', capabilities: {calls: true, history: true, typing: true}});
        this.sent = [];
        this.failNext = false;
        this.remote = opts.remote ?? [];
    }

    async getContacts() {
        return [{id: 'a', name: 'Alice', presence: 'online'}, {id: 'b', name: 'Bob', presence: 'away'}];
    }

    async getHistory(contactId) {
        return this.remote.filter(m => m.contactId === contactId);
    }

    async sendMessage(contactId, text) {
        if (this.failNext) {
            this.failNext = false;
            throw new Error('network down');
        }
        this.sent.push([contactId, text]);
        return {id: `srv-${this.sent.length}`, status: 'sent'};
    }

    async startCall(contactId, {video}) {
        const call = {id: 'c1', contactId, video, state: 'ringing-out'};
        this.updateCall(call);
        return call;
    }

    async endCall(id) {
        this.updateCall({id, contactId: 'a', state: 'ended'});
    }
}

function tmpStore() {
    const dir = GLib.dir_make_tmp('chathub-test-XXXXXX');
    return new HistoryStore(dir);
}

await test('normalize contact and message', () => {
    eq(normalizeContact({id: 5}).name, '5');
    eq(normalizeContact({id: 'x', presence: 'weird'}).presence, 'offline');
    const m = normalizeMessage({contactId: 'a', text: 'hi', direction: 'out'});
    eq(m.status, 'sent');
    ok(m.id && m.time > 0);
});

await test('history store: append, status, load, clear', async () => {
    const s = tmpStore();
    await s.append('acc', {id: '1', contactId: 'a', text: 'one', time: 1000, direction: 'in'});
    await s.append('acc', {id: '2', contactId: 'a', text: 'two', time: 2000, direction: 'out', status: 'sent'});
    await s.setStatus('acc', 'a', '2', 'read');
    let msgs = await s.load('acc', 'a');
    eq(msgs.map(m => m.text), ['one', 'two']);
    eq(msgs[1].status, 'read');
    eq((await s.load('acc', 'a', 1)).map(m => m.id), ['2']);
    eq(await s.load('acc', 'zzz'), []);
    await s.clear('acc', 'a');
    eq(await s.load('acc', 'a'), []);
    s.clearAll();
});

await test('history store: broken line does not lose the rest', async () => {
    const s = tmpStore();
    await s.append('acc', {id: '1', contactId: 'a', text: 'one', time: 1000});
    const path = s._file('acc', 'a').get_path();
    const [, bytes] = GLib.file_get_contents(path);
    GLib.file_set_contents(path, `${new TextDecoder().decode(bytes)}{"broken\n`);
    await s.append('acc', {id: '2', contactId: 'a', text: 'two', time: 2000});
    eq((await s.load('acc', 'a')).map(m => m.id), ['1', '2']);
    s.clearAll();
});

await test('manager: contacts, send, history file, unread', async () => {
    const store = tmpStore();
    const m = new ChatManager({history: store});
    const p = new FakeProvider();
    await m.addProvider(p);
    await tick(10);
    eq(m.conversations.map(c => c.contact.name), ['Alice', 'Bob']);
    const key = conversationKey('fake', 'a');
    await m.loadHistory(key);

    const updates = [];
    m.connect('message-updated', (_m, conv, msg, oldId) => updates.push([msg.id, oldId, msg.status]));
    await m.send(key, '  hello  ');
    eq(p.sent, [['a', 'hello']]);
    eq(m.conversation(key).messages.map(x => [x.id, x.status]), [['srv-1', 'sent']]);
    ok(updates[0][1].startsWith('local-'), 'update carries old local id');

    p.updateMessageStatus('a', 'srv-1', 'read');
    eq(m.conversation(key).messages[0].status, 'read');

    let unread = -1;
    m.connect('unread-changed', (_m, n) => (unread = n));
    await tick(5);
    p.receiveMessage({contactId: 'b', text: 'yo'});
    eq(unread, 1);
    eq(m.conversations[0].contact.name, 'Bob', 'newest chat first');
    m.setActive(conversationKey('fake', 'b'));
    eq(unread, 0);
    p.receiveMessage({contactId: 'b', text: 'while open'});
    eq(m.unreadTotal, 0, 'active conversation does not count unread');

    await store.flush();
    const disk = await store.load('fake', 'a');
    eq(disk.map(x => [x.text, x.status]), [['hello', 'read']]);
    m.destroy();
    store.clearAll();
});

await test('manager: failed send and retry', async () => {
    const m = new ChatManager({history: tmpStore()});
    const p = new FakeProvider();
    await m.addProvider(p);
    await tick(10);
    const key = conversationKey('fake', 'a');
    const errors = [];
    m.connect('error', (_m, t) => errors.push(t));
    p.failNext = true;
    await m.send(key, 'x');
    const msg = m.conversation(key).messages[0];
    eq(msg.status, 'failed');
    ok(errors[0].includes('network down'));
    await m.retry(key, msg.id);
    eq(m.conversation(key).messages.map(x => x.status), ['sent']);
    eq(m.conversation(key).messages.length, 1);
    m.destroy();
});

await test('manager: provider echo of a sent message is not shown twice', async () => {
    const m = new ChatManager();
    const p = new FakeProvider();
    p.sendMessage = async (contactId, text) => {
        p.echoMessage({id: 'same', contactId, text});
        return {id: 'same'};
    };
    await m.addProvider(p);
    await tick(10);
    const key = conversationKey('fake', 'a');
    await m.loadHistory(key);
    await m.send(key, 'hi');
    eq(m.conversation(key).messages.map(x => x.id), ['same']);
    m.destroy();
});

await test('manager: remote history loads when local is empty', async () => {
    const store = tmpStore();
    const m = new ChatManager({history: store});
    const p = new FakeProvider({remote: [{id: 'r1', contactId: 'a', text: 'old', time: 5, direction: 'in'}]});
    await m.addProvider(p);
    await tick(10);
    const conv = await m.loadHistory(conversationKey('fake', 'a'));
    eq(conv.messages.map(x => x.text), ['old']);
    await store.flush();
    eq((await store.load('fake', 'a')).map(x => x.id), ['r1'], 'remote history saved locally');
    m.destroy();
    store.clearAll();
});

await test('manager: calls and call log line', async () => {
    const m = new ChatManager();
    const p = new FakeProvider();
    await m.addProvider(p);
    await tick(10);
    const key = conversationKey('fake', 'a');
    const states = [];
    m.connect('call-changed', (_m, call) => states.push(call.state));
    await m.startCall(key, {video: false});
    eq(m.callForConversation(key)?.id, 'c1');
    p.updateCall({id: 'c1', contactId: 'a', state: 'active'});
    ok(m.calls[0].startedAt > 0, 'active call gets a start time');
    await m.endCall('c1');
    eq(states, ['ringing-out', 'active', 'ended']);
    eq(m.calls.length, 0);
    ok(m.conversation(key).messages.at(-1).text.startsWith('📞 Call'), 'call is logged in the chat');
    m.destroy();
});

await test('manager: provider error state is reported', async () => {
    const m = new ChatManager();
    const P2 = class extends FakeProvider {
        async start() {
            throw new Error('bad password');
        }
    };
    const q = new P2();
    await m.addProvider(q);
    eq(q.state, ProviderState.ERROR);
    eq(m.accounts[0].errorText, 'bad password');
    m.destroy();
});

print(`\n${passed} passed, ${failed} failed`);
if (failed)
    imports.system.exit(1);
