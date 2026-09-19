"""Demo production checkout service and live incident dashboard."""

from __future__ import annotations

import logging
import subprocess
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.pricing import apply_discount

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
APP_LOG = LOG_DIR / "app.log"
DEPLOYS_LOG = LOG_DIR / "deploys.log"

logger = logging.getLogger("checkout")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.FileHandler(APP_LOG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s checkout: %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    )
    logger.addHandler(handler)

app = FastAPI(title="checkout-service-demo")
WINDOW_S = 300
_events: deque[tuple[float, bool]] = deque()


class CheckoutRequest(BaseModel):
    cart_total: float
    code: str = ""


def _record(is_error: bool) -> None:
    now = time.time()
    _events.append((now, is_error))
    cutoff = now - WINDOW_S
    while _events and _events[0][0] < cutoff:
        _events.popleft()


def _error_rate(window_s: int) -> dict:
    now = time.time()
    cutoff = now - window_s
    recent = [event for event in _events if event[0] >= cutoff]
    total = len(recent)
    errors = sum(1 for _, is_error in recent if is_error)
    rate = errors / total if total else 0.0
    return {
        "window_s": window_s,
        "requests": total,
        "errors": errors,
        "error_rate": round(rate, 4),
    }


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()
    except Exception:
        return "unavailable"


def _tail(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]


@app.post("/checkout")
def checkout(req: CheckoutRequest):
    try:
        total = apply_discount(req.cart_total, req.code)
    except Exception:
        logger.error(
            "checkout failed for code=%r cart_total=%r\n%s",
            req.code,
            req.cart_total,
            traceback.format_exc(),
        )
        _record(is_error=True)
        raise HTTPException(status_code=500, detail="checkout failed")
    logger.info("checkout ok code=%r cart_total=%r total=%r", req.code, req.cart_total, total)
    _record(is_error=False)
    return {"total": total}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics(window_s: int = Query(default=60, ge=1, le=300)):
    return JSONResponse(_error_rate(window_s))


@app.get("/status-data")
def status_data():
    metrics_now = _error_rate(60)
    recent_errors = [line for line in _tail(APP_LOG, 300) if " ERROR " in line][-5:]
    deploys = _tail(DEPLOYS_LOG, 5)
    return {
        **metrics_now,
        "commit": _git("rev-parse", "--short=12", "HEAD"),
        "commit_message": _git("log", "-1", "--pretty=%s"),
        "deploys": deploys,
        "recent_errors": recent_errors,
        "updated_at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    }


STATUS_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>OnCall Forge · Live Incident Console</title>
  <style>
    :root { --bg:#090b10; --panel:#11151d; --line:#242b38; --text:#f6f7fb; --muted:#8f9bad; --green:#55d68b; --amber:#f6c85f; --red:#ff665f; --violet:#8b7cff; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; color:var(--text); background:radial-gradient(circle at 20% 0%,#15152b 0,transparent 36%),var(--bg); font:14px/1.45 Inter,ui-sans-serif,system-ui,sans-serif; }
    .shell { max-width:1200px; margin:auto; padding:28px; }
    header { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-bottom:22px; }
    .brand { display:flex; align-items:center; gap:13px; }
    .mark { width:40px; height:40px; display:grid; place-items:center; border:1px solid #5149a8; border-radius:12px; background:#201d45; color:#bdb7ff; font-weight:900; }
    h1 { margin:0; font-size:20px; letter-spacing:-.02em; }
    .eyebrow { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.12em; }
    .live { display:flex; align-items:center; gap:8px; color:var(--muted); }
    .pulse { width:8px; height:8px; border-radius:50%; background:var(--green); box-shadow:0 0 0 5px #55d68b18; }
    .grid { display:grid; grid-template-columns:1.45fr .8fr; gap:18px; }
    .panel { border:1px solid var(--line); border-radius:16px; background:linear-gradient(180deg,#131823,#0f131b); box-shadow:0 18px 55px #0005; }
    .hero { padding:28px; min-height:330px; }
    .hero-top { display:flex; justify-content:space-between; align-items:flex-start; gap:18px; }
    .state { display:inline-flex; gap:8px; align-items:center; border-radius:999px; padding:7px 11px; font-weight:700; background:#55d68b18; color:var(--green); border:1px solid #55d68b45; }
    .rate { margin:18px 0 0; font-size:76px; line-height:1; font-weight:800; letter-spacing:-.07em; color:var(--green); }
    .rate-label { margin-top:8px; color:var(--muted); }
    canvas { width:100%; height:120px; margin-top:24px; }
    .kpis { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:16px; }
    .kpi { padding:15px; border:1px solid var(--line); border-radius:12px; background:#0c1017; }
    .kpi strong { display:block; font-size:24px; }
    .kpi span { color:var(--muted); font-size:12px; }
    .side { padding:22px; }
    .section-title { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }
    h2 { margin:0; font-size:15px; }
    .commit { padding:14px; background:#0b0f16; border:1px solid var(--line); border-radius:12px; }
    code { color:#beb8ff; font:12px ui-monospace,SFMono-Regular,Consolas,monospace; }
    .commit-message { margin-top:7px; color:#cbd2dc; }
    .timeline { margin:18px 0 0; padding:0; list-style:none; }
    .timeline li { position:relative; margin-left:8px; padding:0 0 18px 22px; border-left:1px solid #303746; color:var(--muted); }
    .timeline li::before { content:""; position:absolute; left:-5px; top:3px; width:9px; height:9px; border-radius:50%; background:#586174; }
    .timeline li.active { color:var(--text); }
    .timeline li.active::before { background:var(--violet); box-shadow:0 0 0 4px #8b7cff24; }
    .bottom { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:18px; }
    .list { padding:22px; min-height:210px; }
    .rows { display:flex; flex-direction:column; gap:9px; }
    .row { padding:11px 12px; border:1px solid var(--line); border-radius:10px; background:#0b0f16; color:#b9c1ce; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font:12px ui-monospace,SFMono-Regular,Consolas,monospace; }
    .empty { color:var(--muted); padding:28px 0; text-align:center; }
    .footer { margin-top:16px; color:#6f7a8c; font-size:12px; text-align:right; }
    body.incident .rate { color:var(--red); } body.incident .state { color:var(--red); background:#ff665f18; border-color:#ff665f45; } body.incident .pulse { background:var(--red); }
    body.degraded .rate { color:var(--amber); } body.degraded .state { color:var(--amber); background:#f6c85f18; border-color:#f6c85f45; } body.degraded .pulse { background:var(--amber); }
    @media(max-width:800px){ .grid,.bottom{grid-template-columns:1fr}.rate{font-size:58px}.shell{padding:16px} }
  </style>
</head>
<body>
<div class="shell">
  <header>
    <div class="brand"><div class="mark">OF</div><div><div class="eyebrow">checkout-service-demo</div><h1>OnCall Forge · Live Incident Console</h1></div></div>
    <div class="live"><span class="pulse"></span><span id="updated">connecting…</span></div>
  </header>
  <div class="grid">
    <section class="panel hero">
      <div class="hero-top"><div><div id="state" class="state">● Healthy</div><div id="rate" class="rate">0.0%</div><div class="rate-label">error rate · rolling 60-second window</div></div></div>
      <canvas id="chart" width="760" height="120"></canvas>
      <div class="kpis"><div class="kpi"><strong id="requests">0</strong><span>Requests / 60s</span></div><div class="kpi"><strong id="errors">0</strong><span>Errors / 60s</span></div><div class="kpi"><strong id="slo">99.0%</strong><span>Success target</span></div></div>
    </section>
    <aside class="panel side">
      <div class="section-title"><h2>Active deployment</h2><span class="eyebrow">main</span></div>
      <div class="commit"><code id="commit">loading</code><div id="commit-message" class="commit-message">—</div></div>
      <ol class="timeline"><li class="active">Traffic and telemetry live</li><li id="incident-step">No active incident</li><li>Agent investigation in TrueForge</li><li>Human-approved recovery</li></ol>
    </aside>
  </div>
  <div class="bottom">
    <section class="panel list"><div class="section-title"><h2>Recent deployments</h2><span class="eyebrow">GitHub → local</span></div><div id="deploys" class="rows"></div></section>
    <section class="panel list"><div class="section-title"><h2>Recent failures</h2><span class="eyebrow">untrusted data</span></div><div id="failures" class="rows"></div></section>
  </div>
  <div class="footer">Refreshes every 2 seconds · Operator actions remain approval-gated in TrueForge</div>
</div>
<script>
const history=[]; const chart=document.getElementById('chart'); const ctx=chart.getContext('2d');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function rows(id,items,empty){document.getElementById(id).innerHTML=items.length?items.map(x=>`<div class="row" title="${esc(x)}">${esc(x)}</div>`).join(''):`<div class="empty">${empty}</div>`}
function draw(color){const w=chart.clientWidth,dpr=devicePixelRatio||1;chart.width=w*dpr;chart.height=120*dpr;ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,120);ctx.strokeStyle='#272e3a';ctx.lineWidth=1;[20,60,100].forEach(y=>{ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke()});if(history.length<2)return;ctx.strokeStyle=color;ctx.lineWidth=3;ctx.lineJoin='round';ctx.beginPath();history.forEach((v,i)=>{const x=i*(w/(Math.max(history.length-1,1))),y=108-Math.min(v,50)/50*96;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()}
async function refresh(){try{const d=await fetch('/status-data',{cache:'no-store'}).then(r=>r.json());const pct=d.error_rate*100;history.push(pct);if(history.length>60)history.shift();const mode=pct>=10?'incident':pct>=1?'degraded':'healthy';document.body.className=mode==='healthy'?'':mode;document.getElementById('state').textContent=mode==='incident'?'● Incident active':mode==='degraded'?'● Recovering':'● All systems operational';document.getElementById('rate').textContent=pct.toFixed(1)+'%';document.getElementById('requests').textContent=d.requests;document.getElementById('errors').textContent=d.errors;document.getElementById('slo').textContent=(100-pct).toFixed(1)+'%';document.getElementById('commit').textContent=d.commit;document.getElementById('commit-message').textContent=d.commit_message;document.getElementById('updated').textContent='Live · '+d.updated_at;document.getElementById('incident-step').textContent=mode==='healthy'?'No active incident':mode==='degraded'?'Recovery window clearing':'Elevated checkout failures detected';document.getElementById('incident-step').className=mode==='healthy'?'':'active';rows('deploys',d.deploys,'No deployments recorded yet');rows('failures',d.recent_errors,'No recent checkout failures');draw(mode==='incident'?'#ff665f':mode==='degraded'?'#f6c85f':'#55d68b')}catch(e){document.getElementById('updated').textContent='Telemetry unavailable'}}
refresh();setInterval(refresh,2000);addEventListener('resize',()=>draw('#55d68b'));
</script>
</body></html>"""


@app.get("/status", response_class=HTMLResponse)
def status():
    return STATUS_HTML
