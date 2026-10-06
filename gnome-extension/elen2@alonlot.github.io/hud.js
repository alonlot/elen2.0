// On-screen HUD: draws visual specs from the daemon as Jarvis-style panels,
// directly on the desktop (no window, no browser tab).
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {Orb} from './orb.js';

function wrapLabel(text, styleClass) {
    const label = new St.Label({text: String(text ?? ''), style_class: styleClass, x_expand: true});
    label.clutter_text.line_wrap = true;
    label.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
    label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
    return label;
}

function vbox(styleClass = '') {
    return new St.BoxLayout({vertical: true, style_class: styleClass, x_expand: true});
}

function hbox(styleClass = '') {
    return new St.BoxLayout({style_class: styleClass, x_expand: true});
}

function ring(percent, size) {
    const area = new St.DrawingArea({width: size, height: size});
    area.connect('repaint', a => {
        const cr = a.get_context();
        const [w, h] = a.get_surface_size();
        const r = Math.min(w, h) / 2 - 4;
        const p = Math.max(0, Math.min(100, Number(percent) || 0)) / 100;
        cr.setLineWidth(5);
        cr.setSourceRGBA(0.24, 0.91, 1.0, 0.15);
        cr.arc(w / 2, h / 2, r, 0, 2 * Math.PI);
        cr.stroke();
        const color = p > 0.85 ? [1, 0.3, 0.35] : p > 0.65 ? [1, 0.76, 0.22] : [0.24, 0.91, 1.0];
        cr.setSourceRGBA(...color, 0.95);
        cr.arc(w / 2, h / 2, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * p);
        cr.stroke();
        cr.$dispose();
    });
    return area;
}

function eventPhase(ev) {
    const now = Date.now();
    const s = Date.parse(ev.start_iso ?? '');
    const e = Date.parse(ev.end_iso ?? '');
    if (Number.isNaN(s) || Number.isNaN(e))
        return 'future';
    if (now >= e)
        return 'past';
    if (now >= s)
        return 'now';
    return 'future';
}

function flow(styleClass = '') {
    return new St.Widget({
        style_class: styleClass,
        x_expand: true,
        layout_manager: new Clutter.FlowLayout({column_spacing: 6, row_spacing: 6, homogeneous: false}),
    });
}

function humanize(key) {
    return String(key)
        .replace(/[_-]+/g, ' ')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .trim()
        .toUpperCase();
}

function formatScalar(v) {
    if (v === null || v === undefined || v === '')
        return '—';
    if (typeof v === 'boolean')
        return v ? 'Yes' : 'No';
    if (typeof v === 'number')
        return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, {maximumFractionDigits: 2});
    return String(v);
}

const isScalar = v => v === null || typeof v !== 'object';
const SERIES_COLORS = [[0.24, 0.91, 1.0], [1.0, 0.71, 0.28], [0.36, 1.0, 0.66], [0.85, 0.55, 1.0]];

// Lay out any JSON value: objects as label/value fields, arrays of objects as
// tables, arrays of scalars as tags. No definition needed.
function renderData(value, depth = 0) {
    if (isScalar(value))
        return wrapLabel(formatScalar(value), 'elen-data-value');
    if (Array.isArray(value)) {
        if (!value.length)
            return new St.Label({text: '—', style_class: 'elen-data-value'});
        if (value.every(isScalar)) {
            const tags = flow('elen-data-tags');
            for (const v of value)
                tags.add_child(new St.Label({text: formatScalar(v), style_class: 'elen-chip'}));
            return tags;
        }
        if (value.every(v => v && typeof v === 'object' && !Array.isArray(v))) {
            const cols = [];
            for (const row of value) {
                for (const k of Object.keys(row)) {
                    if (!cols.includes(k) && cols.length < 6)
                        cols.push(k);
                }
            }
            return RENDERERS.table({
                columns: cols.map(humanize),
                rows: value.map(row => cols.map(k => (isScalar(row[k]) ? formatScalar(row[k])
                    : Array.isArray(row[k]) ? row[k].map(formatScalar).join(', ') : '{…}'))),
            });
        }
        const box = vbox('elen-data-list');
        value.forEach(v => box.add_child(renderData(v, depth + 1)));
        return box;
    }
    const box = vbox(depth ? 'elen-data-nested' : 'elen-data');
    for (const [key, v] of Object.entries(value)) {
        if (isScalar(v) || (Array.isArray(v) && v.every(isScalar))) {
            const row = hbox('elen-data-row');
            row.add_child(new St.Label({text: humanize(key), style_class: 'elen-data-key'}));
            row.add_child(renderData(v, depth + 1));
            box.add_child(row);
        } else {
            box.add_child(new St.Label({text: humanize(key), style_class: 'elen-data-section'}));
            box.add_child(renderData(v, depth + 1));
        }
    }
    return box;
}

function barChart(spec) {
    const box = vbox('elen-chart');
    const labels = spec.labels ?? [];
    const series = (spec.series ?? []).filter(s => s.values?.length);
    const max = Math.max(1e-9, ...series.flatMap(s => s.values.filter(v => v !== null).map(Math.abs)));
    const width = 460;
    labels.forEach((label, i) => {
        const row = hbox('elen-chart-row');
        row.add_child(new St.Label({text: label, style_class: 'elen-chart-label', y_align: Clutter.ActorAlign.CENTER}));
        const bars = vbox('elen-chart-bars');
        series.forEach((ser, si) => {
            const v = ser.values[i];
            const line = hbox();
            const [r, g, b] = SERIES_COLORS[si % SERIES_COLORS.length];
            const w = v === null || v === undefined ? 0 : Math.max(2, Math.round(width * Math.abs(v) / max));
            line.add_child(new St.Widget({
                style_class: 'elen-chart-bar',
                style: `width: ${w}px; background-color: rgba(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)},0.85);`,
                y_align: Clutter.ActorAlign.CENTER,
            }));
            line.add_child(new St.Label({text: `${formatScalar(v)}${spec.unit ? ` ${spec.unit}` : ''}`, style_class: 'elen-chart-value'}));
            bars.add_child(line);
        });
        row.add_child(bars);
        box.add_child(row);
    });
    return box;
}

function lineChart(spec) {
    const box = vbox('elen-chart');
    const labels = spec.labels ?? [];
    const series = (spec.series ?? []).filter(s => s.values?.length);
    const all = series.flatMap(s => s.values.filter(v => v !== null && v !== undefined));
    if (!all.length) {
        box.add_child(new St.Label({text: 'NO DATA', style_class: 'elen-empty'}));
        return box;
    }
    const min = Math.min(...all), max = Math.max(...all);
    const span = max - min || 1;
    const area = new St.DrawingArea({height: 220, x_expand: true, style_class: 'elen-chart-area'});
    area.connect('repaint', a => {
        const cr = a.get_context();
        const [w, h] = a.get_surface_size();
        const pad = 8;
        cr.setLineWidth(1);
        cr.setSourceRGBA(0.24, 0.91, 1.0, 0.12);
        for (let i = 0; i <= 4; i++) {
            const y = pad + ((h - 2 * pad) * i) / 4;
            cr.moveTo(0, y);
            cr.lineTo(w, y);
        }
        cr.stroke();
        series.forEach((ser, si) => {
            const [r, g, b] = SERIES_COLORS[si % SERIES_COLORS.length];
            cr.setSourceRGBA(r, g, b, 0.95);
            cr.setLineWidth(2.5);
            let started = false;
            ser.values.forEach((v, i) => {
                if (v === null || v === undefined)
                    return;
                const x = pad + ((w - 2 * pad) * i) / Math.max(1, ser.values.length - 1);
                const y = pad + (h - 2 * pad) * (1 - (v - min) / span);
                if (started)
                    cr.lineTo(x, y);
                else
                    cr.moveTo(x, y);
                started = true;
            });
            cr.stroke();
        });
        cr.$dispose();
    });
    const scale = hbox('elen-chart-scale');
    scale.add_child(new St.Label({text: `max ${formatScalar(max)}${spec.unit ? ` ${spec.unit}` : ''}`, style_class: 'elen-meta', x_expand: true}));
    scale.add_child(new St.Label({text: `min ${formatScalar(min)}${spec.unit ? ` ${spec.unit}` : ''}`, style_class: 'elen-meta'}));
    box.add_child(scale);
    box.add_child(area);
    if (labels.length) {
        const axis = hbox('elen-chart-axis');
        axis.add_child(new St.Label({text: labels[0], style_class: 'elen-meta', x_expand: true}));
        if (labels.length > 2)
            axis.add_child(new St.Label({text: labels[Math.floor(labels.length / 2)], style_class: 'elen-meta', x_expand: true, x_align: Clutter.ActorAlign.CENTER}));
        axis.add_child(new St.Label({text: labels[labels.length - 1], style_class: 'elen-meta'}));
        box.add_child(axis);
    }
    return box;
}

function legend(spec) {
    const series = spec.series ?? [];
    if (series.length < 2)
        return null;
    const row = flow('elen-chart-legend');
    series.forEach((ser, si) => {
        const [r, g, b] = SERIES_COLORS[si % SERIES_COLORS.length];
        row.add_child(new St.Label({
            text: `■ ${ser.name}`,
            style: `color: rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)});`,
            style_class: 'elen-meta',
        }));
    });
    return row;
}

const RENDERERS = {
    data(spec) {
        return renderData(spec.data);
    },

    card(spec) {
        const box = vbox('elen-card');
        const top = hbox('elen-card-top');
        if (spec.image) {
            top.add_child(new St.Bin({
                style_class: 'elen-card-image',
                style: `background-image: url("${GLib.filename_to_uri(spec.image, null)}"); background-size: cover;`,
            }));
        }
        const fields = vbox('elen-data');
        for (const f of spec.fields ?? []) {
            const row = hbox('elen-data-row');
            row.add_child(new St.Label({text: String(f.label ?? '').toUpperCase(), style_class: 'elen-data-key'}));
            row.add_child(wrapLabel(formatScalar(f.value), 'elen-data-value'));
            fields.add_child(row);
        }
        top.add_child(fields);
        box.add_child(top);
        if (spec.tags?.length) {
            const tags = flow('elen-data-tags');
            for (const t of spec.tags)
                tags.add_child(new St.Label({text: t, style_class: 'elen-chip'}));
            box.add_child(tags);
        }
        if (spec.body)
            box.add_child(wrapLabel(spec.body, 'elen-text-body'));
        return box;
    },

    chart(spec) {
        const box = vbox('elen-chart-wrap');
        const l = legend(spec);
        if (l)
            box.add_child(l);
        box.add_child(spec.kind === 'line' ? lineChart(spec) : barChart(spec));
        return box;
    },

    timeline(spec) {
        const box = vbox('elen-timeline');
        for (const item of spec.items ?? []) {
            const row = hbox('elen-tl-row');
            row.add_child(new St.Label({text: item.time ?? '', style_class: 'elen-tl-time'}));
            row.add_child(new St.Widget({style_class: 'elen-tl-line', y_expand: true}));
            const text = vbox('elen-tl-text');
            const head = hbox();
            head.add_child(wrapLabel(item.title ?? '', 'elen-cal-title'));
            if (item.badge)
                head.add_child(new St.Label({text: item.badge, style_class: 'elen-badge', y_align: Clutter.ActorAlign.START}));
            text.add_child(head);
            if (item.detail)
                text.add_child(wrapLabel(item.detail, 'elen-cal-loc'));
            row.add_child(text);
            box.add_child(row);
        }
        return box;
    },

    calendar(spec) {
        const box = vbox('elen-cal');
        const events = spec.events ?? [];
        if (!events.length) {
            box.add_child(new St.Label({text: 'NO EVENTS SCHEDULED', style_class: 'elen-empty'}));
            return box;
        }
        const allDay = events.filter(e => e.all_day);
        if (allDay.length) {
            const chips = hbox('elen-cal-allday');
            for (const ev of allDay)
                chips.add_child(new St.Label({text: `◆ ${ev.title}`, style_class: 'elen-chip'}));
            box.add_child(chips);
        }
        let nextMarked = false;
        for (const ev of events.filter(e => !e.all_day)) {
            let phase = eventPhase(ev);
            if (phase === 'future' && !nextMarked) {
                phase = 'next';
                nextMarked = true;
            }
            const row = hbox(`elen-cal-row ${phase}`);
            const time = vbox('elen-cal-time');
            time.add_child(new St.Label({text: ev.start ?? '', style_class: 'elen-cal-start'}));
            time.add_child(new St.Label({text: ev.end ?? '', style_class: 'elen-cal-end'}));
            row.add_child(time);
            row.add_child(new St.Widget({style_class: `elen-cal-line ${phase}`, y_expand: true}));
            const card = vbox(`elen-cal-card ${phase}`);
            const head = hbox();
            head.add_child(wrapLabel(ev.title, 'elen-cal-title'));
            if (phase === 'now' || phase === 'next')
                head.add_child(new St.Label({text: phase === 'now' ? 'NOW' : 'NEXT', style_class: `elen-badge ${phase}`}));
            card.add_child(head);
            if (ev.location)
                card.add_child(wrapLabel(`⌖ ${ev.location}`, 'elen-cal-loc'));
            if (ev.calendar)
                card.add_child(new St.Label({text: ev.calendar, style_class: 'elen-meta'}));
            row.add_child(card);
            box.add_child(row);
        }
        return box;
    },

    list(spec) {
        const box = vbox('elen-list');
        const items = spec.items ?? [];
        if (!items.length)
            box.add_child(new St.Label({text: 'NOTHING TO SHOW', style_class: 'elen-empty'}));
        items.forEach((item, i) => {
            const row = hbox(`elen-list-row${item.unread ? ' unread' : ''}`);
            row.add_child(new St.Label({text: String(i + 1).padStart(2, '0'), style_class: 'elen-list-index'}));
            const text = vbox();
            text.add_child(wrapLabel(item.title ?? '', 'elen-list-title'));
            if (item.subtitle)
                text.add_child(wrapLabel(item.subtitle, 'elen-list-sub'));
            row.add_child(text);
            if (item.meta)
                row.add_child(new St.Label({text: item.meta, style_class: 'elen-meta', y_align: Clutter.ActorAlign.CENTER}));
            if (item.badge)
                row.add_child(new St.Label({text: item.badge, style_class: 'elen-badge', y_align: Clutter.ActorAlign.CENTER}));
            box.add_child(row);
        });
        return box;
    },

    stats(spec) {
        const box = vbox('elen-stats');
        const items = spec.items ?? [];
        for (let i = 0; i < items.length; i += 4) {
            const row = hbox('elen-stats-row');
            for (const item of items.slice(i, i + 4)) {
                const tile = vbox('elen-tile');
                if (item.percent !== undefined && item.percent !== null && item.percent !== '') {
                    const stack = new St.Widget({layout_manager: new Clutter.BinLayout(), x_align: Clutter.ActorAlign.CENTER});
                    stack.add_child(ring(item.percent, 84));
                    stack.add_child(new St.Label({
                        text: `${item.value ?? ''}${item.unit ?? ''}`, style_class: 'elen-tile-ring-value',
                        x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
                    }));
                    tile.add_child(stack);
                } else {
                    tile.add_child(new St.Label({text: `${item.value ?? ''}`, style_class: 'elen-tile-value', x_align: Clutter.ActorAlign.CENTER}));
                    if (item.unit)
                        tile.add_child(new St.Label({text: item.unit, style_class: 'elen-meta', x_align: Clutter.ActorAlign.CENTER}));
                }
                tile.add_child(new St.Label({text: String(item.label ?? '').toUpperCase(), style_class: 'elen-tile-label', x_align: Clutter.ActorAlign.CENTER}));
                row.add_child(tile);
            }
            box.add_child(row);
        }
        return box;
    },

    table(spec) {
        const box = vbox('elen-table');
        const cols = spec.columns ?? [];
        const header = hbox('elen-table-head');
        for (const c of cols)
            header.add_child(new St.Label({text: String(c).toUpperCase(), style_class: 'elen-table-cell head', x_expand: true}));
        box.add_child(header);
        for (const r of spec.rows ?? []) {
            const row = hbox('elen-table-row');
            for (let i = 0; i < Math.max(cols.length, r.length); i++)
                row.add_child(wrapLabel(r[i] ?? '', 'elen-table-cell'));
            box.add_child(row);
        }
        return box;
    },

    text(spec) {
        const box = vbox('elen-text');
        box.add_child(wrapLabel(spec.body ?? '', 'elen-text-body'));
        return box;
    },

    email(spec) {
        const box = vbox('elen-email');
        for (const key of ['from', 'to', 'cc', 'date']) {
            if (!spec[key])
                continue;
            const row = hbox('elen-email-field');
            row.add_child(new St.Label({text: key.toUpperCase(), style_class: 'elen-email-key'}));
            row.add_child(wrapLabel(spec[key], 'elen-email-value'));
            box.add_child(row);
        }
        box.add_child(wrapLabel(spec.subject ?? '(no subject)', 'elen-email-subject'));
        box.add_child(wrapLabel(spec.body ?? '', 'elen-text-body'));
        return box;
    },

    image(spec) {
        const box = vbox('elen-image');
        const uri = GLib.filename_to_uri(spec.path ?? '', null);
        box.add_child(new St.Bin({
            style_class: 'elen-image-view',
            style: `background-image: url("${uri}"); background-size: contain;`,
            x_expand: true,
        }));
        if (spec.caption)
            box.add_child(new St.Label({text: spec.caption, style_class: 'elen-meta'}));
        return box;
    },

    panels(spec) {
        const box = vbox('elen-panels');
        const panels = spec.panels ?? [];
        for (let i = 0; i < panels.length; i += 2) {
            const row = hbox('elen-panels-row');
            for (const p of panels.slice(i, i + 2)) {
                const cell = vbox('elen-subpanel');
                if (p.title)
                    cell.add_child(new St.Label({text: p.title.toUpperCase(), style_class: 'elen-subpanel-title'}));
                cell.add_child(renderBody(p));
                row.add_child(cell);
            }
            box.add_child(row);
        }
        return box;
    },
};

function renderBody(spec) {
    const render = RENDERERS[spec.type] ?? RENDERERS.data;
    try {
        return render(spec);
    } catch (e) {
        console.error(`Elen HUD: cannot render ${spec.type}: ${e}`);
        return wrapLabel(JSON.stringify(spec, null, 1), 'elen-text-body');
    }
}

export class Hud {
    constructor() {
        this._actor = null;
        this._orb = null;
        this._timeout = 0;
        this._toast = null;
        this._toastTimeout = 0;
    }

    get actor() {
        return this._actor;
    }

    show(spec) {
        this.hide(true);
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const width = Math.min(820, monitor.width - 64);

        const frame = vbox('elen-hud');
        frame.set_width(width);
        frame.reactive = true;

        const header = hbox('elen-hud-header');
        this._orb = new Orb(46, 'hud');
        header.add_child(this._orb.actor);
        const titles = vbox('elen-hud-titles');
        titles.add_child(new St.Label({text: (spec.title || spec.type || '').toUpperCase(), style_class: 'elen-hud-title'}));
        const now = GLib.DateTime.new_now_local();
        titles.add_child(new St.Label({text: spec.subtitle || now.format('%A %d %B · %H:%M'), style_class: 'elen-hud-subtitle'}));
        header.add_child(titles);
        const close = new St.Button({style_class: 'elen-hud-close', label: '✕', y_align: Clutter.ActorAlign.START});
        close.connect('clicked', () => this.hide());
        header.add_child(close);
        frame.add_child(header);
        frame.add_child(new St.Widget({style_class: 'elen-hud-rule', x_expand: true}));

        const scroll = new St.ScrollView({
            style_class: 'elen-hud-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
            x_expand: true,
            style: `max-height: ${Math.floor(monitor.height * 0.62)}px;`,
        });
        const content = vbox('elen-hud-content');
        content.add_child(renderBody(spec));
        scroll.set_child(content);
        frame.add_child(scroll);
        frame.add_child(new St.Label({text: 'ELEN 2.0  //  VISUAL INTERFACE', style_class: 'elen-hud-footer', x_align: Clutter.ActorAlign.END}));

        Main.layoutManager.addTopChrome(frame, {affectsInputRegion: true});
        frame.set_position(
            monitor.x + Math.floor((monitor.width - width) / 2),
            monitor.y + Main.panel.height + 48);
        frame.set_pivot_point(0.5, 0);
        frame.opacity = 0;
        frame.scale_y = 0.86;
        frame.ease({opacity: 255, scale_y: 1, duration: 320, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._actor = frame;

        const seconds = Number(spec.duration ?? 25);
        if (seconds > 0) {
            this._timeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => {
                this._timeout = 0;
                this.hide();
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    hide(instant = false) {
        if (this._timeout) {
            GLib.source_remove(this._timeout);
            this._timeout = 0;
        }
        const actor = this._actor;
        this._actor = null;
        this._orb = null;
        if (!actor)
            return;
        const remove = () => {
            Main.layoutManager.removeChrome(actor);
            actor.destroy();
        };
        if (instant) {
            remove();
            return;
        }
        actor.ease({opacity: 0, scale_y: 0.9, duration: 220, mode: Clutter.AnimationMode.EASE_IN_QUAD, onComplete: remove});
    }

    // Subtitle-style reply at the bottom of the screen, for answers while the chat is closed.
    toast(text, warning = false) {
        this._hideToast();
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !text)
            return;
        const width = Math.min(640, monitor.width - 64);
        const box = vbox(`elen-toast${warning ? ' warning' : ''}`);
        box.set_width(width);
        box.add_child(wrapLabel(text.length > 600 ? `${text.slice(0, 600)}…` : text, 'elen-toast-text'));
        Main.layoutManager.addTopChrome(box, {affectsInputRegion: false});
        const [, natHeight] = box.get_preferred_height(width);
        box.set_position(monitor.x + Math.floor((monitor.width - width) / 2),
            monitor.y + monitor.height - natHeight - 96);
        box.opacity = 0;
        box.ease({opacity: 255, duration: 200, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._toast = box;
        const seconds = Math.min(20, Math.max(5, Math.round(text.length / 14)));
        this._toastTimeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => {
            this._toastTimeout = 0;
            this._hideToast();
            return GLib.SOURCE_REMOVE;
        });
    }

    _hideToast() {
        if (this._toastTimeout) {
            GLib.source_remove(this._toastTimeout);
            this._toastTimeout = 0;
        }
        if (this._toast) {
            Main.layoutManager.removeChrome(this._toast);
            this._toast.destroy();
            this._toast = null;
        }
    }

    // Hide/show everything (used while taking a screenshot).
    setVisible(visible) {
        for (const a of [this._actor, this._toast]) {
            if (a)
                a.opacity = visible ? 255 : 0;
        }
    }

    destroy() {
        this.hide(true);
        this._hideToast();
    }
}
