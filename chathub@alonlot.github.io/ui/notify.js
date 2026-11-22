// Desktop notifications for new messages. Works on GNOME 45 and 46+
// (the MessageTray API changed in 46).
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import {PACKAGE_VERSION} from 'resource:///org/gnome/shell/misc/config.js';

import {gicon} from './widgets.js';

const MAJOR = parseInt(PACKAGE_VERSION, 10);

export class Notifier {
    constructor() {
        this._source = null;
        this._byKey = new Map();
    }

    _ensureSource() {
        if (this._source)
            return this._source;
        let source;
        if (MAJOR >= 46) {
            source = new MessageTray.Source({title: 'Chat Hub', icon: gicon('chathub')});
        } else {
            source = new MessageTray.Source('Chat Hub', 'user-available-symbolic');
            source.getIcon = () => gicon('chathub');
        }
        source.connect('destroy', () => {
            this._source = null;
            this._byKey.clear();
        });
        Main.messageTray.add(source);
        this._source = source;
        return source;
    }

    // Show (or replace) the notification for one conversation.
    show(key, title, body, onActivate) {
        // Remove the old one first: GNOME can destroy the source with its
        // last notification.
        this._byKey.get(key)?.destroy();
        const source = this._ensureSource();
        let n;
        if (MAJOR >= 46) {
            n = new MessageTray.Notification({source, title, body});
        } else {
            n = new MessageTray.Notification(source, title, body);
        }
        n.connect('activated', () => onActivate());
        n.connect('destroy', () => {
            if (this._byKey.get(key) === n)
                this._byKey.delete(key);
        });
        this._byKey.set(key, n);
        if (MAJOR >= 46)
            source.addNotification(n);
        else
            source.showNotification(n);
    }

    // Remove the notification of a conversation the user has opened.
    dismiss(key) {
        this._byKey.get(key)?.destroy();
    }

    destroy() {
        this._source?.destroy();
        this._source = null;
        this._byKey.clear();
    }
}
