// Elen 2.0 settings window: model, custom URL and key for the brain, vision,
// rule checker and speech to text. Saved into ~/.config/elen/config.toml by the daemon.
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max'];
const TOOL_MODES = ['auto', 'native', 'prompt'];

const SECTIONS = [
    {
        key: 'brain', title: 'Brain (any LLM)',
        description: 'openai_compatible works with any server that has the OpenAI chat API: ' +
            'Ollama, LM Studio, vLLM, LiteLLM, OpenAI, OpenRouter, Groq, Gemini, Mistral, DeepSeek... ' +
            'Pick a preset or type your own URL and model name.',
    },
    {key: 'vision', title: 'Vision', description: 'Looks at screenshots. Needs a model that accepts images. "same as brain" = brain settings.'},
    {
        key: 'checker', title: 'Rule checker',
        description: 'Checks actions and replies against your permanent rules. "same as brain" = brain settings.',
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
            const labels = providers.map(v => v || 'same as brain');
            const isLlm = section.key !== 'stt';

            let presetRow = null;
            const presets = data.presets ?? [];
            if (isLlm) {
                presetRow = new Adw.ComboRow({
                    title: 'Preset',
                    subtitle: 'Fills provider and URL',
                    model: Gtk.StringList.new(['custom', ...presets.map(p => p.name)]),
                });
                group.add(presetRow);
            }

            const providerRow = new Adw.ComboRow({title: 'Provider', model: Gtk.StringList.new(labels)});
            providerRow.selected = Math.max(0, providers.indexOf(values.provider ?? ''));
            group.add(providerRow);
            rows[section.key].provider = () => providers[providerRow.selected] ?? '';

            const entries = {};
            const entry = (name, title, password = false) => {
                const cls = password ? Adw.PasswordEntryRow : Adw.EntryRow;
                const row = new cls({title, text: String(values[name] ?? '')});
                group.add(row);
                entries[name] = row;
                rows[section.key][name] = () => row.text;
            };
            entry('model', 'Model name (free text)');
            entry('base_url', 'Custom URL');
            entry('api_key', 'API key (env:VAR, cmd:..., or the key; empty = none)', true);

            presetRow?.connect('notify::selected', () => {
                const preset = presets[presetRow.selected - 1];
                if (!preset)
                    return;
                providerRow.selected = Math.max(0, providers.indexOf(preset.provider));
                entries.base_url.text = preset.base_url;
                if (preset.model)
                    entries.model.text = preset.model;
            });

            if (!isLlm) {
                entry('language', 'Language (empty = auto)');
                continue;
            }
            const modeRow = new Adw.ComboRow({
                title: 'Tool calling',
                subtitle: 'auto = native tools, or prompt-based tools if the model has none',
                model: Gtk.StringList.new(TOOL_MODES),
            });
            modeRow.selected = Math.max(0, TOOL_MODES.indexOf(values.tool_mode || 'auto'));
            group.add(modeRow);
            rows[section.key].tool_mode = () => TOOL_MODES[modeRow.selected];
            const effortRow = new Adw.ComboRow({
                title: 'Effort', subtitle: 'Only anthropic and claude_cli use it',
                model: Gtk.StringList.new(EFFORTS.map(e => e || 'default')),
            });
            effortRow.selected = Math.max(0, EFFORTS.indexOf(values.effort ?? ''));
            group.add(effortRow);
            rows[section.key].effort = () => EFFORTS[effortRow.selected];
            entry('auth_token', 'Auth token (claude_cli gateways only)', true);
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
