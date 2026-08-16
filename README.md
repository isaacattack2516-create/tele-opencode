# Tele-OpenCode

Telegram bot that routes your messages to the correct machine's **OpenCode**
instance. Each machine runs `opencode serve`; the bot forwards a prompt to the
selected machine's HTTP API and relays the answer back into Telegram. Includes
a local web dashboard for watching activity and catching errors.

## How it works

- One machine runs the **bot** (this one), a **dashboard**, and a local
  `opencode serve`.
- Every machine (Linux, Mac, Windows) runs its own `opencode serve`.
- You text the bot: `linux: summarize this project` → goes to the Linux box.
  `mac: …` → Mac. `windows: …` → Windows. No prefix → default device.
- Responses **stream** to Telegram live as they're generated.

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

## Setup (this Linux device)

1. Create the bot and get a token:
   - Open Telegram, message **@BotFather**
   - Send `/newbot`, pick a name and username
   - BotFather replies with a token like `123456:ABC-DEF...`
   - Paste it into `config.json` → `"bot_token"`
2. Start the services:

   ```bash
   ./tele-opencode/setup.sh
   ```

3. Text your bot `/start`. The **first** person to message it becomes the
   owner (stored in `config.json` → `owner_id`) — everyone else is ignored.
4. Try: `linux: reply with the word pong`

## Adding the Mac and Windows machines

1. Install `opencode` on that machine and run `opencode serve` on its own port:
   ```bash
   OPENCODE_SERVER_USERNAME=opencode \
   OPENCODE_SERVER_PASSWORD=<a strong password> \
   opencode serve --hostname 0.0.0.0 --port 19124
   ```
   (For Windows, set these in the environment or a `.bat`/PowerShell launcher.)
2. Make the port reachable from this machine: same LAN IP, or a tunnel
   (ngrok / cloudflared) if they're not on the same network.
3.  In `config.json`, update the `mac` / `windows` entries: `url`, `password`,
    and `directory`. Optional `model` per device, e.g.
    `"model": {"providerID": "opencode", "modelID": "laguna-s-2.1-free"}`.
4.  Optional per-device latency knobs (defaults are fine for most setups):
    - `rotate_input_tokens` (default `12000`) and `rotate_cache_tokens`
      (default `100000`) — start a fresh session once context grows past these,
      since a large prompt cache makes the provider stall.
    - `first_token_timeout` (default `30`) — seconds to wait for the model to
      start producing output before rotating to a fresh session and retrying.
4. `/devices` in the chat shows live up/down status for each machine.

## Commands

| Command | What it does |
|---|---|
| `linux: <prompt>` / `mac: …` / `windows: …` | Route to a specific machine |
| `any message` | Routes to the default device |
| `/devices` | List machines and their status |
| `/set <device>` | Change the default device for this chat |
| `/default` | Show the current default device |
| `/help` | Help message |

## Files

- `bot.py` — the bot (long-polling + opencode client, stdlib only)
- `eventlog.py` — shared activity log (JSONL) used by the bot and dashboard
- `dashboard.py` — Flask web UI for monitoring
- `config.json` — token, owner, and per-device endpoints (**gitignored**)
- `config.example.json` — template for the secret config
- `serve.sh` / `run-bot.sh` / `run-dashboard.sh` — launchers used by systemd
- `setup.sh` — installs the three systemd user services
- `tele-opencode-*.service` — systemd units

## Git / secrets

`config.json`, `state.json`, and `activity.jsonl` are gitignored so bot tokens
and server passwords are never committed. When cloning elsewhere, copy
`config.example.json` → `config.json` and fill in real values.

## Troubleshooting

- Bot never replies: check `config.json` has a valid `bot_token` and that you
  messaged it once to become the owner.
- `❌ unreachable` in `/devices`: the `opencode serve` on that machine isn't
  running or the port/password are wrong.
- Logs: `journalctl --user -u tele-opencode-bot -f`
