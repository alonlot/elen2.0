// Approval dialog. Every "write" or "dangerous" action waits here for the user.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';

const LONG_FIELDS = ['body', 'task', 'description', 'command', 'text', 'message', 'content'];
const ARM_SECONDS = 3;

const RECIPIENT_STATUS = {
    contact: ['IN CONTACTS', 'ok'],
    typed_by_you: ['TYPED BY YOU', 'ok'],
    from_data: ['FROM EMAIL DATA', 'info'],
    unverified: ['UNVERIFIED', 'bad'],
    invalid: ['INVALID', 'bad'],
};

function asText(value) {
    if (value === null || value === undefined)
        return '';
    if (Array.isArray(value))
        return value.join(', ');
    if (typeof value === 'object')
        return JSON.stringify(value, null, 1);
    return String(value);
}

function wrap(label) {
    label.clutter_text.line_wrap = true;
    label.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
    label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
    return label;
}

const ElenConfirmDialog = GObject.registerClass(
class ElenConfirmDialog extends ModalDialog.ModalDialog {
    _init(conf, onDecision) {
        super._init({styleClass: 'elen-confirm', destroyOnClose: true});
        this._conf = conf;
        this._onDecision = onDecision;
        this._decided = false;
        this._entries = {};
        this._armTimer = 0;
        const danger = conf.risk === 'dangerous';

        const head = new St.BoxLayout({style_class: 'elen-confirm-head'});
        head.add_child(new St.Label({text: 'ELEN REQUESTS AUTHORIZATION', style_class: 'elen-confirm-kicker', x_expand: true}));
        head.add_child(new St.Label({text: danger ? 'DANGEROUS' : 'ACTION', style_class: `elen-risk ${conf.risk}`}));
        this.contentLayout.add_child(head);
        this.contentLayout.add_child(new St.Label({text: conf.title, style_class: 'elen-confirm-title'}));
        this.contentLayout.add_child(new St.Label({text: conf.tool, style_class: 'elen-meta'}));

        for (const w of conf.warnings ?? [])
            this.contentLayout.add_child(wrap(new St.Label({text: `⚠ ${w}`, style_class: 'elen-confirm-warning'})));

        if (conf.recipients?.length) {
            const box = new St.BoxLayout({vertical: true, style_class: 'elen-confirm-recipients'});
            for (const r of conf.recipients) {
                const [text, kind] = RECIPIENT_STATUS[r.status] ?? [r.status, 'info'];
                const row = new St.BoxLayout({style_class: 'elen-recipient-row'});
                row.add_child(new St.Label({text: `${r.field.toUpperCase()}  ${r.value}`, x_expand: true, style_class: 'elen-recipient'}));
                row.add_child(new St.Label({text, style_class: `elen-recipient-status ${kind}`}));
                box.add_child(row);
            }
            this.contentLayout.add_child(box);
        }

        const fields = new St.BoxLayout({vertical: true, style_class: 'elen-confirm-fields'});
        const editable = new Set(conf.editable ?? []);
        for (const [key, value] of Object.entries(conf.arguments ?? {})) {
            fields.add_child(new St.Label({text: key.toUpperCase() + (editable.has(key) ? '  (editable)' : ''), style_class: 'elen-field-label'}));
            const text = asText(value);
            if (editable.has(key)) {
                const entry = new St.Entry({text, style_class: 'elen-field-entry', can_focus: true, x_expand: true});
                if (LONG_FIELDS.includes(key) || text.length > 80 || text.includes('\n')) {
                    entry.clutter_text.single_line_mode = false;
                    entry.clutter_text.activatable = false;
                    entry.clutter_text.line_wrap = true;
                    entry.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
                    entry.add_style_class_name('multiline');
                }
                this._entries[key] = {entry, original: value};
                fields.add_child(entry);
            } else {
                fields.add_child(wrap(new St.Label({text, style_class: 'elen-field-value'})));
            }
        }
        const scroll = new St.ScrollView({
            style_class: 'elen-confirm-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
        });
        scroll.set_child(fields);
        this.contentLayout.add_child(scroll);

        this._reason = new St.Entry({
            hint_text: 'Optional note for Elen if you reject (for example: "make it shorter")',
            style_class: 'elen-field-entry reason', can_focus: true, x_expand: true,
        });
        this.contentLayout.add_child(this._reason);

        this.addButton({label: 'Reject', action: () => this._decide(false), key: Clutter.KEY_Escape});
        this._approve = this.addButton({label: danger ? 'Execute' : 'Approve', action: () => this._decide(true)});
        this._approve.add_style_class_name(danger ? 'elen-approve-danger' : 'elen-approve');

        // Arm delay: prevents an approval by a stray click or key press.
        this._approve.reactive = false;
        this._approve.can_focus = false;
        let left = danger ? ARM_SECONDS : 1;
        const base = this._approve.label;
        this._approve.label = `${base} (${left})`;
        this._armTimer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            left -= 1;
            if (left > 0) {
                this._approve.label = `${base} (${left})`;
                return GLib.SOURCE_CONTINUE;
            }
            this._approve.label = base;
            this._approve.reactive = true;
            this._approve.can_focus = true;
            this._armTimer = 0;
            return GLib.SOURCE_REMOVE;
        });
        this.connect('destroy', () => {
            if (this._armTimer)
                GLib.source_remove(this._armTimer);
            this._armTimer = 0;
        });
    }

    _collect() {
        const args = {...this._conf.arguments};
        for (const [key, {entry, original}] of Object.entries(this._entries)) {
            const text = entry.get_text();
            if (Array.isArray(original))
                args[key] = text.split(',').map(s => s.trim()).filter(s => s);
            else if (typeof original === 'number')
                args[key] = Number(text);
            else
                args[key] = text;
        }
        return args;
    }

    _decide(approved) {
        if (this._decided)
            return;
        this._decided = true;
        this._onDecision(approved, this._collect(), this._reason.get_text());
        this.close();
    }

    // Closed by the daemon (timeout); no answer is sent.
    dismiss() {
        this._decided = true;
        this.close();
    }
});

export class ConfirmManager {
    constructor(client) {
        this._client = client;
        this._queue = [];
        this._dialog = null;
        this._currentId = null;
    }

    add(conf) {
        if (this._currentId === conf.id || this._queue.some(c => c.id === conf.id))
            return;
        this._queue.push(conf);
        this._next();
    }

    closed(id) {
        this._queue = this._queue.filter(c => c.id !== id);
        if (this._dialog && this._currentId === id)
            this._dialog.dismiss();
    }

    _next() {
        if (this._dialog || !this._queue.length)
            return;
        const conf = this._queue.shift();
        this._currentId = conf.id;
        this._dialog = new ElenConfirmDialog(conf, (approved, args, reason) => {
            this._client.call('Confirm', [conf.id, approved, JSON.stringify(args), reason])
                .catch(e => console.error(`Elen: Confirm failed: ${e}`));
        });
        this._dialog.connect('closed', () => {
            this._dialog = null;
            this._currentId = null;
            this._next();
        });
        if (!this._dialog.open()) {
            // Could not grab the screen (for example the lock screen is up). Try again later.
            this._dialog.destroy();
            this._dialog = null;
            this._queue.unshift(conf);
            this._currentId = null;
            GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
                this._next();
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    destroy() {
        this._queue = [];
        if (this._dialog)
            this._dialog.dismiss();
        this._dialog = null;
    }
}
