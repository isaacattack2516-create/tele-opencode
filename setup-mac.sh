#!/bin/zsh
# One-time macOS setup for Tele-OpenCode.
# Installs and starts three launchd LaunchAgents: serve, bot, dashboard.
# Run AFTER config.json has been filled in.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LA="$HOME/Library/LaunchAgents"
mkdir -p "$LA" "$DIR/logs"
UID_NUM="$(id -u)"

for name in serve bot dashboard; do
  label="com.teleopencode.$name"
  plist="$LA/$label.plist"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$DIR/run-service.sh</string>
    <string>$name</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/$name.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/$name.err.log</string>
  <key>WorkingDirectory</key><string>$DIR</string>
</dict>
</plist>
PLIST
  # Unload previous (ignore failure), then load.
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$plist"
  echo "started $label"
done

echo "Mac setup complete."
echo "Dashboard: http://127.0.0.1:19125"
