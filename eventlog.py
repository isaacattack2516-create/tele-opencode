#!/usr/bin/env python3
"""
Minimal shared event log for the Telegram bot and the dashboard UI.

The bot appends one JSON line per activity/error to a JSONL file, and workers
running inside the bot process can also write to an in-memory buffer. The UI
reads both, so it shows live activity even though it's a separate process.
"""

import json
import os
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "activity.jsonl"
MAX_MEMORY = 500

_lock = threading.Lock()
_memory = []


def record(kind, **fields):
    entry = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        **fields,
    }
    line = json.dumps(entry)
    with _lock:
        _memory.append(entry)
        if len(_memory) > MAX_MEMORY:
            del _memory[: len(_memory) - MAX_MEMORY]
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    return entry


def recent(limit=200):
    with _lock:
        return list(_memory[-limit:])


def read_log(limit=500):
    rows = []
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        return []
    return rows[-limit:]
