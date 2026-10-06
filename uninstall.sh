#!/usr/bin/env bash
# Remove Elen 2.0. Your config (~/.config/elen) and data (~/.local/share/elen/*.json) are kept.
set -euo pipefail
UUID="elen2@alonlot.github.io"
gnome-extensions disable "$UUID" 2>/dev/null || true
systemctl --user disable --now elen.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/elen.service" "$HOME/.local/share/dbus-1/services/org.elen.Assistant.service"
rm -rf "$HOME/.local/share/gnome-shell/extensions/$UUID" "$HOME/.local/share/elen/venv"
systemctl --user daemon-reload
echo "Elen 2.0 removed. Config kept in ~/.config/elen."
