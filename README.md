# Tele-OpenCode

Telegram bot that routes your messages to the correct machine's **OpenCode**
instance. A single "hub" machine (default: the Mac) runs the bot and dashboard
and controls every machine: this Mac, a Linux box, and a Windows box. Each
machine runs `opencode serve`; the hub forwards a prompt to the selected
machine's HTTP API and relays the answer back into Telegram.

## How it works

- The **hub** (Mac) runs the bot, the dashboard, and a local `opencode serve`.
- Every machine (Mac, Linux, Windows) runs its own `opencode serve`.
- You text the bot: `mac: summarize this project`, `linux: check disk space`,
  `windows: run ipconfig`. No prefix → default device.
- Responses **stream** to Telegram live as they are generated.
- Bump a version in `version.py`, push to GitHub, then text the bot
  **`/update`** — every machine pulls the new code and restarts its services
  automatically.

## New in v2 (the Mac hub)

- **Single hub**: only one machine (the Mac) polls Telegram. The Mac device
  entry is marked `"is_hub": true` in `config.json`.
- **`/update [devices]`**: pulls the latest code from GitHub on every machine
  and restarts their services. Non-hub machines update in parallel first; the
  hub updates last (its updater restarts the bot on the new code).
- **`/version`**: shows the bot's running version and each device's version.
- **macOS support**: `setup-mac.sh` installs launchd agents (`run-service.sh`)
  for serve/bot/dashboard; `updater.sh` detects macOS vs Linux.

### Backward compatibility (policy)

The config schema and code are **additive-only**: new fields are added and read
with `.get()` defaults; nothing is removed or renamed. This keeps at least two
releases back working: an old machine talking to a new hub (or vice versa) stays
functional. The opencode HTTP endpoints used are stable core APIs. Keep changes
additive to preserve this guarantee.

## Dashboard

A local web UI shows live activity, device status, request stats, and errors.

- Start (auto-started by `setup.sh`): `./run-dashboard.sh`
- Open: `http://127.0.0.1:19125`
- Endpoints: `/` (UI), `/api/status` (stats + devices + recent events),
  `/api/events` (raw event list)
- Activity is appended to `activity.jsonl` by the bot and read by the dashboard.
- To expose on the LAN (use with care): `DASHBOARD_HOST=0.0.0.0 ./run-dashboard.sh`

## Requirements on this device

- Python 3 (stdlib only — no pip packages needed)
- Flask (`pip install flask`) for the dashboard
- `opencode` binary (here at `~/.opencode/bin/opencode`)

## Setup

The **hub** (the Mac) is the only machine that runs the bot. To set it up, give
the Mac's OpenCode the prompt in **`ONBOARDING_MAC.md`** — it clones the repo,
writes `config.json` (hub flagged), and installs the launchd services.

Set up the **controlled** machines (Linux and Windows):
1. Install `opencode` and clone this repo on each one.
2. On each, create `config.json` from `config.example.json` and set that
   machine's `is_hub: false`, its local `url`/`password`/`repo_dir`, plus the
   shared `repo_url`/`github_token`/`git_branch`.
3. Run their `opencode serve` bound so the Mac can reach them:
   ```bash
   OPENCODE_SERVER_USERNAME=opencode \
   OPENCODE_SERVER_PASSWORD=<strong password> \
   opencode serve --hostname 0.0.0.0 --port 19124
   ```
   (Windows: same idea via env vars or a `.bat`/PowerShell launcher.)
4. Make each port reachable from the Mac: same LAN IP, or a tunnel
   (ngrok / cloudflared) if not on the same network.
5. Each machine should run `updater.sh`-compatible startup so `/update` works.
   On Linux use `setup.sh` (systemd); on macOS use `setup-mac.sh` (launchd).

Optional per-device latency knobs (defaults are fine for most setups):
- `rotate_input_tokens` (default `12000`) and `rotate_cache_tokens`
  (default `100000`) — start a fresh session once context grows past these.
- `first_token_timeout` (default `30`) — seconds to wait before rotating.

Once configured, text the hub bot `/devices` to check everything is reachable.

## Commands

| Command | What it does |
|---|---|
| `linux:` / `mac:` / `windows:` `<prompt>` | Route to a specific machine |
| `any message` | Routes to the default device |
| `/devices` | List machines (hub marked) and their status |
| `/update [devices]` | Pull latest code from GitHub on every machine and restart |
| `/version` | Show bot version and each device's version |
| `/set <device>` / `/default` | Manage the default device for this chat |
| `/new` | Start a fresh session (reset context) |
| `/help` | Help message |

## Files

- `bot.py` — the bot (long-polling + opencode client, stdlib only)
- `eventlog.py` — shared activity log (JSONL) used by the bot and dashboard
- `dashboard.py` — Flask web UI for monitoring
- `config.json` — token, owner, and per-device endpoints (**gitignored**)
- `config.example.json` — template for the secret config
- `serve.sh` / `run-bot.sh` / `run-dashboard.sh` — Linux launchers used by systemd
- `run-service.sh` / `setup-mac.sh` — macOS launchers / launchd installer
- `updater.sh` — cross-platform script that pulls code + restarts services (used by `/update`)
- `version.py` — version constant (bump on each release)
- `config.example.json` / `config.json` — template + per-machine secrets (gitignored)
- `ONBOARDING_MAC.md` — prompt to give the Mac's OpenCode to set it up as the hub
- `tele-opencode-*.service` — systemd units

## Git / secrets

`config.json`, `state.json`, `activity.jsonl`, and `logs/` are gitignored so
bot tokens, server passwords, and GitHub PATs are never committed. When cloning
elsewhere, copy `config.example.json` → `config.json` and fill in real values.

## Troubleshooting

- Bot never replies: check `config.json` has a valid `bot_token` and that you
  messaged it once to become the owner.
- `? unreachable ?` in `/devices`: an `opencode serve` on that machine isn't
  running or the port/password are wrong.
- Only one machine can run the bot; if two poll, Telegram returns a 409 conflict.
- Linux logs: `journalctl --user -u tele-opencode-bot -f`
- macOS logs: `~/tele-opencode/logs/{bot,serve,dashboard}.{out,err}.log`
- `github_token` must be a **fine-grained read-only** PAT scoped to this repo.
