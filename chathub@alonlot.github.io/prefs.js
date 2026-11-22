// Chat Hub settings window.
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const BUS_PREFIX = 'io.github.alonlot.ChatHub.Backend.';

function switchRow(settings, key, title, subtitle = '') {
    const row = new Adw.SwitchRow({title, subtitle});
    settings.bind(key, row, 'active', Gio.SettingsBindFlags.DEFAULT);
    return row;
}

export default class ChatHubPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        window.set_default_size(640, 720);
        window.search_enabled = true;

        // ---- Profile ----
        const page = new Adw.PreferencesPage({title: 'General', icon_name: 'user-info-symbolic'});
        window.add(page);

        const profile = new Adw.PreferencesGroup({title: 'Profile', description: 'How you appear in Chat Hub.'});
        page.add(profile);
        const name = new Adw.EntryRow({title: `Your name (empty = ${GLib.get_real_name() || GLib.get_user_name()})`});
        settings.bind('display-name', name, 'text', Gio.SettingsBindFlags.DEFAULT);
        profile.add(name);

        const avatarRow = new Adw.ActionRow({title: 'Picture'});
        const updateAvatar = () => {
            const p = settings.get_string('avatar-path');
            avatarRow.subtitle = p || 'Initials on a color';
        };
        updateAvatar();
        settings.connect('changed::avatar-path', updateAvatar);
        const choose = new Gtk.Button({label: 'Choose…', valign: Gtk.Align.CENTER});
        choose.connect('clicked', () => {
            const filter = new Gtk.FileFilter({name: 'Images'});
            filter.add_mime_type('image/*');
            const filters = new Gio.ListStore({item_type: Gtk.FileFilter});
            filters.append(filter);
            const dialog = new Gtk.FileDialog({title: 'Choose a picture', filters, modal: true});
            dialog.open(window, null, (d, res) => {
                try {
                    const file = d.open_finish(res);
                    if (file?.get_path())
                        settings.set_string('avatar-path', file.get_path());
                } catch {}
            });
        });
        const reset = new Gtk.Button({icon_name: 'edit-clear-symbolic', valign: Gtk.Align.CENTER, tooltip_text: 'Use initials'});
        reset.add_css_class('flat');
        reset.connect('clicked', () => settings.set_string('avatar-path', ''));
        avatarRow.add_suffix(choose);
        avatarRow.add_suffix(reset);
        profile.add(avatarRow);

        const presence = new Adw.ComboRow({
            title: 'Presence',
            model: Gtk.StringList.new(['Online', 'Do not disturb', 'Away', 'Invisible']),
        });
        const values = ['online', 'busy', 'away', 'offline'];
        presence.selected = Math.max(0, values.indexOf(settings.get_string('presence')));
        presence.connect('notify::selected', () => settings.set_string('presence', values[presence.selected]));
        settings.connect('changed::presence', () => {
            presence.selected = Math.max(0, values.indexOf(settings.get_string('presence')));
        });
        profile.add(presence);

        // ---- Alerts ----
        const alerts = new Adw.PreferencesGroup({title: 'Alerts', description: '"Do not disturb" mutes all alerts.'});
        page.add(alerts);
        alerts.add(switchRow(settings, 'notifications', 'Notifications', 'Show new messages when the chat window is closed'));
        alerts.add(switchRow(settings, 'sounds', 'Sounds', 'Play a sound for new messages and calls'));

        const keys = new Adw.PreferencesGroup({
            title: 'Keyboard',
            description: GLib.markup_escape_text('Format: <Super><Alt>c. Leave empty for no shortcut. Press Enter to save.', -1),
        });
        page.add(keys);
        const shortcut = new Adw.EntryRow({title: 'Shortcut to open the chat', show_apply_button: true});
        shortcut.text = settings.get_strv('toggle-chat')[0] ?? '';
        shortcut.connect('apply', () => {
            const accel = shortcut.text.trim();
            const [ok] = Gtk.accelerator_parse(accel);
            if (accel === '' || ok) {
                settings.set_strv('toggle-chat', accel ? [accel] : []);
                shortcut.remove_css_class('error');
            } else {
                shortcut.add_css_class('error');
            }
        });
        keys.add(shortcut);

        // ---- Accounts ----
        const accPage = new Adw.PreferencesPage({title: 'Accounts', icon_name: 'system-users-symbolic'});
        window.add(accPage);
        const builtIn = new Adw.PreferencesGroup({title: 'Built-in'});
        accPage.add(builtIn);
        const demo = new Adw.SwitchRow({title: 'Demo account', subtitle: 'Sample contacts that answer and call you. Good to try the UI.'});
        demo.active = settings.get_strv('providers').includes('demo');
        demo.connect('notify::active', () => {
            const list = settings.get_strv('providers').filter(p => p !== 'demo');
            if (demo.active)
                list.unshift('demo');
            settings.set_strv('providers', list);
        });
        builtIn.add(demo);

        const backends = new Adw.PreferencesGroup({
            title: 'Backends',
            description: 'Programs that connect a chat network (Jabber, your own service) over D-Bus. ' +
                'Chat Hub finds them by itself. See docs/API.md.',
        });
        accPage.add(backends);
        backends.add(switchRow(settings, 'autostart-backends', 'Start installed backends',
            'Start D-Bus services named io.github.alonlot.ChatHub.Backend.*'));
        const running = new Adw.ActionRow({title: 'Running now', subtitle: '…'});
        const refresh = new Gtk.Button({icon_name: 'view-refresh-symbolic', valign: Gtk.Align.CENTER});
        refresh.add_css_class('flat');
        const scan = () => {
            Gio.DBus.session.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'ListNames', null, new GLib.VariantType('(as)'), Gio.DBusCallFlags.NONE, -1, null, (c, r) => {
                    try {
                        const [names] = c.call_finish(r).deepUnpack();
                        const found = names.filter(n => n.startsWith(BUS_PREFIX)).map(n => n.slice(BUS_PREFIX.length));
                        running.subtitle = found.length ? found.join(', ') : 'None';
                    } catch (e) {
                        running.subtitle = e.message;
                    }
                });
        };
        refresh.connect('clicked', scan);
        running.add_suffix(refresh);
        backends.add(running);
        scan();

        // ---- History ----
        const hist = new Adw.PreferencesGroup({title: 'Chat history', description: 'Stored on this computer in ~/.local/share/chathub/history.'});
        page.add(hist);
        const limit = new Adw.SpinRow({
            title: 'Messages to load per chat',
            adjustment: new Gtk.Adjustment({lower: 50, upper: 20000, step_increment: 50, page_increment: 500}),
        });
        settings.bind('history-limit', limit, 'value', Gio.SettingsBindFlags.DEFAULT);
        hist.add(limit);
        const clearRow = new Adw.ActionRow({title: 'Delete all chat history', subtitle: 'This cannot be undone.'});
        const clear = new Gtk.Button({label: 'Delete…', valign: Gtk.Align.CENTER});
        clear.add_css_class('destructive-action');
        clear.connect('clicked', () => {
            const dialog = new Adw.MessageDialog({
                transient_for: window,
                heading: 'Delete all chat history?',
                body: 'All messages stored on this computer are deleted. Accounts with server history can load them again.',
            });
            dialog.add_response('cancel', 'Cancel');
            dialog.add_response('delete', 'Delete');
            dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE);
            dialog.connect('response', (_d, response) => {
                if (response === 'delete')
                    settings.set_int64('history-cleared-at', GLib.get_real_time());
            });
            dialog.present();
        });
        clearRow.add_suffix(clear);
        hist.add(clearRow);
    }
}
