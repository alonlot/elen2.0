// End-to-end Jabber test: Chat Hub (alice, via jabber_backend.py) <-> bot (bob).
// Run: tests/run_jabber_test.sh (needs a local XMPP server with users alice and bob)
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

import {BackendWatcher, DBusProvider} from '../chathub@alonlot.github.io/lib/dbusBridge.js';
import {ChatManager, conversationKey} from '../chathub@alonlot.github.io/lib/manager.js';

const loop = new GLib.MainLoop(null, false);
let failed = 0;
const check = (cond, msg) => {
    print(`${cond ? 'ok  ' : 'FAIL'} ${msg}`);
    if (!cond)
        failed++;
};
const wait = (pred, ms = 15000) => new Promise((resolve, reject) => {
    const start = Date.now();
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 50, () => {
        if (pred()) {
            resolve();
            return GLib.SOURCE_REMOVE;
        }
        if (Date.now() - start > ms) {
            reject(new Error(`timeout waiting for: ${pred}`));
            return GLib.SOURCE_REMOVE;
        }
        return GLib.SOURCE_CONTINUE;
    });
});

async function main() {
    const manager = new ChatManager();
    const watcher = new BackendWatcher({
        autostart: false,
        onAdded: async name => manager.addProvider(await DBusProvider.create(Gio.DBus.session, name)),
        onRemoved: () => {},
    });
    const id = 'xmpp:alice@localhost';
    await wait(() => manager.account(id)?.state === 'connected');
    check(true, 'Jabber account connected');
    check(manager.account(id).self.id === 'alice@localhost', 'own JID reported');
    check(manager.account(id).capabilities.calls === false, 'calls are off for Jabber');
    const key = conversationKey(id, 'bob@localhost');
    await wait(() => manager.conversation(key)?.contact.presence === 'online');
    check(true, 'bob is in the roster and online');
    check(manager.conversation(key).contact.statusText === 'Test bot', 'status text from presence');

    await manager.loadHistory(key);
    let typing = false;
    manager.connect('typing', (_m, c) => {
        if (c.typing)
            typing = true;
    });
    await manager.send(key, 'ping שלום');
    const conv = manager.conversation(key);
    check(conv.messages[0].status === 'sent', 'message sent');
    await wait(() => conv.messages.some(m => m.direction === 'in'));
    const reply = conv.messages.find(m => m.direction === 'in');
    check(reply.text === 'pong: ping שלום', `reply arrives: "${reply.text}"`);
    check(typing, 'typing (XEP-0085) arrives');
    await wait(() => conv.messages[0].status === 'delivered', 5000).catch(() => {});
    check(conv.messages[0].status === 'delivered', 'delivery receipt (XEP-0184) arrives');
    check(!conv.typing, 'typing stops when the reply arrives');

    watcher.destroy();
    manager.destroy();
}

main().catch(e => {
    failed++;
    print(`FAIL ${e}`);
}).finally(() => loop.quit());
loop.run();
print(failed ? `\n${failed} failed` : '\nall passed');
imports.system.exit(failed ? 1 : 0);
