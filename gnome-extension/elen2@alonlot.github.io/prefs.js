// Elen 2.0 settings window: model, custom URL and key for the brain, vision,
// rule checker and speech to text. Saved into ~/.config/elen/config.toml by the daemon.
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max'];

const SECTIONS = [
    {
        key: 'brain', title: 'Brain (the agent)',
        description: 'claude_cli runs "claude -p" as the agent and uses your Claude Code login. ' +
            'Model: any name, for example opus, sonnet, claude-opus-5-5, or a model on your own server.',
    },
    {key: 'vision', title: 'Vision', description: 'Looks at screenshots.'},
    {
        key: 'checker', title: 'Rule checker',
        description: 'Checks actions and replies against your permanent rules. Provider "same as brain" = brain settings at low effort.',
    },
    {key: 'stt', title: 'Speech to text', description: 'openai = any OpenAI-compatible transcription URL.'},
];

function proxy() {
    return Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, null,
        'org.elen.Assistant', '/org/elen/Assistant', 'org.elen.Assistant', null);
}

function callJson(p, method, args = null) {
    const result = p.call_sync(method, args, Gio.DBusCallFlags.NONE, 10000, null);
    return JSON.parse(result.deepUnpack()[0]);
}

export default class ElenPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        window.set_default_size(640, 820);
        const page = new Adw.PreferencesPage({title: 'Models', icon_name: 'preferences-system-symbolic'});
        window.add(page);

        let p, data;
        try {
            p = proxy();
            data = callJson(p, 'GetModels');
        } catch (e) {
            const group = new Adw.PreferencesGroup({
                title: 'The Elen daemon is not running',
                description: `Start it with: systemctl --user start elen\n\n${e.message}`,
            });
            page.add(group);
            return;
        }

        const rows = {};
        for (const section of SECTIONS) {
            const values = data[section.key] ?? {};
            const group = new Adw.PreferencesGroup({title: section.title, description: section.description});
            page.add(group);
            rows[section.key] = {};

            const providers = [...(data.providers?.[section.key] ?? [])];
            if (section.key === 'checker')
                providers.unshift('');
            const labels = providers.map(v => v || 'same as brain');
            const providerRow = new Adw.ComboRow({title: 'Provider', model: Gtk.StringList.new(labels)});
            providerRow.selected = Math.max(0, providers.indexOf(values.provider ?? ''));
            group.add(providerRow);
            rows[section.key].provider = () => providers[providerRow.selected] ?? '';

            const entry = (name, title, password = false) => {
                const cls = password ? Adw.PasswordEntryRow : Adw.EntryRow;
                const row = new cls({title, text: String(values[name] ?? '')});
                group.add(row);
                rows[section.key][name] = () => row.text;
            };
            entry('model', 'Model (free text)');
            entry('base_url', 'Custom URL (empty = default)');
            entry('api_key', 'API key (env:VAR, cmd:..., or the key; empty = claude login)', true);
            if (section.key === 'stt') {
                entry('language', 'Language (empty = auto)');
            } else {
                entry('auth_token', 'Auth token for a gateway (optional)', true);
                const effortRow = new Adw.ComboRow({title: 'Effort', model: Gtk.StringList.new(EFFORTS.map(e => e || 'default'))});
                effortRow.selected = Math.max(0, EFFORTS.indexOf(values.effort ?? ''));
                group.add(effortRow);
                rows[section.key].effort = () => EFFORTS[effortRow.selected];
            }
        }

        const applyGroup = new Adw.PreferencesGroup();
        const button = new Gtk.Button({label: 'Save and restart Elen', css_classes: ['suggested-action', 'pill'], halign: Gtk.Align.CENTER});
        button.connect('clicked', () => {
            const out = {};
            for (const [section, getters] of Object.entries(rows)) {
                out[section] = {};
                for (const [name, get] of Object.entries(getters))
                    out[section][name] = get();
            }
            let message;
            try {
                const res = callJson(p, 'SetModels', new GLib.Variant('(s)', [JSON.stringify(out)]));
                message = res.ok ? 'Saved. Elen is reloading.' : `Not saved: ${res.error}`;
            } catch (e) {
                message = `Not saved: ${e.message}`;
            }
            window.add_toast(new Adw.Toast({title: message, timeout: 4}));
        });
        applyGroup.add(button);
        page.add(applyGroup);
    }
}
