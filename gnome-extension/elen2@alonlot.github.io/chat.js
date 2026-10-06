// The panel button in the top middle of the screen and its chat window.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {Orb} from './orb.js';

const STATE_TEXT = {
    idle: 'ONLINE',
    thinking: 'ANALYSING…',
    working: 'EXECUTING…',
    waiting: 'AWAITING YOUR APPROVAL',
    listening: 'LISTENING…',
    transcribing: 'TRANSCRIBING…',
    offline: 'DAEMON OFFLINE',
};

function plain(text) {
    // Light markdown cleanup for a plain label.
    return String(text ?? '')
        .replace(/\*\*(.+?)\*\*/g, '$1')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/^#+\s*/gm, '');
}

export const ElenIndicator = GObject.registerClass(
class ElenIndicator extends PanelMenu.Button {
    _init(client, openPrefs) {
        super._init(0.5, 'Elen 2.0', false);
        this._client = client;
        this._openPrefs = openPrefs;
        this._ids = new Set();
        this._state = 'offline';

        const box = new St.BoxLayout({style_class: 'elen-panel-box'});
        this._orb = new Orb(18, 'offline');
        this._orb.actor.y_align = Clutter.ActorAlign.CENTER;
        box.add_child(this._orb.actor);
        box.add_child(new St.Label({text: 'ELEN', y_align: Clutter.ActorAlign.CENTER, style_class: 'elen-panel-label'}));
        this.add_child(box);

        this.menu.actor.add_style_class_name('elen-popup');
        this._buildChat();
        this.menu.connect('open-state-changed', (_m, open) => {
            if (open)
                this._onOpen();
        });
    }

    _buildChat() {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false, style_class: 'elen-chat-item'});
        const root = new St.BoxLayout({vertical: true, style_class: 'elen-chat', x_expand: true});

        // Half-circle CLEAR button hanging from the top edge.
        const clear = new St.Button({
            style_class: 'elen-clear-tab',
            x_align: Clutter.ActorAlign.CENTER,
            can_focus: true,
            child: new St.Label({text: 'C L E A R', y_align: Clutter.ActorAlign.START, x_align: Clutter.ActorAlign.CENTER}),
        });
        clear.connect('clicked', () => this._clear());
        root.add_child(clear);

        const header = new St.BoxLayout({style_class: 'elen-chat-header'});
        header.add_child(new St.Label({text: 'E L E N   2 . 0', style_class: 'elen-chat-title', x_expand: true}));
        this._status = new St.Label({text: STATE_TEXT.offline, style_class: 'elen-chat-status', y_align: Clutter.ActorAlign.CENTER});
        header.add_child(this._status);
        const gear = new St.Button({
            style_class: 'elen-icon-button small',
            can_focus: true,
            child: new St.Icon({icon_name: 'emblem-system-symbolic', icon_size: 14}),
        });
        gear.connect('clicked', () => {
            this.menu.close();
            this._openPrefs?.();
        });
        header.add_child(gear);
        root.add_child(header);

        this._scroll = new St.ScrollView({
            style_class: 'elen-chat-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
            x_expand: true,
            y_expand: true,
        });
        this._messages = new St.BoxLayout({vertical: true, style_class: 'elen-chat-messages', x_expand: true});
        this._scroll.set_child(this._messages);
        root.add_child(this._scroll);

        const input = new St.BoxLayout({style_class: 'elen-chat-input'});
        this._entry = new St.Entry({
            hint_text: 'Ask Elen…',
            can_focus: true,
            x_expand: true,
            style_class: 'elen-entry',
        });
        this._entry.clutter_text.connect('activate', () => this._send());
        input.add_child(this._entry);

        this._mic = new St.Button({
            style_class: 'elen-icon-button',
            can_focus: true,
            child: new St.Icon({icon_name: 'audio-input-microphone-symbolic', icon_size: 16}),
        });
        this._mic.connect('clicked', () => this.toggleListening());
        input.add_child(this._mic);

        const send = new St.Button({
            style_class: 'elen-icon-button',
            can_focus: true,
            child: new St.Icon({icon_name: 'go-up-symbolic', icon_size: 16}),
        });
        send.connect('clicked', () => this._send());
        input.add_child(send);
        root.add_child(input);

        item.add_child(root);
        this.menu.addMenuItem(item);
    }

    _onOpen() {
        this.reloadHistory();
        GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            global.stage.set_key_focus(this._entry);
            return GLib.SOURCE_REMOVE;
        });
    }

    reloadHistory() {
        this._client.call('GetHistory').then(json => {
            this._messages.destroy_all_children();
            this._ids.clear();
            for (const m of JSON.parse(json))
                this.addMessage(m, false);
            this._scrollToEnd();
        }).catch(() => this._showOffline());
    }

    _showOffline() {
        if (this._messages.get_n_children() === 0) {
            this._addLabel('The Elen daemon is not running. Start it with: systemctl --user start elen',
                'elen-msg activity');
        }
    }

    addMessage(m, scroll = true) {
        if (m.id && this._ids.has(m.id))
            return;
        if (m.id)
            this._ids.add(m.id);
        if (m.role === 'user')
            this._addLabel(m.text, 'elen-msg user', m.meta?.source === 'voice' ? '🎙 ' : '');
        else if (m.role === 'assistant')
            this._addLabel(plain(m.text), `elen-msg elen${m.meta?.warning ? ' warned' : ''}${m.meta?.error ? ' error' : ''}`);
        else
            this._addLabel(m.text, 'elen-msg activity', '· ');
        if (scroll)
            this._scrollToEnd();
    }

    _addLabel(text, styleClass, prefix = '') {
        const label = new St.Label({text: `${prefix}${text}`, style_class: styleClass});
        label.clutter_text.line_wrap = true;
        label.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        const align = styleClass.includes('user') ? Clutter.ActorAlign.END : Clutter.ActorAlign.START;
        const row = new St.BoxLayout({x_expand: true});
        label.x_align = align;
        label.x_expand = true;
        row.add_child(label);
        this._messages.add_child(row);
    }

    _scrollToEnd() {
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 60, () => {
            const adj = this._scroll.vadjustment ?? this._scroll.get_vscroll_bar?.()?.get_adjustment();
            if (adj)
                adj.value = adj.upper - adj.page_size;
            return GLib.SOURCE_REMOVE;
        });
    }

    _send() {
        const text = this._entry.get_text().trim();
        if (!text)
            return;
        this._entry.set_text('');
        this._client.call('SendMessage', [text]).catch(e => {
            this._addLabel(`Could not reach the Elen daemon: ${e.message}`, 'elen-msg elen error');
        });
    }

    _clear() {
        this._client.call('ClearHistory').catch(() => {});
        this._messages.destroy_all_children();
        this._ids.clear();
    }

    toggleListening() {
        this._client.call('ToggleListening').catch(e => {
            this._addLabel(`Voice input failed: ${e.message}`, 'elen-msg elen error');
        });
    }

    setState(state) {
        this._state = state;
        this._orb?.setState(state);
        this._status.text = STATE_TEXT[state] ?? state.toUpperCase();
        if (state === 'listening')
            this._mic.add_style_class_name('active');
        else
            this._mic.remove_style_class_name('active');
    }

    setToolStatus(ev) {
        if (ev.status === 'running')
            this._status.text = `▸ ${String(ev.title).toUpperCase()}`;
    }

    showTranscript(text) {
        if (this.menu.isOpen && text)
            this._status.text = `HEARD: ${text.slice(0, 40)}`;
    }

    clearView() {
        this._messages.destroy_all_children();
        this._ids.clear();
    }

    destroy() {
        this._orb?.destroy();
        this._orb = null;
        super.destroy();
    }
});
