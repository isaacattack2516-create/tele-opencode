#!/usr/bin/env python3
"""
Telegram <-> OpenCode router bot.

The bot listens for Telegram messages and forwards them to the correct
machine's `opencode serve` HTTP server (routed by a "device:" prefix).
Replies are streamed back into the chat as they are generated.

Latency optimizations:
  * responses stream token-by-token via prompt_async + /event SSE
  * sessions are rotated when context grows too large
  * updates are processed concurrently so messages never queue behind
    a long-running request
"""

import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import eventlog
import version as version_mod

BOT_VERSION = version_mod.VERSION
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
TG_API = "https://api.telegram.org"
PREFIX_RE = re.compile(r"^\s*([a-zA-Z0-9_-]+)\s*[:,\s]\s*(.+)$")

STATE_LOCK = threading.Lock()
BUSY_LOCKS = {}

ROTATE_INPUT_TOKENS = 12000
ROTATE_CACHE_TOKENS = 100000
DEFAULT_FIRST_TOKEN_TIMEOUT = 30


class FirstTokenTimeout(Exception):
    """Raised when the model hasn't produced any output within the timeout.

    The caller should rotate to a fresh session (small context) and retry,
    since a stale session with a large prompt cache is what makes the
    provider stall.
    """


# --------------------------------------------------------------------------- #
# config / state
# --------------------------------------------------------------------------- #
def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    tmp.replace(path)


def load_config():
    return load_json(CONFIG_PATH)


def save_config(cfg):
    save_json(CONFIG_PATH, cfg)


def load_state():
    return load_json(STATE_PATH)


def save_state(state):
    with STATE_LOCK:
        save_json(STATE_PATH, state)


def get_busy_lock(key):
    with STATE_LOCK:
        return BUSY_LOCKS.setdefault(key, threading.Lock())


# --------------------------------------------------------------------------- #
# opencode server client
# --------------------------------------------------------------------------- #
DEFAULT_SYSTEM = (
    "You are an autonomous assistant running directly on this machine with "
    "full shell access via the OpenCode bash tool. You can run any command and "
    "launch applications. Never claim you are unable to do something on this "
    "machine -- accomplish it with bash instead of refusing.\n"
    "To play music or audio, launch a player in the background so the command "
    "returns immediately, e.g.:\n"
    "nohup ffplay -nodisp -autoexit -volume 80 \"<absolute path>\" >/dev/null 2>&1 &\n"
    "Reply concisely with what you did, including any file paths."
)


def _request(method, url, headers=None, body=None, timeout=30):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


class Device:
    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.base = cfg["url"].rstrip("/")
        self.directory = cfg.get("directory") or "~"
        self.model = cfg.get("model") or None
        self.is_hub = bool(cfg.get("is_hub"))
        self.repo_dir = cfg.get("repo_dir") or ""
        self.rotate_input = int(cfg.get("rotate_input_tokens") or ROTATE_INPUT_TOKENS)
        self.rotate_cache = int(cfg.get("rotate_cache_tokens") or ROTATE_CACHE_TOKENS)
        self.first_token_timeout = float(
            cfg.get("first_token_timeout") or DEFAULT_FIRST_TOKEN_TIMEOUT
        )

    def auth_headers(self):
        username = self.cfg.get("username", "opencode")
        password = self.cfg.get("password", "")
        if not password:
            return {}
        import base64

        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": "Basic " + token}

    def _body(self, prompt):
        body = {"parts": [{"type": "text", "text": prompt}]}
        if self.model:
            body["model"] = self.model
        system = self.cfg.get("system") or DEFAULT_SYSTEM
        if system:
            body["system"] = system
        return body

    def create_session(self):
        body = {"directory": self.directory}
        _, resp = _request(
            "POST", f"{self.base}/session", headers=self.auth_headers(), body=body, timeout=20
        )
        return json.loads(resp)["id"]

    def session_info(self, session_id):
        try:
            _, resp = _request(
                "GET", f"{self.base}/session/{session_id}",
                headers=self.auth_headers(), timeout=15,
            )
            return json.loads(resp)
        except Exception:
            return {}

    def ensure_session(self, session_id=None):
        rotated = False
        if session_id:
            info = self.session_info(session_id)
            tokens = info.get("tokens") or {}
            big_input = (tokens.get("input") or 0) > self.rotate_input
            big_cache = (tokens.get("cache", {}).get("read") or 0) > self.rotate_cache
            if big_input or big_cache:
                session_id = self.create_session()
                rotated = True
            return session_id, rotated
        return self.create_session(), rotated

    def prompt_async(self, session_id, prompt):
        _request(
            "POST",
            f"{self.base}/session/{session_id}/prompt_async",
            headers=self.auth_headers(),
            body=self._body(prompt),
            timeout=20,
        )

    def list_messages(self, session_id, limit=1):
        try:
            _, resp = _request(
                "GET", f"{self.base}/session/{session_id}/message?limit={limit}",
                headers=self.auth_headers(), timeout=30,
            )
            data = json.loads(resp)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def fetch_reply(self, session_id, timeout=600, poll=1.0):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            rows = self.list_messages(session_id)
            if rows:
                m = rows[-1]
                info = m.get("info") or {}
                if info.get("role") == "assistant" and info.get("time", {}).get("completed"):
                    return extract_reply(m)
                last = m
            time.sleep(poll)
        return extract_reply(last) if last else "(no response)"

    def stream_reply(self, session_id, prompt, on_delta=None,
                     first_token_timeout=DEFAULT_FIRST_TOKEN_TIMEOUT):
        done = threading.Event()
        first_token = threading.Event()
        streamed = {"ok": False}

        def reader():
            req = urllib.request.Request(
                f"{self.base}/event",
                headers={**self.auth_headers(), "Accept": "text/event-stream"},
            )
            try:
                with urllib.request.urlopen(req, timeout=600) as resp:
                    assistant_id = None
                    text = ""
                    buf = b""
                    while not done.is_set():
                        try:
                            chunk = resp.read(4096)
                        except Exception:
                            break
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n\n" in buf:
                            block, buf = buf.split(b"\n\n", 1)
                            data = b"".join(
                                line[5:].strip()
                                for line in block.splitlines()
                                if line.startswith(b"data:")
                            )
                            if not data:
                                continue
                            try:
                                ev = json.loads(data)
                            except ValueError:
                                continue
                            props = ev.get("properties") or {}
                            if props.get("sessionID") != session_id:
                                continue
                            etype = ev.get("type")
                            if etype == "message.updated":
                                info = props.get("info") or {}
                                if info.get("role") == "assistant":
                                    if assistant_id is None:
                                        assistant_id = info.get("id")
                                        first_token.set()
                                    if info.get("time", {}).get("completed"):
                                        done.set()
                                        break
                            elif etype == "message.part.updated":
                                part = props.get("part") or {}
                                if part.get("type") == "text" and part.get("messageID") == assistant_id:
                                    t = part.get("text") or ""
                                    if len(t) > len(text):
                                        first_token.set()
                                        if on_delta:
                                            on_delta(t[len(text):])
                                        text = t
                streamed["ok"] = done.is_set()
            except Exception:
                pass
            finally:
                done.set()

        threading.Thread(target=reader, daemon=True).start()
        try:
            self.prompt_async(session_id, prompt)
        except Exception as exc:
            done.set()
            raise RuntimeError(f"failed to start message: {exc}")

        if first_token_timeout:
            started = time.time()
            deadline = started + first_token_timeout
            while not done.is_set() and not first_token.is_set():
                if time.time() > deadline:
                    raise FirstTokenTimeout(
                        f"no output started within {first_token_timeout:.0f}s"
                    )
                time.sleep(0.25)
        done.wait(timeout=600)
        if streamed["ok"]:
            return self.fetch_reply(session_id, timeout=15)
        return self.fetch_reply(session_id, timeout=600)

    def ping(self):
        try:
            _request("GET", f"{self.base}/provider", headers=self.auth_headers(), timeout=5)
            return True
        except Exception:
            return False

    def run_shell(self, command, timeout=300):
        """Run a shell command on this machine through its opencode serve.

        Returns (ok, output). Used by /update to trigger the repo services
        update on a device. The opencode model is asked to run the command via
        bash and return its output.
        """
        prompt = (
            "Run the following shell command on this machine exactly as given, "
            "using the bash tool, then reply with its full output. If it fails, "
            "include the error.\n\n"
            f"{command}"
        )
        try:
            session_id, _ = self.ensure_session(None)
            reply = self.stream_reply(session_id, prompt, first_token_timeout=60)
            low = reply.lower()
            ok = not ("[updater] error" in low or low.strip().startswith("error"))
            return ok, reply
        except Exception as exc:
            return False, str(exc)

    def get_version(self, timeout=90):
        """Best-effort: read this device's running code version via opencode."""
        if not self.ping():
            return "unreachable"
        if not self.repo_dir:
            return "?"
        cmd = (
            f"python3 -c \"import sys; sys.path.insert(0,'{self.repo_dir}'); "
            f"import version; print(version.VERSION)\""
        )
        try:
            session_id, _ = self.ensure_session(None)
            reply = self.stream_reply(
                session_id,
                "Run this command with bash and reply with ONLY the exact stdout "
                "(a version number), nothing else: " + cmd,
                first_token_timeout=60,
            )
            line = reply.strip().splitlines()[0] if reply.strip() else "?"
            if line.lower().startswith("error") or line.startswith("Traceback"):
                return "?"
            return line[:20]
        except Exception:
            return "?"


def extract_reply(data):
    parts = data.get("parts", [])
    texts = []
    reasoning = []
    for p in parts:
        if p.get("type") == "text" and p.get("text"):
            texts.append(p["text"])
        elif p.get("type") == "reasoning" and p.get("text"):
            reasoning.append(p["text"])
    if texts:
        return "\n\n".join(texts).strip()
    if data.get("info", {}).get("error"):
        return f"error: {data['info']['error']}"
    return "(no textual response returned)"


# --------------------------------------------------------------------------- #
# telegram helpers
# --------------------------------------------------------------------------- #
def tg_call(token, method, payload=None, timeout=60):
    url = f"{TG_API}/bot{token}/{method}"
    try:
        status, resp = _request("POST", url, body=payload or {}, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise RuntimeError("conflict: another bot instance is polling")
        raise
    return json.loads(resp) if resp else {}


def send_text(token, chat_id, text):
    return tg_call(token, "sendMessage", {"chat_id": chat_id, "text": text})


def edit_text(token, chat_id, message_id, text):
    return tg_call(
        token, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text}
    )


def typing_loop(token, chat_id, stop_event):
    while not stop_event.is_set():
        try:
            tg_call(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:
            pass
        stop_event.wait(4)


def split_message(text, limit=4000):
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# --------------------------------------------------------------------------- #
# bot logic
# --------------------------------------------------------------------------- #
def parse_route(text, devices):
    m = PREFIX_RE.match(text)
    if m:
        key, prompt = m.group(1).lower(), m.group(2).strip()
        if key in devices:
            return key, prompt
    return None, text


def get_device_sessions(state, chat_id, device):
    with STATE_LOCK:
        key = f"{chat_id}:{device}"
        return state.setdefault("sessions", {}).get(key)


def set_device_session(state, chat_id, device, session_id):
    with STATE_LOCK:
        state.setdefault("sessions", {})[f"{chat_id}:{device}"] = session_id
        save_json(STATE_PATH, state)


def handle_command(token, cfg, state, devices, chat_id, text):
    cmd, _, rest = text.partition(" ")

    if text in ("/start", "/help"):
        return send_text(
            token,
            chat_id,
            "Tele-OpenCode\n\n"
            "Send a message to a machine with a prefix:\n"
            "linux: summarize this project\n"
            "mac: check disk space\n"
            "windows: run ipconfig\n\n"
            "No prefix = default device.\n\n"
            "Commands:\n"
            "/devices - list configured machines and status\n"
            "/set <device> - change default device for this chat\n"
            "/default - show current default\n"
            "/version - show bot version and per-device code versions\n"
            "/new - start a fresh session (resets context)\n"
            "/update [devices] - pull latest code from GitHub on every machine\n"
            "/help - this message",
        )

    if text == "/devices":
        lines = []
        results = {}

        def probe(name, d):
            results[name] = "up" if d.ping() else "unreachable"

        threads = [
            threading.Thread(target=probe, args=(n, d), daemon=True)
            for n, d in devices.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=8)
        for name in devices:
            hub = " (hub)" if devices[name].is_hub else ""
            lines.append(f"- {name}{hub}: {results.get(name, 'unknown')}")
        lines.append(f"\nDefault: {cfg.get('default_device', 'linux')}")
        return send_text(token, chat_id, "\n".join(lines))

    if cmd == "/set":
        target = rest.strip().lower()
        if target not in devices:
            return send_text(token, chat_id, f"Unknown device. Available: {', '.join(devices)}")
        cfg["default_device"] = target
        save_config(cfg)
        return send_text(token, chat_id, f"Default device set to {target}.")

    if text == "/default":
        return send_text(token, chat_id, f"Default: {cfg.get('default_device', 'linux')}")

    if text == "/version":
        lines = [f"Bot version: {BOT_VERSION}"]
        results = {}

        def vprobe(name, d):
            results[name] = d.get_version()

        threads = [threading.Thread(target=vprobe, args=(n, d), daemon=True)
                   for n, d in devices.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=100)
        for name in devices:
            lines.append(f"- {name}: {results.get(name, '?')}")
        return send_text(token, chat_id, "\n".join(lines))

    if text == "/new":
        device_name = cfg.get("default_device", "linux")
        if device_name not in devices:
            device_name = next(iter(devices))
        device = devices[device_name]
        try:
            session_id = device.create_session()
            set_device_session(state, chat_id, device_name, session_id)
            return send_text(
                token, chat_id, f"[{device_name}] fresh session started (context reset)."
            )
        except Exception as exc:
            return send_text(token, chat_id, f"[{device_name}] error starting session: {exc}")

    if text.startswith("/update"):
        return handle_update_cmd(token, cfg, state, devices, chat_id, text)

    return None


def handle_update_cmd(token, cfg, state, devices, chat_id, text):
    """Pull latest code from GitHub on every machine and restart services.

    Non-hub devices are updated first, in parallel, via each machine's
    opencode serve running the local updater.sh. The hub is updated last, and
    its updater is launched detached so the bot can reply before the hub (and
    the bot itself) restart on the new code.
    """
    parts = text.split()
    if len(parts) == 1 or parts[1].lower() in ("all", "*"):
        targets = list(devices)
    else:
        requested = [p.lower().lstrip("/") for p in parts[1:]]
        targets = [d for d in requested if d in devices]
        unknown = [d for d in requested if d not in devices]
        if unknown:
            send_text(token, chat_id, f"Unknown device(s): {', '.join(unknown)}")
        if not targets:
            return send_text(token, chat_id, "No valid devices to update.")

    hub_devices = [name for name in targets if devices[name].is_hub]
    remote_devices = [name for name in targets if not devices[name].is_hub]

    msg = send_text(
        token, chat_id,
        f"Updating {len(remote_devices)} remote + {len(hub_devices)} hub: "
        + ", ".join(targets),
    )
    status_id = msg["result"]["message_id"]

    results = {}
    if remote_devices:
        def do_remote(name):
            dev = devices[name]
            if not dev.ping():
                results[name] = ("unreachable", "cannot reach opencode server")
                return
            if not dev.repo_dir:
                results[name] = (
                    "failed",
                    "repo_dir not set; configure this device's config.json",
                )
                return
            updater = f"bash '{dev.repo_dir}/updater.sh'"
            ok, out = dev.run_shell(updater, timeout=300)
            tail = "\n".join(out.splitlines()[-6:]) if out else ""
            results[name] = ("ok" if ok else "failed", tail)

        threads = [threading.Thread(target=do_remote, args=(n,), daemon=True)
                   for n in remote_devices]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=320)

    # Hub last: launch updater detached so it can restart the bot.
    for name in hub_devices:
        dev = devices[name]
        if not dev.repo_dir:
            results[name] = ("failed", "repo_dir not set for hub")
            continue
        try:
            subprocess.Popen(
                ["bash", f"{dev.repo_dir}/updater.sh"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            results[name] = ("ok", "updater launched; bot will restart on new code")
        except Exception as exc:
            results[name] = ("failed", str(exc))

    lines = []
    for name in targets:
        status, detail = results.get(name, ("unknown", ""))
        icon = {"ok": "OK", "failed": "FAILED", "unreachable": "UNREACHABLE"}.get(status, status)
        lines.append(f"- {name}: {icon}")
        if detail:
            lines.append(f"    {detail}")
    edit_text(token, chat_id, status_id, "/update\n" + "\n".join(lines))
    eventlog.record("update", devices=",".join(targets),
                    results={n: results.get(n, ("?", ""))[0] for n in targets})
    return None


def run_message(token, cfg, state, devices, chat_id, device_name, prompt):
    device = devices[device_name]
    status = send_text(token, chat_id, f"[{device_name}] running...")
    status_id = status["result"]["message_id"]

    lock = get_busy_lock(f"{chat_id}:{device_name}")
    with lock:
        stop = threading.Event()
        typer = threading.Thread(target=typing_loop, args=(token, chat_id, stop), daemon=True)
        typer.start()
        eventlog.record(
            "request_start", device=device_name, chat_id=chat_id, prompt=prompt[:200]
        )

        holder = {"text": "", "last": 0.0}
        stream_lock = threading.Lock()

        def on_delta(delta):
            with stream_lock:
                holder["text"] += delta
                now = time.time()
                if now - holder["last"] >= 0.6 and holder["text"]:
                    holder["last"] = now
                    preview = holder["text"][-3500:]
                    try:
                        edit_text(
                            token, chat_id, status_id, f"[{device_name}] {preview}"
                        )
                    except Exception:
                        pass

        try:
            started = time.time()
            session_id, rotated = device.ensure_session(
                get_device_sessions(state, chat_id, device_name)
            )
            set_device_session(state, chat_id, device_name, session_id)
            try:
                reply = device.stream_reply(
                    session_id, prompt, on_delta=on_delta,
                    first_token_timeout=device.first_token_timeout,
                )
            except FirstTokenTimeout:
                # Context got too large and the provider stalled. Rotate to a
                # fresh session and wait it out -- this avoids the multi-minute
                # stalls that happen on sessions with a large prompt cache.
                rotated = True
                session_id = device.create_session()
                set_device_session(state, chat_id, device_name, session_id)
                reply = device.stream_reply(
                    session_id, prompt, on_delta=on_delta,
                    first_token_timeout=None,
                )
            elapsed = time.time() - started
            print(f"[{device_name}] reply in {elapsed:.1f}s", file=sys.stderr)
            eventlog.record(
                "request_ok",
                device=device_name,
                chat_id=chat_id,
                elapsed=round(elapsed, 2),
                rotated=bool(rotated),
            )
        except urllib.error.URLError:
            stop.set()
            eventlog.record(
                "request_error",
                device=device_name,
                chat_id=chat_id,
                error="cannot reach opencode server",
            )
            return edit_text(
                token, chat_id, status_id, f"[{device_name}] cannot reach the opencode server."
            )
        except Exception as exc:
            stop.set()
            eventlog.record(
                "request_error",
                device=device_name,
                chat_id=chat_id,
                error=str(exc)[:500],
            )
            return edit_text(token, chat_id, status_id, f"[{device_name}] error: {exc}")
        finally:
            stop.set()

        chunks = split_message(reply)
        if not chunks:
            chunks = ["(no response)"]
        chunks[0] = f"[{device_name}] {chunks[0]}"
        edit_text(token, chat_id, status_id, chunks[0])
        for chunk in chunks[1:]:
            send_text(token, chat_id, chunk)
        if rotated:
            send_text(
                token,
                chat_id,
                f"[{device_name}] note: started a fresh session (previous context was large).",
            )


def handle_update(token, upd, cfg, state, devices):
    msg = upd.get("message")
    if not msg:
        return
    chat = msg["chat"]
    chat_id = chat["id"]
    text = (msg.get("text") or "").strip()
    user_id = msg["from"]["id"]

    if cfg.get("owner_id") is None:
        with STATE_LOCK:
            cfg["owner_id"] = user_id
            save_config(cfg)
        eventlog.record("owner_bound", user_id=user_id, chat_id=chat_id)
        send_text(
            token,
            chat_id,
            f"You are now the owner (user {user_id}). Only you can use this bot.",
        )

    if cfg.get("owner_id") != user_id:
        eventlog.record(
            "blocked_user", user_id=user_id, chat_id=chat_id, text=text[:200]
        )
        return

    if not text:
        return

    eventlog.record(
        "message_received", user_id=user_id, chat_id=chat_id, text=text[:200]
    )

    if text.startswith("/"):
        handle_command(token, cfg, state, devices, chat_id, text)
        return

    device_name, prompt = parse_route(text, devices)
    if device_name is None:
        device_name = cfg.get("default_device", "linux")
        if device_name not in devices:
            device_name = next(iter(devices))
    if prompt in ("", None):
        return send_text(token, chat_id, "Please include a prompt, e.g. linux: check disk")

    run_message(token, cfg, state, devices, chat_id, device_name, prompt)


def main():
    cfg = load_config()
    token = cfg.get("bot_token") or ""
    if not token:
        print(
            "ERROR: config.json 'bot_token' is empty. "
            "Create a bot with @BotFather and paste the token into config.json.",
            file=sys.stderr,
        )
        sys.exit(1)

    devices = {name: Device(name, d) for name, d in cfg.get("devices", {}).items()}
    state = load_state()
    offset = None
    errors = 0

    while True:
        try:
            payload = {"timeout": 50, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            resp = tg_call(token, "getUpdates", payload, timeout=80)
            errors = 0
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                threading.Thread(
                    target=lambda u=upd: handle_update(token, u, cfg, state, devices),
                    daemon=True,
                ).start()
        except RuntimeError as exc:
            print(f"fatal: {exc}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as exc:
            errors += 1
            delay = min(30, 2**errors)
            print(f"network error: {exc}; retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
        except Exception as exc:
            errors += 1
            print(f"unexpected: {exc}", file=sys.stderr)
            time.sleep(min(30, 2**errors))


if __name__ == "__main__":
    main()
