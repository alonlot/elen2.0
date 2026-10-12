// Mouse and keyboard input for Elen. Uses Clutter virtual input devices, so it works on
// Wayland without ydotool or root rights. Positions come as fractions of the screen.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const MODIFIERS = {
    ctrl: 'Control_L', control: 'Control_L', shift: 'Shift_L', alt: 'Alt_L',
    super: 'Super_L', win: 'Super_L', meta: 'Super_L', cmd: 'Super_L', altgr: 'ISO_Level3_Shift',
};
const ALIASES = {
    enter: 'Return', return: 'Return', esc: 'Escape', escape: 'Escape', tab: 'Tab', space: 'space',
    backspace: 'BackSpace', delete: 'Delete', del: 'Delete', insert: 'Insert', home: 'Home',
    end: 'End', pageup: 'Page_Up', pagedown: 'Page_Down', pgup: 'Page_Up', pgdn: 'Page_Down',
    up: 'Up', down: 'Down', left: 'Left', right: 'Right', menu: 'Menu', print: 'Print',
    capslock: 'Caps_Lock', plus: 'plus', minus: 'minus',
};
const BUTTONS = {left: Clutter.BUTTON_PRIMARY, middle: Clutter.BUTTON_MIDDLE, right: Clutter.BUTTON_SECONDARY};
const SCROLL = {
    up: Clutter.ScrollDirection.UP, down: Clutter.ScrollDirection.DOWN,
    left: Clutter.ScrollDirection.LEFT, right: Clutter.ScrollDirection.RIGHT,
};

function sleep(ms) {
    return new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.max(1, ms), () => {
        resolve();
        return GLib.SOURCE_REMOVE;
    }));
}

function now() {
    return GLib.get_monotonic_time();
}

function keyval(name) {
    const lower = name.toLowerCase();
    if (MODIFIERS[lower])
        return Clutter[`KEY_${MODIFIERS[lower]}`];
    if (ALIASES[lower])
        return Clutter[`KEY_${ALIASES[lower]}`];
    if (/^f([1-9]|1[0-9]|2[0-4])$/.test(lower))
        return Clutter[`KEY_F${lower.slice(1)}`];
    if ([...name].length === 1)
        return Clutter.unicode_to_keysym(lower.codePointAt(0));
    const direct = Clutter[`KEY_${name}`];
    if (direct === undefined)
        throw new Error(`Unknown key '${name}'`);
    return direct;
}

export class InputController {
    // isBlocked() returns an error text when input must stop (for example an approval
    // dialog or Elen's chat is open), or ''.
    constructor(isBlocked = () => '') {
        this._isBlocked = isBlocked;
        const seat = Clutter.get_default_backend().get_default_seat();
        this._pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._cancelled = new Set();
    }

    cancel(id) {
        this._cancelled.add(id);
    }

    // Run the actions of one input_request in order. Throws on the first error.
    async run(id, actions) {
        try {
            for (const action of actions) {
                if (this._cancelled.has(id))
                    throw new Error('Stopped by the user');
                const blocked = this._isBlocked();
                if (blocked)
                    throw new Error(blocked);
                // eslint-disable-next-line no-await-in-loop
                await this._do(id, action);
            }
        } finally {
            this._cancelled.delete(id);
        }
    }

    async _do(id, a) {
        switch (a.type) {
        case 'move':
            await this._move(a);
            break;
        case 'click':
            await this._click(a);
            break;
        case 'scroll':
            if (a.fx !== undefined)
                await this._move(a);
            for (let i = 0; i < (a.amount ?? 3); i++) {
                this._pointer.notify_discrete_scroll(now(), SCROLL[a.direction] ?? SCROLL.down,
                    Clutter.ScrollSource.WHEEL);
                // eslint-disable-next-line no-await-in-loop
                await sleep(30);
            }
            break;
        case 'keys':
            await this._combo(a.combo);
            break;
        case 'type':
            await this._type(id, a.text ?? '', a.delay_ms ?? 8);
            break;
        default:
            throw new Error(`Unknown input action '${a.type}'`);
        }
    }

    async _move({fx, fy}) {
        const x = Math.round(Math.min(1, Math.max(0, fx)) * (global.stage.width - 1));
        const y = Math.round(Math.min(1, Math.max(0, fy)) * (global.stage.height - 1));
        this._pointer.notify_absolute_motion(now(), x, y);
        await sleep(40);
        return [x, y];
    }

    async _click(a) {
        const button = BUTTONS[a.button] ?? Clutter.BUTTON_PRIMARY;
        let pos = null;
        if (a.fx !== undefined)
            pos = await this._move(a);
        for (let i = 0; i < (a.count ?? 1); i++) {
            this._pointer.notify_button(now(), button, Clutter.ButtonState.PRESSED);
            // eslint-disable-next-line no-await-in-loop
            await sleep(30);
            this._pointer.notify_button(now(), button, Clutter.ButtonState.RELEASED);
            // eslint-disable-next-line no-await-in-loop
            await sleep(a.count > 1 ? 60 : 30);
        }
        if (pos)
            this._ring(...pos);
    }

    async _key(keyval, pressed) {
        this._keyboard.notify_keyval(now(), keyval,
            pressed ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);
        await sleep(12);
    }

    async _combo(combo) {
        const keys = String(combo).split('+').map(keyval);
        const down = [];
        try {
            for (const k of keys) {
                // eslint-disable-next-line no-await-in-loop
                await this._key(k, true);
                down.push(k);
            }
        } finally {
            // Always release what was pressed, so no key stays stuck.
            for (const k of down.reverse())
                // eslint-disable-next-line no-await-in-loop
                await this._key(k, false);
        }
        await sleep(30);
    }

    async _type(id, text, delay) {
        for (const ch of text) {
            if (this._cancelled.has(id))
                throw new Error('Stopped by the user');
            let k;
            if (ch === '\n')
                k = Clutter.KEY_Return;
            else if (ch === '\t')
                k = Clutter.KEY_Tab;
            else if (ch === '\r')
                continue;
            else
                k = Clutter.unicode_to_keysym(ch.codePointAt(0));
            // eslint-disable-next-line no-await-in-loop
            await this._key(k, true);
            // eslint-disable-next-line no-await-in-loop
            await this._key(k, false);
            if (delay > 0)
                // eslint-disable-next-line no-await-in-loop
                await sleep(delay);
        }
    }

    // A short ring where Elen clicked, so the user sees it.
    _ring(x, y) {
        const size = 40;
        const ring = new St.Widget({
            style_class: 'elen-click-ring', reactive: false,
            x: x - size / 2, y: y - size / 2, width: size, height: size,
        });
        Main.layoutManager.uiGroup.add_child(ring);
        ring.set_pivot_point(0.5, 0.5);
        ring.ease({
            opacity: 0, scale_x: 1.8, scale_y: 1.8, duration: 600,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => ring.destroy(),
        });
    }

    destroy() {
        this._pointer = null;
        this._keyboard = null;
    }
}
