# Onboarding the Mac as the Tele-OpenCode hub

Paste the prompt below into OpenCode on your **Mac**. It clones the Tele-OpenCode
repo, configures this Mac as the hub (the single machine that runs the bot and
dashboard and controls every machine), and starts the Mac's local `opencode serve`.

Before you start, gather (from this Linux machine currently under setup):

1. The GitHub fine-grained **read-only** PAT for cloning the private repo:
   - GitHub → Settings → Developer settings → Personal access tokens →
     Fine-grained tokens → Generate new token
   - Repository access: "Only select repositories" → `tele-opencode`
   - Permissions → Contents: **Read-only**
2. The repo URL: `https://github.com/isaacattack2516-create/tele-opencode.git`
3. The Mac's LAN IP and the telegram bot token (same token used everywhere).

---

## Prompt for the Mac (paste into `opencode` on your Mac)

```
You are setting up this Mac as the central hub for Tele-OpenCode, a Telegram
bot that controls multiple machines (this Mac, a Linux box, and a Windows box)
through each machine's headless `opencode serve`. On this Mac you will run the
bot, the dashboard, and a local `opencode serve`, and you will act as the single
controller for every machine.

FIRST, copy config.json.example.json to config.json on this machine is NOT yet
present, and fill in the following exact settings BEFORE doing anything else.

GitHub access (do not commit any token):
- repo_url = "https://github.com/isaacattack2516-create/tele-opencode.git"
- github_token = "<PASTE_FINE_GRAINED_READ_ONLY_PAT>"
- git_branch = "main"

1. CLONE the repo into a stable local folder, e.g. ~/tele-opencode, using the
   read-only PAT so the private repo can be fetched:
     git clone "https://x-access-token:<PAT>@github.com/isaacattack2516-create/tele-opencode.git" \
       "$HOME/tele-opencode"

2. In that folder, create config.json from config.example.json and set:
   - bot_token = the shared Telegram bot token
   - owner_id = leave null (first person to message the bot claims it), or set it
   - repo_dir = "/Users/<YOUR_MAC_USERNAME>/tele-opencode"   (the clone path above)
   - devices.mac (THIS machine):
       url  = "http://127.0.0.1:19123"            # local, reachable on this box
       username = "opencode"
       password = "<generate a strong unique password for the mac serve>"
       is_hub = true
       repo_dir = "/Users/<YOUR_MAC_USERNAME>/tele-opencode"
       model = { "providerID": "opencode", "modelID": "deepseek-v4-flash-free" }
   - devices.linux (remote):
       url = "http://<LINUX_LAN_IP>:19123"
       username = "opencode"
       password = "<the password the Linux serve uses>"
       is_hub = false
       repo_dir = "/home/isaac-ramos/Desktop/tele-opencode"  (Linux clone path)
       directory = "/home/isaac-ramos"
   - devices.windows (remote):
       url = "http://<WINDOWS_LAN_IP>:19124"
       username = "opencode"
       password = "<the password the Windows serve uses>"
       is_hub = false
       repo_dir = "C:/Users/<USER>/tele-opencode"
       directory = "C:/Users/<USER>"

3. If `opencode` is not on your PATH, install the CLI:
     brew install opencode   (or follow the official install for your shell)

4. Run the macOS setup, which installs launchd agents for serve, bot, and
   dashboard, auto-starting them and keeping them alive:
     bash "$HOME/tele-opencode/setup-mac.sh"

5. Verify:
   - opencode serve is listening:  curl -s http://127.0.0.1:19123/provider
   - the dashboard is up:          open http://127.0.0.1:19125
   - the bot is running:           check ~/tele-opencode/logs/bot.err.log

6. STRONGLY PREFERRED: keep the Mac's serve reachable only on localhost
   (127.0.0.1). Remote machines do NOT need to reach the Mac; the Mac reaches
   THEM. On Linux and Windows, run their `opencode serve` bound so the Mac can
   reach them (e.g. --hostname 0.0.0.0), and make their ports reachable from
   the Mac (LAN or a tunnel).

IMPORTANT SAFETY RULES:
- Never print or commit any token/password. Keep secrets only in config.json,
  which is gitignored.
- Do not run "git push" or modify the shared repo unless explicitly asked.
- If any step fails, stop and report the exact error rather than guessing.
- Windows paths use backslashes / C:; keep them as strings in config.json.
```

---

## After the Mac is the hub

1. **Stop the bot on Linux** so only the Mac polls Telegram (two bots on one
   token conflict). On Linux:
   ```bash
   systemctl --user disable --now tele-opencode-bot
   ```
   (The Linux `opencode serve` and dashboard can keep running.)
2. Verify from Telegram: `linux: ...`, `mac: ...`, `windows: ...` and `/devices`.
3. When you publish a new version to GitHub, run `/update` in the chat. The
   hub pulls its own code and restarts, and asks Linux/Windows to do the same.
