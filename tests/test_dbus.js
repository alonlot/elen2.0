// Integration test: the D-Bus bridge against the Python backends.
// Run: tests/run_dbus_test.sh  (it starts a private session bus)
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
const wait = (pred, ms = 8000) => new Promise((resolve, reject) => {
    const start = Date.now();
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 30, () => {
        if (pred()) {
            resolve();
            return GLib.SOURCE_REMOVE;
        }
        if (Date.now() - start > ms) {
            reject(new Error('timeout'));
            return GLib.SOURCE_REMOVE;
        }
        return GLib.SOURCE_CONTINUE;
    });
});

async function main() {
    const manager = new ChatManager();
    const added = [];
    const watcher = new BackendWatcher({
        autostart: false,
        onAdded: async name => {
            const p = await DBusProvider.create(Gio.DBus.session, name);
            added.push(name);
            await manager.addProvider(p);
        },
        onRemoved: name => {
            added.splice(added.indexOf(name), 1);
            manager.removeProvider(name === 'io.github.alonlot.ChatHub.Backend.Custom' ? 'custom:example' : 'minimal');
        },
    });

    await wait(() => added.length === 2);
    check(true, 'both backends found on the bus');
    await wait(() => manager.conversations.length === 3);
    check(manager.conversations.map(c => c.contact.name).sort().join() === 'Clock Bot,Echo Service,Solo', 'contacts from both backends');
    const acc = manager.account('custom:example');
    check(acc?.name === 'Custom' && acc.capabilities.calls === true && acc.state === 'connected', 'account info and capabilities');

    const key = conversationKey('custom:example', 'echo');
    await manager.loadHistory(key);
    let typingSeen = false;
    manager.connect('typing', (_m, conv) => {
        if (conv.typing)
            typingSeen = true;
    });
    await manager.send(key, 'ping 👋');
    const conv = manager.conversation(key);
    check(conv.messages[0].status === 'sent' && conv.messages[0].id.startsWith('custom-'), 'send returns the backend id');
    await wait(() => conv.messages.length === 2);
    check(conv.messages[1].direction === 'in' && conv.messages[1].text === 'ping 👋', 'echo reply arrives (unicode kept)');
    check(conv.messages[0].status === 'read', 'read status arrives');
    check(typingSeen, 'typing event arrives');

    await manager.startCall(key, {video: false});
    check(manager.callForConversation(key)?.state === 'ringing-out', 'call is ringing');
    await wait(() => manager.callForConversation(key)?.state === 'active');
    check(true, 'call becomes active');
    await manager.endCall(manager.callForConversation(key).id);
    await wait(() => manager.calls.length === 0);
    check(conv.messages.at(-1).text.startsWith('📞 Call'), 'ended call is logged');

    const errors = [];
    manager.connect('error', (_m, t) => errors.push(t));
    const solo = conversationKey('minimal', 'solo');
    await manager.loadHistory(solo);
    await manager.send(solo, 'hello');
    check(manager.conversation(solo).messages[0].status === 'failed', 'unsupported send fails cleanly');
    check(/sending messages/.test(errors[0] ?? ''), `error text is readable: "${errors[0]}"`);
    const call = await manager.startCall(solo);
    check(call === null && /calls/.test(errors[1] ?? ''), `unsupported call reports an error: "${errors[1]}"`);

    // Backend goes away -> account removed.
    const [pid] = Gio.DBus.session.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'GetConnectionUnixProcessID', new GLib.Variant('(s)', ['io.github.alonlot.ChatHub.Backend.Minimal']),
        null, Gio.DBusCallFlags.NONE, -1, null).deepUnpack();
    GLib.spawn_command_line_sync(`kill ${pid}`);
    await wait(() => !manager.account('minimal'));
    check(true, 'account removed when the backend exits');

    watcher.destroy();
    manager.destroy();
}

main().catch(e => {
    failed++;
    print(`FAIL ${e}\n${e.stack}`);
}).finally(() => loop.quit());
loop.run();
print(failed ? `\n${failed} failed` : '\nall passed');
imports.system.exit(failed ? 1 : 0);
