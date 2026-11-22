#!/bin/sh
# Install the Chat Hub GNOME Shell extension for the current user.
#   ./install.sh                 install the extension
#   ./install.sh --jabber        also install the Jabber backend as a D-Bus service
#   ./install.sh --custom        also install the example custom backend
set -e
cd "$(dirname "$0")"
UUID=chathub@alonlot.github.io
EXT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"
LIB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/chathub/backends"
SVC_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services"

echo "Installing the extension to $EXT_DIR"
rm -rf "$EXT_DIR"
mkdir -p "$EXT_DIR"
cp -r "$UUID"/. "$EXT_DIR"/
rm -f "$EXT_DIR/schemas/gschemas.compiled"
glib-compile-schemas "$EXT_DIR/schemas"

install_backend() { # $1 = script, $2 = D-Bus name suffix
    mkdir -p "$LIB_DIR" "$SVC_DIR"
    cp backends/chathub_backend.py "backends/$1" "$LIB_DIR"/
    cat > "$SVC_DIR/io.github.alonlot.ChatHub.Backend.$2.service" <<SERVICE
[D-BUS Service]
Name=io.github.alonlot.ChatHub.Backend.$2
Exec=/usr/bin/python3 $LIB_DIR/$1
SERVICE
    echo "Installed the $2 backend. Chat Hub starts it when needed."
}

for arg in "$@"; do
    case "$arg" in
        --jabber) install_backend jabber_backend.py Jabber ;;
        --custom) install_backend example_custom.py Custom ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

if command -v gnome-extensions >/dev/null; then
    gnome-extensions enable "$UUID" 2>/dev/null || true
fi
echo
echo "Done. Log out and log in again (Wayland), then the chat icon is in the top panel."
echo "If it is not enabled: gnome-extensions enable $UUID"
