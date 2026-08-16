#!/usr/bin/env bash
# Installs and starts the Tele-OpenCode systemd user services.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cp "$DIR/tele-opencode-serve.service" "$DIR/tele-opencode-bot.service" "$DIR/tele-opencode-dashboard.service" "$UNIT_DIR/"

systemctl --user daemon-reload
systemctl --user enable --now tele-opencode-serve.service tele-opencode-bot.service tele-opencode-dashboard.service

echo "Services installed and started."
echo "Check status:  systemctl --user status tele-opencode-serve tele-opencode-bot tele-opencode-dashboard"
