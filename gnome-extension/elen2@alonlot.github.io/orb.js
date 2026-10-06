// Animated "arc reactor" orb, drawn with Cairo. Used in the panel and the HUD.
import GLib from 'gi://GLib';
import St from 'gi://St';

const COLORS = {
    idle: [0.24, 0.91, 1.0],
    thinking: [1.0, 0.76, 0.22],
    working: [1.0, 0.76, 0.22],
    waiting: [1.0, 0.52, 0.16],
    listening: [1.0, 0.27, 0.36],
    transcribing: [0.72, 0.5, 1.0],
    offline: [0.45, 0.5, 0.55],
    hud: [0.24, 0.91, 1.0],
};

export class Orb {
    constructor(size, state = 'offline') {
        this.actor = new St.DrawingArea({width: size, height: size, style_class: 'elen-orb'});
        this._state = state;
        this._phase = 0;
        this._timer = 0;
        this.actor.connect('repaint', area => this._draw(area));
        this.actor.connect('destroy', () => this._stopTimer());
        this.setState(state);
    }

    setState(state) {
        this._state = COLORS[state] ? state : 'idle';
        const animate = !['idle', 'offline'].includes(this._state);
        if (animate && !this._timer) {
            this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 40, () => {
                this._phase += 0.07;
                this.actor.queue_repaint();
                return GLib.SOURCE_CONTINUE;
            });
        } else if (!animate) {
            this._stopTimer();
        }
        this.actor.queue_repaint();
    }

    _stopTimer() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
    }

    _draw(area) {
        const cr = area.get_context();
        const [w, h] = area.get_surface_size();
        const cx = w / 2, cy = h / 2;
        const r = Math.min(w, h) / 2 - 1;
        const [red, green, blue] = COLORS[this._state];
        const pulse = this._state === 'listening'
            ? 0.55 + 0.45 * Math.abs(Math.sin(this._phase * 2.2))
            : 0.85;

        // Outer segmented ring, rotating.
        cr.setLineWidth(Math.max(1.2, r * 0.14));
        const segments = 6;
        for (let i = 0; i < segments; i++) {
            const start = this._phase + (i * 2 * Math.PI) / segments;
            cr.setSourceRGBA(red, green, blue, 0.9 * pulse);
            cr.arc(cx, cy, r * 0.88, start, start + (Math.PI / segments) * 1.2);
            cr.stroke();
        }
        // Inner counter-rotating ring.
        cr.setLineWidth(Math.max(1, r * 0.08));
        cr.setSourceRGBA(red, green, blue, 0.55);
        cr.arc(cx, cy, r * 0.6, -this._phase * 1.6, -this._phase * 1.6 + Math.PI * 1.4);
        cr.stroke();
        // Core.
        cr.setSourceRGBA(red, green, blue, 0.35 * pulse);
        cr.arc(cx, cy, r * 0.42, 0, 2 * Math.PI);
        cr.fill();
        cr.setSourceRGBA(Math.min(1, red + 0.4), Math.min(1, green + 0.4), Math.min(1, blue + 0.4), pulse);
        cr.arc(cx, cy, r * 0.2, 0, 2 * Math.PI);
        cr.fill();
        cr.$dispose();
    }

    destroy() {
        this._stopTimer();
        this.actor.destroy();
    }
}
