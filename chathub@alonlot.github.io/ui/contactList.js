// Left side of the chat window: the user card, search and the contact list.
import Clutter from 'gi://Clutter';
import St from 'gi://St';

import {Avatar, PRESENCE_LABEL, formatShort, icon, iconButton, label} from './widgets.js';

const PRESENCE_CHOICES = ['online', 'busy', 'away', 'offline'];

class ContactRow {
    constructor(conv, onClick) {
        this.key = conv.key;
        this.actor = new St.Button({
            style_class: 'chathub-contact',
            can_focus: true,
            track_hover: true,
            x_expand: true,
            x_align: Clutter.ActorAlign.FILL,
        });
        const box = new St.BoxLayout({style_class: 'chathub-contact-box', x_expand: true});
        this._avatar = new Avatar(40);
        box.add_child(this._avatar.actor);

        const text = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER, style_class: 'chathub-contact-text'});
        const top = new St.BoxLayout({x_expand: true});
        this._name = label('', 'chathub-contact-name', {xExpand: true});
        this._time = label('', 'chathub-contact-time');
        top.add_child(this._name);
        top.add_child(this._time);
        const bottom = new St.BoxLayout({x_expand: true});
        this._preview = label('', 'chathub-contact-preview', {xExpand: true});
        this._badge = new St.Label({style_class: 'chathub-badge', y_align: Clutter.ActorAlign.CENTER, visible: false});
        bottom.add_child(this._preview);
        bottom.add_child(this._badge);
        text.add_child(top);
        text.add_child(bottom);
        box.add_child(text);
        this.actor.set_child(box);
        this.actor.connect('clicked', () => onClick(this.key));
        this.update(conv, false);
    }

    update(conv, active) {
        const c = conv.contact;
        this._avatar.update({name: c.name, avatar: c.avatar, presence: c.presence, seed: c.id});
        this._name.text = c.name;
        this._time.text = formatShort(conv.lastTime);
        if (conv.typing) {
            this._preview.text = 'typing…';
            this._preview.add_style_class_name('typing');
        } else {
            this._preview.remove_style_class_name('typing');
            if (conv.lastText)
                this._preview.text = `${conv.lastDirection === 'out' ? 'You: ' : ''}${conv.lastText.replace(/\s+/g, ' ')}`;
            else
                this._preview.text = c.statusText || `${PRESENCE_LABEL[c.presence] ?? ''} · ${conv.accountName}`;
        }
        this._badge.visible = conv.unread > 0;
        this._badge.text = conv.unread > 99 ? '99+' : String(conv.unread);
        if (conv.unread > 0)
            this.actor.add_style_class_name('unread');
        else
            this.actor.remove_style_class_name('unread');
        if (active)
            this.actor.add_style_class_name('active');
        else
            this.actor.remove_style_class_name('active');
    }
}

export class ContactList {
    constructor(manager, {onSelect, onPresence, onSettings}) {
        this._manager = manager;
        this._onSelect = onSelect;
        this._rows = new Map();
        this._active = null;
        this._filter = '';
        this._presence = 'online';

        this.actor = new St.BoxLayout({vertical: true, style_class: 'chathub-sidebar', y_expand: true, x_expand: false});

        // ---- me card ----
        const me = new St.BoxLayout({style_class: 'chathub-me'});
        this._meAvatar = new Avatar(42);
        me.add_child(this._meAvatar.actor);
        const meText = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER, style_class: 'chathub-me-text'});
        this._meName = label('', 'chathub-me-name');
        this._presenceButton = new St.Button({style_class: 'chathub-presence-button', x_align: Clutter.ActorAlign.START, can_focus: true});
        const pb = new St.BoxLayout({style_class: 'chathub-presence-button-box'});
        this._presenceDot = new St.Widget({style_class: 'chathub-presence-dot small online', width: 8, height: 8, y_align: Clutter.ActorAlign.CENTER});
        this._presenceLabel = label('Online', 'chathub-presence-label');
        pb.add_child(this._presenceDot);
        pb.add_child(this._presenceLabel);
        pb.add_child(icon('pan-down-symbolic', 12, 'chathub-presence-arrow'));
        this._presenceButton.set_child(pb);
        this._presenceButton.connect('clicked', () => {
            this._picker.visible = !this._picker.visible;
        });
        meText.add_child(this._meName);
        meText.add_child(this._presenceButton);
        me.add_child(meText);
        me.add_child(iconButton('emblem-system-symbolic', {tooltip: 'Settings', onClick: () => onSettings()}));
        this.actor.add_child(me);

        // ---- presence picker ----
        this._picker = new St.BoxLayout({style_class: 'chathub-presence-picker', visible: false});
        for (const p of PRESENCE_CHOICES) {
            const b = new St.Button({style_class: 'chathub-presence-choice', can_focus: true, x_expand: true});
            const bb = new St.BoxLayout({x_align: Clutter.ActorAlign.CENTER});
            bb.add_child(new St.Widget({style_class: `chathub-presence-dot small ${p}`, width: 8, height: 8, y_align: Clutter.ActorAlign.CENTER}));
            bb.add_child(label(p === 'offline' ? 'Invisible' : PRESENCE_LABEL[p].replace('Do not disturb', 'Busy'), 'chathub-presence-choice-label'));
            b.set_child(bb);
            b.connect('clicked', () => {
                this._picker.visible = false;
                onPresence(p);
            });
            this._picker.add_child(b);
        }
        this.actor.add_child(this._picker);

        // ---- search ----
        this._search = new St.Entry({
            style_class: 'chathub-search',
            hint_text: 'Search',
            can_focus: true,
            x_expand: true,
            primary_icon: icon('edit-find-symbolic', 14, 'chathub-search-icon'),
        });
        this._search.clutter_text.connect('text-changed', () => {
            this._filter = this._search.text.trim().toLowerCase();
            this.refresh();
        });
        this._search.clutter_text.connect('activate', () => {
            const first = this._visibleKeys()[0];
            if (first)
                this._onSelect(first);
        });
        this.actor.add_child(this._search);

        // ---- list ----
        this._scroll = new St.ScrollView({
            style_class: 'chathub-contact-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
            x_expand: true,
            y_expand: true,
        });
        this._list = new St.BoxLayout({vertical: true, style_class: 'chathub-contact-list', x_expand: true});
        this._scroll.set_child(this._list);
        this.actor.add_child(this._scroll);

        this._empty = label('No contacts yet.\nAdd an account in Settings.', 'chathub-list-empty', {wrap: true});
        this._empty.clutter_text.set_line_alignment(1);

        // ---- accounts footer ----
        this._accounts = new St.BoxLayout({style_class: 'chathub-accounts'});
        this.actor.add_child(this._accounts);
    }

    get searchEntry() {
        return this._search;
    }

    setActive(key) {
        this._active = key;
        this.refresh();
    }

    setSelf({name, avatar, presence}) {
        this._presence = presence;
        this._meName.text = name;
        this._meAvatar.update({name, avatar, presence, seed: name});
        this._presenceDot.style_class = `chathub-presence-dot small ${presence}`;
        this._presenceLabel.text = presence === 'offline' ? 'Invisible' : PRESENCE_LABEL[presence];
    }

    _matches(conv) {
        if (!this._filter)
            return true;
        const c = conv.contact;
        return `${c.name} ${c.id} ${conv.accountName}`.toLowerCase().includes(this._filter);
    }

    _visibleKeys() {
        return this._manager.conversations.filter(c => this._matches(c)).map(c => c.key);
    }

    refresh() {
        const convs = this._manager.conversations.filter(c => this._matches(c));
        const seen = new Set();
        convs.forEach((conv, i) => {
            let row = this._rows.get(conv.key);
            if (!row) {
                row = new ContactRow(conv, key => this._onSelect(key));
                this._rows.set(conv.key, row);
            }
            row.update(conv, conv.key === this._active);
            if (row.actor.get_parent() !== this._list)
                this._list.insert_child_at_index(row.actor, i);
            else
                this._list.set_child_at_index(row.actor, i);
            seen.add(conv.key);
        });
        for (const [key, row] of this._rows) {
            if (!seen.has(key)) {
                row.actor.destroy();
                this._rows.delete(key);
            }
        }
        if (convs.length === 0 && !this._empty.get_parent()) {
            this._empty.text = this._filter ? 'No match.' : 'No contacts yet.\nTurn on the demo account or start a backend.';
            this._list.add_child(this._empty);
        } else if (convs.length > 0 && this._empty.get_parent()) {
            this._list.remove_child(this._empty);
        }
    }

    refreshAccounts() {
        this._accounts.destroy_all_children();
        for (const a of this._manager.accounts) {
            const chip = new St.BoxLayout({style_class: `chathub-account-chip ${a.state}`});
            chip.add_child(new St.Widget({style_class: `chathub-state-dot ${a.state}`, width: 6, height: 6, y_align: Clutter.ActorAlign.CENTER}));
            chip.add_child(label(a.name, 'chathub-account-name'));
            this._accounts.add_child(chip);
        }
        this._accounts.visible = this._accounts.get_n_children() > 0;
    }

    destroy() {
        this._rows.clear();
        this._empty.destroy();
        this.actor.destroy();
    }
}
