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
        if (this._shotDelayId) {
            // The screenshot promise never resolves then; it only waits for this timer.
            GLib.source_remove(this._shotDelayId);
            this._shotDelayId = 0;
        }
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
            this._confirm?.clear();
            return;
        }
        this._client.call('RegisterUI').then(json => {
            // The daemon may be busy (after a screen unlock the extension starts again).
            const status = JSON.parse(json ?? '{}');
            this._indicator?.setState(status.state ?? 'idle');
            this._indicator?.setWakeState(status.wake ?? {enabled: false});
            return this._client?.call('PendingConfirmations');
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
        case 'reloaded':
            // Settings were saved and the daemon restarted its parts: the wake listener
            // can be on or off now.
            this._indicator.setWakeState(data.wake ?? {enabled: false});
            if (data.state)
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
        case 'highlight':
            this._hud.highlight(data);
            break;
        case 'highlight_hide':
            this._hud.clearHighlight();
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
        case 'wake':
            if (!this._indicator.menu.isOpen)
                this._hud.toast('Listening…');
            break;
        case 'wake_state':
            this._indicator.setWakeState(data);
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

    // Visible windows, bottom to top, in stage pixels. The daemon uses them to put the
    // accessibility boxes of an app on the screen and to drop boxes under other windows.
    _visibleWindows() {
        const rect = r => ({x: r.x, y: r.y, width: r.width, height: r.height});
        const workspace = global.workspace_manager.get_active_workspace();
        return global.get_window_actors()
            .map(a => a.get_meta_window())
            .filter(w => w && !w.minimized && w.showing_on_its_workspace() &&
                (w.is_on_all_workspaces() || w.get_workspace() === workspace) &&
                w.get_window_type() !== Meta.WindowType.DESKTOP)
            .map(w => ({
                pid: w.get_pid(),
                title: w.get_title() ?? '',
                wm_class: w.get_wm_class() ?? '',
                x11: w.get_client_type() === Meta.WindowClientType.X11,
                focus: w.has_focus(),
                frame: rect(w.get_frame_rect()),
                buffer: rect(w.get_buffer_rect()),
            }));
    }

    // Monitor positions in stage pixels, so the daemon can split the screenshot per monitor
    // and map a found spot back to the screen.
    _writeLayout(path) {
        const lm = Main.layoutManager;
        let windows = [];
        try {
            windows = this._visibleWindows();
        } catch (e) {
            console.error(`Elen: cannot list windows: ${e}`);
        }
        const layout = {
            stage: {width: global.stage.width, height: global.stage.height},
            monitors: lm.monitors.map(m => ({
                index: m.index, x: m.x, y: m.y, width: m.width, height: m.height,
                scale: m.geometry_scale ?? 1, primary: m.index === lm.primaryIndex,
            })),
            windows,
        };
        try {
            GLib.file_set_contents(path, JSON.stringify(layout));
        } catch (e) {
            console.error(`Elen: cannot write monitor layout: ${e}`);
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
            await new Promise(resolve => {
                this._shotDelayId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
                    this._shotDelayId = 0;
                    resolve();
                    return GLib.SOURCE_REMOVE;
                });
            });
            if (!this._indicator)
                throw new Error('extension disabled');
            const file = Gio.File.new_for_path(path);
            const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
            const shooter = new Shell.Screenshot();
            await shooter.screenshot(false, stream);
            stream.close(null);
            this._writeLayout(`${path}.json`);
            ok = true;
        } catch (e) {
            console.error(`Elen: screenshot failed: ${e}`);
        } finally {
            // Read the menu again: disable() may have destroyed the one from before the await.
            if (menuWasVisible && this._indicator?.menu === menu)
                menu.actor.opacity = 255;
            this._hud?.setVisible(true);
        }
        this._client?.call('ScreenshotDone', [id, ok]).catch(() => {});
    }
}
