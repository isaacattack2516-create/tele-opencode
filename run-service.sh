#!/bin/zsh
# Cross-service launcher for Tele-OpenCode on macOS (invoked by launchd).
# Usage: run-service.sh <bot|serve|dashboard>
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

service_name() {
  /usr/bin/python3 - "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], "r"))["devices"]
# A machine is the hub if any device has is_hub true; its local serve runs
# on this machine. Prefer the hub device; fall back to any entry keyed to this box.
hub = next((v for v in d.values() if v.get("is_hub")), None)
if hub and hub.get("password"):
    print(hub["password"])
    sys.exit(0)
for v in d.values():
    if v.get("password"):
        print(v["password"]); sys.exit(0)
PY
}

case "$1" in
  serve)
    PASSWORD="$(service_name "$DIR/config.json")"
    export OPENCODE_SERVER_USERNAME="opencode"
    export OPENCODE_SERVER_PASSWORD="$PASSWORD"
    exec "$HOME/.opencode/bin/opencode" serve --hostname 127.0.0.1 --port 19123
    ;;
  bot) exec /usr/bin/python3 "$DIR/bot.py" ;;
  dashboard) exec /usr/bin/python3 "$DIR/dashboard.py" ;;
  *) echo "unknown service: $1" >&2; exit 1 ;;
esac
