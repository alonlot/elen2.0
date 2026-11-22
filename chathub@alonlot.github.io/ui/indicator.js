// The panel button and the chat window it opens.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {ContactList} from './contactList.js';
import {ConversationView} from './conversation.js';
import {formatDuration, icon, label} from './widgets.js';

export const ChatIndicator = GObject.registerClass({
    Signals: {'conversation-opened': {param_types: [GObject.TYPE_STRING]}},
}, class ChatIndicator extends PanelMenu.Button {
    _init({manager, onPresence, openPrefs}) {
        super._init(0.5, 'Chat Hub', false);
        this._manager = manager;
        this._toastTimer = 0;
        this._call = null;

        // ---- panel ----
        const box = new St.BoxLayout({style_class: 'chathub-panel-box'});
        this._panelIcon = icon('chathub', 16, 'system-status-icon chathub-panel-icon');
        this._panelIcon.y_align = Clutter.ActorAlign.CENTER;
        box.add_child(this._panelIcon);
        this._badge = new St.Label({
            style_class: 'chathub-panel-badge',
            visible: false,
            y_align: Clutter.ActorAlign.CENTER,
        });
        box.add_child(this._badge);
        this._pill = new St.BoxLayout({style_class: 'chathub-panel-call', visible: false, y_align: Clutter.ActorAlign.CENTER});
        this._pill.add_child(icon('chathub-call', 12));
        this._pillText = label('', 'chathub-panel-call-text');
        this._pill.add_child(this._pillText);
        box.add_child(this._pill);
        this.add_child(box);

        // ---- window ----
        this.menu.actor.add_style_class_name('chathub-popup');
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false, style_class: 'chathub-item'});
        const stack = new St.Widget({layout_manager: new Clutter.BinLayout(), x_expand: true});
        const window = new St.BoxLayout({style_class: 'chathub-window', x_expand: true});

        this.contacts = new ContactList(manager, {
            onSelect: key => this.select(key),
            onPresence,
            onSettings: () => {
                this.menu.close();
                openPrefs();
            },
        });
        window.add_child(this.contacts.actor);
        this.view = new ConversationView(manager, {onToast: t => this.toast(t)});
        window.add_child(this.view.actor);
        stack.add_child(window);

        this._toast = new St.Label({
            style_class: 'chathub-toast',
            opacity: 0,
            visible: false,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.END,
        });
        stack.add_child(this._toast);
        item.add_child(stack);
        this.menu.addMenuItem(item);

        this.menu.connect('open-state-changed', (_m, open) => {
            if (open) {
                this.contacts.refresh();
                this.contacts.refreshAccounts();
                if (this.view.conversation) {
                    manager.setActive(this.view.conversation.key);
                    GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                        this.view.focusEntry();
                        return GLib.SOURCE_REMOVE;
                    });
                } else {
                    GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                        this.contacts.searchEntry.grab_key_focus();
                        return GLib.SOURCE_REMOVE;
                    });
                }
            } else {
                manager.setActive(null);
            }
        });
    }

    get isOpen() {
        return this.menu.isOpen;
    }

    // Open the window, and a conversation if a key is given.
    open(key = null) {
        if (key)
            this.select(key);
        if (!this.menu.isOpen)
            this.menu.open();
    }

    select(key) {
        const conv = this._manager.conversation(key);
        if (!conv)
            return;
        this.contacts.setActive(key);
        this.view.show(conv);
        if (this.menu.isOpen) {
            this._manager.setActive(key);
            this.view.focusEntry();
        }
        this.emit('conversation-opened', key);
    }

    setUnread(n) {
        this._badge.visible = n > 0;
        this._badge.text = n > 9 ? '9+' : String(n);
        if (n > 0)
            this._panelIcon.add_style_class_name('unread');
        else
            this._panelIcon.remove_style_class_name('unread');
    }

    setCall(call) {
        this._call = call && call.state !== 'ended' ? call : null;
        this._pill.visible = !!this._call;
        if (this._call)
            this._pill.style_class = `chathub-panel-call ${this._call.state}`;
        this.tick();
    }

    tick() {
        const c = this._call;
        if (!c)
            return;
        if (c.state === 'active')
            this._pillText.text = formatDuration(Date.now() - (c.startedAt ?? Date.now()));
        else if (c.state === 'ringing-in')
            this._pillText.text = 'Incoming';
        else
            this._pillText.text = 'Calling';
        this.view.tick();
    }

    toast(text) {
        this._toast.text = text;
        this._toast.visible = true;
        this._toast.ease({opacity: 255, duration: 150});
        if (this._toastTimer)
            GLib.source_remove(this._toastTimer);
        this._toastTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 3500, () => {
            this._toastTimer = 0;
            this._toast.ease({opacity: 0, duration: 250, onComplete: () => (this._toast.visible = false)});
            return GLib.SOURCE_REMOVE;
        });
    }

    destroy() {
        if (this._toastTimer) {
            GLib.source_remove(this._toastTimer);
            this._toastTimer = 0;
        }
        this.view?.destroy();
        this.contacts?.destroy();
        this.view = null;
        this.contacts = null;
        super.destroy();
    }
});
