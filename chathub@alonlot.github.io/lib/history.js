// Local chat history. One append-only JSON Lines file per conversation:
//   ~/.local/share/chathub/history/<account hash>/<contact hash>.jsonl
//
// Each line is a message, or a status update {"t":"status","id":..,"status":..}.
// Writes are queued per file so lines never interleave.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {normalizeMessage} from './api.js';

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function hash(s) {
    return GLib.compute_checksum_for_string(GLib.ChecksumType.SHA1, String(s), -1).slice(0, 20);
}

function loadFile(file) {
    return new Promise((resolve, reject) => {
        file.load_contents_async(null, (f, res) => {
            try {
                const [, bytes] = f.load_contents_finish(res);
                resolve(decoder.decode(bytes));
            } catch (e) {
                if (e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
                    resolve('');
                else
                    reject(e);
            }
        });
    });
}

function appendFile(file, text) {
    return new Promise((resolve, reject) => {
        file.append_to_async(Gio.FileCreateFlags.PRIVATE, GLib.PRIORITY_DEFAULT, null, (f, res) => {
            let stream;
            try {
                stream = f.append_to_finish(res);
            } catch (e) {
                reject(e);
                return;
            }
            stream.write_bytes_async(new GLib.Bytes(encoder.encode(text)), GLib.PRIORITY_DEFAULT, null, (s, r) => {
                try {
                    s.write_bytes_finish(r);
                    s.close(null);
                    resolve();
                } catch (e) {
                    reject(e);
                }
            });
        });
    });
}

export class HistoryStore {
    constructor(baseDir = GLib.build_filenamev([GLib.get_user_data_dir(), 'chathub', 'history'])) {
        this._base = baseDir;
        this._queues = new Map();
    }

    get baseDir() {
        return this._base;
    }

    _file(accountId, contactId) {
        return Gio.File.new_for_path(GLib.build_filenamev([this._base, hash(accountId), `${hash(contactId)}.jsonl`]));
    }

    _ensureDir(file) {
        const dir = file.get_parent();
        if (!dir.query_exists(null))
            GLib.mkdir_with_parents(dir.get_path(), 0o700);
    }

    _enqueue(file, text) {
        const key = file.get_path();
        const prev = this._queues.get(key) ?? Promise.resolve();
        const next = prev.then(() => {
            this._ensureDir(file);
            return appendFile(file, text);
        }).catch(e => logError(e, 'Chat Hub: cannot write history'));
        this._queues.set(key, next);
        next.then(() => {
            if (this._queues.get(key) === next)
                this._queues.delete(key);
        });
        return next;
    }

    // Wait for all pending writes (used by tests and on shutdown).
    flush() {
        return Promise.all([...this._queues.values()]);
    }

    append(accountId, message) {
        const m = normalizeMessage(message);
        return this._enqueue(this._file(accountId, m.contactId), `${JSON.stringify(m)}\n`);
    }

    setStatus(accountId, contactId, messageId, status) {
        return this._enqueue(this._file(accountId, contactId),
            `${JSON.stringify({t: 'status', id: messageId, status})}\n`);
    }

    // Return all stored messages for a conversation, oldest first,
    // at most `limit` (the newest ones).
    async load(accountId, contactId, limit = 500) {
        const file = this._file(accountId, contactId);
        await (this._queues.get(file.get_path()) ?? Promise.resolve());
        const text = await loadFile(file);
        const byId = new Map();
        for (const line of text.split('\n')) {
            if (!line.trim())
                continue;
            let rec;
            try {
                rec = JSON.parse(line);
            } catch {
                continue; // a broken line (power loss) must not lose the rest
            }
            if (rec.t === 'status') {
                const m = byId.get(rec.id);
                if (m)
                    m.status = rec.status;
            } else if (rec.contactId) {
                try {
                    const m = normalizeMessage(rec);
                    byId.set(m.id, m);
                } catch {}
            }
        }
        const all = [...byId.values()].sort((a, b) => a.time - b.time);
        return all.slice(-limit);
    }

    async clear(accountId, contactId) {
        const file = this._file(accountId, contactId);
        await (this._queues.get(file.get_path()) ?? Promise.resolve());
        try {
            file.delete(null);
        } catch {}
    }

    // Delete the history of every conversation.
    clearAll() {
        const root = Gio.File.new_for_path(this._base);
        const rm = f => {
            const type = f.query_file_type(Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
            if (type === Gio.FileType.DIRECTORY) {
                const en = f.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
                let info;
                while ((info = en.next_file(null)))
                    rm(f.get_child(info.get_name()));
            }
            f.delete(null);
        };
        if (root.query_exists(null))
            rm(root);
    }
}
