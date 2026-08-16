#!/usr/bin/env python3
"""
Local web dashboard for Tele-OpenCode.

Serves a live view of bot activity (messages, requests, errors), device
status, and session stats. Reads the same activity.jsonl that the bot writes,
plus the in-memory buffer when running in the bot process, and queries the
local opencode server for live status.

By default binds to 127.0.0.1 — only reachable from this machine. If you want
it exposed on the LAN, run with DASHBOARD_HOST=0.0.0.0 (use with care).
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, render_template_string

import eventlog

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

app = Flask(__name__)
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASHBOARD_PORT", "19125"))


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def reachable(base, username, password):
    import base64

    headers = {}
    if password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = "Basic " + token
    req = urllib.request.Request(
        f"{base.rstrip('/')}/provider", headers=headers, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200, None
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def device_statuses(cfg):
    out = []
    for name, d in cfg.get("devices", {}).items():
        ok, err = reachable(d.get("url", ""), d.get("username", "opencode"), d.get("password", ""))
        out.append({"name": name, "up": ok, "detail": err})
    return out


def stats(cfg):
    rows = eventlog.read_log(limit=20000)
    total = len(rows)
    by_kind = {}
    for r in rows:
        by_kind[r.get("kind")] = by_kind.get(r.get("kind"), 0) + 1
    requests = [r for r in rows if r.get("kind") == "request_ok"]
    errors = [r for r in rows if r.get("kind") == "request_error"]
    avg = None
    if requests:
        avg = round(sum(r.get("elapsed", 0) for r in requests) / len(requests), 2)
    device_req = {}
    for r in rows:
        if r.get("kind") in ("request_ok", "request_error"):
            dev = r.get("device", "?")
            device_req.setdefault(dev, {"ok": 0, "err": 0})
            if r.get("kind") == "request_ok":
                device_req[dev]["ok"] += 1
            else:
                device_req[dev]["err"] += 1
    return {
        "total_events": total,
        "by_kind": by_kind,
        "requests_ok": len(requests),
        "errors": len(errors),
        "avg_seconds": avg,
        "device_reqs": device_req,
    }


PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tele-OpenCode Dashboard</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; background: var(--bg);
         color: var(--fg); --bg:#f5f6f8; --fg:#18181b; --card:#fff; --line:#e4e4e7; }
  @media (prefers-color-scheme: dark) {
    body { --bg:#17181c; --fg:#ececec; --card:#202228; --line:#2f323a; }
  }
  header { padding: 14px 20px; border-bottom: 1px solid var(--line); display:flex;
           align-items:center; gap: 14px; flex-wrap: wrap; }
  header h1 { font-size: 18px; margin: 0; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr));
           gap: 12px; padding: 20px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
          padding: 14px 16px; }
  .card .num { font-size: 26px; font-weight: 700; }
  .card .lbl { font-size: 12px; opacity: .7; text-transform: uppercase; letter-spacing: .04em; }
  .card.up .dot { background:#22c55e; }
  .card.down .dot { background:#ef4444; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
         margin-right:6px; vertical-align:middle; }
  .row { display:flex; gap:16px; padding: 0 20px 20px; flex-wrap: wrap; }
  .panel { flex:1; min-width: 320px; background: var(--card); border:1px solid var(--line);
           border-radius:10px; padding: 14px 16px; }
  .panel h2 { font-size: 14px; margin: 0 0 10px; opacity:.8; }
  table { width:100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line); }
  th { opacity:.6; font-weight: 600; }
  .err { color:#ef4444; }
  .ok { color:#22c55e; }
  .mono { font-family: ui-monospace, monospace; }
  #events { max-height: 60vh; overflow:auto; }
  .ev { padding: 6px 8px; border-bottom:1px solid var(--line); font-size:13px; }
  .ev .t { opacity:.55; margin-right:8px; }
  .badge { border-radius: 999px; padding: 1px 8px; font-size: 11px; background:var(--line); }
  .badge.err { background:#ef4444; color:#fff; }
  .badge.ok { background:#22c55e; color:#fff; }
  .muted { opacity:.5; }
</style>
</head>
<body>
<header>
  <h1>Tele-OpenCode</h1>
  <span class="badge" id="live">connected</span>
  <span class="muted" id="clock"></span>
</header>

<div class="cards" id="cards"></div>

<div class="row">
  <div class="panel">
    <h2>Devices</h2>
    <table id="devices"></table>
  </div>
  <div class="panel">
    <h2>Stats by device (requests)</h2>
    <table id="devstats"></table>
  </div>
</div>

<div class="row">
  <div class="panel" style="flex:100%">
    <h2>Recent activity / errors</h2>
    <div id="events"></div>
  </div>
</div>

<script>
let lastIds = new Set();

function node(d, cls){ const e=document.createElement('div'); e.className=cls;
  e.textContent = (d.t ? d.t+'  ' : '') + (d.txt||''); return e; }

function render(data){
  const cards=[];
  cards.push({num:data.requests_ok, lbl:'requests OK'});
  cards.push({num:data.errors, lbl:'errors', cls: data.errors>0?'down':'up'});
  cards.push({num:data.avg_seconds!=null? data.avg_seconds+'s':'—', lbl:'avg reply time'});
  cards.push({num:data.total_events, lbl:'total events'});
  const el=document.getElementById('cards'); el.innerHTML='';
  cards.forEach(c=>{ const d=document.createElement('div');
    d.className='card '+(c.cls||'up');
    d.innerHTML='<div class="num">'+c.num+'</div><div class="lbl">'+c.lbl+'</div>';
    el.appendChild(d); });

  const dt=document.getElementById('devices'); dt.innerHTML='';
  data.devices.forEach(dev=>{ const tr=document.createElement('tr');
    tr.innerHTML='<td><span class="dot" style="background:'+
      (dev.up?'#22c55e':'#ef4444')+'"></span>'+dev.name+'</td>'+
      '<td>'+(dev.up?'<span class="ok">up</span>':'<span class="err">down</span>')+
      '</td><td class="muted">'+(dev.detail||'')+'</td>';
    dt.appendChild(tr); });

  const ds=document.getElementById('devstats'); ds.innerHTML='';
  Object.entries(data.device_reqs).forEach(([dev,s])=>{ const tr=document.createElement('tr');
    tr.innerHTML='<td>'+dev+'</td><td class="ok">'+s.ok+' ok</td><td class="err">'+s.err+' err</td>';
    ds.appendChild(tr); });

  const ev=document.getElementById('events');
  data.recent.forEach(entry=>{
    const id = entry.ts + '-' + (entry.kind||'') + '-' + (entry.iso||'');
    if(lastIds.has(id)) return; lastIds.add(id);
    const div=document.createElement('div'); div.className='ev';
    const t=document.createElement('span'); t.className='t'; t.textContent=entry.iso||'';
    div.appendChild(t);
    const b=document.createElement('span');
    b.className='badge '+(entry.kind==='request_error'||entry.kind==='blocked_user'?'err':'ok');
    b.textContent=entry.kind; div.appendChild(b);
    const txt=entry.text || entry.prompt || entry.error || entry.device || '';
    const s=document.createElement('span'); s.textContent='  '+(txt.slice(0,140));
    div.appendChild(s);
    ev.prepend(div);
  });
  while(ev.children.length>200) ev.removeChild(ev.lastChild);

  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}

async function poll(){
  try{
    const r=await fetch('/api/status');
    const d=await r.json();
    document.getElementById('live').textContent='connected';
    render(d);
  }catch(e){
    document.getElementById('live').textContent='disconnected';
  }
}
setInterval(poll, 2000); poll();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/api/status")
def api_status():
    cfg = load_config()
    recent = eventlog.read_log(limit=80)[::-1]
    s = stats(cfg)
    s["devices"] = device_statuses(cfg)
    s["recent"] = recent
    return jsonify(s)


@app.route("/api/events")
def api_events():
    return jsonify(eventlog.read_log(limit=200)[::-1])


if __name__ == "__main__":
    print(f"Dashboard: http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, threaded=True)
