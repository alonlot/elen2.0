// Small shared UI parts: icons, avatars, time labels.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import St from 'gi://St';

let _iconDir = null;

export function setExtensionPath(path) {
    _iconDir = path ? GLib.build_filenamev([path, 'icons']) : null;
}

// Our own icons (icons/<name>-symbolic.svg), or a theme icon name.
export function gicon(name) {
    if (_iconDir && name.startsWith('chathub')) {
        const file = Gio.File.new_for_path(GLib.build_filenamev([_iconDir, `${name}-symbolic.svg`]));
        return new Gio.FileIcon({file});
    }
    return new Gio.ThemedIcon({name});
}

export function icon(name, size = 16, styleClass = '') {
    return new St.Icon({gicon: gicon(name), icon_size: size, style_class: styleClass});
}

export function iconButton(name, {size = 16, styleClass = 'chathub-icon-button', tooltip = '', onClick = null} = {}) {
    const b = new St.Button({
        style_class: styleClass,
        can_focus: true,
        reactive: true,
        track_hover: true,
        child: icon(name, size),
        accessible_name: tooltip,
    });
    if (onClick)
        b.connect('clicked', () => onClick(b));
    return b;
}

export function label(text, styleClass, {wrap = false, ellipsize = true, xExpand = false} = {}) {
    const l = new St.Label({text: text ?? '', style_class: styleClass, x_expand: xExpand, y_align: Clutter.ActorAlign.CENTER});
    const ct = l.clutter_text;
    if (wrap) {
        ct.line_wrap = true;
        ct.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        ct.ellipsize = Pango.EllipsizeMode.NONE;
    } else if (ellipsize) {
        ct.ellipsize = Pango.EllipsizeMode.END;
    }
    return l;
}

// ---- Avatars --------------------------------------------------------------

const PALETTE = [
    ['#7c5cff', '#b25cff'], ['#3aa0ff', '#5c7cff'], ['#14c3a2', '#2fa7d8'],
    ['#ff7a59', '#ff4f8b'], ['#f7b733', '#fc8a4a'], ['#ff5c9d', '#c25cff'],
    ['#43c6ac', '#3a8dde'], ['#6a82fb', '#a46bf5'],
];

function colorsFor(seed) {
    let h = 0;
    for (const ch of String(seed))
        h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return PALETTE[h % PALETTE.length];
}

export function initials(name) {
    const parts = String(name ?? '?').trim().split(/[\s._@-]+/).filter(Boolean);
    if (parts.length === 0)
        return '?';
    const first = [...parts[0]][0] ?? '';
    const second = parts.length > 1 ? [...parts[parts.length - 1]][0] ?? '' : '';
    return (first + second).toUpperCase();
}

// Round avatar with initials (or a picture) and an optional presence dot.
export class Avatar {
    constructor(size = 40, {presence = true} = {}) {
        this._size = size;
        // Fixed layout: children are placed by x/y (BinLayout alignment is
        // not reliable for the small presence dot).
        this.actor = new St.Widget({
            width: size,
            height: size,
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'chathub-avatar-wrap',
        });
        this._circle = new St.Bin({
            style_class: 'chathub-avatar',
            width: size,
            height: size,
            x: 0,
            y: 0,
        });
        this._initials = new St.Label({
            style_class: 'chathub-avatar-initials',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            style: `font-size: ${Math.round(size * 0.38)}px;`,
        });
        this._circle.set_child(this._initials);
        this.actor.add_child(this._circle);
        this._dot = null;
        if (presence) {
            const d = Math.max(9, Math.round(size * 0.3));
            this._dot = new St.Widget({
                style_class: 'chathub-presence-dot offline',
                width: d,
                height: d,
                x: size - d + 1,
                y: size - d + 1,
                style: `border-radius: ${d}px;`,
            });
            this.actor.add_child(this._dot);
        }
    }

    update({name = '', avatar = null, presence = null, seed = null} = {}) {
        const r = this._size / 2;
        // The path goes into CSS, so allow only safe characters.
        const safe = avatar && /^\/[^"'()\\;\n]+$/.test(avatar);
        const file = safe && GLib.file_test(avatar, GLib.FileTest.EXISTS) ? avatar : null;
        if (file) {
            this._initials.text = '';
            this._circle.style = `border-radius: ${r}px; background-image: url("file://${file}"); background-size: cover;`;
        } else {
            const [a, b] = colorsFor(seed ?? name);
            this._initials.text = initials(name);
            this._circle.style = `border-radius: ${r}px; background-gradient-direction: vertical; ` +
                `background-gradient-start: ${a}; background-gradient-end: ${b};`;
        }
        if (this._dot) {
            this._dot.visible = !!presence;
            this._dot.style_class = `chathub-presence-dot ${presence ?? 'offline'}`;
        }
    }
}

// ---- Time -----------------------------------------------------------------

function dt(ms) {
    return GLib.DateTime.new_from_unix_local(Math.floor(ms / 1000));
}

export function formatTime(ms) {
    return dt(ms).format('%H:%M');
}

function dayNumber(ms) {
    const d = dt(ms);
    return d.get_year() * 1000 + d.get_day_of_year();
}

export function sameDay(a, b) {
    return dayNumber(a) === dayNumber(b);
}

export function formatDay(ms) {
    const now = Date.now();
    if (sameDay(ms, now))
        return 'Today';
    if (sameDay(ms, now - 86400000))
        return 'Yesterday';
    const d = dt(ms);
    if (now - ms < 6 * 86400000)
        return d.format('%A');
    if (d.get_year() === dt(now).get_year())
        return d.format('%e %B').trim();
    return d.format('%e %B %Y').trim();
}

// Short time for the contact list: 14:05 / Yesterday / Mon / 12 Nov.
export function formatShort(ms) {
    if (!ms)
        return '';
    const now = Date.now();
    if (sameDay(ms, now))
        return formatTime(ms);
    if (sameDay(ms, now - 86400000))
        return 'Yesterday';
    const d = dt(ms);
    if (now - ms < 6 * 86400000)
        return d.format('%a');
    return d.format('%e %b').trim();
}

export function formatDuration(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const ss = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

export const PRESENCE_LABEL = {
    online: 'Online',
    busy: 'Do not disturb',
    away: 'Away',
    offline: 'Offline',
};
