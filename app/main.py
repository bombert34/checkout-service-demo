"""Demo "production" checkout service.

Endpoints:
  POST /checkout  - apply a discount code to a cart and "charge" it
  GET  /health    - liveness probe
  GET  /status    - HTML dashboard, auto-refreshing, big error-rate number + sparkline
  GET  /metrics   - JSON error-rate window, consumed by the observability MCP server
"""

import logging
import time
import traceback
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.pricing import apply_discount

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("checkout")
logger.setLevel(logging.INFO)
handler = logging.FileHandler(LOG_DIR / "app.log")
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s checkout: %(message)s", "%Y-%m-%dT%H:%M:%SZ"))
logger.addHandler(handler)

app = FastAPI(title="checkout-service-demo")

# Rolling window of (timestamp, is_error) for the last WINDOW_S seconds.
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
    recent = [e for e in _events if e[0] >= cutoff]
    total = len(recent)
    errors = sum(1 for _, is_error in recent if is_error)
    rate = (errors / total) if total else 0.0
    return {"window_s": window_s, "requests": total, "errors": errors, "error_rate": round(rate, 4)}


@app.post("/checkout")
def checkout(req: CheckoutRequest):
    try:
        total = apply_discount(req.cart_total, req.code)
    except Exception:
        logger.error("checkout failed for code=%r cart_total=%r\n%s", req.code, req.cart_total, traceback.format_exc())
        _record(is_error=True)
        raise HTTPException(status_code=500, detail="checkout failed")
    logger.info("checkout ok code=%r cart_total=%r total=%r", req.code, req.cart_total, total)
    _record(is_error=False)
    return {"total": total}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return JSONResponse(_error_rate(60))


@app.get("/status", response_class=HTMLResponse)
def status():
    m = _error_rate(60)
    pct = m["error_rate"] * 100
    color = "#2ecc71" if pct < 1 else ("#f1c40f" if pct < 10 else "#e74c3c")
    return f"""
    <html>
      <head>
        <meta http-equiv="refresh" content="2">
        <title>checkout-service-demo status</title>
        <style>
          body {{ font-family: system-ui, sans-serif; background: #111; color: #eee; text-align: center; padding-top: 4rem; }}
          .big {{ font-size: 6rem; font-weight: 700; color: {color}; }}
          .label {{ color: #999; }}
        </style>
      </head>
      <body>
        <div class="big">{pct:.1f}%</div>
        <div class="label">error rate (last {m['window_s']}s) &middot; {m['requests']} requests, {m['errors']} errors</div>
      </body>
    </html>
    """
