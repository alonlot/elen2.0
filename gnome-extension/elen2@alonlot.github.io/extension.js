// Elen 2.0 GNOME Shell extension: panel orb + chat, HUD visuals, approvals,
// screenshots and keyboard shortcuts. The brain runs in the Elen daemon.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {ElenIndicator} from './chat.js';
import {ConfirmManager} from './confirm.js';
import {ElenClient} from './dbus.js';
import {Hud} from './hud.js';

Gio._promisify(Shell.Screenshot.prototype, 'screenshot', 'screenshot_finish');

export default class ElenExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._hud = new Hud();
        this._client = new ElenClient(
            (kind, data) => this._onEvent(kind, data),
            connected => this._onConnection(connected));
        this._confirm = new ConfirmManager(this._client);
        this._indicator = new ElenIndicator(this._client, () => this.openPreferences());
        Main.panel.addToStatusArea(this.uuid, this._indicator, 1, 'center');

        const modes = Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW | Shell.ActionMode.POPUP;
        Main.wm.addKeybinding('toggle-chat', this._settings, Meta.KeyBindingFlags.NONE, modes,
            () => this._indicator.menu.toggle());
        Main.wm.addKeybinding('push-to-talk', this._settings, Meta.KeyBindingFlags.NONE, modes,
            () => this._indicator.toggleListening());
        Main.wm.addKeybinding('stop', this._settings, Meta.KeyBindingFlags.NONE, modes,
            () => this._indicator.stop());
    }

    disable() {
        Main.wm.removeKeybinding('toggle-chat');
        Main.wm.removeKeybinding('push-to-talk');
        Main.wm.removeKeybinding('stop');
        this._confirm?.destroy();
        this._confirm = null;
        this._hud?.destroy();
        this._hud = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._client?.destroy();
        this._client = null;
        this._settings = null;
    }

    _onConnection(connected) {
        if (!this._indicator)
            return;
        if (!connected) {
            this._indicator.setState('offline');
            return;
        }
        this._client.call('RegisterUI').then(() => {
            this._indicator?.setState('idle');
            return this._client.call('PendingConfirmations');
        }).then(json => {
            for (const conf of JSON.parse(json ?? '[]'))
                this._confirm?.add(conf);
        }).catch(e => console.error(`Elen: ${e}`));
    }

    _onEvent(kind, data) {
        if (!this._indicator)
            return;
        switch (kind) {
        case 'state':
            this._indicator.setState(data.state);
            break;
        case 'message':
            this._indicator.addMessage(data);
            if (data.role === 'assistant' && !this._indicator.menu.isOpen &&
                this._settings.get_boolean('show-reply-toast'))
                this._hud.toast(data.text, !!data.meta?.warning);
            break;
        case 'tool':
            this._indicator.setToolStatus(data);
            break;
        case 'delta':
            this._indicator.addDelta(data.id, data.text ?? '');
            break;
        case 'delta_end':
            this._indicator.endDelta();
            break;
        case 'visual':
            this._hud.show(data);
            break;
        case 'visual_hide':
            this._hud.hide();
            break;
        case 'confirm':
            this._indicator.menu.close();
            this._confirm.add(data);
            break;
        case 'confirm_closed':
            this._confirm.closed(data.id);
            break;
        case 'screenshot_request':
            this._takeScreenshot(data);
            break;
        case 'transcript':
            this._indicator.showTranscript(data.text);
            break;
        case 'history_cleared':
            this._indicator.clearView();
            break;
        case 'error':
            Main.notify('Elen 2.0', data.text ?? 'Error');
            break;
        }
    }

    async _takeScreenshot({id, path}) {
        let ok = false;
        const menu = this._indicator?.menu;
        const menuWasVisible = menu?.actor.visible;
        try {
            // Hide Elen's own UI so it does not cover what the user sees.
            if (menuWasVisible)
                menu.actor.opacity = 0;
            this._hud?.setVisible(false);
            await new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
                resolve();
                return GLib.SOURCE_REMOVE;
            }));
            const file = Gio.File.new_for_path(path);
            const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
            const shooter = new Shell.Screenshot();
            await shooter.screenshot(false, stream);
            stream.close(null);
            ok = true;
        } catch (e) {
            console.error(`Elen: screenshot failed: ${e}`);
        } finally {
            if (menuWasVisible && menu)
                menu.actor.opacity = 255;
            this._hud?.setVisible(true);
        }
        this._client?.call('ScreenshotDone', [id, ok]).catch(() => {});
    }
}
