// Right side of the chat window: header with call buttons, messages, composer.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Avatar, PRESENCE_LABEL, formatDay, formatDuration, formatTime, gicon, icon, iconButton, label, sameDay} from './widgets.js';

const GROUP_MS = 5 * 60 * 1000;
const EMOJI = [
    '😀', '😂', '😊', '😍', '😘', '😎', '🤔', '😅',
    '😢', '😮', '😡', '🙏', '👍', '👎', '👏', '🙌',
    '🎉', '❤️', '🔥', '✨', '💯', '✅', '☕', '🍕',
];
const TICKS = {sending: '•••', sent: '✓', delivered: '✓✓', read: '✓✓', failed: '!', retried: ''};

export class ConversationView {
    constructor(manager, {onToast}) {
        this._manager = manager;
        this._toast = onToast;
        this._drafts = new Map();
        this._call = null;
        this._conv = null;
        this._bubbles = new Map();
        this._last = null; // last rendered message
        this._typingSent = 0;
        this._typingTimer = 0;
        this._dotsTimer = 0;
        this._confirmTimer = 0;
        this._stick = true;

        this.actor = new St.Widget({
            layout_manager: new Clutter.BinLayout(),
            style_class: 'chathub-main',
            x_expand: true,
            y_expand: true,
        });

        this._buildEmpty();
        this._buildContent();
        this._content.visible = false;
    }

    _buildEmpty() {
        this._emptyBox = new St.BoxLayout({
            vertical: true,
            style_class: 'chathub-empty',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        const art = new St.Bin({style_class: 'chathub-empty-art', x_align: Clutter.ActorAlign.CENTER, child: icon('chathub', 44)});
        this._emptyBox.add_child(art);
        this._emptyBox.add_child(new St.Label({text: 'Your conversations', style_class: 'chathub-empty-title', x_align: Clutter.ActorAlign.CENTER}));
        this._emptyBox.add_child(new St.Label({text: 'Pick a contact on the left to start chatting.', style_class: 'chathub-empty-text', x_align: Clutter.ActorAlign.CENTER}));
        this.actor.add_child(this._emptyBox);
    }

    _buildContent() {
        this._content = new St.BoxLayout({vertical: true, x_expand: true, y_expand: true, style_class: 'chathub-content'});

        // ---- header ----
        const header = new St.BoxLayout({style_class: 'chathub-header'});
        this._avatar = new Avatar(40);
        header.add_child(this._avatar.actor);
        const titles = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER, style_class: 'chathub-header-text'});
        this._title = label('', 'chathub-header-name');
        this._subtitle = label('', 'chathub-header-status');
        titles.add_child(this._title);
        titles.add_child(this._subtitle);
        header.add_child(titles);
        this._callBtn = iconButton('chathub-call', {tooltip: 'Call', styleClass: 'chathub-icon-button round', onClick: () => this._startCall(false)});
        this._videoBtn = iconButton('chathub-video', {tooltip: 'Video call', styleClass: 'chathub-icon-button round', onClick: () => this._startCall(true)});
        this._clearBtn = iconButton('user-trash-symbolic', {tooltip: 'Clear history', styleClass: 'chathub-icon-button round', onClick: () => this._clearHistory()});
        header.add_child(this._callBtn);
        header.add_child(this._videoBtn);
        header.add_child(this._clearBtn);
        this._content.add_child(header);

        // ---- call bar ----
        this._callBar = new St.BoxLayout({style_class: 'chathub-callbar', visible: false});
        this._callIcon = icon('chathub-call', 14, 'chathub-callbar-icon');
        this._callText = label('', 'chathub-callbar-text', {xExpand: true});
        this._callAccept = iconButton('chathub-call', {styleClass: 'chathub-call-btn accept small', size: 14, tooltip: 'Answer',
            onClick: () => this._call && this._manager.answerCall(this._call.id)});
        this._callMute = iconButton('chathub-mic', {styleClass: 'chathub-call-btn small', size: 14, tooltip: 'Mute',
            onClick: () => this._call && this._manager.setMuted(this._call.id, !this._call.muted)});
        this._callHang = iconButton('chathub-hangup', {styleClass: 'chathub-call-btn hangup small', size: 14, tooltip: 'Hang up',
            onClick: () => this._call && this._manager.endCall(this._call.id)});
        for (const w of [this._callIcon, this._callText, this._callAccept, this._callMute, this._callHang])
            this._callBar.add_child(w);
        this._content.add_child(this._callBar);

        // ---- messages ----
        this._scroll = new St.ScrollView({
            style_class: 'chathub-messages-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
            x_expand: true,
            y_expand: true,
        });
        this._messages = new St.BoxLayout({vertical: true, style_class: 'chathub-messages', x_expand: true});
        this._scroll.set_child(this._messages);
        const adj = this._adjustment();
        adj.connect('notify::upper', () => {
            if (this._stick)
                adj.value = adj.upper - adj.page_size;
        });
        adj.connect('notify::value', () => {
            this._stick = adj.value >= adj.upper - adj.page_size - 24;
        });
        this._content.add_child(this._scroll);

        this._typing = label('', 'chathub-typing');
        this._typing.opacity = 0;
        this._content.add_child(this._typing);

        // ---- emoji ----
        this._emojiPanel = new St.Widget({
            style_class: 'chathub-emoji-panel',
            layout_manager: new Clutter.GridLayout({column_homogeneous: true}),
            visible: false,
            x_expand: true,
        });
        const grid = this._emojiPanel.layout_manager;
        EMOJI.forEach((e, i) => {
            const b = new St.Button({label: e, style_class: 'chathub-emoji', can_focus: true});
            b.connect('clicked', () => this._insert(e));
            grid.attach(b, i % 12, Math.floor(i / 12), 1, 1);
        });
        this._content.add_child(this._emojiPanel);

        // ---- composer ----
        const composer = new St.BoxLayout({style_class: 'chathub-composer'});
        composer.add_child(iconButton('chathub-emoji', {size: 18, tooltip: 'Emoji', styleClass: 'chathub-icon-button composer',
            onClick: () => {
                this._emojiPanel.visible = !this._emojiPanel.visible;
            }}));
        this._entry = new St.Entry({
            style_class: 'chathub-entry',
            hint_text: 'Write a message…',
            can_focus: true,
            x_expand: true,
        });
        this._entry.clutter_text.connect('activate', () => this._send());
        this._entry.clutter_text.connect('text-changed', () => this._onTextChanged());
        composer.add_child(this._entry);
        this._sendBtn = iconButton('chathub-send', {size: 16, tooltip: 'Send', styleClass: 'chathub-send', onClick: () => this._send()});
        composer.add_child(this._sendBtn);
        this._content.add_child(composer);

        this.actor.add_child(this._content);
    }

    _adjustment() {
        return this._scroll.vadjustment ?? this._scroll.vscroll.adjustment;
    }

    get conversation() {
        return this._conv;
    }

    focusEntry() {
        if (this._conv)
            this._entry.grab_key_focus();
    }

    // ---- show a conversation ----------------------------------------------

    async show(conv) {
        if (this._conv && this._conv.key !== conv?.key)
            this._stopTyping();
        this._conv = conv;
        this._emptyBox.visible = !conv;
        this._content.visible = !!conv;
        this._emojiPanel.visible = false;
        if (!conv)
            return;
        this._restoring = true;
        this._entry.text = this._drafts.get(conv.key) ?? '';
        this._restoring = false;
        this.updateHeader(conv);
        this.updateCall(this._manager.callForConversation(conv.key));
        this._renderAll();
        if (!conv.loaded) {
            const loaded = await this._manager.loadHistory(conv.key);
            if (loaded && this._conv === loaded)
                this._renderAll();
        }
    }

    updateHeader(conv) {
        if (!this._conv || conv.key !== this._conv.key)
            return;
        const c = conv.contact;
        const account = this._manager.account(conv.accountId);
        this._avatar.update({name: c.name, avatar: c.avatar, presence: c.presence, seed: c.id});
        this._title.text = c.name;
        const parts = [PRESENCE_LABEL[c.presence] ?? c.presence];
        if (c.statusText)
            parts.push(c.statusText);
        parts.push(conv.accountName);
        this._subtitle.text = parts.join('  ·  ');
        const caps = account?.capabilities ?? {};
        this._callBtn.visible = !!caps.calls;
        this._videoBtn.visible = !!caps.calls && !!caps.video;
        this._setTypingVisible(conv.typing);
    }

    _renderAll() {
        this._messages.destroy_all_children();
        this._bubbles.clear();
        this._last = null;
        this._stick = true;
        const conv = this._conv;
        if (conv.hasMore) {
            const more = new St.Button({label: 'Load earlier messages', style_class: 'chathub-load-more', x_align: Clutter.ActorAlign.CENTER, can_focus: true});
            more.connect('clicked', async () => {
                more.reactive = false;
                more.label = 'Loading…';
                try {
                    await this._manager.loadEarlier(conv.key);
                } catch (e) {
                    this._toast(e.message);
                }
                if (this._conv === conv)
                    this._renderAll();
            });
            this._messages.add_child(more);
        }
        if (conv.messages.length === 0 && conv.loaded) {
            const hello = new St.BoxLayout({vertical: true, style_class: 'chathub-hello', x_align: Clutter.ActorAlign.CENTER});
            const av = new Avatar(64, {presence: false});
            av.update({name: conv.contact.name, avatar: conv.contact.avatar, seed: conv.contact.id});
            av.actor.x_align = Clutter.ActorAlign.CENTER;
            hello.add_child(av.actor);
            hello.add_child(new St.Label({text: `Say hi to ${conv.contact.name.split(' ')[0]} 👋`, style_class: 'chathub-hello-text', x_align: Clutter.ActorAlign.CENTER}));
            this._messages.add_child(hello);
            this._hello = hello;
        } else {
            this._hello = null;
        }
        for (const m of conv.messages)
            this._append(m);
    }

    _append(m) {
        if (this._hello) {
            this._hello.destroy();
            this._hello = null;
        }
        const prev = this._last;
        if (!prev || !sameDay(prev.time, m.time)) {
            const sep = new St.BoxLayout({style_class: 'chathub-day', x_align: Clutter.ActorAlign.CENTER});
            sep.add_child(new St.Label({text: formatDay(m.time), style_class: 'chathub-day-label'}));
            this._messages.add_child(sep);
        }
        const grouped = prev && prev.direction === m.direction && sameDay(prev.time, m.time) &&
            m.time - prev.time < GROUP_MS;
        const isCall = m.id.startsWith('call-');

        const row = new St.BoxLayout({
            style_class: `chathub-row ${m.direction}${grouped ? ' grouped' : ''}`,
            x_expand: true,
        });
        const bubble = new St.Button({
            style_class: `chathub-bubble ${m.direction}${isCall ? ' call' : ''}`,
            x_expand: false,
            x_align: m.direction === 'out' ? Clutter.ActorAlign.END : Clutter.ActorAlign.START,
            reactive: true,
            can_focus: false,
        });
        if (m.direction === 'out')
            row.add_child(new St.Widget({x_expand: true}));
        const inner = new St.BoxLayout({vertical: true});
        const text = label(m.text, 'chathub-bubble-text', {wrap: true});
        inner.add_child(text);
        const meta = new St.BoxLayout({style_class: 'chathub-bubble-meta', x_align: Clutter.ActorAlign.END});
        const time = new St.Label({text: formatTime(m.time), style_class: 'chathub-bubble-time'});
        meta.add_child(time);
        let ticks = null;
        if (m.direction === 'out' && !isCall) {
            ticks = new St.Label({style_class: 'chathub-ticks'});
            meta.add_child(ticks);
        }
        inner.add_child(meta);
        bubble.set_child(inner);
        row.add_child(bubble);
        const entry = {row, bubble, text, time, ticks, msg: m, alive: true};
        row.connect('destroy', () => {
            entry.alive = false;
        });
        bubble.connect('clicked', () => this._onBubble(entry));
        this._setStatus(entry);
        this._messages.add_child(row);
        this._bubbles.set(m.id, entry);
        this._last = m;
    }

    _setStatus(entry) {
        const m = entry.msg;
        if (!entry.ticks)
            return;
        entry.ticks.text = m.status === 'failed' ? '!  Not sent, click to retry' : TICKS[m.status] ?? '';
        entry.ticks.style_class = `chathub-ticks ${m.status}`;
        if (m.status === 'failed')
            entry.bubble.add_style_class_name('failed');
        else
            entry.bubble.remove_style_class_name('failed');
    }

    _onBubble(entry) {
        const m = entry.msg;
        if (m.status === 'failed') {
            this._manager.retry(this._conv.key, m.id);
            return;
        }
        // Click a message to copy it.
        St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, m.text);
        entry.time.text = 'Copied ✓';
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, () => {
            if (entry.alive)
                entry.time.text = formatTime(entry.msg.time);
            return GLib.SOURCE_REMOVE;
        });
    }

    // ---- live updates -----------------------------------------------------

    onMessage(conv, m) {
        if (!this._conv || conv.key !== this._conv.key || !conv.loaded)
            return;
        if (this._bubbles.has(m.id))
            return;
        if (this._last && m.time < this._last.time) {
            this._renderAll(); // out of order (history from another device)
            return;
        }
        if (m.direction === 'out')
            this._stick = true;
        this._append(m);
    }

    onMessageUpdated(conv, m, oldId) {
        if (!this._conv || conv.key !== this._conv.key)
            return;
        const entry = this._bubbles.get(oldId) ?? this._bubbles.get(m.id);
        if (!entry) {
            if (conv.messages.includes(m))
                this.onMessage(conv, m);
            return;
        }
        if (!conv.messages.includes(entry.msg)) {
            // The local copy was dropped for the provider's copy.
            this._renderAll();
            return;
        }
        this._bubbles.delete(oldId);
        entry.msg = m;
        this._bubbles.set(m.id, entry);
        entry.text.text = m.text;
        this._setStatus(entry);
    }

    onHistoryLoaded(conv) {
        if (this._conv && conv.key === this._conv.key)
            this._renderAll();
    }

    onTyping(conv) {
        if (this._conv && conv.key === this._conv.key)
            this.updateHeader(conv);
    }

    _setTypingVisible(typing) {
        const name = this._conv?.contact.name.split(' ')[0] ?? '';
        if (typing) {
            this._subtitle.text = 'typing…';
            this._subtitle.add_style_class_name('typing');
            let n = 0;
            const dots = () => {
                n = (n + 1) % 4;
                this._typing.text = `${name} is typing${'.'.repeat(n)}`;
            };
            dots();
            if (!this._dotsTimer) {
                this._dotsTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
                    dots();
                    return GLib.SOURCE_CONTINUE;
                });
            }
            this._typing.ease({opacity: 255, duration: 150});
        } else {
            this._subtitle.remove_style_class_name('typing');
            if (this._dotsTimer) {
                GLib.source_remove(this._dotsTimer);
                this._dotsTimer = 0;
            }
            this._typing.ease({opacity: 0, duration: 150});
        }
    }

    // ---- calls ----------------------------------------------------------

    updateCall(call) {
        if (call) {
            const mine = this._call?.id === call.id ||
                (this._conv && this._manager.conversationForCall(call.id)?.key === this._conv.key);
            if (!mine)
                return;
        }
        this._call = call && call.state !== 'ended' ? call : null;
        const c = this._call;
        this._callBar.visible = !!c;
        this._callBtn.reactive = this._videoBtn.reactive = !c;
        this._callBtn.opacity = this._videoBtn.opacity = c ? 110 : 255;
        if (!c)
            return;
        this._callBar.style_class = `chathub-callbar ${c.state}`;
        this._callAccept.visible = c.state === 'ringing-in';
        this._callMute.visible = c.state === 'active';
        this._callMute.child.gicon = gicon(c.muted ? 'chathub-mic-off' : 'chathub-mic');
        if (c.muted)
            this._callMute.add_style_class_name('toggled');
        else
            this._callMute.remove_style_class_name('toggled');
        this.tick();
    }

    tick() {
        const c = this._call;
        if (!c)
            return;
        const kind = c.video ? 'Video call' : 'Call';
        if (c.state === 'ringing-out')
            this._callText.text = `Calling ${this._conv.contact.name}…`;
        else if (c.state === 'ringing-in')
            this._callText.text = `Incoming ${kind.toLowerCase()}`;
        else
            this._callText.text = `${kind}  ·  ${formatDuration(Date.now() - (c.startedAt ?? Date.now()))}${c.muted ? '  ·  muted' : ''}`;
    }

    async _startCall(video) {
        if (this._conv)
            await this._manager.startCall(this._conv.key, {video});
    }

    // ---- composer ---------------------------------------------------------

    _insert(text) {
        const ct = this._entry.clutter_text;
        const pos = ct.get_cursor_position();
        ct.insert_text(text, pos < 0 ? ct.text.length : pos);
        this._entry.grab_key_focus();
    }

    _send() {
        const text = this._entry.text.trim();
        if (!text || !this._conv)
            return;
        this._entry.text = '';
        this._drafts.delete(this._conv.key);
        this._emojiPanel.visible = false;
        this._stopTyping();
        this._stick = true;
        this._manager.send(this._conv.key, text);
    }

    _onTextChanged() {
        if (!this._conv)
            return;
        const text = this._entry.text;
        if (text)
            this._drafts.set(this._conv.key, text);
        else
            this._drafts.delete(this._conv.key);
        if (this._sendBtn)
            this._sendBtn.opacity = text.trim() ? 255 : 150;
        if (this._restoring)
            return; // a saved draft, not the user typing
        if (!text) {
            this._stopTyping();
            return;
        }
        const now = Date.now();
        if (now - this._typingSent > 3000) {
            this._typingSent = now;
            this._manager.setTyping(this._conv.key, true);
        }
        if (this._typingTimer)
            GLib.source_remove(this._typingTimer);
        this._typingTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 4000, () => {
            this._typingTimer = 0;
            this._stopTyping();
            return GLib.SOURCE_REMOVE;
        });
    }

    _stopTyping() {
        if (this._typingTimer) {
            GLib.source_remove(this._typingTimer);
            this._typingTimer = 0;
        }
        if (this._typingSent && this._conv) {
            this._typingSent = 0;
            this._manager.setTyping(this._conv.key, false);
        }
    }

    _clearHistory() {
        if (!this._conv)
            return;
        if (!this._confirmTimer) {
            this._clearBtn.add_style_class_name('danger');
            this._confirmTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 3000, () => {
                this._confirmTimer = 0;
                this._clearBtn.remove_style_class_name('danger');
                return GLib.SOURCE_REMOVE;
            });
            this._toast('Click the trash button again to delete this chat history.');
            return;
        }
        GLib.source_remove(this._confirmTimer);
        this._confirmTimer = 0;
        this._clearBtn.remove_style_class_name('danger');
        this._manager.clearHistory(this._conv.key);
    }

    destroy() {
        for (const id of ['_typingTimer', '_dotsTimer', '_confirmTimer']) {
            if (this[id]) {
                GLib.source_remove(this[id]);
                this[id] = 0;
            }
        }
        this.actor.destroy();
    }
}
