#!/bin/sh
# Remove the extension and the backends that install.sh added.
# Your chat history (~/.local/share/chathub/history) is kept. Delete it yourself if you want.
UUID=chathub@alonlot.github.io
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
gnome-extensions disable "$UUID" 2>/dev/null || true
rm -rf "$DATA/gnome-shell/extensions/$UUID" "$DATA/chathub/backends"
rm -f "$DATA"/dbus-1/services/io.github.alonlot.ChatHub.Backend.*.service
echo "Removed. Chat history is still in $DATA/chathub/history"
