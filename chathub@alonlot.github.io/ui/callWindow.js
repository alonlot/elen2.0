// Floating call card at the top of the screen: incoming, calling, in call.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {Avatar, formatDuration, gicon, iconButton, label} from './widgets.js';

export class CallWindow {
    constructor(manager, {onOpenChat, soundEnabled}) {
        this._manager = manager;
        this._onOpenChat = onOpenChat;
        this._soundEnabled = soundEnabled;
        this._call = null;
        this._ringTimer = 0;
        this._suppressed = false;

        this.actor = new St.BoxLayout({style_class: 'chathub-call-window', vertical: true, visible: false, reactive: true});
        const top = new St.BoxLayout({style_class: 'chathub-call-top'});
        this._avatarBtn = new St.Button({style_class: 'chathub-call-avatar-btn', can_focus: true});
        this._avatar = new Avatar(56, {presence: false});
        this._avatarBtn.set_child(this._avatar.actor);
        this._avatarBtn.connect('clicked', () => {
            if (this._call)
                this._onOpenChat(this._call);
        });
        top.add_child(this._avatarBtn);
        const text = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER, style_class: 'chathub-call-texts'});
        this._name = label('', 'chathub-call-name');
        this._state = label('', 'chathub-call-state');
        text.add_child(this._name);
        text.add_child(this._state);
        top.add_child(text);
        this.actor.add_child(top);

        const buttons = new St.BoxLayout({style_class: 'chathub-call-buttons', x_align: Clutter.ActorAlign.CENTER});
        this._mute = iconButton('chathub-mic', {size: 18, styleClass: 'chathub-call-btn', tooltip: 'Mute',
            onClick: () => this._call && this._manager.setMuted(this._call.id, !this._call.muted)});
        this._decline = iconButton('chathub-hangup', {size: 18, styleClass: 'chathub-call-btn hangup', tooltip: 'Hang up',
            onClick: () => this._call && this._manager.endCall(this._call.id)});
        this._accept = iconButton('chathub-call', {size: 18, styleClass: 'chathub-call-btn accept', tooltip: 'Answer',
            onClick: () => this._call && this._manager.answerCall(this._call.id)});
        buttons.add_child(this._mute);
        buttons.add_child(this._decline);
        buttons.add_child(this._accept);
        this.actor.add_child(buttons);

        Main.layoutManager.addTopChrome(this.actor);
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._place());
    }

    _place() {
        const m = Main.layoutManager.primaryMonitor;
        if (!m)
            return;
        // Top right, so it does not cover GNOME's notification banners (top center).
        const [, natW] = this.actor.get_preferred_width(-1);
        const w = Math.max(natW, 340);
        this.actor.set_position(Math.round(m.x + m.width - w - 16), m.y + Main.panel.height + 12);
    }

    get call() {
        return this._call;
    }

    // While the chat window is open its call bar shows the call, so hide this
    // card. An incoming call always shows.
    setSuppressed(suppressed) {
        this._suppressed = suppressed;
        if (!this._call)
            return;
        if (this._wanted())
            this._show();
        else
            this._hide();
    }

    _wanted() {
        return !!this._call && (!this._suppressed || this._call.state === 'ringing-in');
    }

    update(call, conv) {
        if (this._call && call.id !== this._call.id && call.state === 'ended')
            return; // another call ended; keep the one shown
        if (call.state === 'ended') {
            this._call = null;
            this._stopRing();
            this._hide();
            return;
        }
        const wasRinging = this._call?.state === 'ringing-in';
        this._call = call;
        const c = conv?.contact ?? {name: call.contactId, id: call.contactId};
        this._avatar.update({name: c.name, avatar: c.avatar, seed: c.id});
        this._name.text = c.name;
        this._accept.visible = call.state === 'ringing-in';
        this._mute.visible = call.state === 'active';
        this._mute.child.gicon = gicon(call.muted ? 'chathub-mic-off' : 'chathub-mic');
        if (call.muted)
            this._mute.add_style_class_name('toggled');
        else
            this._mute.remove_style_class_name('toggled');
        this.actor.style_class = `chathub-call-window ${call.state}`;
        this.tick();
        if (this._wanted())
            this._show();
        else
            this._hide();
        if (call.state === 'ringing-in' && !wasRinging)
            this._startRing();
        else if (call.state !== 'ringing-in')
            this._stopRing();
    }

    tick() {
        const c = this._call;
        if (!c)
            return;
        const kind = c.video ? 'video call' : 'call';
        if (c.state === 'ringing-in')
            this._state.text = `Incoming ${kind}…`;
        else if (c.state === 'ringing-out')
            this._state.text = 'Calling…';
        else
            this._state.text = `${formatDuration(Date.now() - (c.startedAt ?? Date.now()))}${c.muted ? '  ·  muted' : ''}`;
    }

    _show() {
        if (this.actor.visible)
            return;
        this.actor.opacity = 0;
        this.actor.visible = true;
        this._place();
        this.actor.translation_y = -20;
        this.actor.ease({opacity: 255, translation_y: 0, duration: 220, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    _hide() {
        if (!this.actor.visible)
            return;
        this.actor.ease({
            opacity: 0,
            translation_y: -20,
            duration: 180,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => {
                if (!this._wanted())
                    this.actor.visible = false;
                else
                    this.actor.opacity = 255;
                this.actor.translation_y = 0;
            },
        });
    }

    _ring() {
        if (!this._soundEnabled())
            return;
        try {
            global.display.get_sound_player().play_from_theme('phone-incoming-call', 'Incoming call', null);
        } catch (e) {
            logError(e);
        }
    }

    _startRing() {
        this._stopRing();
        this._ring();
        let beat = 0;
        // One timer: pulse the avatar every second, ring every 3 seconds.
        this._ringTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, () => {
            beat++;
            if (beat % 3 === 0)
                this._ring();
            this._pulse();
            return GLib.SOURCE_CONTINUE;
        });
        this._pulse();
    }

    _pulse() {
        const a = this._avatar.actor;
        if (!a.mapped)
            return;
        a.set_pivot_point(0.5, 0.5);
        a.remove_all_transitions();
        a.ease({
            scale_x: 1.1,
            scale_y: 1.1,
            duration: 350,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => a.ease({scale_x: 1, scale_y: 1, duration: 450, mode: Clutter.AnimationMode.EASE_IN_OUT_SINE}),
        });
    }

    _stopRing() {
        if (this._ringTimer) {
            GLib.source_remove(this._ringTimer);
            this._ringTimer = 0;
        }
        this._avatar.actor.remove_all_transitions();
        this._avatar.actor.set_scale(1, 1);
    }

    destroy() {
        this._stopRing();
        if (this._monitorsId)
            Main.layoutManager.disconnect(this._monitorsId);
        this._avatar.actor.remove_all_transitions();
        Main.layoutManager.removeChrome(this.actor);
        this.actor.destroy();
    }
}
