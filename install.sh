#!/usr/bin/env bash
# Install Elen 2.0 for the current user on Ubuntu 24.04 (GNOME 46).
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$HOME/.local/share/elen"
VENV="$PREFIX/venv"
CONF="$HOME/.config/elen"
UUID="elen2@alonlot.github.io"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"

say() { printf '\033[96m▸ %s\033[0m\n' "$*"; }

say "Checking system packages"
MISSING=()
for pkg in python3-venv alsa-utils libnotify-bin libglib2.0-bin; do
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
done
if ((${#MISSING[@]})); then
  say "Installing: ${MISSING[*]} (needs sudo)"
  sudo apt-get install -y "${MISSING[@]}"
fi

say "Creating Python environment in $VENV"
mkdir -p "$PREFIX"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "$REPO[calendar]"

say "Config in $CONF"
mkdir -p "$CONF/plugins"
[ -f "$CONF/config.toml" ] || install -m 600 "$REPO/config/config.example.toml" "$CONF/config.toml"
[ -f "$CONF/contacts.toml" ] || install -m 600 "$REPO/config/contacts.example.toml" "$CONF/contacts.toml"
[ -f "$CONF/env" ] || install -m 600 "$REPO/config/env.example" "$CONF/env"

say "Service files"
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/share/dbus-1/services"
sed "s|@VENV@|$VENV|g" "$REPO/systemd/elen.service" > "$HOME/.config/systemd/user/elen.service"
sed "s|@VENV@|$VENV|g" "$REPO/systemd/org.elen.Assistant.service" \
  > "$HOME/.local/share/dbus-1/services/org.elen.Assistant.service"
systemctl --user daemon-reload
systemctl --user enable elen.service >/dev/null

say "GNOME Shell extension"
rm -rf "$EXT_DIR"
mkdir -p "$EXT_DIR"
cp -r "$REPO/gnome-extension/$UUID/." "$EXT_DIR/"
glib-compile-schemas "$EXT_DIR/schemas"
gnome-extensions enable "$UUID" 2>/dev/null || true

systemctl --user restart elen.service || true

cat <<MSG

Elen 2.0 is installed.

Next steps:
 1. Put your API keys in $CONF/env   (ANTHROPIC_API_KEY=..., OPENAI_API_KEY=... for voice)
 2. Edit $CONF/config.toml           (models, mail, calendar, plugins)
 3. systemctl --user restart elen
 4. Log out and log in again (GNOME on Wayland loads new extensions only at login).
    Then run: gnome-extensions enable $UUID

Shortcuts: Super+J opens the chat, Super+Shift+J starts or stops voice input.
Logs:      journalctl --user -u elen -f
Test:      $VENV/bin/elen chat
MSG
