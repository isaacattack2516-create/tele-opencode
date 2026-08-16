#!/usr/bin/env bash
# Starts the local `opencode serve` instance that the Telegram bot talks to.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PASSWORD="$(python3 -c "import json,sys; d=json.load(open('$DIR/config.json')); print(d['devices']['linux']['password'])")"

export OPENCODE_SERVER_USERNAME="opencode"
export OPENCODE_SERVER_PASSWORD="$PASSWORD"
exec "$HOME/.opencode/bin/opencode" serve --hostname 0.0.0.0 --port 19123
