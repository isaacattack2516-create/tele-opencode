#!/usr/bin/env bash
# Tele-OpenCode updater.
#
# Pulls the latest code from GitHub for this machine and restarts its local
# OpenCode services. Designed to be run either:
#   - directly on the machine (shell), or
#   - remotely via the bot's /update command, which asks each machine's
#     opencode to run this script through its bash tool.
#
# Reads this machine's own config.json for the repo location, remote URL and
# GitHub read token (all per-machine secrets that are NOT committed to git).
#
# Idempotent and safe: clones the repo if missing, otherwise git pull with a
# fast-forward only (it never force-pulls local changes).
set -euo pipefail

# Directory this script lives in (the repo root on this machine).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config.json"

echo "[updater] repo dir: $SCRIPT_DIR"

if [ ! -f "$CONFIG" ]; then
  echo "[updater] ERROR: no config.json in $SCRIPT_DIR"
  exit 1
fi

# Helper to read a JSON string field.
get_json() { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$CONFIG" "$1"; }

REPO_URL="$(get_json repo_url)"
GITHUB_TOKEN="$(get_json github_token)"
BRANCH="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('git_branch','main'))" "$CONFIG")"

if [ -z "$REPO_URL" ] || [ -z "$GITHUB_TOKEN" ]; then
  echo "[updater] ERROR: config.json must set repo_url and github_token"
  exit 1
fi

# Inject the token into the remote URL so git can pull the private repo.
# repo_url is expected to be like https://github.com/OWNER/REPO.git
if [[ "$REPO_URL" == https://github.com/* ]]; then
  PULL_URL="https://x-access-token:${GITHUB_TOKEN}@${REPO_URL#https://}"
else
  PULL_URL="$REPO_URL"
fi

if [ ! -d "$SCRIPT_DIR/.git" ]; then
  echo "[updater] cloning repo..."
  git clone -b "$BRANCH" "$PULL_URL" "$SCRIPT_DIR"
else
  echo "[updater] pulling latest..."
  git -C "$SCRIPT_DIR" fetch origin "$BRANCH"
  git -C "$SCRIPT_DIR" checkout "$BRANCH" 2>/dev/null || true
  git -C "$SCRIPT_DIR" merge --ff-only "origin/$BRANCH"
fi

echo "[updater] restarting services..."
if [[ "$(uname -s)" == "Darwin" ]]; then
  # macOS: reload LaunchAgents (bot, serve, dashboard).
  for agent in tele-opencode-bot tele-opencode-serve tele-opencode-dashboard; do
    PLIST="$HOME/Library/LaunchAgents/com.teleopencode.$agent.plist"
    if [ -f "$PLIST" ]; then
      launchctl bootout "gui/$(id -u)/com.teleopencode.$agent" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
      echo "[updater] restarted $agent"
    fi
  done
elif command -v systemctl >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user restart tele-opencode-serve.service tele-opencode-dashboard.service tele-opencode-bot.service 2>/dev/null || \
  systemctl --user restart tele-opencode-serve.service tele-opencode-bot.service 2>/dev/null || true
else
  echo "[updater] no service manager found; services must be restarted manually."
fi

echo "[updater] done."
