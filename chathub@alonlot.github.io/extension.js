// Chat Hub: a chat window in the GNOME top panel for many chat networks.
//
//   lib/api.js         provider API (start here to add a network)
//   lib/manager.js     joins all accounts, history, unread, calls
//   lib/history.js     local chat history on disk
//   lib/dbusBridge.js  backends in other languages over D-Bus
//   providers/         in-process providers (demo)
//   ui/                panel button, chat window, call window
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {createProvider, providerTypes} from './lib/api.js';
import {BackendWatcher, DBusProvider} from './lib/dbusBridge.js';
import {HistoryStore} from './lib/history.js';
import {ChatManager} from './lib/manager.js';
import {registerBuiltinProviders} from './providers/index.js';
import {CallWindow} from './ui/callWindow.js';
import {ChatIndicator} from './ui/indicator.js';
import {Notifier} from './ui/notify.js';
import {setExtensionPath} from './ui/widgets.js';

export default class ChatHubExtension extends Extension {
    enable() {
        setExtensionPath(this.path);
        registerBuiltinProviders();
        this._settings = this.getSettings();
        this._history = new HistoryStore();
        this._manager = new ChatManager({
            history: this._history,
            historyLimit: this._settings.get_int('history-limit'),
        });
        this._notifier = new Notifier();
        this._sigs = [];

        this._indicator = new ChatIndicator({
            manager: this._manager,
            onPresence: p => this._settings.set_string('presence', p),
            openPrefs: () => this.openPreferences(),
        });
        Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'right');
        this._indicator.connect('conversation-opened', (_i, key) => this._notifier.dismiss(key));

        this._callWindow = new CallWindow(this._manager, {
            onOpenChat: call => {
                const conv = this._manager.conversationForCall(call.id);
                if (conv)
                    this._indicator.open(conv.key);
            },
            soundEnabled: () => this._settings.get_boolean('sounds'),
        });

        this._indicator.menu.connect('open-state-changed', (_m, open) => this._callWindow?.setSuppressed(open));

        this._connectManager();
        this._connectSettings();
        this._updateSelf();
        this._syncProviders();

        this._backends = new Map(); // bus name -> provider id
        this._watcher = new BackendWatcher({
            autostart: this._settings.get_boolean('autostart-backends'),
            onAdded: name => this._addBackend(name),
            onRemoved: name => this._removeBackend(name),
        });

        Main.wm.addKeybinding('toggle-chat', this._settings, Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
            () => this._indicator.menu.toggle());
    }

    disable() {
        Main.wm.removeKeybinding('toggle-chat');
        if (this._tickId) {
            GLib.source_remove(this._tickId);
            this._tickId = 0;
        }
        for (const [obj, id] of this._sigs ?? [])
            obj.disconnect(id);
        this._sigs = null;
        this._watcher?.destroy();
        this._watcher = null;
        this._backends = null;
        this._pending = null;
        this._inproc = null;
        this._callWindow?.destroy();
        this._callWindow = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._manager?.destroy();
        this._manager = null;
        this._history?.flush();
        this._history = null;
        this._notifier?.destroy();
        this._notifier = null;
        this._settings = null;
    }

    _connectManager() {
        const m = this._manager;
        const ind = this._indicator;
        const on = (name, fn) => this._sigs.push([m, m.connect(name, fn)]);
        let queued = false;
        on('conversations-changed', () => {
            // Many changes can come at once (a roster); redraw one time.
            if (queued)
                return;
            queued = true;
            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                queued = false;
                if (!this._indicator)
                    return GLib.SOURCE_REMOVE;
                if (ind.isOpen)
                    ind.contacts.refresh();
                const active = ind.view.conversation;
                if (active)
                    ind.view.updateHeader(active);
                return GLib.SOURCE_REMOVE;
            });
        });
        on('accounts-changed', () => ind.contacts.refreshAccounts());
        on('message', (_m, conv, msg) => {
            ind.view.onMessage(conv, msg);
            if (msg.direction === 'in')
                this._onIncoming(conv, msg);
        });
        on('message-updated', (_m, conv, msg, oldId) => ind.view.onMessageUpdated(conv, msg, oldId));
        on('history-loaded', (_m, conv) => ind.view.onHistoryLoaded(conv));
        on('typing', (_m, conv) => {
            ind.view.onTyping(conv);
            if (ind.isOpen)
                ind.contacts.refresh();
        });
        on('unread-changed', (_m, n) => ind.setUnread(n));
        on('call-changed', (_m, call, conv) => this._onCall(call, conv));
        on('error', (_m, text) => {
            if (ind.isOpen)
                ind.toast(text);
            else
                console.warn(`Chat Hub: ${text}`);
        });
    }

    _connectSettings() {
        const s = this._settings;
        const on = (key, fn) => this._sigs.push([s, s.connect(`changed::${key}`, fn)]);
        on('display-name', () => this._updateSelf());
        on('avatar-path', () => this._updateSelf());
        on('presence', () => {
            this._updateSelf();
            this._manager.setPresence(s.get_string('presence'));
        });
        on('providers', () => this._syncProviders());
        on('history-cleared-at', () => this._manager.clearAllHistory().then(() => {
            const view = this._indicator?.view;
            if (view?.conversation)
                view.show(view.conversation);
        }));
    }

    _updateSelf() {
        const name = this._settings.get_string('display-name') || GLib.get_real_name() || GLib.get_user_name();
        const avatar = this._settings.get_string('avatar-path') || null;
        this._indicator.contacts.setSelf({name, avatar, presence: this._settings.get_string('presence')});
    }

    // Start and stop in-process providers to match the "providers" setting.
    _syncProviders() {
        const wanted = new Set(this._settings.get_strv('providers'));
        this._inproc ??= new Map(); // type -> provider id
        for (const [type, id] of this._inproc) {
            if (!wanted.has(type)) {
                this._manager.removeProvider(id);
                this._inproc.delete(type);
            }
        }
        for (const type of wanted) {
            if (this._inproc.has(type))
                continue;
            if (!providerTypes().includes(type)) {
                console.warn(`Chat Hub: unknown provider type "${type}"`);
                continue;
            }
            try {
                const p = createProvider(type, this._settings);
                this._inproc.set(type, p.id);
                this._manager.addProvider(p).then(() => this._applyPresence(p));
            } catch (e) {
                logError(e, `Chat Hub: cannot start provider "${type}"`);
            }
        }
    }

    _applyPresence(provider) {
        const presence = this._settings?.get_string('presence');
        if (presence && presence !== 'online' && provider.capabilities.presence)
            provider.setPresence(presence, '').catch(() => {});
    }

    async _addBackend(name) {
        const token = Symbol(name);
        this._pending ??= new Map();
        this._pending.set(name, token);
        try {
            const provider = await DBusProvider.create(Gio.DBus.session, name);
            if (!this._manager || this._pending.get(name) !== token) {
                provider.destroy();
                return;
            }
            this._backends.set(name, provider.id);
            await this._manager.addProvider(provider);
            this._applyPresence(provider);
        } catch (e) {
            console.warn(`Chat Hub: backend ${name} does not answer: ${e.message}`);
        } finally {
            if (this._pending?.get(name) === token)
                this._pending.delete(name);
        }
    }

    _removeBackend(name) {
        this._pending?.delete(name);
        const id = this._backends?.get(name);
        if (id) {
            this._backends.delete(name);
            this._manager?.removeProvider(id);
        }
    }

    _presence() {
        return this._settings.get_string('presence');
    }

    _onIncoming(conv, msg) {
        const ind = this._indicator;
        const visible = ind.isOpen && ind.view.conversation?.key === conv.key;
        if (visible)
            return;
        if (this._presence() === 'busy')
            return; // do not disturb
        if (this._settings.get_boolean('sounds')) {
            try {
                global.display.get_sound_player().play_from_theme('message-new-instant', 'New message', null);
            } catch (e) {
                logError(e);
            }
        }
        if (this._settings.get_boolean('notifications') && !ind.isOpen) {
            const body = conv.unread > 1 ? `${msg.text}\n(${conv.unread} new messages)` : msg.text;
            this._notifier.show(conv.key, conv.contact.name, body, () => this._indicator?.open(conv.key));
        }
    }

    _onCall(call, conv) {
        this._callWindow.update(call, conv);
        this._indicator.view.updateCall(call);
        const shown = this._manager.calls.find(c => c.state === 'active') ?? this._manager.calls[0] ?? null;
        this._indicator.setCall(shown);
        // One timer for all call clocks.
        if (this._manager.calls.length && !this._tickId) {
            this._tickId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
                this._indicator?.tick();
                this._callWindow?.tick();
                if (this._manager?.calls.length)
                    return GLib.SOURCE_CONTINUE;
                this._tickId = 0;
                return GLib.SOURCE_REMOVE;
            });
        }
    }
}
